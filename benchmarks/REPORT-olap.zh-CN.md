# StarRocks / Doris 百万行读写性能与磁盘约束

2026-09-27。基线为未修改上游 `80ec23d`，候选生产代码为干净构建 `38497ff`。测试复用现有 reader/writer，包含 CSV、JSON、普通表四路写入、读取到 PG 和读取到真实文件。**不能将局部中位收益理解为全场景提升 50%，也没有完成亿级零错误验收。**

| 场景 | 原版中位耗时 | 候选中位耗时 | 中位吞吐变化 | 最差配对变化 | 每轮 +50% |
|---|---:|---:|---:|---:|---|
| starrocks / PG → CSV 写入 | 7.597s | 7.648s | -0.67% | -6.47% | 未通过 |
| starrocks / PG → JSON 写入 | 8.991s | 9.019s | -0.31% | -2.74% | 未通过 |
| starrocks / PG 表四路 → JSON 写入 | 10.237s | 9.757s | +4.92% | +4.62% | 未通过 |
| starrocks / Reader → PG | 23.038s | 13.138s | +75.35% | -26.80% | 未通过 |
| starrocks / Reader → 文件 | 2.214s | 2.050s | +8.03% | -1.25% | 未通过 |
| doris / PG → CSV 写入 | 6.001s | 7.000s | -14.28% | -17.72% | 未通过 |
| doris / PG → JSON 写入 | 7.265s | 8.108s | -10.41% | -49.45% | 未通过 |
| doris / PG 表四路 → JSON 写入 | 5.055s | 4.375s | +15.56% | -8.60% | 未通过 |
| doris / Reader → PG，BE 2 GiB | 未计算 | 第 5 轮失败 | 不计算 | 不完整 | 未通过 |
| doris / Reader → PG，BE 3 GiB 对照 | 14.579s | 19.042s | -23.44% | -38.26% | 未通过 |
| doris / Reader → 文件，BE 3 GiB 对照 | 4.796s | 4.977s | -3.63% | -5.35% | 未通过 |

每组计划每版预热一次，再执行五对 AB/BA 交替顺序。八组原始场景完成，Doris→PG 原始组第 5 轮候选失败；另跑两组 BE 内存对照。共启动 132 次百万行传输，其中 131 次成功并完成校验，1 次作业失败；不包括造数、预试和 JFR 诊断作业。完整 JVM 生命周期计时；造数、重建/清空测试目标、存储检查和事后验证不计时。全部有效轮次保留，不用中位数覆盖失败的逐轮门槛。该测试没有新增 querySql 自动分片能力；四路使用 PG 已有的普通表 splitPk。

StarRocks→PG 的耗时波动特别大，原版某轮约 99 秒、候选某轮约 59 秒，同组末轮约 9/12 秒。原版慢轮的日志包含约 70 秒 WaitWriterTime，但仅凭该计数不能确定数据库、虚拟化存储或主机负载各自的影响。尽管这一组中位吞吐收益较大，最差配对仍为负数，不能据此认定稳定提速。没有剔除这些慢轮次。

Doris→PG 原始组的第 5 轮候选因服务端 `MEM_LIMIT_EXCEEDED` 失败：容器限制 5 GiB，但官方 all-in-one 测试默认 BE `mem_limit=40%`，即 2 GiB；日志显示进程约 2.02 GiB 时取消排序查询。该次 DataX 返回失败，目标查询确认 0 行，不能推导其他失败作业也都无残留。原始 11 次成功与失败日志全部保留。之后在容器总限制仍为 5 GiB 的情况下，两版均以 `BE_CONFIG_EXTRA="mem_limit = 60%"` 启动，重新完整比较读到 PG 和文件；表中明确标为 3 GiB 对照，不替代原来失败的 2 GiB 场景。配置机制见 [Doris 官方镜像文档](https://doris.apache.org/community/developer-guide/all-in-one-image/)。

## 正确性验证范围

所有 131 次成功性能传输均通过相应完整验证；失败作业单独记录，不能计入成功样本。样本为静态生成的百万行六列表：BIGINT 键、可空 INT、可空 DECIMAL(20,4)、可空整秒时间戳、真实中文/emoji 和 256 字节文本载荷。文本不含 CSV 分隔符；危险文本另有功能回归。本轮不是在线变化源、亿级单次传输、任意字段类型或故障恢复验收。

- OLAP 目标使用 DUPLICATE KEY，重复记录不会被主键表自动合并掩盖。检查总数、精确 COUNT(DISTINCT id)、键区间，以及按 id 独立计算的全部字段；每行都参与比较。
- 读取到 PG：目标有唯一键，核对总数，并将每个源键对应的全部六列用 IS DISTINCT FROM 比较，检查缺失、额外行和字段变化。
- 文件：逐字节与 PG 直接导出的完整参考文件比 SHA-256、字节数和行数，文件随后删除。参考文件 315,659,578 字节，SHA-256 `360d92105dcce61a0bb7bb58604bb8f4e1a19d709d81506784851ddd7fe592e9`。使用原有 streamwriter 文本格式，NULL 渲染为字符串 null；这不是适用于任意数据的可逆归档格式，也未增加 fsync。

新版另完成本地 StarRocks 16 项和 Doris 16 项小样本回归，包括原版 CSV 缺陷复现、新版拒绝、特殊文本 JSON 往返及真实 reader→PG 校验。它们不表示 32 次无风险传输。重跑重复、批次部分提交、缺少共享快照和未验证类型仍存在，见[回灌风险报告](REPORT-fidelity.zh-CN.md)。

## 本轮代码与剖析

StarRocks、Doris 的 JSON writer 改用已有 Fastjson `toJSONBytes`，减少生成整行 UTF-16 JSON 字符串后再编码为 UTF-8 的中间步骤。字段转换、既有字符串序列化接口、批次边界和重试方式保留；CSV 的歧义值拦截继续生效。没有新增依赖，也没有关闭持久化、约束或数据校验来获得分数。

优化前对原版/旧候选各采集一次 JFR。在候选 242 个执行样本中，UTF-8 编码和 JSON UTF-16 写字符串是两个主要叶节点；这用于确定修改位置，不代表耗时百分比或成功提速。原始 JFR 不公开，发布经过筛选的方法计数汇总。改动后单元测试共 36 项通过，新增 UTF-8 对照覆盖 NULL、长文本、特殊字符和 Unicode 多平面抽样。

StarRocks JSON 正式组的 JVM 用户 CPU 中位数从 8.43s 降至 7.40s，但整作业耗时约持平。这是整套候选相对原版的观察，不能将全部差异归因于单项字节化改动；CPU 秒与墙钟耗时也是不同指标。两轮试跑和正式结果都保留，未将预试的较好数字替代正式结果。

## 环境、磁盘与复现

Apple M5、10 个逻辑 CPU、16 GiB RAM，JDK 8u504，两版 JVM 堆 1 GiB。PG 17.11 容器 4 CPU / 2 GiB，fsync、synchronous_commit、full_page_writes 均开启。StarRocks 4.1.4 或 Doris 官方 4.1.3 ARM64 镜像，各限制 4 CPU / 5 GiB、单 FE/BE；Doris 服务实际版本显示 `doris-4.1.3-rc02-7126cf65d96`。两种 OLAP 服务顺序运行。

原版与候选配置一致，writer maxBatchRows=500000、flushQueueLength=1，strict_mode=true、max_filter_ratio=0。配置中的 maxBatchSize=5 MiB 对 StarRocks 生效；Doris 实际读取 batchSize，故两版 Doris 均使用其默认 90 MiB 字节阈值（此前写入的 maxBatchSize 对 Doris 无效）；JSON 同时使用 strip_outer_array=true。Reader→PG 使用原有 JDBC 写入、batchSize=1024，没有开启 COPY。普通表四路对两版使用相同并发；其他组一路。

测试期间发现历史专用 MySQL 数据卷占 76.56 GB、StarRocks 可写层约 9.13 GB，以及多份已归档的运行包。已删除这些生成数据和 12 份退休运行包，保留结果、配置、日志和指纹；系统可用空间从约 182 GiB 增到 263 GiB。没有删除业务容器、业务卷或进行全局 Docker prune。

五组 StarRocks 已在磁盘清理前完成。随后 Doris 每组使用重新创建的同版本专用容器，组末删除容器以回收数据。3 GiB 读取对照只提高 BE 在同一 5 GiB 容器中的份额，两版配置相同；写入组仍为默认 2 GiB BE。脚本新增一百万行本地样本上限，启动/每轮开始检查 OLAP 可写层与 PG 数据目录总量，默认预算 6 GiB、预留下一轮 1 GiB，并要求主机至少剩余 8 GiB。预算不足时拒绝启动已实测通过。这是轮次间保护，不是文件系统硬配额；容量不足导致未完成的测试不得记为通过。完整执行顺序和历史脚本保留在证据包中。

```sh
python3 benchmarks/olap_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/sr-json \
  --backend starrocks --scenario writer-json --rows 1000000 --rounds 5 --seed
python3 benchmarks/performance_gate.py /tmp/sr-json/results.json
```

只适用于专用 datax_bench 测试数据库，脚本会替换其中的测试表。场景选项为 writer-csv、writer-json、writer-parallel、reader-pg、reader-file；Doris 使用 --backend doris。每组独立运行并回收 OLAP 测试容器。构建和启动方法见[基准指南](README.md)及[CI](../.github/workflows/data-fidelity.yml)。

[完整证据目录](results/2026-09-27-olap/)包含逐轮结果、未通过的性能门槛、配置/日志压缩包、构建指纹、磁盘回收与 CI 结果。生产代码 38497ff 及其仅增加资源保护/文档的后续提交 4a94157 已通过远端 MySQL 和 PG/StarRocks/Doris 回归。全场景 +50% 与不丢、不重、不变值的端到端目标仍未实现。
