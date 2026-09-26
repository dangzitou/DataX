# PostgreSQL TIME / TIMETZ 无损往返修复

2026-09-27，生产代码 `58edd00`。本轮使用 PostgreSQL 17.11、PG JDBC 42.3.3 和完整 DataX Engine，修复 `time(6)` / `timetz(6)` 的静默失真。**它不提供整作业原子性、幂等重跑或亿级零差错保证。**

## 真实复现与结果

样本有 7 行：NULL、六位小数、午夜、`24:00:00`、正负时区偏移、带秒的偏移。对主键及两个时间字段的 PG 文本表示做双向 `EXCEPT ALL`，既比较值，也防止偏移变化被规范化后的时间值掩盖。JVM 使用 UTC、Asia/Shanghai、America/Los_Angeles 和再次 UTC，每种写入路径四次独立进程。

| 路径 | 修复前 | 修复后 |
|---|---|---|
| 上游原版 JDBC | 4 次均报告成功，但每次 6 行变值 | — |
| 旧候选 `a6ad15e` JDBC / COPY，默认连接 | 各 4 次均报告成功，但每次 6 行变值 | 各 4 次逐字段相同 |
| 旧候选 JDBC / COPY，显式强制二进制时间读取 | 各 4 次均报告成功，但每次 6 行变值 | 各 4 次逐字段相同 |

典型问题：`12:34:56.123456` 变成 `12:34:56.123`，`24:00:00` 变成 `00:00:00`，TIMETZ 的原始偏移也发生变化。每次双向差集为 12 条，表示 6 条旧值缺失及 6 条错误值增加，不是丢失了 12 个主键。修复前后目标行数始终为 7，说明只对行数无法发现这些错误。

修复后新增时间场景共 16 次完整 Engine 往返，全部零差异。原有 PG 字段/重跑风险检查 14 项、JDBC 驱动检查 26 项、COPY 检查 25 项也重新完成，均符合各自预期；其中包含原版问题复现、预期失败、重复及部分提交风险，不能称为 65 次无风险传输。相关模块单元测试 38 项通过。本目录共归档 101 次 Engine 检查（含 20 次时间失真复现），没有据此计算性能收益。

## 修复方式与兼容性

PG Reader 对 TIME/TIMETZ 使用 StringColumn 保留数据库文本，PG Writer 的 JDBC 参数继续通过原有 `?::time` / `?::timetz` 类型转换绑定，COPY 直接写入同样的时间文本。NULL 和已有 DateColumn 输入的写入路径保留。没有增加自定义时间解析器或依赖。

PG JDBC 42.3.3 的 `getString` 在二进制 TIME/TIMETZ 路径仍会经过 `java.sql.Time`。因此 Reader 在读取任务连接中将这两个 OID 加入 `binaryTransferDisable`，保留用户已经禁用的类型。单独的真实 JDBC 探针各跑 4 次：强制二进制时，每次 11 个时间字段变值；仅禁用这两种类型后为零。元数据实际传输格式从 `[1,1,1,0]` 变为 `[1,0,0,0]`，对应 BIGINT、TIME、TIMETZ、已禁用二进制的 INT4，证明其他列没有被整体切换到文本。

类型值域及微秒精度见 [PG 时间类型文档](https://www.postgresql.org/docs/17/datatype-datetime.html)；驱动转换见 [42.3.3 PgResultSet](https://github.com/pgjdbc/pgjdbc/blob/REL42.3.3/pgjdbc/src/main/java/org/postgresql/jdbc/PgResultSet.java)，禁用列表的优先级见 [PG JDBC 连接参数](https://jdbc.postgresql.org/documentation/use/)。

这是明确的内部类型变化：PG TIME/TIMETZ 现在是 StringColumn，`common.column.timeFormat` 不再裁剪这些值。依赖 DateColumn 的自定义 transformer、跨数据库 writer 需要单独验证。此次目标与源均为六位时间精度；较低目标精度、TIME 与 TIMETZ 跨类型写入、用户转换表达式、触发器及其他特殊类型不在本轮保证范围。已知重跑重复、失败残留批次和无共享快照问题仍存在。

## 复现

使用[构建指南](README.md)和专用 PG 测试容器；脚本替换 `datax_bench.pg_time_source` / `pg_time_target`，不要指向业务数据库。每次只有 7 行，不生成大规模落盘数据。

```sh
python3 benchmarks/postgresql_time_checks.py /tmp/datax-baseline /tmp/time-baseline --mode jdbc --expect-loss
python3 benchmarks/postgresql_time_checks.py /tmp/datax-candidate /tmp/time-candidate
python3 benchmarks/postgresql_time_checks.py /tmp/datax-candidate /tmp/time-binary --binary
```

[完整结果、原始配置与日志、构建指纹和驱动探针](results/2026-09-27-pg-time/)已归档。默认连接的早期复现配置固定 `common.column.timeZone=UTC`；最终脚本还将其随 JVM 时区一起变化，具体配置均在证据包。CI 已加入默认与强制二进制 URL 的完整时间回归，远端状态以具体提交 Actions 为准。
