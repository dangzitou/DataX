# PG 显式批次原子追加：小数据真实故障验证

2026-09-27。生产实现 `a9f0db4`，测试夹具 schema 命名修正 `2ae138f`。本轮解决的是**显式开启的 PostgreSQL 单表批次追加**：正式表在完整暂存后一次发布，同一不可变批次 ID 重跑时核对内容并避免再次追加。默认模式不变，不是全量替换或 upsert。**尚不能承诺亿级生产零差错；全场景 +25% / +50% 目标仍未完成。**

## 实现与可观察行为

`postgresqlwriter.parameter.atomicBatchId` 命名一个内容固定的逻辑批次。使用同 schema 的 logged 暂存表和持久化 `__datax_atomic_batches_v1` 账本。最后一个事务负责追加正式数据、写批次登记、删除暂存表；中途任务失败不会将暂存行发布到正式表。提交完成但回执丢失时仍明确报错，重跑必须沿用同 ID，重新核对后决定是否已经发布。

源端应明确稳定的批次范围。PG reader 可用已有 `consistentSnapshot=true`；本轮夹具使用该选项和 `querySqlSplitPk=id`、4 channels。writer 本身不强制或证明任意 reader 的源快照正确性。

新增检查包括：

- Core 发布前拒绝脏行、转换失败、被过滤行和读写计数异常；任务另报告实际接收行数，与暂存表行数核对。修正 Job collector 在 scheduler 创建后绑定的问题，使用现有任务消息接口。
- 暂存去掉目标列的精度/长度限制，避免在暂存阶段先静默舍入；正式 INSERT 的 RETURNING 结果与暂存记录以 `record_send` 二进制值做双向 `EXCEPT ALL`，包括重复行重数。发现变化或遗漏即回滚。
- 同 ID 重跑核对列定义、行数和内容指纹。指纹对四段 SHA-256 的有符号整数分别使用 PG numeric 累加，避免 XOR 抵消重复项。**它是概率校验，不是数学上绝无碰撞。**正式发布的二进制多重集合对账是精确比较。
- 同批次 owner 使用会话 advisory lock。每个任务批次写入前锁定暂存表并校验本次代次标记，防止旧 owner 掉线后旧任务写入新 owner 的暂存区。原子模式禁止 JDBC 逐行 fallback；普通模式保留原行为。
- 拒绝 RLS、用户触发器、写规则、非 logged 原生表、多个目标/连接、preSql/postSql、伪造内部配置和不安全的同名账本。允许经检查的原生分区。失败清理不重新连接；清理等待表锁最多 5 秒，必要时保留暂存给下次同 ID 回收。

使用 PG 原生事务、锁和 RETURNING，没有新增依赖。相关语义见 [显式锁](https://www.postgresql.org/docs/17/explicit-locking.html)与 [RETURNING](https://www.postgresql.org/docs/17/dml-returning.html)。发布锁会阻塞目标并发写入；这些机制有真实成本。

## 最终实际测试

本机 PostgreSQL **17.11**、JDK 8、真实 DataX Engine。基准原版仍为未修改 `80ec23d`。候选运行包来自 clean `a9f0db4`，最终检查脚本来自 `2ae138f`（只改测试 schema 名，没有改运行代码）。fsync、synchronous_commit、full_page_writes 全部开启。8 行、9 列夹具含有意重复源行、NULL、bigint 极值、38 位 numeric、微秒时间戳/时区、中文 emoji、换行/分隔字符、bytea、空字符串。

**85 个结果条目，对应 93 次 Engine 运行，全部符合断言**；不是 93 次成功迁移，许多场景要求明确失败且正式表不受影响。结果来自 `pg-atomic-final-v2.json`，每次配置与日志在同名 artifacts 压缩文件中。

| 场景 | 最终结果 |
|---|---|
| JDBC / COPY 首次发布、同 ID 重跑、同 ID 改内容；各重复 4 次 | 首次与重跑均精确 8 行；重数与全部字段相同；改内容失败，原 8 行保留 |
| 两模式 numeric(38,18)→(38,6)、timestamp(6)→(3)；各重复 4 次 | 检测到发布改变数值，失败、正式表 0 行、无批次登记 |
| 正式表约束失败、过滤输入、转换脏行；各重复 4 次 | 即使 errorLimit=100 也拒绝发布，正式表 0 行 |
| 已有目标行 + 新批次约束失败；重复 4 次 | 原有一行所有字段不变，未追加也未登记 |
| PG 已提交暂存批次，TCP 代理丢弃 COMMIT 回执；重复 4 次 | 作业明确失败，正式表 0 行；同 ID 重跑最终精确 8 行 |
| PG 已提交正式发布与账本，TCP 代理丢弃 COMMIT 回执；重复 4 次 | 作业报告提交未确认，正式表精确 8 行、账本 1 行；同 ID 重跑仍为 8 行 |
| 同批次并发 owner、杀掉旧 owner 后新 owner 替换暂存，再恢复旧任务；重复 4 次，每组 3 个 Engine | 活 owner 阻止重复启动；旧任务代次不匹配而失败；新任务最终精确 8 行、账本 1 行 |
| 空批次、原生分区、非法选项、RLS、跳行触发器、结构错误的账本 | 空批次登记 0 行，分区精确 8 行；所有不支持配置均拒绝，未污染正式表 |

TCP 测试实际转发 PG 协议并在服务器发出 COMMIT 完成后断连；不是仅抛一个 Java 异常。发布故障还要求在同一事务看到账本 INSERT 与 DROP TABLE，避免误拦截建连或暂存事务。

另跑 **48 项单元测试**，以及 **48 次既有 Engine 回归**：字段保真/旧风险 14 次、JDBC rewrite/回滚/参数边界/触发器 26 次、原版与候选普通写入的真实 TCP 断连各 4 次。均符合原有断言；普通写入全量重跑重复、失败保留已提交批次仍被复现。这些回归不能称为全部安全迁移。不同表的功能检查有并行执行；日志耗时不作吞吐基准，本轮没有性能提升结论。

开发阶段失败也保留：最早检查直接比较读写计数，忽略 Channel 拉取计数包含每 task 一个 TerminateRecord，导致正常作业被拒绝，修正后再测；后来测试夹具误用 PG 保留的 `pg_` schema 前缀导致脚本中止，改为 `datax_` 后完整重跑。`pg-atomic-pilot`、`initial`、`v3`、`final` 是开发证据，最终验收只使用 `final-v2`。早期运行包包含未提交的新文件，不能用其 tracked diff 哈希完整重建；最终运行包有 clean 提交和全部 JAR 哈希。

## 亿级回灌仍未证明的事项

这不是整条业务链路的 exactly-once 承诺。不同 ID 追加同一批数据仍会重复；源本来存在的重复会如实保留。账本和目标表的身份、schema 必须稳定，不能人为清空账本、重建/迁移目标表或改写暂存区后继续沿用旧保证。源内容变化后重跑会拒绝，不能改 ID 绕过核对。

未证明任意 reader 的未报告丢行、所有特殊 PG 类型及自定义转换器无损，也不覆盖外部脚本/管理员对目标数据的修改。精度拒绝覆盖了本次 numeric/timestamp 夹具，不是所有类型之间任意转换的保真证明。正式业务仍要按源快照和批次边界对账，不能只看作业返回成功或 count 相等。

暂存保存整批数据，正式发布还需要事务 WAL、索引维护和二进制对账临时空间，并持有目标写锁。**没有亿级规模的空间、锁等待、故障恢复时间或吞吐验收。**不能将此前 querySql 单路径提速套用到该模式。PG→StarRocks/Doris/MySQL 也不因此具备本模式的发布/恢复保障；全量替换与 upsert 尚未实现。

本轮只建 8 行新夹具。收尾无原子暂存表、无测试遗留连接；PG 数据目录约 1.34 GiB（含以前的百万行源表），临时根目录约 751 MiB；清理了已归档的旧 commit-guard 运行包，保留原版和当前候选。精确字节数及存储环境见 `storage.json`。这不是本模式亿级运行的空间上界。

## 复现

```sh
python3 benchmarks/postgresql_atomic_checks.py /tmp/datax-candidate /tmp/pg-atomic-checks
```

按[构建指南](README.md)准备 JDK 8 与 postgresqlreader/postgresqlwriter，使用专用 `datax-perf-postgres`、端口 25432、`datax_bench`；脚本会重建自己的夹具表及 `datax_atomic_collision` schema，不应指向业务数据库。已加入 `data-fidelity.yml`。CI 必须按具体提交查看；本地通过不等同远端 CI 已完成。

[完整结果、配置、日志、源码快照和运行包哈希](results/2026-09-27-pg-atomic/)。
