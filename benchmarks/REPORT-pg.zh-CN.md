# PostgreSQL 批量写入与百万行性能实测

2026-09-27。基线为未修改上游 `80ec23d`，候选为本 fork 的干净构建 `01f5530`。**六组全部未达到每轮吞吐至少提升 50%。** COPY 是新增的可选路径，不能将其称为通用提速 50% 的方案；它在本轮单路 querySql 中还略慢于原版。

| 场景 | 原版中位耗时 | 候选中位耗时 | 中位吞吐变化 | 最差配对变化 | 每轮 +50% |
|---|---:|---:|---:|---:|---|
| querySql → PG，COPY | 9.959s | 10.100s | -1.40% | -1.59% | 未通过 |
| querySql → PG，默认 JDBC 对照 | 14.972s | 11.909s | +25.73% | -16.78% | 未通过 |
| 普通表单路 → PG，COPY | 14.238s | 13.038s | +9.21% | +5.64% | 未通过 |
| 普通表四路 → PG，COPY | 10.211s | 8.284s | +23.27% | +18.31% | 未通过 |
| 四路生成记录 → PG，COPY | 6.608s | 5.232s | +26.32% | +12.65% | 未通过 |
| PG → 本地文件 | 4.991s | 4.375s | +14.06% | -12.83% | 未通过 |

每组每版先预热一次，再测五对 AB/BA 交替顺序，共 **72 次百万行传输**；计时包含完整 DataX JVM 生命周期，造数、清空目标和事后验证不计时。各组独立顺序运行，不跨组拼接“最佳原版”和“最佳候选”。默认 JDBC 对照与 COPY 组的基线耗时明显不同，说明共享开发机器、虚拟化存储和数据库状态仍有波动；这也限制了中位数的解释。所有有效轮次均保留，未通过的逐轮门槛不会被中位数覆盖。

两版每组使用相同 JDK 8u504、JVM 1 GiB 堆、UTC JVM 时区、读 fetchSize=1024、写 batchSize=1024、默认 batchByteSize=32 MiB、通道并发数与源/目标结构。仅标为 COPY 的候选显式设置 `useCopy=true`；这是数据写入实现选择，不是给候选提高并发或改变批次提交参数。PG 原有 `splitPk` 用于普通表四路；本轮没有给 PG 增加 querySql 自动分片。

PostgreSQL 17.11 ARM64，独立 Docker 容器限制 4 CPU / 2 GiB；`fsync`、`synchronous_commit`、`full_page_writes` 均开启。主机为 Apple M5、10 个逻辑 CPU、16 GiB RAM。未关闭日志、索引、约束或持久化来换取分数，性能运行没有与 Maven 构建或其他数据库基准并行。

## 数据正确性边界

性能源是静态生成的百万行表，包含六列：唯一 BIGINT 主键、可空整数、可空精确小数、可空整秒时间戳、真正的中文/emoji、256 字节文本载荷。整秒时间戳让未修改原版也能完整保真，亚毫秒精度另用功能测试验证，不能将两类证据混为一谈。

- PG 表间检查：两端主键保证唯一，核对总数，并按每个源主键用 `IS DISTINCT FROM` 比较全部六列，检测缺失、额外记录和字段变化。
- 生成记录写入：每个通道 25 万行、四路共百万行常量记录；目标无主键，验证总数及每行全部字段。这是隔离写入侧成本的样本，不代表百万条不同业务记录。
- 文件输出：使用已有 streamwriter 文件模式，逐字节与 PG 直接导出的完整参考文件比 SHA-256、字节数和行数。文件为 315,659,578 字节，SHA-256 为 `360d92105dcce61a0bb7bb58604bb8f4e1a19d709d81506784851ddd7fe592e9`。单个 querySql 显式按主键排序；这不表示并发 DataX 默认保证顺序。没有添加额外 fsync，不能称为持久化磁盘带宽测试。

上述 72 次传输均完整通过相应验证，但不是亿级、在线变更源、故障恢复或生产回灌验收。

## COPY 实现与回归

新增 `postgresqlwriter.parameter.useCopy=true`，默认仍走 JDBC INSERT。复用已有 writer 的缓冲、连接和批次边界，通过已有 pgJDBC 的 COPY API 传输，不新增运行依赖。表名由 PostgreSQL 的 regclass 解析后引用，列名由驱动转义；日期/时间/二进制格式使用驱动已有方法。

COPY 中所有非 NULL 值都加 CSV 引号，NULL 使用未引用的空字段。这样可区分 NULL、空串、字面 `\N`，并保留分隔符、引号、CR/LF。先用严格 CharsetEncoder 将整批编码为 UTF-8，再用字节流发送；非法 UTF-16 会失败，不会替换成问号。每批核对服务器返回行数；若触发器跳过行，整批回滚并报错。错误不被忽略，也不自动重放；此前已提交批次仍保留。

**25 项真实 COPY 检查通过**：三种 JVM 时区各重复四次；包含 nullable boolean、numeric(38,18)、微秒 timestamp/timestamptz、1970 年前时间、NULL/空串、字面 `\N`、8 万字符的长文本及 emoji、全部 256 种字节值；另含重复键回滚 4 次、触发器跳过行检测/回滚 4 次、带空格的限定标识符，以及非法 UTF-16 拒绝 4 次。检查脚本和日志公开，成功传输逐字段做双向 `EXCEPT ALL`。

COPY 的数据库语义不是所有 INSERT 的透明替代。语句级触发器执行次数、规则、RLS、默认/生成列等仍需按目标表验收，详见 [PostgreSQL COPY 文档](https://www.postgresql.org/docs/17/sql-copy.html) 和[插件用法](../postgresqlwriter/doc/postgresqlwriter.md)。该模式仍没有整作业原子性、幂等重跑或源一致快照；绝不能根据这些测试直接批准亿级生产回灌。

## 保留的失败尝试

1. 驱动 `reWriteBatchedInserts=true` 的两对百万行预试，吞吐分别 **-35.68%、-15.40%**，所以没有改为默认开启。另有 **26 项真实驱动检查**，覆盖两种模式下的字段、重复键逐行回退和 1024 行 × 100 列参数边界。各重复四次的语句级触发器检查确认：四行、两行一批时，原 INSERT 路径触发四次，合并后触发两次，即使目标表行本身一样，副作用也可能不同。
2. COPY 最初使用驱动的 Reader 重载，长文本中的 emoji 恰逢字符缓冲边界时会损坏，测试发现 8 万字符变成 80,001 字符。这个原型被替换为严格 UTF-8 字节流；失败日志保留，不算作成功验证。官方 API 提供 [Reader 与 InputStream 两种重载](https://jdbc.postgresql.org/documentation/publicapi/org/postgresql/copy/CopyManager.html)，本实现使用后者。
3. 最早造数脚本在进行数值转换前先做了 32 位整数乘法，百万行造数溢出，尚未运行 DataX。已改为先转 numeric 再乘，并重新完整造数；该预检错误不计入有效性能轮次。

## 复现与证据

独立测试容器和构建方法见 [PG / StarRocks 正确性说明](REPORT-fidelity.zh-CN.md)。`postgresql_checks.py` 创建功能测试的边界样本；`postgresql_copy_checks.py` 和 `postgresql_writer_checks.py` 在该基础上验证两种写入路径。仅用于 `datax_bench` 测试数据库，脚本会替换测试表。

```sh
python3 benchmarks/postgresql_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-query-copy \
  --scenario query-single --rows 1000000 --rounds 5 --seed --candidate-copy
python3 benchmarks/performance_gate.py /tmp/pg-query-copy/results.json
```

普通表场景为 `table-single` / `table-parallel`，隔离写入为 `stream-to-pg`，文件为 `pg-to-file`。默认 JDBC 对照不加 `--candidate-copy`；文件输出也不加该选项。基线、候选使用同一驱动版本，构建与测试进程串行执行。

[结果目录](results/2026-09-27-pg/)提供六组完整结果、门槛、配置/日志压缩包、构建指纹、失败预试与 CI 状态。全场景 +50% 仍未实现，StarRocks / Doris 性能与剩余正确性范围还要继续验证。
