# Doris 真实读写与文本保真检查

2026-09-27。复用现有回归脚本，新增 `--backend doris`，在真实 Doris 上完成 16 项检查。**这补充了此前只有单元测试的 Doris 覆盖，不能证明亿级零错误、故障恢复安全或性能提升 50%。**

| 检查 | 次数 | 实际结果 |
|---|---:|---|
| 未修改原版 PG → Doris CSV | 4 | 作业成功，但字面 `\N` 被静默写成 NULL，重复复现 |
| 候选 PG → Doris CSV | 4 | 明确拒绝危险值，本用例目标为空 |
| 候选 PG → Doris JSON | 4 | 5 行逐字段 UTF-8 HEX 相同，包含 NULL、字面 `\N`、中文/emoji、换行、制表符、引号、反斜杠和空串 |
| 候选 dorisreader → PG | 4 | 同一组 5 行，双向 `EXCEPT ALL` 无差异；PG 目标不设主键，重复记录不会被约束掩盖 |

原版 CSV 出错时导入响应的 loaded rows 与输入行数一致、filtered rows 为零。这直接说明只看作业成功、行数或脏记录计数不足以证明字段正确。候选采用已有的 CSV 拦截和 JSON 编码，未新增运行依赖；本轮没有修改生产 Java 代码。

基线为未修改上游 `80ec23d5c5328eb90ca364d2749e92dfaf44541e`；候选生产代码为 `01f553078083ecb5c73007cd1b0d2e8b8e4c3825`。候选运行包在测试脚本存在未提交修改时构建，完整 tracked-diff 指纹和 JAR SHA-256 保存在结果元数据，不能把整个构建工作区称为干净树。此次也用同一候选运行包复跑默认 StarRocks 分支的 16 项检查。

本地 Apple M5 / JDK 8u504，PostgreSQL 17.11，以及官方 `apache/doris:all-in-one-4.1.3` 的 ARM64 镜像；Doris 实际服务器版本报告为 `doris-4.1.3-rc02-7126cf65d96`。Doris 单 FE / 单 BE，容器 4 CPU / 5 GiB，FE_HEAP=1024m、BE_HEAP=512m；仅绑定本机测试端口。镜像 index 为 `sha256:82a5cabc7900ebcd3d080412d636c0cebd6602799fffe2605c652b2ea7d42609`，本地 ARM64 manifest 为 `sha256:76839a1beea086888529b042b5bc97a2116f63108365b00faa167f015ba1934c`。

本样本只有 BIGINT / STRING 两列，未覆盖其他 Doris 类型、目标表模型、在线变化、断网重试、进程中断、集群故障或提交结果不确定。CSV 拒绝用例仅证明该批小样本未写入；不能推导任何失败作业都不会保留此前提交的数据。生产回灌仍需固定数据视图、隔离暂存、重跑策略和完整对账。

启动与就绪检查见 [CI](../.github/workflows/data-fidelity.yml)；只适用于独立的 `datax_bench` 测试数据库，脚本会替换测试表。CI 顺序停止 StarRocks 后再启动 Doris，避免两者同时占用运行器内存。远端运行结果以 Actions 为准，本地通过不等于 CI 通过。

```sh
python3 benchmarks/starrocks_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/doris-checks --backend doris
python3 benchmarks/starrocks_checks.py /tmp/datax-baseline /tmp/datax-candidate /tmp/starrocks-checks
```

[原始结果、配置、进程日志和镜像信息](results/2026-09-27-doris/)均保留 SHA-256。每项耗时只用于追踪进程，本报告不据此计算速度提升。重跑重复、批次部分提交等问题详见[回灌风险报告](REPORT-fidelity.zh-CN.md)。
