# 写入失败后的队列停止与任务关闭

2026-09-27。生产改动 `305cd33`，测试增强及 gzip 代理修正 `82d630a` / `656fdf7`。本轮修复 StarRocks、Doris、SelectDB 异步管理器在一批终止失败后继续发送已排队后续批次的问题。**不提供整作业回滚、幂等重跑或亿级零差错保证，也没有新增吞吐提升结论。**

## 原因和修复

原实现将异常存入 `flushException`，但工作线程仍继续循环，取出下一批并请求数据库；调用方可能直到 `close` 才收到失败。同时每批完成都会重建定时执行器，正常关闭后工作线程仍在等待，Task 的空 `destroy` 也不取消待发送数据。

三份现有管理器分别作相同修复，没有新框架或依赖：终止失败记录错误后停止队列和定时器；Task.destroy 调用 abort；close 先完成正常刷出与队列屏障，再在 finally 清理。单个定时执行器复用，取消的定时任务及时移除。队列生产者使用带超时的 offer 并检查失败/停止状态，避免工作线程退出后生产者永久卡在满队列。关闭后继续写入会报错，重复正常关闭可成功，失败后的关闭仍报告失败。

这里的“终止失败”指原重试策略耗尽，或此前已规定不得重试的错误。SR 默认仍为 2 次尝试，Doris/SelectDB 为 4 次；没有改变它们的默认重试数量。abort 不能撤回已发出的请求或已提交批次；在途 HTTP 尚未返回时，工作线程可能仍存在，不能声称任何网络状态下都即时退出。SelectDB 既有 HTTP 客户端/响应的所有资源释放路径也未在本轮全面修复。

## 实际证据

| 层级 | 结果与边界 |
|---|---|
| JDK 8 单元测试 | 原有 55 项通过 |
| 实际管理器/Task/HTTP 客户端 + 本机模拟服务 | 新版 60 项通过：3 后端 × 5 场景 × 4 次；正常批次、定时刷出均逐字节核对 3 行（含中文 emoji、重复行），并验证线程回收、异常 Task 不再刷出缓冲行、满队列取消释放生产者 |
| 相同模拟服务的旧版对照 | 未修改原版 `80ec23d` 的 SR/Doris 共 8 次、前版 fork `2d2eab0` 的三后端共 12 次，均复现首批最终失败后仍发送两批尾部数据 |
| 真实 SR/Doris + 排队代理 | 每后端原版/修复版各重复 4 次，共 16 次。真实数据库拒绝首批无效整数，代理缓存该拒绝响应并在重试时原样重放；后续合法批次实际写数据库。原版最后报错但目标仍有 `(2,20),(3,30)`；修复版最后报错且目标为空。全部查询核对字段和重数。此项直接运行管理器，不含 PG reader 或完整 Engine |
| 完整 Engine + HTTP 模拟服务 | 56 次既有状态/行数回归符合断言，覆盖未知响应、少行、过滤行、超界计数等；不是 56 次真实数据库迁移 |
| PG→SR/Doris 及读回 PG 的完整 Engine | 两后端共 76 次小数据回归符合断言，含原版对照、预期失败和已知风险复现；验证 JSON/NULL/Unicode、CSV 拒绝、读取往返及过滤/排除行检测。失败但已部分提交的两行仍存在，未伪称整作业回滚 |

共 96 次管理器层最终检查和 132 次完整 Engine 回归，另有 55 项 JUnit。各层不混计，开发重复运行也不计入上述最终次数。数据库使用固定镜像的 StarRocks 4.1.4 与 Doris 4.1.3-rc02，均限制 4 CPU / 4 GiB，串行启动与删除；PG 17.11 保留原有专用测试实例和持久化配置。本轮只用 3–5 行夹具，没有新建百万或亿级数据。

开发失败一并归档：最初探针把 Doris 的 4 次尝试误判为 2 次；模拟 SelectDB 空响应写入零字节时触发 StreamClosedException；Task 检查误以为 DataXException 会包装原错误码；真实 Doris 返回 gzip，而自建测试代理最初未解压，导致失败响应无法解析。这些是测试夹具错误，修正后重新验证，不用作产品错误率统计。含有错误代理的 `82d630a` 旧 CI 被主动取消，修正提交单独运行 CI。

## 对亿级 PG 回灌的含义

这是减少失败后的额外写入。它没有让默认追加模式成为 exactly-once：重跑整作业会生成新的批次 label，之前写入的行可能再次追加；普通任务失败也可能留下前面已经成功提交的批次。StarRocks 的 label 去重本身有保留时间，不能把单次请求去重等同于永久的业务批次幂等，见[官方 Stream Load 说明](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/STREAM_LOAD/)。

源库还在修改时，需要同一快照或明确的增量衔接边界。PG 导出快照只在导出事务存续期间可供新事务导入；新一次作业并不会自动恢复旧快照，见[PG 快照同步语义](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION)。fork 的 `consistentSnapshot` 和 PG writer 的 `atomicBatchId` 都需显式配置并满足限制，后者不保护 SR/Doris 目标，也未完成亿级空间、锁等待和恢复时间验证。

严格校验可能增加明确失败的任务数：例如原版“成功但少行”的输入现在失败。这是识别原有错误，不代表数据错误率可用报错次数比较。现有测试无法估计生产错误率，也无法证明任意类型、转换器和故障组合无新增回归。字段保真要比较值、NULL、精度和重复行重数，不能仅比较 count。要把“这种不能出现”作为上线条件，仍需目标库对应的发布/恢复设计、固定源边界和逐批对账验收。

## 复现及证据

按[构建指南](README.md)准备 JDK 8 与对应插件；模拟测试不需要数据库：

```sh
python3 benchmarks/stream_load_queue_checks.py /tmp/datax-baseline /tmp/queue-before --expect-unsafe
python3 benchmarks/stream_load_queue_checks.py /tmp/datax-candidate /tmp/queue-after --backends starrocks,doris,selectdb
python3 benchmarks/stream_load_queue_checks.py /tmp/datax-candidate /tmp/sr-queue --backends starrocks --real-database
```

最后一项只能用于 `.github/workflows/data-fidelity.yml` 描述的本机专用容器/端口，会创建/清空 `datax_bench.queue_failure`。Doris 改 `--backends doris`。所有脚本均已接入 CI。

[结果、原始配置/日志、编译探针、源码快照及 JAR 哈希](results/2026-09-27-stream-queue/)保留正反对照和开发失败。编译探针以 base64 放入压缩 artifacts 的 `compiled_java_base64`，便于核对早期脚本版本；最终结果在 validation-summary.json 中明确列出。归档时新提交 `656fdf7` 的 [MySQL CI](https://github.com/dangzitou/DataX/actions/runs/36277564376) 已成功，[PG/SR/Doris CI](https://github.com/dangzitou/DataX/actions/runs/36277564509) 正在执行 HTTP 状态故障检查，最终结果仍待完成。本地通过不替代远端 CI 的终态。

收尾删除两个本轮 OLAP 容器及已核对归档的前版运行包（148,023,432 字节），保留未修改原版与当前候选。临时根目录约 814 MiB，PG 数据目录约 1.37 GiB，未遗留原子暂存表或其他测试连接；缓存镜像、Maven 和工作区未计入上述空间。没有亿级运行的空间上界结论。
