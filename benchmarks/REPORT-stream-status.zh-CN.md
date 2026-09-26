# Stream Load 成功状态与中断检测

2026-09-27。生产修复 `a6ad15e`。本轮运行完整 DataX Engine、真实 writer 和异步刷新/重试代码，但服务端是 Python 标准库实现的本机 HTTP 故障模拟器，**不是 StarRocks/Doris 数据库或持久化故障验收**。

| 检查 | 原版 `80ec23d` | 修复版 |
|---|---|---|
| HTTP 200，Status 为未知错误字符串 | 两个 writer 各 4 次错误报告成功 | 各 4 次明确失败 |
| Status 为 null 或空字符串 | 两个 writer 均错误报告成功 | 均明确失败 |
| 缺少 Status、明确 Fail | 均失败 | 均失败 |
| Success、Publish Timeout | 成功，不重复发送批次 | 保持原行为 |
| Label Already Exists | 查询标签，VISIBLE/COMMITTED 后返回成功 | 保持原行为 |
| 标签轮询前线程已被中断 | 两个原实现各 4 次错误返回成功 | 各 4 次抛出带原因的 IOException，并保留中断标志 |

前五项共归档 48 次独立 Engine 进程检查，每次输入 3 行；最后一项使用单元测试直接进入轮询方法，检查中断发生后尚未联网的分支。修复前两项单元测试均失败，修复后完整相关模块 38 项单元测试通过。模拟器还核对每次请求的完整 UTF-8 内容、重试沿用同一标签、Publish Timeout 不重发、已有标签确实发起状态查询。

代码只接受已知的成功状态或已确认的标签状态。未知/null/空状态不能再直接落入正常返回分支；中断也不能跳出轮询后伪装成功。对于已提交但暂时不可见的 Publish Timeout，继续遵循 [StarRocks](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/STREAM_LOAD/) 和 [Doris](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/) 的协议，避免因为发布延迟重新导入。

本修复未添加整作业事务、稳定的重跑标签、跨作业幂等或输出字段对账；也未证明服务端过滤行数为零。Success 本身不能证明全部输入行无损写入。显式配置的过滤规则、目标类型精度、已提交批次、网络断开后的结果确认仍需要额外验收，不能据此承诺亿级零错误。

```sh
python3 benchmarks/stream_load_status_checks.py /tmp/datax-baseline /tmp/status-baseline --expect-unsafe
python3 benchmarks/stream_load_status_checks.py /tmp/datax-candidate /tmp/status-candidate
mvn -B -pl core,plugin-rdbms-util,postgresqlreader,postgresqlwriter,starrocksreader,starrockswriter,dorisreader,doriswriter -am test
```

使用 JDK 8，构建方式见[指南](README.md)。[结果、配置、请求摘要与日志](results/2026-09-27-stream-status/)均保留；该检查已加入远端 CI，CI 是否通过以具体提交的 Actions 状态为准。
