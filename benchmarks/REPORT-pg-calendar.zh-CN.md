# PG 日期时间转换：静默改值的复现与拦截

2026-09-27。**此前 fork 与原版均存在“作业成功，但时间值已改变”的路径。当前修复让有损转换明确失败，并支持显式 PG 文本原样写回；不能据此承诺亿级零差错。新增校验有实测性能成本，全场景 +25% / +50% 仍未达到。**

## 复现的真实问题

在独立测试 PG 中，源字段 `timestamp '2024-03-10 02:30:00.123456'`，DataX 的 `common.column.timeZone=America/New_York`。`java.sql.Timestamp` 经过夏令时空缺转换后，旧 fork 写回的是 `03:30:00.123456`，而 Engine 报告成功。

- 未修改上游 `80ec23d`：四次复现，同时保留其已有微秒截断结果。
- 旧 fork clean `c9b499f`：普通追加四次、原子追加四次均复现一小时偏移。两个相同源行都被改变，二进制多重集合的双向差集共四项。
- 同一旧 fork 对 `1582-10-10` 的日期时间另做四次重复，观察到历法转换引入十天偏移。
- 原子模式只比较暂存与正式目标时，无法发现已经在 reader 内改变的字段；行数相等也不能发现。

首次 DST 探针只传了 JVM `-Duser.timezone`，而公共配置仍是 UTC。日志证明 Engine 覆盖了 JVM 设置；这一组作为 UTC 对照保留，不能计作纽约时区复现。之后同时配置 `common.column.timeZone` 并完整重跑，没有覆盖原始结果。

## 最终修复

PG reader 保留已有 DateColumn 类型，读取 DATE/TIMESTAMP/TIMESTAMPTZ 时，将 Java SQL 日期时间表示与驱动 JDBC 4.2 的 LocalDate、LocalDateTime、OffsetDateTime 表示比较。发现变化就抛出 SQLState `22008`，整个读取任务失败；不发送该记录，也不能用宽松 `errorLimit` 跳过它。NULL 和 PG 日期时间无穷值另有实际回归覆盖。

pgJDBC 的官方类型映射见 [Java 8 日期时间支持](https://jdbc.postgresql.org/documentation/query/#using-java-8-date-and-time-classes)。实际拦截与保真结论来自本目录的 Engine 结果，不能由驱动文档代替测试。

PG writer 的 JDBC 和 COPY 路径支持 DATE/TIMESTAMP 类型的 StringColumn 直接交给 PG 解析，沿用现有 TIME 文本路径。需要搬运 Java 日期表示无法无损表达的值时，可显式读取：

```sql
SELECT id, ts::text AS ts, tz::text AS tz, day::text AS day
FROM source_table
```

目标列仍须是对应的 PG 日期时间类型。此路径保留微秒、时区偏移、BC、日期无穷值和本次测试的宽日期范围；它不是其他 writer 的通用转换方案，也不自动保证任意 schema 或类型映射正确。

未保留“将所有 PG 日期时间字段默认改成 StringColumn”的原型：只转换本地日期/时间的版本仍在 timestamptz 历法检查失败；全部转换文本的版本虽然通过 32 个 PG 场景，却改变了现有 transformer 和其他 writer 的输入类型。最终采用保持接口类型的检测，在用户显式 `::text` 时使用 PG 原生写回。

## 验证结果

新增 [postgresql_calendar_checks.py](postgresql_calendar_checks.py)：UTC、纽约、上海、Apia 四个时区，文本/请求二进制读取，JDBC/COPY 写入。

| 检查 | 场景 | Engine 次数 | 结果 |
|---|---:|---:|---|
| 默认类型、安全值、原子发布 | 16 | 16 | 8 行、全部二进制字段与重复次数一致 |
| 默认类型、有损值、允许 100 脏行、原子发布 | 16 | 16 | 必须失败；预存目标行完全不变，账本为 0 |
| 显式文本、普通追加 | 16 | 16 | 全部 16 行精确相等 |
| 显式文本、原子发布及同 ID 重跑 | 16 | 32 | 两次均精确 16 行、账本为 1 |

合计 64 个场景、80 次 Engine。包含 NULL、重复记录、夏令时空缺与重叠、上海历史时制、Apia 跳过整日、1582 年历法边界、BC、正负 infinity、微秒、PG 宽日期范围。使用 `record_send(ROW(...))` 的双向 `EXCEPT ALL` 核对完整二进制行和重复数量，不以哈希相同代替精确比较。显式 `::text` 的 SQL 输出本来是文本，不把它当作日期二进制解码验证；默认类型组才覆盖两种日期读取模式。

最终运行包还通过：

- reader/writer 18 项 JUnit。
- 149 条原子回归结果 / 157 次 Engine，包含预期失败。
- logged 暂存的 24 次硬崩溃、72 次 Engine，严格检查与同 ID 恢复全部通过。
- 14 条基础 PG 保真/重跑检查，包括原版问题复现、普通追加重复及失败后部分提交的既有风险。

这些是测试场景计数，不是生产错误率样本，不能据此计算或承诺零错误率。

## 校验成本：没有提速达标

对照为之前 clean `c9b499f`；候选为 `be0b7c7` 加归档实现补丁。百万行、六列静态现代日期数据；普通 JDBC 追加，双方并发相同，COPY/rewrite/原子模式均关闭。每场景双方各一次预热、五对 AB/BA，24 次完整 Engine 均通过全字段对账。

| 场景 | 对照中位秒 | 候选中位秒 | 吞吐中位变化 | 最差配对 | 每对 +25% / +50% |
|---|---:|---:|---:|---:|---|
| 普通表单通道 | 7.9308 | 8.3640 | −5.18% | −6.63% | 均失败 |
| 普通表四通道 | 5.0952 | 5.2604 | −3.14% | −8.78% | 均失败 |

额外转换检查增加了成本，保留它用于拦截错误，不把它写成性能优化。计时涵盖完整 JVM 作业，建表和外部对账在计时外；使用所有预定配对，没有删掉慢轮。测量期间没有并行构建、数据库测试或压缩，其他项目容器仍运行。主机 Apple M5 / 16 GiB、PG 17.11、JDK 8u504、pgJDBC 42.7.13；本组是相对前版 fork 的校验成本，不是相对原版的全场景速度结论。

## 复现、边界与磁盘

```sh
python3 benchmarks/postgresql_calendar_checks.py /tmp/runtime /tmp/calendar-checks
python3 benchmarks/postgresql_scenarios.py /tmp/previous /tmp/candidate /tmp/perf \
  --scenario table-single --rounds 5
```

测试脚本只面向专用 `datax-perf-postgres` 测试容器，会创建和删除自己的夹具表。新日历检查已加入 data-fidelity CI；本报告的本地通过不替代新提交的 CI 终态。

[完整证据](results/2026-09-27-pg-calendar/)保留初次 UTC 对照、纽约重跑、历史日期复现、两个未保留原型、最终检查、配置、日志、构建元数据、预先记录的性能计划、全部计时和 SHA-256 清单。各原型及最终实现补丁的哈希均与相应构建元数据一致。首个原型留下的 `pending_run` 是真实失败，不能视为通过。

归档并校验后删除两个未保留的原型运行包，释放 325707969 字节（约 311 MiB），删除本轮百万行目标和小型日期夹具，保留原百万行源。临时根目录 1259248982 字节，PG 数据目录 1474899968 字节；无遗留暂存或测试连接。

**仍未解决的验收边界**：普通追加失败可能留下已提交的部分数据；不同批次 ID 的范围重叠仍可能重复；可变源的重跑不等于旧快照；目标端其他 writer 的提交与幂等语义不能套用 PG 原子模式。亿级真实表、实际目标类型映射、全量源目标对账、跨机故障与恢复均未完成。发现旧批次已被错误转换后，修复不会自动改写已发布数据，仍需核对与恢复。
