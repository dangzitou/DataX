# 各场景最低 +25%：已有证据重判

2026-09-27，按用户更新后的最低吞吐提升 25% 重新计算，50% 继续作为优化目标。**全场景最低 25% 仍未达成，数据正确性和恢复保证也未完成。**

复用原始计时与逐字段检查结果，不重新计时，不改写历史 +50% 文件。沿用保守验收方法：每组至少 5 个有效配对，每个配对吞吐都提升至少 25%，且所有数据检查通过。中位数达到 25% 而最差配对不足的场景仍未稳定达标。

30 组历史对照中仅 2 组满足该判据，其余 28 组未通过或不完整。它们来自各报告记录的不同代码提交、配置和环境；**这不是当前 HEAD 的全场景性能回归，也不是 30 种互不重叠的业务场景**，其中包含控制组和中间实现。

| 历史对照 | 吞吐中位变化 | 最差配对 | +25% 判定 |
|---|---:|---:|---|
| [MySQL querySql：单查询→自动四分片](results/2026-09-26-final/parallel.json) | +72.66% | +62.86% | 通过 |
| [MySQL querySql：手工四分片对照](results/2026-09-26-final/manual-control.json) | +3.72% | -9.44% | 未通过 |
| [MySQL querySql：相同单任务配置](results/2026-09-26-final/same-config.json) | +4.80% | -1.49% | 未通过 |
| [MySQL 普通表单任务](results/2026-09-26-scenarios/table-single.json) | +9.49% | +2.20% | 未通过 |
| [MySQL 普通表并行](results/2026-09-26-scenarios/table-parallel.json) | +4.78% | -5.68% | 未通过 |
| [MySQL→文件](results/2026-09-26-scenarios/mysql-to-file.json) | +0.05% | -12.17% | 未通过 |
| [Stream→MySQL](results/2026-09-26-scenarios/stream-to-mysql.json) | +7.83% | -7.00% | 未通过 |
| [PG querySql→COPY](results/2026-09-27-pg/query-copy.json) | -1.40% | -1.59% | 未通过 |
| [PG querySql 默认 JDBC 对照](results/2026-09-27-pg/query-default-control.json) | +25.73% | -16.78% | 未通过 |
| [PG 普通表单任务→COPY](results/2026-09-27-pg/table-single-copy.json) | +9.21% | +5.64% | 未通过 |
| [PG 普通表并行→COPY](results/2026-09-27-pg/table-parallel-copy.json) | +23.27% | +18.31% | 未通过 |
| [Stream→PG COPY](results/2026-09-27-pg/stream-copy.json) | +26.32% | +12.65% | 未通过 |
| [PG→文件](results/2026-09-27-pg/pg-file.json) | +14.06% | -12.83% | 未通过 |
| [Doris→文件（BE 内存 60%）](results/2026-09-27-olap/doris-final-reader-file-mem60.json) | -3.63% | -5.35% | 未通过 |
| [Doris→PG（BE 内存 60%）](results/2026-09-27-olap/doris-final-reader-pg-mem60.json) | -23.44% | -38.26% | 未通过 |
| [Doris→PG（默认 BE 内存 40%）](results/2026-09-27-olap/doris-final-reader-pg.json) | 未完成 | 未完成 | 不完整 |
| [Doris writer CSV](results/2026-09-27-olap/doris-final-writer-csv.json) | -14.28% | -17.72% | 未通过 |
| [Doris writer JSON](results/2026-09-27-olap/doris-final-writer-json.json) | -10.41% | -49.45% | 未通过 |
| [Doris writer 并行](results/2026-09-27-olap/doris-final-writer-parallel.json) | +15.56% | -8.60% | 未通过 |
| [StarRocks→文件](results/2026-09-27-olap/starrocks-final-reader-file.json) | +8.03% | -1.25% | 未通过 |
| [StarRocks→PG](results/2026-09-27-olap/starrocks-final-reader-pg.json) | +75.35% | -26.80% | 未通过 |
| [StarRocks writer CSV](results/2026-09-27-olap/starrocks-final-writer-csv.json) | -0.67% | -6.47% | 未通过 |
| [StarRocks writer JSON](results/2026-09-27-olap/starrocks-final-writer-json.json) | -0.31% | -2.74% | 未通过 |
| [StarRocks writer 并行](results/2026-09-27-olap/starrocks-final-writer-parallel.json) | +4.92% | +4.62% | 未通过 |
| [PG querySql：单查询→自动四分片](results/2026-09-27-pg-query/pg-query-million.json) | +85.48% | +78.51% | 通过 |
| [PG querySql：手工四分片对照](results/2026-09-27-pg-query/pg-query-manual-control.json) | -8.60% | -19.86% | 未通过 |
| [PG→文件：原版对照首版文件修复](results/2026-09-27-file/pg-file-records.json) | +7.89% | -9.20% | 未通过 |
| [PG→文件：上一版 fork 对照首版文件修复](results/2026-09-27-file/pg-file-records-control.json) | -6.58% | -33.97% | 未通过 |
| [PG→文件：原版对照最终文件修复](results/2026-09-27-file/pg-file-batch.json) | +9.47% | -6.71% | 未通过 |
| [PG→文件：上一版 fork 对照最终文件修复](results/2026-09-27-file/pg-file-batch-control.json) | +17.58% | +0.28% | 未通过 |

MySQL 历史性能源的非 ASCII 文本曾受 latin1 客户端建数影响，两版搬运的是同一实际夹具，但不是预期的中文内容；后来修复的 UTF8 正确性检查不能回填成这些历史性能用例已重测。详见[字符集说明](REPORT-scenarios.zh-CN.md)。

Doris 默认 BE 内存下的 reader→PG 比较有明确内存超限失败和缺少配对，不能计为通过；默认内存的 reader→文件尚未运行（NOT_RUN），内存 60% 的对照不能替代它。详见[OLAP 报告](REPORT-olap.zh-CN.md)。

两项通过都包含从单查询到自动并行的任务数变化；相同任务数对照没有达到最低目标。不能把这两项提升写成底层引擎、任意 SQL 或所有 writer 的普遍提升。最新 PG 检查和资源限制见[PG querySql 报告](REPORT-pg-query.zh-CN.md)。

亿级真实业务、源持续变化、未知类型/目标精度、跨作业幂等、隔离暂存和原子发布仍没有完整验收；性能通过也不能替代这些保证。下一步优化仍需面对未达标的读取、文件写入和数据库写入路径，不以降低持久性、跳过校验或隐藏回归达标。

[文件完整性修复及四组新增对照](REPORT-file.zh-CN.md)记录并发输出内容损坏的重复复现和修复，不将正确性改善写成 +25% 性能达标。

[最新机器可读重判结果及输入文件 SHA256](results/2026-09-27-file/gates-25-all.json)可用现有 `performance_gate.py --threshold 25` 复算；此前 26 组快照保留在 PG querySql 结果目录。
