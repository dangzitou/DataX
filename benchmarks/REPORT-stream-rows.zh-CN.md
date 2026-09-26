# Stream Load 缺行检测：真实 StarRocks / Doris 验证

2026-09-27，生产修复 `5e11e63`。**本轮修复“服务端返回 Success 但少写了行，DataX 仍成功”这处漏检，
没有实现整作业原子提交、重跑幂等或亿级零错误保证。全场景 +50% 仍未达成。**

## 真实数据库结果

运行完整 DataX Engine，源为 PostgreSQL 17.11，分别写入 StarRocks 4.1.4 和
Doris 4.1.3-rc02 单 BE。原版为未修改 `80ec23d`，候选为干净提交 `5e11e63`；同一 JDK 8u504、
同一数据库和相同任务参数。每轮 3 行、2 列，目标采用 DUPLICATE KEY，不依靠主键掩盖重复。

| 场景 | 每种数据库的原版 | 每种数据库的修复版 |
|---|---|---|
| strict_mode=true，max_filter_ratio=1；一行文本不能转换为 BIGINT | 4/4 报成功，目标只有 2/3 行 | 4/4 明确失败，保留已提交的 2 行 |
| where 排除一行合法输入 | 4/4 报成功，目标只有 2/3 行 | 4/4 明确失败，保留已提交的 2 行 |
| strict_mode=true，max_filter_ratio=0；含一行坏数据 | 作业失败，目标为空 | 作业失败，目标为空 |
| 全部字段合法、不配置过滤 | 本轮未额外测此控制 | 4/4 成功，3 行全部字段完全相同 |

合计 **44 次真实数据库作业检查**：16 次原版缺行漏检复现，16 次候选检测到缺行，
4 次服务端严格拒绝坏批次，以及 8 次候选完整写入。每次直接查询目标全部行、全部字段及重复的重数。
候选缺行场景还断言 HTTP 导入只发送一次，日志包含 `Incomplete Stream Load`。
失败用例保留下来的 2 行是测试明确验证的风险，不是“作业失败就全部回滚”。

额外重新运行两种数据库原有文本保真测试，共 **32 次真实数据库作业检查**：复现原版 CSV 的
字面 `\N` 转 NULL；候选拒绝不安全 CSV；候选 JSON 保存 NULL、空串、中文/emoji、换行、制表符、
引号及反斜线；两种 reader 写回 PG 后双向 `EXCEPT ALL` 完全相同。这些检查覆盖本轮改动的正常导入路径，
不代表其他任意类型都已验证。

## 修复与兼容性

两种 writer 复用一个小的行数验证函数，对 `Status=Success` 要求：

- `NumberTotalRows == NumberLoadedRows == 本批发送行数`。
- `NumberFilteredRows == NumberUnselectedRows == 0`。
- 四个字段都存在且能精确解析为整数；不使用可能截断小数或溢出的 `Number.longValue()`。

这四个字段由 [StarRocks Stream Load 协议](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/STREAM_LOAD/)
和 [Doris Stream Load 协议](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)提供；
成功状态本身并不表示没有行被质量规则或 WHERE 条件过滤。

计数不符合时抛出带标签和实际/预期计数的 `ProtocolException`，异步刷新器将其作为终止错误，
不再重试该批次。否则已提交的部分批次在下一次请求中可能返回 `Label Already Exists`，
标签可见性检查又将它判成成功，抹掉最初的缺行证据。没有增加依赖、配置开关或改变批次大小。

**这是有意的兼容性变化**：原先依赖 `where` 丢行、允许过滤坏数据或不返回完整计数的服务端，
现在会使作业失败。它增加的是已知不完整结果的报错，不能据此声称所有错误率都降低了。

## 故障模拟与单元检查

完整 Engine 对本机 HTTP 模拟器运行 **144 次**：原版 56 次、前一版 `7e5b38f` 32 次、候选 56 次。
其中每个 writer 有 14 个不完整/非法计数用例及 2 个合法计数用例，包含四次重复的过滤行响应、
未选中行、少/多写行、空批次计数、缺失、null、小数、负数及整数溢出。前一版的 28 个异常计数
仍全部漏检；候选全部明确拒绝，合法整数和数字字符串均通过。

模拟器在第二次请求时会返回可见的已存在标签，测试断言异常计数只发送一次，以覆盖重试掩盖失败的路径。
既有未知状态、缺失状态、Fail、Publish Timeout 和标签查询检查也重新执行。
**这些是协议故障注入，不是实际服务器持久化/网络故障测试。**

相关模块 **41 项单元测试通过**，其中新的一个数据驱动测试遍历四个计数字段及缺失、错误类型、
小数、溢出等输入。真实数据库和模拟器的毫秒级耗时仅记录运行过程，不用于性能结论。

## 仍然不能保证的部分

检查发生在提交响应之后，不能撤回部分写入；服务端 `strict_mode=true`、`max_filter_ratio=0`
应同时配置，但也不能替代逐字段对账。本次没有更改已有参数默认值或显式过滤配置。
宽松类型转换、精度不足、目标表合并相同键等可能在行数完全匹配时改变数据。

`Publish Timeout` 和网络重试后查询已存在标签仍遵循原恢复行为，未从这些状态证明每行完整性。
整作业失败仍可能保留已提交批次，跨作业重跑仍可能重复。源端 PG 共享快照也需要显式开启，
且不提供跨重跑的同一快照。本次没有完成暂存目标、发布事务或生产恢复协议。

因此，**不能把该版本描述为“已满足亿级不重复、不遗漏、不脏写”**。生产验收仍需针对实际源/目标表、
数据类型、并发更新和恢复方式完成完整对账与发布验证。

## 复现与资源

```sh
python3 benchmarks/stream_load_status_checks.py /tmp/datax-baseline /tmp/status-baseline --expect-unsafe --expect-incomplete
python3 benchmarks/stream_load_status_checks.py /tmp/datax-candidate /tmp/status-candidate
python3 benchmarks/stream_load_row_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/sr-row-checks
python3 benchmarks/stream_load_row_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/doris-row-checks --backend doris
```

JDK 8 构建及一次性容器启动方式见[指南](README.md)和 [CI 工作流](../.github/workflows/data-fidelity.yml)。
脚本仅适用于专门的 `datax-perf-*` 测试容器和 `datax_bench` 测试库，会替换其测试表；不能指向业务库。
新增检查已加入 CI，远端状态必须按具体提交查询，不能把旧提交的成功视为本提交成功。

本机为 Apple M5 / 16 GiB；每次只运行一个 OLAP 测试容器，限制 4 CPU / 4 GiB 内存。
缺行脚本只用 3 行，文本回归只用 5 行；每轮检查可写层，超过 4 GiB 即停止。
这是轮次间防护，不是硬磁盘配额。两个一次性 OLAP 容器验证后删除，仅保留小型结果、日志与构建证据。

[原始结果、配置、完整日志、构建校验及 SHA256SUMS](results/2026-09-27-stream-rows/)保留了包括缺行、
原版漏检和候选预期失败在内的全部检查。结果是本机小样本证据，不能外推生产错误率或亿级稳定性。
