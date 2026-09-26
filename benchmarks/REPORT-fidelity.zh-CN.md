# PG / StarRocks 字段保真与重跑风险验证

2026-09-26。**不能据此承诺亿级回灌零重复、零遗漏、零字段变化。全场景提升 50% 也尚未达成。** 本轮优先处理真实复现的数据正确性问题，使用本地真实数据库、生成的小样本、真实 DataX Engine 进程；不是亿级测试、生产验收或性能报告。

## 已复现与修复

| 检查 | 未修改原版 `80ec23d` | 本轮修复版 | 证据范围 |
|---|---|---|---|
| PG → PG：9 列，包括 timestamp(6)、timestamptz、nullable boolean、numeric(38,18)、bytea、中文/emoji | 4 次运行均成功，但 4 行均不完全相同 | 4 次逐字段完全相同 | 每次 4 行，双向 `EXCEPT ALL`，保留重复的重数 |
| PG → StarRocks 默认 CSV，普通字符串 `\N` | 4 次静默变成 NULL | 4 次明确拒绝该危险值；本用例目标为空 | 每次 3 行，实际服务器读回 HEX |
| PG → StarRocks JSON，NULL、字面 `\N`、中文/emoji、换行、制表符、引号、空串 | 未做此项比较 | 4 次完全相同 | 每次 5 行，以 UTF-8 HEX 逐行比较 |
| StarRocks Reader → PG | 未做此项比较 | 4 次完全相同 | 同一组 5 行，双向 `EXCEPT ALL`，目标无主键 |
| Doris CSV 序列化 | 同一不转义实现，未运行真实 Doris 服务器 | 单元检查拒绝危险值、已有 JSON 路径保留文本 | **仅单元测试，不能称为真实 Doris 集成通过** |

PG 原版不一致包含两个原因：原有 nullable boolean 读取会丢失 NULL，以及 timestamp 经 DateColumn / JDBC writer 丢失亚毫秒精度。前者已在此前提交修复；本轮让 DateColumn 保存 JDBC Timestamp 的 nanos，JDBC writer 直接绑定保留精度的 Timestamp，默认日期文本输出保留有效小数位。指定非默认日期格式时仍遵循该格式；普通 Date / epoch-millis 输入保留原行为。单元测试还覆盖纳秒、1970 年前时间、NULL、返回值被修改及自定义格式。

StarRocks 与 Doris 的现有 CSV 编码没有字段引用/转义。本轮在各自已有编码器中拒绝字面 `\N`、列分隔符和配置的行分隔符，报错提示使用已有 JSON 模式：

```json
"loadProps": {
  "format": "json",
  "strip_outer_array": true,
  "strict_mode": true,
  "max_filter_ratio": 0
}
```

这会使某些原来“成功但写错”的 CSV 作业现在报错，是有意的行为变化。JSON 路径已有实现，本轮没有新增序列化依赖或自动改换格式。已明确配置 CSV 的调用方需要选择合适格式；这些拦截不等于所有 CSV 参数组合、所有字段类型均已验证。此次真实 StarRocks 验证覆盖文本，没有验证任意二进制、复杂类型或所有时间类型到 StarRocks 的转换。

## 确实存在的风险，尚未解决

1. **直接重跑会重复。** PG 目标无主键，输入 4 行；第一次 4 行，第二次 8 行，双向差集检测到 4 行额外记录。两次作业均正常结束。PG writer 当前仍是 INSERT，不具有整作业幂等语义。
2. **失败不会全部回滚。** 4 次在最后批次注入重复键，`errorLimit.record=0`，每次作业均失败，但目标仍保留先前写入的 4 行。这个检查成功复现风险，并不表示风险已修复。
3. **并发连接没有共享快照。** 源持续写入时，分片可能看到不同时间点的数据。这里使用静态源，没有验证在线变化中的一致性。PG 的快照导出/导入需要合适隔离级别与事务执行顺序，见[官方事务文档](https://www.postgresql.org/docs/17/sql-set-transaction.html)。本 fork 没有实现这种协调。
4. **检测到脏数据不等于不会产生错误数据。** `errorLimit.record=0` 对已检测错误生效，不能发现所有静默精度/类型转换。TIME 小数、jsonb/uuid/array、特殊日期、目标精度小于源、触发器/约束、副本故障、网络断开后提交结果不确定等尚未完成全套验收。
5. **性能收益不能替代正确性。** 本轮毫秒级小样本耗时仅用于追踪运行，不计算速度提升。此前普通表/文件/单独写入场景只有约 0–10% 收益，仍未达到全场景 +50%。

对于不能容忍错误结果对业务可见的亿级回灌，需要针对实际链路建立：固定快照或静态源、稳定键与分片覆盖证明、隔离暂存目标、失败重跑策略、重复与缺失检查、逐字段对账、验收通过后发布。主键只能阻止部分重复，行数相等也不能证明数据相等。此处没有实施生产切换，也未声称已经具备这些端到端保证。

## 复现

环境：Apple M5、JDK 8u504，PostgreSQL 17.11 ARM64（fsync / synchronous_commit / full_page_writes 均开启），StarRocks 4.1.4 ARM64 单 BE。两版相同驱动与数据库。本地 PG 容器 4 CPU / 2 GiB；StarRocks 4 CPU / 5 GiB，均只映射本机端口。基线和候选构建顺序执行，避免 Maven SNAPSHOT 相互覆盖。

构建器增加 `--extra-modules` 选项，并修正只复制 `0.0.1-SNAPSHOT` 模块 JAR 的限制：StarRocksWriter 使用 1.1.0。打包会验证每个模块存在且包含 class，基线和候选使用同一构建器，不改原版源码或依赖版本。

```sh
python3 benchmarks/build.py ../DataX-baseline /tmp/datax-baseline --extra-modules \
  postgresqlreader postgresqlwriter starrocksreader starrockswriter dorisreader doriswriter
python3 benchmarks/build.py . /tmp/datax-candidate --extra-modules \
  postgresqlreader postgresqlwriter starrocksreader starrockswriter dorisreader doriswriter
```

独立测试容器的启动参数与就绪检查见 [.github/workflows/data-fidelity.yml](../.github/workflows/data-fidelity.yml)。脚本只适用于这些专门测试的容器与 `datax_bench` 数据库，会替换其中的测试表；不能指向业务数据库。

```sh
python3 benchmarks/postgresql_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-checks
python3 benchmarks/starrocks_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/sr-checks
```

[完整结果、构建指纹和原始日志](results/2026-09-26-fidelity/)保留每次配置与输出。PG 共 14 项（含风险复现），StarRocks 共 16 项（含原版缺陷复现与新版拒绝），不能表述为 30 次无风险传输。单元测试 34 项通过，覆盖公共读写、通道、PG 时间戳与两种 CSV 编码器；原有 42 项真实 MySQL 回归也重新通过。生产代码提交为 `9bf1520`，完整构建提交及内容指纹见结果元数据。GitHub 新增真实 PG / StarRocks CI，远端状态以 Actions 为准，不能把本地通过当作远端通过。
