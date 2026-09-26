# PostgreSQL 整数读取：原生 JDBC 与完整对照

2026-09-27。运行代码 `2d2eab0`，驱动格式探针及最终检查 `2edf0e8`。本轮减少 PG 整数读取的中间转换，不改变事务、重试、分片或发布规则。全场景至少 +25% / +50% 和亿级零差错仍未完成。

## 改动与适用范围

PG reader 对 JDBC 整数列使用 getLong，紧接着检查 wasNull；仍构造原来的 BigInteger 型 LongColumn。复用已有构造函数接收源表示长度，非 NULL 值按十进制文本长度统计，避免把原来的读取字节统计全部变为固定 8 字节。NUMERIC/DECIMAL 不走这个路径，38 位整数和高精度小数不会被收窄。

该路径仅对 PostgreSQL 开启。其他驱动可能有无符号 BIGINT、补零格式及不同类型映射，保留其原有 getString 路径。没有新配置或依赖，也没有给 PG 默认开启强制二进制传输。pgJDBC 支持的二进制传输与 prepareThreshold 的关系见[官方连接参数](https://jdbc.postgresql.org/documentation/use/)；测试固定使用 42.3.3，不把当前文档的其他新功能当作该旧版本的能力。

## 真实数据库和故障检查

新增夹具仅 9 行，包含重复行与重复 NULL、smallint/integer/bigint 的正负极值、超过有符号 64 位的 38 位 NUMERIC、18 位小数。没有目标主键掩盖重复。querySql 与 table/column 两条入口，分别禁用二进制和请求强制二进制，每组合重复 4 次。

最终脚本在前版 `3a0d252` 与新运行包各完成 16 次 Engine，全部双向 EXCEPT ALL 差异为 0。额外的真实 JDBC/reader 探针每版 8 次：使用与 reader 相同的 DBUtil.query 与 fetchSize=2，读取驱动 Field 的实际 format，确认收到了文本或二进制整数；逐格比较原生路径与 getString 构造的 BigInteger 值、类型和 byteSize。两种模式均 9 行、三个整数列合计 123 字节。探针不是完整 Engine 或外部网络抓包，不能混入 Engine 次数。

最终运行包还通过 55 项 JUnit，以及 142 次既有 Engine 回归：PG 字段/旧风险 14、共享快照 15、原子追加 93、参数类型/越界 20。连同新增候选 16 次，共 158 次最终回归 Engine 运行；其中字段检查包含 4 次上游对照，且部分检查要求失败或复现旧风险，不是 158 次成功迁移。原子追加仍检查真实 COMMIT 回执丢失、同 ID 恢复及旧 owner 隔离。

开发记录保留：第一版试图从 reader 调用受保护的 setByteSize，编译失败；改为复用显式长度构造函数后完整单测通过。最初没有 JDBC 格式探针的两版各 16 次 Engine 也保留，最终统计不重复计入。没有发现字段差异后“修正目标数据”来通过检查。

## 百万行性能结果

| 场景 | 参照 | 参照中位秒 | 候选中位秒 | 吞吐变化 | 最差配对 | 每轮 +25% |
|---|---|---:|---:|---:|---:|---|
| 八列整数→文件 | 前版 3a0d252 | 1.982 | 1.791 | +10.66% | -12.70% | 未通过 |
| 八列整数→文件 | 原版 80ec23d | 2.167 | 1.817 | +19.30% | -1.44% | 未通过 |
| 六列混合→文件 | 前版 3a0d252 | 2.644 | 2.404 | +9.99% | -7.08% | 未通过 |
| 六列混合→文件 | 原版 80ec23d | 2.832 | 2.407 | +17.65% | +1.28% | 未通过 |
| 普通表单路→PG | 前版 3a0d252 | 7.936 | 8.079 | -1.76% | -3.21% | 未通过 |
| 普通表单路→PG | 原版 80ec23d | 9.080 | 8.300 | +9.39% | +7.01% | 未通过 |
| 普通表四路→PG | 前版 3a0d252 | 6.087 | 6.094 | -0.11% | -4.54% | 未通过 |
| 普通表四路→PG | 原版 80ec23d | 6.403 | 6.308 | +1.51% | -28.83% | 未通过 |

共 96 次百万行作业，包括双方预热；全部完成对应数据检查。八组中每轮 +25% 通过 0 组，+50% 通过 0 组。中位数不能覆盖变慢的配对；相对前版的结果不能当作相对原版的收益。

文件导出相对前版中位约 +10%，但整数/混合字段最差配对分别 −12.70% / −7.08%；普通表相对前版单路 −1.76%、四路 −0.11%。因此这是一项有局部收益、尚未建立稳定全场景收益的改动。四路相对原版最差 −28.83%，同样保留，不以中位数掩盖。读取路径减少中间转换不等于整个数据库传输自动加速。

同机 JDK 8u504、JVM 1 GiB，PG 17.11 容器 4 CPU / 2 GiB，fsync、synchronous_commit、full_page_writes 开启。原版与候选使用相同驱动、数据和配置。用户态 CPU 中位值另见 summary.json，不把 CPU 减少当作完整作业提速。

固定八组计划在计时前保存：八列整数→文件、六列混合字段→文件、普通表单路→PG、普通表四路→PG，每种分别对照前版与未修改原版 `80ec23d`。双方各预热一次，再测五对 AB/BA。完整 JVM 时间包含启动/退出，排除造数、重建目标和对账。文件每次核对完整 SHA256/字节数/行数并删除；表间每次核对百万行总数及全部字段。

所有任务复用原有静态百万行源表。fetchSize=1024、batchSize=1024、相同并发，默认 JDBC INSERT；未开启 COPY、rewrite 或 querySql 自动分片。计时期间没有本地构建、故障测试或证据压缩。相对前版运行包的 class 变化限 LongColumn 和 CommonRdbmsReader，包括后者的行号调试信息变化。

## 边界与证据

本改动不解决默认追加模式重跑重复、失败留下已提交批次的问题。PG 共享快照和原子追加仍需显式开启并满足各自限制；这些保障不自动适用于 StarRocks/Doris。小数据故障检查和多次百万行计时不能作为一次亿级容量、磁盘、锁等待、恢复时间或生产错误率的证明。

[原始结果、逐次配置/日志、构建哈希、源脚本及存储记录](results/2026-09-27-pg-integer-read/)保留全部对照和开发失败。[50 组历史门槛重判](results/2026-09-27-pg-integer-read/gates-25-all.json)仅 2 组通过，其余 48 组未通过或不完整；历史组包含中间版本和前版 fork 对照，不是当前 HEAD 的 50 种场景验收。

本轮没有新建大源表或启动其他数据库容器。性能阶段 PG 数据目录采样最高约 1.73 GiB，最大单次文件约 301 MiB，输出验证后立即删除。收尾删除已核对归档元数据的前版运行包（149,009,659 字节）及百万行测试目标表；保留未修改原版和本轮候选。PG 目录约 1.37 GiB，临时根目录约 807 MiB，没有原子暂存表或其他测试客户端连接。缓存镜像、Maven 和工作区不计入数据库夹具预算，采样不是全过程硬上限。

归档时 `2edf0e8` 的 [MySQL CI](https://github.com/dangzitou/DataX/actions/runs/36275306005) 已通过，[PG/StarRocks/Doris CI](https://github.com/dangzitou/DataX/actions/runs/36275306007) 在启动 StarRocks，此前 PG 步骤已完成，未出现失败；最终结果仍待该运行结束。前版 `3a0d252` 的两条 CI 均已成功，终态快照保留，不能替代新提交的完整 CI 结果。

```sh
python3 benchmarks/postgresql_integer_checks.py /tmp/datax-candidate /tmp/pg-integer-checks
python3 benchmarks/postgresql_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/pg-integer-file \
  --scenario pg-integer-file --rows 1000000 --rounds 5
python3 benchmarks/performance_gate.py /tmp/pg-integer-file/results.json --threshold 25
```

脚本只面向专用 datax-perf-postgres / datax_bench 测试库，会替换自有夹具表；不能指向业务数据库。构建与运行要求见[指南](README.md)。
