# PostgreSQL querySql 自动分片：百万行结果与正确性

2026-09-27。自动分片生产提交 `3c40554`；随后 `cf5f164` 仅补充 PG 取消错误分类和测试。
性能实测使用 `3c40554`，最终正确性检查使用 `cf5f164`。读取/分片类的字节码哈希在两版一致，
但这里仍按实际运行的提交区分证据，不将旧二进制测试写成新二进制重测。

当前最低目标已调整为各场景吞吐至少 +25%，继续争取 +50%。**本轮只证明特定 PG 单查询通过；
同任务数对照反而下降，其他场景及亿级零重复、零遗漏、零脏数据目标仍未完成。**

## 百万行实测

每组使用同一百万行、6 列静态合成源：BIGINT 主键、nullable integer、numeric(20,4)、
timestamp(0)、含中文/emoji 的 nullable text、256 字符 payload。已核对源编码为 UTF8、
id 范围为 1..1000000，首条文本确为 `中文😀-1`。两版使用相同 fetchSize=1024、batchSize=1024、
JDK 8u504、JVM 1 GiB、UTC，未启用 COPY 或 reWriteBatchedInserts。

数据库为本机真实 PostgreSQL 17.11，容器限制 4 CPU / 2 GiB，fsync、synchronous_commit、
full_page_writes 均开启。源、目标有主键，每次目标重新建立；计时之外逐字段全量对账。
源/目标数量相同、主键约束以及每个源键的全字段一致共同检查本样本的缺失、额外记录和字段变化。

每组包含两版各一次预热、5 组 AB/BA 交替配对。指标为 JVM 启动至成功退出的总耗时，包含
快照创建、MIN/MAX 边界发现和任务启动；不包含建表、清理及对账。两组共 **24 次百万行作业**，
全部 100 万行、全部 6 列校验一致。测量期间没有并行构建或运行其他测试；宿主机不是专用性能服务器。

| 对照 | 原版耗时中位数 | 候选耗时中位数 | 吞吐变化（中位耗时比） | 最差配对 | 每轮 +25% / +50% |
|---|---:|---:|---:|---:|---|
| 原版一条 querySql → 候选自动四分片 | 10.704 秒 | 5.771 秒 | **+85.48%** | **+78.51%** | 均通过 |
| 原版手工四分片 → 候选自动四分片 | 5.622 秒 | 6.151 秒 | **−8.60%** | **−19.86%** | 均未通过 |

第一组各轮吞吐提升依次为 98.58%、89.44%、101.86%、85.18%、78.51%；耗时中位数减少
46.09%，不能把吞吐 +85.48% 写成耗时减少 85.48%。第一组日志确认原版 1 个任务、候选 4 个；
第二组两版都为 4 个任务，手工 SQL 使用与自动分片相同的整数边界。候选额外持有共享快照，
原版在这里依靠静态源保证比较数据相同。

因此收益主要体现为自动增加读取并行度，**不是底层管道普遍变快**。第二组包含自动边界发现、
共享快照和其他历史代码差异，不能仅凭这组下降把开销全部归因于某一个功能。
第一组 JVM RSS 中位数为 443.36 / 484.95 MiB，第二组为 436.83 / 452.61 MiB（原版 / 候选）。

## 实现及正确性检查

复用已有整数范围切分器和 PG 原生共享快照，未引入依赖。配置 `querySqlSplitPk` 时，PG 强制
要求 `consistentSnapshot=true`；边界查询及各任务使用同一快照。PG 分片列按实际输出大小写引用，
SQL 解析采用已有 Druid 的 PG 方言。NULL 键归入第一段，范围互不重叠，使用 BigInteger 处理极值。

`cf5f164` 的 **30 项真实 PG 检查均符合预期**，每轮至多 9 行、目标无主键，双向 `EXCEPT ALL`
保留重复的重数并比较全部字段：

- 四轮 NULL / 重复键 / BIGINT 极值，另覆盖 CTE、`::bigint`、大小写别名、多 querySql、空结果、
  全 NULL 键、常量键及单 channel。实际任务数同时从日志验证。
- 四轮在快照导出后、范围发现前移动分片键、删除和插入源行，输出仍与原始 9 行逐字段一致。
- 缺少共享快照、非法或大小写不匹配的键、文本键、多条 SQL 明确失败。
- dry-run 验证全部 querySql、普通 querySql、普通表、列缺失、分片键类型和查询取消；目标保持为空。
  在具有写权限的源账号下，带 DELETE 的 CTE 和执行 DELETE 的函数均被只读事务拒绝，源仍有 9 行。
- 所有检查结束后，该应用标记的源连接数均为 0。

既有共享快照检查在 `3c40554` 上再次完成 **15 项**，包括手工 querySql 并发更新、普通表范围、
快照被终止后的明确失败及配置拒绝。快照失效用例仍留下先前提交的 4 行；该风险没有修复。

相关模块最终 **43 项单元测试通过**。初始 25 项查询检查，以及扩展过程中一次失败的测试断言
和此前成功的 29 项结果也完整保留：该断言预期 `statement timeout` 文本，实际 PG JDBC 返回
`canceling statement due to user request`，并且旧分类使用通用 SQL 配置错误。

随后另做 4 次真实重复复现，再在修复后重复 4 次：设置 queryTimeout=1 执行 pg_sleep(3)，
始终明确失败、目标为空，修复后识别为 `DBUtilErrorCode-24` 并保留 `SQLState=57014`。
[PG 官方定义](https://www.postgresql.org/docs/17/errcodes-appendix.html)将此状态定义为查询取消，
它不能单独区分超时与外部取消，所以新诊断明确列出两种可能，不伪装成已经确定是哪一种。

## 边界与复现

共享快照只在一次作业内有效；重跑会获取新快照，已提交目标批次不会随作业失败整体回滚。
原 SELECT 必须确定且只读，random()、易变函数、外部数据和不稳定 LIMIT 不由快照固定；
用户手工配置的多条 querySql 本身也不能重叠。长快照会影响源端旧版本回收。
详见[PG Reader 配置](../postgresqlreader/doc/postgresqlreader.md)。

```sh
python3 benchmarks/postgresql_querysql_checks.py /tmp/datax-candidate /tmp/pg-query-checks
python3 benchmarks/postgresql_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-query-million --scenario query-parallel
python3 benchmarks/postgresql_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-query-manual --scenario query-parallel --baseline-manual-split
python3 benchmarks/performance_gate.py /tmp/pg-query-million/results.json /tmp/pg-query-manual/results.json --threshold 25
```

基线为未修改上游 `80ec23d`。构建见[指南](README.md)，仅在专用 `datax-perf-postgres` 容器及
`datax_bench` 测试库执行。测试源复用既有百万行表，没有扩到亿级；PG 性能脚本新增百万行上限和
轮次间磁盘检查：6 GiB 数据预算预留 1 GiB、宿主机至少留 8 GiB 空间；这是防护，不是硬配额。
两轮性能测试 PG 数据目录约 1.69–1.72 GiB，正常波动没有触发预算。旧运行包在归档构建证据后清理。

[结果、每次配置/日志、构建信息及校验和](results/2026-09-27-pg-query/)保留所有成功、预期失败和
测试断言失败。[全部既有对照按 +25% 重判](REPORT-25pct.zh-CN.md)单独列出，未修改历史 +50% 结果。
远端 CI 结果必须按具体提交查看，不能把旧提交的成功当作新提交验收。
