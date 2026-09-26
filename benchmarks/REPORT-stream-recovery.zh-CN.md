# 已提交批次的回执丢失与行数检测

2026-09-27，生产改动 `8b95214`。StarRocks/Doris 的 `Publish Timeout` 原先绕过计数检查；
`Label Already Exists` 在标签处于 `VISIBLE` / `COMMITTED` 时也直接成功，无法确认原批次是否少行。
本轮修复这两条误报成功路径。**这是提交后的错误检测，不能回滚已提交数据，也不是亿级零差错验收。**

## 改变的行为与代价

两个 writer 复用已有 `StreamLoadResponseValidator`，对 `Success` 和 `Publish Timeout` 都要求
发送行数等于总行数、加载行数，过滤和排除行数都为零。校验失败抛出原有管理器不会重试的
`ProtocolException`，停止后续排队批次。没有新增依赖、配置或上传格式。

对已存在标签仍先查询原来的状态；若已提交/可见，明确报 `Unverified Stream Load`，
附上 label 并要求对账。该响应不能提供原请求的完整计数；实测 Doris 在重复标签响应里返回的
计数均为 0，它们不是原批次加载计数。原来已中止标签的处理未改。

**完整导入后的回执丢失，也会从自动恢复成功变为明确失败。**这是有意的可用性取舍：
不把无法验证的数据判为成功。失败后正式表仍有已提交行，不能换 label 或盲目重跑整作业。
若需要自动恢复，仍需目标库对应的可靠导入审计、不可变批次身份和发布/对账协议。

计数完整的 `Publish Timeout` 不重放批次，也不等待数据可查询；该状态表示提交和可见性有区别，
见 [StarRocks Stream Load](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/STREAM_LOAD/)
及 [Doris 4.x Stream Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)。
计数检查仍无法识别字段转换、精度损失或目标主键合并造成的内容变化。

## 实际验证

JDK 8、PostgreSQL 17.11，StarRocks 4.1.4 与 Doris 4.1.3-rc02 顺序运行，
每个 OLAP 容器限制 4 CPU / 4 GiB。生产运行包来自 clean `8b95214`；与前版运行包比较，
变化仅涉及共享验证器和两个 HTTP visitor（含编译产生的内部类）。全部 JAR 哈希及类差异已保存。

| 检查层级 | 结果 |
|---|---|
| JUnit | 58 项通过，包括缺失/畸形/溢出计数、超时计数与已存在标签的拒绝 |
| 完整 Engine + 本机 HTTP 模拟服务 | 未修改上游 `80ec23d` 48 次、前版 fork `305cd33` 48 次、新版 98 次均符合断言。未知状态、少行、缺计数、VISIBLE/COMMITTED、回执断连等与正确响应分开检查；每种新增故障重复 4 次 |
| 完整 PG→SR/Doris + 响应代理 | 64 次，2 后端 × 前版/新版 × 4 场景 × 4 次；核对真实目标的每行两个字段及重复重数 |
| 既有真实数据库回归 | 76 次，包括两后端原版/候选行数检测、JSON/NULL/Unicode 和 reader 回到 PG 的字段比对；包含预期失败和旧风险复现 |

上述共 **334 次 Engine 运行**，其中 194 次仅模拟 HTTP；不能称为 334 次真实成功迁移。
另外的 58 项 JUnit 不混入 Engine 次数。只使用 3–5 行夹具，没有新建大数据集，也没有吞吐基准。

响应代理验证的基线是前版 fork `305cd33`，不是未修改上游；未修改上游在模拟服务与既有回归中单独保留。
每个后端的四种场景如下：

| 场景（各重复 4 次/版本） | 前版 fork | 新版 | 真实目标内容 |
|---|---|---|---|
| 服务端实际过滤 1/3 行，代理仅把 Success 改成 Publish Timeout | 作业成功 | 明确失败、不重试 | 两行仍已提交 |
| 服务端实际过滤 1/3 行，代理在提交成功后断掉回执 | 同 label 重试、查询标签后成功 | 同 label 重试确认已提交，随后明确失败、不再重试 | 两行仍已提交 |
| 三行完整，代理仅把 Success 改成 Publish Timeout | 作业成功 | 作业成功、只上传一次 | 三行字段一致 |
| 三行完整，代理在提交成功后断掉回执 | 标签恢复成功 | 明确失败，需对账 | 三行字段一致、没有重复 |

过滤是通过测试配置 `strict_mode=true,max_filter_ratio=1` 和一个无效整数实际触发的。
实际服务器响应、原始计数、同 label 重试的请求体 SHA-256、真实状态查询结果均保存。
**Publish Timeout 是代理注入的状态，没有强迫数据库真实发生发布超时；回执断连发生在真实数据库提交后。**
测试没有模拟整机断电、标签过期、跨作业重跑或网络故障的全部组合。

所有本地最终检查通过。检查脚本最后只补了 `pending_run` 保存，便于将中止任务保留为未验证；
更改前已启动的模拟套件与 StarRocks 使用的脚本单独以 `executed-8b95214-*` 保存，Doris 使用补记录后的版本。
当前提交的远端 CI 应按 GitHub Actions 终态核对，不能用本地结果或前一提交成功替代。

## 对亿级回灌的结论

明确报错数可能上升；这些故障注入不是生产随机样本，不能用于估算数据错误率，也不能证明无新增回归。
默认追加模式的失败后部分提交、跨作业重跑重复仍然存在。PG reader 共享快照需要显式开启，
跨重跑不会自动恢复旧快照；PG writer 的实验性原子批次不保护 SR/Doris 目标。

对“不能重复、遗漏或改值”的上线要求，需要固定源快照/增量边界、不可变批次与隔离暂存，
逐批核对全部字段及重复重数，验收后通过目标库支持的机制发布，并验证中断和恢复。
当前还缺实际目标库/版本、表模型、字段类型及源并发修改方式，尚未完成用户实际链路验收。
**全场景 +25% / +50% 和亿级零差错目标仍未达成。**

## 复现与磁盘

按[构建指南](README.md)准备基线与候选、专用 PG 和对应 OLAP 测试容器；不得指向业务库。

```sh
python3 benchmarks/stream_load_status_checks.py /tmp/datax-baseline /tmp/recovery-before --suite recovery --expect-unverified
python3 benchmarks/stream_load_status_checks.py /tmp/datax-candidate /tmp/recovery-after
python3 benchmarks/stream_load_recovery_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/recovery-sr
python3 benchmarks/stream_load_recovery_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/recovery-doris --backend doris
```

新增场景已接入 CI。脚本仅重建 `pg_load_recovery` 和 `datax_bench.load_recovery` 专属夹具，
每次运行前检查 OLAP 可写层不超过 4 GiB；这是轮次间检查，不是全过程硬配额。
收尾删除本轮创建的两个 OLAP 容器，保留既有百万行 PG 源表。
[结果、配置、完整日志、运行包元数据、源码及存储记录](results/2026-09-27-stream-recovery/)
有逐文件 SHA-256。没有亿级磁盘、锁等待或恢复时间的容量证明。
