# 共享整数转换与 PostgreSQL 参数类型回退修复

2026-09-27。整数转换提交 `1ea6ae7`，基准扩展 `fa413e1`，最终 JDBC 绑定修复 `3a0d252`。本轮先验证共享整数路径的实际收益，继而发现并修复了**本 fork 之前的整数绑定优化引入的 PG 性能回退**。完整结果仍不能证明全场景至少 +25% / +50%，也不是亿级生产可靠性验收。

## 已复现的根因与修复

原版 `80ec23d` 对整数使用字符串参数，非空值和 NULL 都绑定为 VARCHAR。本 fork 此前对 SMALLINT、INTEGER、BIGINT 的非空整数统一使用 `setLong`，但 NULL 使用目标列自己的 JDBC 类型。pgJDBC 42.3.3 中，setLong 使用 INT8，而 SMALLINT/INTEGER 的 setNull 分别使用 INT2/INT4；参数类型变化会使已准备的查询失效。相关驱动实现见 [PgPreparedStatement](https://github.com/pgjdbc/pgjdbc/blob/REL42.3.3/pgjdbc/src/main/java/org/postgresql/jdbc/PgPreparedStatement.java) 和 [QueryExecutorImpl](https://github.com/pgjdbc/pgjdbc/blob/REL42.3.3/pgjdbc/src/main/java/org/postgresql/core/v3/QueryExecutorImpl.java)。

真实 PG 17.11 + DataX Engine，128 行交替 NULL 的 smallint/integer，另含 bigint 极值；每版独立重复 4 次。驱动 FINEST 协议日志显示：

| 运行包 | 每次 INSERT Parse 数 | 参数 OID 向量 | 数据检查 |
|---|---:|---|---|
| 未修改原版 `80ec23d` | 1 | `{1043,1043,1043,1043}` | 128 行，双向 EXCEPT ALL 差异 0 |
| 修复前 fork `1ea6ae7` | 128 | `{20,20,20,20}` 与 `{20,21,23,20}` 各 64 次 | 同上 |
| 修复后 `3a0d252` | 1 | `{20,21,23,20}` | 同上 |

这里计数的是驱动记录的 Parse 消息，不是外部抓包，也不是“吞吐提高 128 倍”。测试说明该路径存在反复准备语句，且该夹具的值没有因此变化。源码、日志、配置和真实落库核对均保留。

修复在共享 JDBC writer 中按目标类型绑定：值在范围内时 SMALLINT 用 setShort、INTEGER 用 setInt，BIGINT 继续用 setLong。越过有符号目标范围的值仍用 setLong，由数据库处理实际范围和无符号语义；不直接强转截断。NULL 保留目标列类型。没有新增依赖、配置开关，未改变事务批次、提交异常禁止重放或原子追加的发布检查。

## 共享整数路径的独立实验

LongColumn 只对长度小于 19 的整数文本采用 Long.parseLong，再保留为原来的 BigInteger 对象；更长整数继续走 BigInteger，科学计数法/小数仍走原有兼容路径。输出在有符号 64 位范围内使用 Long.toString；溢出检查用 BigInteger.bitLength。保留 rawData 类型、byteSize、NULL、无符号 64 位和更大整数，不用截断换速度。

新增差分检查把 20,013 个边界/随机输入与 BigDecimal/BigInteger 的精确结果核对，包含 Unicode 数字、有符号极值、超范围数、前导零、科学计数法及原有小数截断语义。**保留兼容语义不等于任意小数转整数都无损**；需要保留小数的 reader/转换器仍必须使用对应数值类型。

对上一版 `5b81262` 的整数导出，用户态 CPU 中位耗时从 2.29s 降至 1.87s，但完整 JVM 时间基本持平；混合字段导出还有变慢配对。该实验没有达到吞吐最低门槛，不能把 CPU 改善换算成端到端提速。修改前的两次 JFR 诊断分别只有 51 / 83 个执行样本；文件路径主要样本在 UTF-8 编码，数据库路径更多落在驱动/绑定。采样用于定位，不当作精确耗时比例。

## 百万行性能结果

每组双方各预热一次，再做五对 AB/BA 交替运行。完整 JVM 启动至退出计时，不含重建目标、导出参考文件和对账。初次固定计划为八组；其中稳定出现普通表相对原版回退，随后用上述 4 次真实复现定位根因，再预先固定四组绑定修复对照。所有先前结果保留，不跨组拼接最慢基线和最快候选。

初版候选 `1ea6ae7` 的八组结果：

| 场景 | 参照 | 参照中位秒 | 候选中位秒 | 吞吐变化 | 最差配对 |
|---|---|---:|---:|---:|---:|
| 八列整数→文件 | 上一版 5b81262 | 1.994 | 2.003 | -0.42% | -9.92% |
| 八列整数→文件 | 原版 80ec23d | 2.194 | 2.014 | +8.93% | -8.79% |
| 六列混合→文件 | 上一版 5b81262 | 2.379 | 2.610 | -8.84% | -20.35% |
| 六列混合→文件 | 原版 80ec23d | 2.864 | 2.381 | +20.32% | +11.14% |
| 普通表单路→PG | 上一版 5b81262 | 10.107 | 10.106 | +0.01% | -1.19% |
| 普通表单路→PG | 原版 80ec23d | 8.496 | 10.111 | -15.98% | -24.28% |
| 普通表四路→PG | 上一版 5b81262 | 6.525 | 6.437 | +1.37% | -8.26% |
| 普通表四路→PG | 原版 80ec23d | 6.353 | 7.392 | -14.06% | -21.41% |

最终候选 `3a0d252` 的四组结果：

| 场景 | 参照 | 参照中位秒 | 候选中位秒 | 吞吐变化 | 最差配对 |
|---|---|---:|---:|---:|---:|
| 普通表单路→PG | 修复前 1ea6ae7 | 10.126 | 7.861 | +28.82% | +21.87% |
| 普通表单路→PG | 原版 80ec23d | 8.716 | 8.064 | +8.08% | +4.45% |
| 普通表四路→PG | 修复前 1ea6ae7 | 7.289 | 6.270 | +16.25% | +12.07% |
| 普通表四路→PG | 原版 80ec23d | 6.519 | 6.048 | +7.78% | -8.68% |

**12 组全部未达到每轮 +25%，+50% 也全部未通过。**单路相对修复前 fork 的中位增益虽为 28.82%，最差配对只有 21.87%；相对原版的中位增益是 8.08%，不能混用参照。四路相对原版中位 +7.78%，仍出现 −8.68% 的配对。共 **144 次、每次 100 万行**的性能运行，包括双方预热，全部完成对应数据核对；多次百万行不等同一次亿级容量测试。

整数文件是复用现有百万行源表的八列整数投影，无新大表、无自动 querySql 分片。完整参考文件为 105,415,868 字节，SHA-256 `3f509cebcddbc69cfdc7bc52e66ecffec751c25a89646fc433290aad279a3e66`。混合字段文件仍为六列、315,659,578 字节，SHA-256 `360d92105dcce61a0bb7bb58604bb8f4e1a19d709d81506784851ddd7fe592e9`。每次比完整 SHA-256、字节数和行数，立即删除文件。该文本格式不为任意字段提供可逆转义；本次整数/已知混合夹具才在此验证范围内。

普通表场景用原有 table/column、单路或 splitPk 四路，reader fetchSize=1024、writer batchSize=1024；都使用 JDBC INSERT，未开启 COPY、rewrite 或 atomicBatchId。目标有主键，每轮验证百万行总数及每个源键对应的全部字段。源是静态生成数据，不是持续更新的业务源。

同机 JDK 8u504、1 GiB JVM，PG 17.11 容器 4 CPU / 2 GiB，fsync/synchronous_commit/full_page_writes 均开启。计时期间没有本地构建、其他测试或证据压缩；远端 CI 独立运行。初版与上一版运行包仅 LongColumn / OverFlowUtil class 变化；最终与初版仅 CommonRdbmsWriter.Task class 变化，保留逐 class 哈希对照。文件实验对应 `1ea6ae7`，不能改写成最终提交另跑过的文件成绩。

## 最终正确性与故障回归

最终构建的 **54 项 JUnit、105 项纯编码器/工厂检查、223 次真实 Engine 功能/故障运行**符合断言。223 次包含预期失败和旧风险复现，不是全部成功迁移：

- MySQL 8.0.46：45 次 Engine / 46 个结果项。新增 4 次无符号 SMALLINT/INT 回归，覆盖 32768、65535、2147483648、4294967295 及 NULL；原有 unsigned BIGINT、缓存批次、类型、超时及错误分类检查保留。
- PG 绑定检查 20 次：上述正确绑定 4 次，加 smallint/int 正负越界各 4 次，共 16 次明确返回 SQLSTATE 22003、目标 0 行。修复未将越界输入绕回另一个整数。
- PG 字段/旧风险 14 次、原生 JDBC writer 26 次、COPY 25 次、原子追加 93 次。后者含实际提交回执丢失、同 ID 重跑、旧 owner 隔离与精度拒绝等原有断言。

最终 SelectDB 仍只有纯编码器检查，未测真实服务；本轮没有重新启动本地 StarRocks/Doris 做整套服务端回归。远端 CI 范围和结果另按提交标识查看。共享整数类和 JDBC setter 变化不构成全部插件、任意转换器或所有目标类型的证明。

## 证据与复现

```sh
python3 benchmarks/postgresql_binding_checks.py /tmp/datax-before /tmp/pg-bind-before --expect-churn
python3 benchmarks/postgresql_binding_checks.py /tmp/datax-candidate /tmp/pg-bind-after
python3 benchmarks/postgresql_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-table \
  --scenario table-single --rows 1000000 --rounds 5
python3 benchmarks/performance_gate.py /tmp/pg-table/results.json --threshold 25
```

before 需使用已知存在类型切换的本 fork 提交，未修改上游不加 expect-churn。基准文件场景为 pg-to-file / pg-integer-file，四路为 table-parallel。只使用专用 `datax_bench` 测试库，脚本会替换自有夹具表；构建和容器准备见[指南](README.md)。

[原始结果、配置、日志、源脚本、运行包哈希及存储记录](results/2026-09-27-integer/)按初版和绑定修复分开保存。带驱动 TRACE/JFR 的运行不计入性能结果。普通模式仍可能重跑重复、失败保留已提交批次；PG 原子追加仍限显式同 ID 的单表追加，未做亿级容量/锁等待/磁盘验收。全场景 +25% / +50% 与亿级零差错目标保持未完成。

新整数/协议夹具最大 128 行，MySQL 复用 1 万行功能夹具；百万行性能源沿用已有表。性能轮次开始时 PG 数据目录采样最高约 1.73 GiB，单次文件最大约 301 MiB；这不包含缓存镜像/Maven，也不是全过程硬配额或绝对峰值。收尾移除两个本轮 MySQL 容器及其匿名卷、百万行测试目标、两份已核对归档元数据的中间运行包和临时采样 JSON。PG 数据目录约 1.37 GiB，暂存表及测试客户端连接均为 0；临时根目录约 791 MiB，保留原版与最终运行包。精确数字见 storage.json。

归档时 `3a0d252` 的 [MySQL CI](https://github.com/dangzitou/DataX/actions/runs/36273743004) 已通过，[PG/StarRocks/Doris CI](https://github.com/dangzitou/DataX/actions/runs/36273742918) 仍在运行；JSON 保存当时状态。上一轮 `d9b2dec` 的 PG/StarRocks/Doris CI 后续已通过，终态也保存。
