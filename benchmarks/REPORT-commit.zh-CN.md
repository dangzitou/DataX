# JDBC 提交确认异常：禁止自动重放已提交批次

2026-09-27，生产修复 `0872423`，补充故障检查与 CI 配置 `3634309`。本轮解决公共 JDBC writer 在提交异常后自动重放、可能重复插入的问题。**这不是整作业原子提交或幂等重跑；全场景最低 +25% 和亿级零差错目标仍未完成。**

## 缺陷与最小修复

`CommonRdbmsWriter.Task.doBatchInsert` 原来把执行批次和 `Connection.commit()` 放在同一个异常处理块。两者任一抛 SQLException，都会尝试 rollback，然后逐行自动提交重写该批次。但 rollback 成功也不能撤销此前已经成功的 commit：提交已发生、确认异常且连接仍可使用时，自动重写会产生重复。

修复将 commit 移出语句失败的逐行降级路径。提交抛 SQLException 时，尽力 rollback 清理，但无论 rollback 是否成功都停止，不自动重放；报告 `DBUtilErrorCode-25`，提示提交结果未确认、核对目标后才能决定是否重试。保留原始提交异常以及 rollback 的 suppressed 异常。语句执行失败且 rollback 成功时，原有逐行处理保留；rollback 失败则保留原始语句错误并停止。

这是公用 JDBC 路径的修复，真实验证覆盖 PostgreSQL 默认 JDBC 与 MySQL。使用独立 HTTP Stream Load 的 StarRocks/Doris 不走该路径；PG `useCopy=true` 也有自己的批次处理且原来就不自动重放。不能据此宣称所有 writer 的提交恢复均已验证。

## 两种不同的故障证据

### 真实数据库写入 + 模拟 JDBC 确认异常

最终检查各用未修改原版 `80ec23d` 和修复版，分别在真实 PostgreSQL 17.11、MySQL 8.0.46 上运行。每库每版 16 项，合计 **64 项**，每个场景重复 4 次。测试直接调用真实 CommonRdbmsWriter 批次方法，通过 Java Connection 代理在指定位置抛异常；不是完整 Engine，也不是网络断连测试。

每批输入 2 行，无目标主键，以独立观察连接读回实际提交值，核对每个键的重数和中文/emoji 文本。注入的 SQLState `40003` 来自测试代理，不是声称数据库实际返回该状态。

| 场景（PG、MySQL 均重复 4 次） | 原版 | 修复版 |
|---|---|---|
| 数据库完成 commit 后，代理抛确认异常，连接仍可用 | 返回成功，目标 4 行，每个键重复 2 次 | 明确失败，目标 2 行，每个键 1 次；不自动重写 |
| commit 执行前，代理抛异常 | rollback 后自动重写，目标 2 行 | 明确失败并 rollback，目标 0 行 |
| 批次执行前抛语句异常，rollback 成功 | 降级逐行写入，2 行精确一致 | 保留原行为，2 行精确一致 |
| 正常批次 | 2 行精确一致 | 2 行精确一致 |

因此，新版在提交异常时会比旧版更倾向于让作业失败；这是避免错误恢复的行为变化，不是声称报错率一定下降。失败后目标可能已经有完整的这个批次，不能看到失败就盲目重跑。

### 真实 Engine + PG TCP 连接中断

另做 **8 项真实 Engine 网络故障检查**：原版和修复版各 4 次。Python 标准库代理转发实际 PG 协议，确认同一连接已收到两次 INSERT 完成后，在服务器发出 COMMIT 完成消息时丢弃该消息并断开 TCP。输入共 4 行、batchSize=2。

这组中原版和新版都失败，目标都保留已提交的前 2 行，没有自动重复；连接断开后，旧版的 rollback 本身就失败，所以不能把这组说成“原版真实断网会重复”。新版会明确输出 `DBUtilErrorCode-25`，保留提交和回滚错误；原版缺少该诊断。两版均核对了前 2 行的全部值且未留下目标端测试连接。

初版代理错误地拦截了 JDBC 建连阶段的 COMMIT，作业重连后正常完成，导致测试断言失败。该日志和初版探针保留；最终探针增加 INSERT 完成的前置条件后重跑，不把建连失败当成批次提交失败的证据。

## 回归、存储与边界

相关模块 **32 项单元测试通过**，覆盖四种 SQLState/空状态下的禁止重放、异常链保留、关闭连接、正常提交，以及语句失败后仍正确恢复事务。

真实 Engine 另完成 **82 项既有回归**：PG 字段保真/风险复现 14 项、PG JDBC rewrite/回滚/宽参数/触发器 26 项、MySQL 42 项，均符合原有判据。其中仍明确复现无主键全量重跑重复和失败残留已提交批次，不能称为 82 次“无风险传输”。本轮没有新增性能计时或 +25% 通过结论。

最终检查前还保留了上一版 fork、最初原版检查和扩大字段校验前的结果。最初 Java 探针误用了 Fastjson 1 的导入名，编译失败，修正为项目已有的 Fastjson 2；编译错误也已归档，没有加入依赖。

只使用本机专用测试数据库。故障夹具只有 2–4 行，普通 MySQL 回归建数 1 万行；没有新建百万/亿级夹具。临时 MySQL 容器 2 CPU / 1 GiB，InnoDB、utf8mb4、`innodb_flush_log_at_trx_commit=1`、`sync_binlog=1`，数据目录约 226 MiB；测试结束移除该专用容器和其匿名卷。PG 继续复用现有容器。旧运行包在归档构建元数据后清理，详见存储记录。

这次修复消除的是**提交异常后自动逐行重放这一条风险路径**。当前仍没有确定未确认事务结果的通用协议，没有整作业隔离暂存、原子发布或跨作业幂等。真实业务还需要确定替换、按键更新还是带批次标识的追加语义，再实现相应发布与恢复保障；不以本轮测试代替它们。

## 复现与 CI

```sh
python3 benchmarks/jdbc_commit_checks.py /tmp/datax-baseline /tmp/pg-commit-before --expect-legacy
python3 benchmarks/jdbc_commit_checks.py /tmp/datax-candidate /tmp/pg-commit-after
python3 benchmarks/jdbc_commit_checks.py /tmp/datax-candidate /tmp/mysql-commit-after --backend mysql
python3 benchmarks/postgresql_commit_wire_checks.py /tmp/datax-baseline /tmp/wire-before --expect-legacy
python3 benchmarks/postgresql_commit_wire_checks.py /tmp/datax-candidate /tmp/wire-after
```

按[构建指南](README.md)准备 JDK 8 和运行包，测试只允许连接专用 `datax-perf-postgres` / `datax-perf-mysql`、`datax_bench`；脚本会重建自己的夹具表。原版 MySQL 对照也使用 `--backend mysql --expect-legacy`。

[结果、原始日志、探针源码、构建和存储信息](results/2026-09-27-commit/)包含所有故障、预期结果和测试自身失败。新增两类故障检查已接入 CI。为避免仅归档报告时重复构建，两条 workflow 的 push 事件忽略 Markdown 和结果目录，源码/测试脚本变更仍触发，pull_request 和手动运行仍完整执行；这是 [GitHub 原生路径过滤](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onpushpull_requestpull_request_targetpathspaths-ignore)，不修改任何测试通过条件。CI 状态必须按具体源码提交查看。
