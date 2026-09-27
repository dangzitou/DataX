# 分批 HTTP 正文：内存改善与未达标的吞吐结果

2026-09-27。最终生产实现 `b618ac3`，只保留 **StarRocks 和 SelectDB** 的流式正文；
Doris 上传恢复为本轮之前的实现。三个试验版本、负面结果和最终选择均保留。
**这是局部内存改善；全场景至少 +25%、争取 +50% 和亿级零差错均未完成。**

## 改了什么

原来先缓存编码后的每行，再用 `ByteBuffer.allocate` 拼出第二份整批正文，随后上传。
SR/SelectDB 现在复用 common 的 `BatchPayload`：只复制行引用，保持原有 CSV/JSON 字节顺序，
长度用 long 计算，避免整批长度转 int。调用方必须保持编码行的字节数组不变；现有 manager
收到的是编码器新生成的数组，排队时已与生产者的列表分离。

StarRocks 使用现有 Apache HTTP `EntityTemplate` 的可重复发送机制，提供准确 Content-Length
和可重新打开的流，写出端只使用固定 64 KiB 缓冲。SelectDB 沿用原有未知长度、chunked
的 `InputStreamEntity`，公开的 byte[] put 方法仍保留。没有新增依赖，没有改变行编码、
并发数、批次阈值、label 或提交策略。空批次、尾部分隔符和多字节分隔符保持原样。

它没有把全部作业内存固定为 64 KiB：编码行、队列、JDBC 缓冲、DataX Record 和并发任务仍占内存。
单个大字段、其他 OOM 路径、SelectDB 既有 HTTP 资源释放问题以及失败后的部分提交不由此解决。

## 受限堆与传输完整性

使用实际 visitor/HTTP 客户端访问本机哈希接收端，**不是数据库或完整 DataX Engine 测试**。
每个大批次实际分配 64 个不同的 1 MiB 行数组，不用同一数组重复引用来冒充 64 MiB 数据。
请求 JVM `-Xms32m -Xmx128m -XX:NewRatio=4`，实测 maxMemory=125304832 字节。
接收端仅流式计算长度与 SHA-256，不将正文写入磁盘；关闭 DEBUG wire 日志以避免记录大正文。

- 前版 fork `8b95214`：3 插件 × CSV/JSON × 4 次，共 24 次大批次均在发送正文前 OOM。
- 未修改上游 `80ec23d`：已构建的 SR/Doris 共 16 次相同大批次均 OOM。
- 最终 SR/SelectDB：16 次大批次全部传输完成，长度与 SHA-256 一致；SR 在读完第一份正文后
  再返回 307，第二次正文也完全匹配，检查确实覆盖重放。
- 最终 Doris：恢复旧实现后，同条件 8 次仍 OOM，明确记录为未解决限制，不计作成功传输。

各版本另检查短正文、空批次、中文 emoji、NULL 编码、有意重复行及 HTTP 500 后同 label 重放。
SR/Doris 核对 Content-Length，SelectDB 核对原有 chunked 方式。最终候选共 96 项客户端检查，
其中 SR/SelectDB 64 项成功，Doris 32 项包括上述 8 次预期 OOM。上游 64 项也包括预期 OOM。

干净构建通过 60 项 JUnit；小正文做字节数组精确比较。大于 2 GiB 的长度检查使用重复的
不可变数组引用和计数接收端，**没有实际分配或发送 2 GiB 正文，也不是数据库容量验证**。

## 同配置百万行对照与取舍

三版都先对照上一版正式运行包 `8b95214`。每版预热一次，然后五对 AB/BA，每次使用已有的
同一百万行、六列 PG 静态源。逐次检查总数、精确不同键数和全部字段；目标 Duplicate Key
表保留重复，避免用主键覆盖掩盖重复。每组 12 次完整 JVM 运行，共 144 次百万行作业。

| 试验 | StarRocks CSV | StarRocks JSON | Doris CSV | Doris JSON |
|---|---:|---:|---:|---:|
| 直接逐行写，`61d7c41` | −0.69% | −0.53% | +0.33% | −0.74% |
| 固定 BufferedOutputStream，`5188855` | +0.14% | +0.13% | +0.81% | −0.78% |
| 固定 ByteBuffer，`52ed53c` | −1.12% | −0.29% | −0.59% | +3.21% |

表内是相对前版的中位吞吐变化，不是相对阿里原版，也不代表显著的因果收益。
这些幅度大多接近持平；12 组每轮 +25% / +50% 门槛全部失败。不能将“内存不再溢出”
换算为速度提升百分比，也没有通过选择最快单轮来声称完成目标。

保留条件与每版计划在计时前保存。保留第二版中 SR 的两种格式及 SelectDB 的内存改动，
Doris 两种格式有一项未满足中位不下降条件，因此恢复该后端原实现。第三版也有退化，未采用；
没有为几个不到 1% 的差异增加各后端专用缓冲策略。最终 clean 构建逐个比较 class，确认
Doris 实现与前版相同，仅新公共正文类和 SR/SelectDB visitor 发生变化（含内部类）。

对照统一使用 PG 17.11、4 CPU/2 GiB，fsync、synchronous_commit、full_page_writes 均开启；
StarRocks 4.1.4 与 Doris 4.1.3-rc02，各 4 CPU/4 GiB。Doris BE 配额显式设为容器的 60%，
与之前已证明能完成独立字段校验的配置一致。双方 JDK 8u504、1 GiB JVM、单通道、fetchSize=1024、
5 MiB 字节批次、500000 行上限、队列 1，strict_mode=true、max_filter_ratio=0。
每组新建并删除专用 OLAP 容器；计时期间没有运行本地构建、压缩或其他功能测试。

## 最终版本对照阿里原版

最终 `b618ac3` 另与未修改上游 `80ec23d` 对照，沿用相同配置及五对 AB/BA，
每组含各一次预热。两组共 24 次百万行作业，行数、不同键数和六字段均一致。

| 场景 | 原版中位耗时 | 最终版中位耗时 | 中位吞吐变化 | 最差配对 | +25% / +50% |
|---|---:|---:|---:|---:|---|
| writer-csv | 6.983396 s | 6.954264 s | +0.42% | -3.48% | 未通过 / 未通过 |
| writer-json | 8.377967 s | 8.243428 s | +1.63% | -1.21% | 未通过 / 未通过 |

这两组与三版前版对照共 168 次已验证的百万行运行；不是一次亿级作业，也不是生产样本。
原版对照中同样存在变慢配对，不据此声称稳定提速。真实 SelectDB 性能未运行。

## 回归、复现和边界

最终版本另通过 98 次完整 Engine 状态/计数故障检查，以及 60 项 manager/Task 队列检查；
两类均使用模拟 HTTP，后者不是完整 Engine，均不能计为真实数据库迁移。
本地验证已完成；本次推送的远端 CI 需等待对应提交的 [Actions](https://github.com/dangzitou/DataX/actions)
终态，不能用历史提交通过代替。

初次客户端探针因测试分隔符误写为两个 `\\x` 前缀而失败，已保存原探针、编译文件、日志和
pending_run；改为解析器实际支持的 `\\x0D0A` 后重跑。该夹具错误不作为产品错误率统计。

按[构建指南](README.md)准备运行包，只使用专用测试容器。客户端检查不需要数据库：

```sh
python3 benchmarks/stream_load_payload_checks.py /tmp/datax-baseline /tmp/payload-before --backends starrocks,doris --expect-oom
python3 benchmarks/stream_load_payload_checks.py /tmp/datax-candidate /tmp/payload-after --backends starrocks,selectdb
python3 benchmarks/stream_load_payload_checks.py /tmp/datax-candidate /tmp/payload-doris --backends doris --expect-oom
python3 benchmarks/olap_scenarios.py /tmp/datax-baseline /tmp/datax-candidate /tmp/sr-csv --backend starrocks --scenario writer-csv --rounds 5
```

源码、运行包哈希、所有配置/日志、预先保存的计划、弃用版本与空间记录见
[完整证据](results/2026-09-27-stream-payload/)。每组之间限制数据库空间并预留 1 GiB，
总预算 6 GiB；这不是文件系统硬配额，也不是亿级运行的空间上界。测试复用既有百万行源，
不创建亿级数据集。14 个专用 OLAP 容器均已删除；验证归档与 JAR 哈希后，清理 5 个弃用
运行包，释放约 716 MiB。`/tmp/datax-perf` 收尾约 864 MiB，保留原版与最终候选；
既有 PG 数据目录约 1.37 GiB。证据归档约 4.6 MiB，未触碰其他项目容器。

**真实 SelectDB 服务端、亿级容量和故障恢复、所有数据类型以及生产错误率仍未验证。**
源快照、重跑幂等、隔离暂存、发布和逐字段对账仍是独立要求；本次改动不提供整作业回滚。
