# PG 原子暂存的 DOMAIN 舍入缺陷与批量写入试验

2026-09-27。发现并拦截了 fork 新增原子暂存路径中的静默舍入缺陷。**不能因此宣称生产错误率为零，亿级回灌和全场景 +25% / +50% 仍未验收。**

## 缺陷、影响与修复

在此前运行包 `973d440` 上，源查询返回 `1.2345::numeric`，目标列为 `DOMAIN AS numeric(9,2)`，启用 `atomicBatchId`。JDBC、COPY 各四次真实 Engine 作业都成功，但目标值已经变成 `1.23`。这不是异常提示不够清楚，而是新模式漏检了字段变化；不能据此前通过的普通 numeric/timestamp 用例推出所有类型都安全。

原因是暂存表的 CAST 仍保留了 DOMAIN 本身携带的精度。输入进入暂存时先被舍入，随后暂存与正式表的二进制比较只能看到两个相同的错误值。PG 的 [DOMAIN 文档](https://www.postgresql.org/docs/17/domains.html)说明它建立在另一个类型之上；仅去掉列上的精度声明不足以消除类型内部限制。

生产修复 `5f0c22b` 在创建暂存和启动传输前拒绝配置列中的 DOMAIN 和自定义类型。当前只接受 `pg_catalog` 类型且排除 DOMAIN，因此自定义类型的数组、复合类型、枚举也保守拒绝。支持这些类型需要单独证明源到暂存的保真性，不能绕过此检查强行宣布无损。普通非原子写入没有因为本次修复获得同样保护。

同一原始用例修复后 JDBC/COPY 各四次均明确失败、目标零行。修复采取的是拒绝不支持的映射，而非把 DOMAIN 转成其他类型后继续写入。

## 真实回归与范围

使用本机 PostgreSQL 17.11、JDK 8u504、pgJDBC 42.7.13，持久化设置开启。运行包从 clean `5f0c22b` 构建；最终测试脚本为 `e659bb6`，后者仅修正测试夹具，没有修改生产代码。

- 45 项现有单元测试通过。
- 原子回归新增 40 次真实 Engine 检查：DOMAIN、嵌套 DOMAIN、DOMAIN 数组、含 DOMAIN 的复合类型和枚举，JDBC/COPY 各四次。全部拒绝，已有目标行逐字段保持不变，未登记批次、未留下暂存。
- 包括既有精度拒绝、同 ID 重跑、内容变化、脏行/过滤、部分失败、真实 COMMIT 回执丢失及旧任务恢复隔离，最终原子回归共 125 条结果、133 次 Engine，全部符合断言。许多断言要求明确失败，不能称为 133 次成功迁移。
- 浮点原子检查另外覆盖 JDBC/COPY、二进制读取、过滤、首次发布和同 ID 重跑；8 条结果、16 次 Engine，14 行夹具的 float4/float8 二进制值及 numeric 文本均精确一致。

首次扩展测试在数组场景的校验 SQL 失败：直接比较 numeric 与数组类型不合法。Engine 当时已正确拒绝，但整套检查未完成；修正为相同自定义类型的源/目标 NULL 夹具后完整重跑。原始舍入的修复前后探针仍使用非 NULL 的 `1.2345`，没有用 NULL 用例替代缺陷复现。未完成的 `atomic-types-regression` 及其日志保留，最终验收使用 `atomic-types-regression-v2`。

这些都是小规模生成夹具。尚未覆盖所有内置类型转换、亿级空间/锁等待/恢复时间、实际业务主键与批次边界。该模式只保护 PG 单表追加；PG→StarRocks/Doris/MySQL 不会继承其事务和同批次重跑机制。不同批次 ID 的重叠数据仍可能重复，源端未报告的遗漏或转换仍需独立对账。

## 同期性能证据：配置试验，尚未默认开启

先对前版同一运行包做两次 JFR 诊断，普通 PG 单通道采得 4,869 个事件，生成数据写 PG 采得 2,250 个。writer 栈上 `flushIfDeadlockRisk` 分别出现 2,171 / 1,198 次，促使本轮验证驱动原生批量合并。采样次数不是可相加的耗时比例，采样运行也不参与速度比较。

随后只改变 JDBC URL 中 `reWriteBatchedInserts=true`，双方都使用 `973d440`，其余参数相同。每组一百万行，双方各一次预热和五对 AB/BA，24 次完整 Engine 均通过行数/全字段核验。计时期间没有构建、采样、压缩或其他本地测试。

| 场景 | 关闭→开启的耗时中位数 | 中位数比值的吞吐变化 | 最差配对 | 每对 +25% / +50% |
|---|---:|---:|---:|---|
| 普通 PG 表，单通道 | 7.8107s → 6.4141s | +21.77% | +14.91% | 均失败 |
| 生成数据写 PG，四通道 | 3.0614s → 2.2892s | +33.73% | +4.47% | 均失败 |

这既不是相对未修改上游的源码收益，也不是原子模式的完整作业收益。已有真实触发器检查证明批量合并可能改变 statement trigger 调用次数，因此本轮没有全局开启，也没有将试验结果宣称为已交付的默认提速。最初计划、所有配对及两个门槛的计算结果都保留。

## 复现与证据

只对专用测试容器运行，不要指向业务库：

```sh
python3 benchmarks/postgresql_atomic_checks.py /tmp/datax-candidate /tmp/atomic-types
python3 benchmarks/postgresql_float_checks.py /tmp/datax-candidate /tmp/atomic-float --atomic
```

[证据目录](results/2026-09-27-atomic-types/)包含修复前后探针、最终与失败回归、配置日志、构建哈希、批量试验及选定 JFR 栈事件。完整 JFR 含环境事件，不公开；公开的是 ExecutionSample/NativeMethodSample。复用已有百万行源，没有新建容器；沿用 6 GiB 数据预算的轮次间检查，它不是亿级空间上界。

归档后核验压缩内容和运行包哈希，删除本轮大体积诊断文件、已归档的旧中间运行包及测试目标表，释放本地文件约 348 MiB。临时根目录约 1.02 GiB、PG 数据目录约 1.37 GiB；收尾没有原子暂存表或遗留测试连接。保留原版、前一版完整运行包和本次 PG 修复运行包，后者没有打包 SelectDB；共有模块只改变 PostgresqlAtomicBatch.class，详见 class-diff 和 storage 记录。

归档时，代码提交 `e659bb6` 的 [MySQL CI](https://github.com/dangzitou/DataX/actions/runs/36288392465)已成功，[PG/SR/Doris CI](https://github.com/dangzitou/DataX/actions/runs/36288392478)仍在运行；本地通过不等于该远端检查已完成。
