# PG 共享快照与并发源变化

2026-09-27，生产实现 `9f71028`，缺失快照保护及最终检查 `7e5b38f`。新增 reader 参数 `consistentSnapshot=true`，使同一 PG 作业的分片边界查询和读取任务导入同一份原生快照。默认关闭；它是源端一致性功能，**不是整作业事务或幂等重跑**。

## 真实验证

PostgreSQL 17.11、PG JDBC 42.3.3、JDK 8，真实 DataX Engine。每次仅 8 行，目标无主键，使用双向 EXCEPT ALL 比较每个字段及重复重数。最终检查的源账号仅有源表 SELECT 权限；并发更新与故障注入由测试管理连接执行。

| 场景 | 未修改原版 `80ec23d` | 最终候选 |
|---|---|---|
| 两条 querySql，第一片查询开始后修改分片键、更新值、删除和插入记录 | 4 次均成功退出，但 8 行变成 9 行，只有 8 个不同键，双向差集 9 条 | 4 次均为原快照的 8 行、8 个键、字段零差异 |
| Reader prepare 后、边界发现前移动最小分片键 | 4 次读取更新后的值，与预先固定的参考相差 2 条 | 4 次保留导出快照的原始 8 行，零差异 |
| 第一片已导入快照后终止导出连接 | 无共享快照，未进行该项比较 | 4 次均明确失败，后续任务拒绝改用独立快照 |
| 多 connection、多主机 JDBC、用户注入内部快照字段 | 未比较 | 3 次均在写入前明确拒绝，目标为空 |

第二行是验证新增快照语义及边界覆盖：原版读取更新后的值本身符合其原有行为，不能将这项差异单独称为原版混合快照错误。第一行才是实际复现重复、遗漏和混合时点的并发测试。

快照失效测试中，目标仍保留第一片已提交的 4 行；失败不意味着整体回滚。最终 15 项均检查预期结果，不表示 15 次成功传输。前 12 项结束后，按专属 application_name 查询确认源连接数为 0。

初次上游测试暴露了测试脚本的调度假设：DataX 随机安排任务，未加屏障的一片可能先完成，使那一次刚好得到一致结果。该记录及断言失败日志均保留。最终脚本给两片都设置屏障，根据实际先开始的一片移动记录，随后完整重复四次，不靠剔除某次结果证明问题。证据包也保留旧候选 8 项和早期快照候选 12 项试跑。

## 实现及生命周期

Reader prepare 开启只读 REPEATABLE READ 事务并调用 pg_export_snapshot，连接保持至作业销毁。每个边界发现连接和读取连接在第一次数据查询前导入该快照。缺失、无效、过期快照均失败；没有自动降级为普通读取。机制见 [PG 快照同步文档](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION)和[事务文档](https://www.postgresql.org/docs/17/sql-set-transaction.html)。

还修复了 JobContainer 清理路径：writer.destroy 抛异常时仍然调用 reader.destroy，保留最初异常，后续清理异常作为 suppressed exception 附加。修复前单元测试的 8 次检查全部未清理 reader；修复后均执行清理。相关模块最终 40 项单元测试通过，包含缺失/畸形快照及原始 SQL 异常保留检查。

约束：一个 connection、同一 PG 数据库及单个服务端，不支持跨库共同快照或多主机故障切换。长快照可能延迟源端旧行版本回收；导出连接被服务端超时终止会令后续任务失败。并发 DDL、外部数据源、易变函数和非确定性 SQL 不由 MVCC 快照保证。重跑会创建新快照，仍需隔离暂存、幂等恢复及目标对账。不能据此宣称亿级无损或全场景 +50%；本轮小样本没有计算提速。

## 复现

专用 datax_bench 数据库；脚本创建只读测试角色，并重建 pg_snapshot_source、pg_snapshot_expected、pg_snapshot_target，不能指向业务数据库。八行数据不需要大规模落盘。

```sh
python3 benchmarks/postgresql_snapshot_checks.py /tmp/datax-baseline /tmp/snapshot-baseline --expect-inconsistent
python3 benchmarks/postgresql_snapshot_checks.py /tmp/datax-candidate /tmp/snapshot-candidate
```

[配置、逐次结果、日志和构建指纹](results/2026-09-27-pg-snapshot/)均已归档，含不满足测试调度假设的初始记录。该检查已加入 CI；实际远端状态以对应提交的 Actions 为准。
