# 异步线程内存错误的传播与退出

2026-09-27，生产实现 `6c77ab5`。SR/Doris/SelectDB 的后台上传线程和定时 flush 只捕获
`Exception`，`OutOfMemoryError` 会越过它们，导致后台任务退出但没有记录失败；满队列上的
生产者可能继续等待。修复使后台捕获 `Throwable`，先保存原始原因并执行已有的 abort，
再重新抛出 `Error`。没有重试内存错误，没有改变成功路径的数据编码或提交策略。

**这是错误传播修复，不是 OOM 消除、整作业回滚或亿级零差错保证。**

## 实测

JDK 8u504；每个故障 JVM 请求 `-Xms32m -Xmx128m -XX:NewRatio=4`。请求正文只有几行。
本机 HTTP 模拟端发送一个 80 MiB Content-Length 响应，按 8 KiB 分块写出；实际客户端
`EntityUtils.toString` 分配字符缓冲时发生真实 OOM，堆栈确认在 `CharArrayBuffer`。
没有手动抛异常来冒充这一分配失败，也没有将响应正文写入日志或文件。

| 层级 | 改动前 | 改动后 |
|---|---|---|
| 实际 manager + HTTP 客户端：3 后端各 4 次真实分配 OOM | 前版 fork `b618ac3` 共 12 次后台线程退出、错误未记录；生产者在额外 1 秒观察期仍阻塞，由夹具 abort 清理 | 12 次保存 OOM 原因、释放生产者、拒绝 close 成功、停止后台线程；每次只请求一批 |
| 定时 flush：3 后端各 4 次**合成** OOM 注入 | 12 次定时任务失败但错误未记录、调度器未关闭 | 12 次保存原因、关闭调度器，后续写入带原始 OOM 失败 |
| 完整 DataX Engine + HTTP 模拟端：SR/Doris 各 4 次真实分配 OOM | **未修改上游** `80ec23d` 共 8 次在 10 秒内未退出，由夹具超时杀死并回收 JVM；不是原版自行返回失败 | 8 次自行退出，退出码均为 1，约 3.53–3.63 秒；保留 OOM 堆栈，每次只请求一批 |
| 既有队列、正常关闭、定时刷新、Task 清理回归 | 本轮没有重跑旧版这组已有检查 | 60 项符合断言 |
| JUnit 干净构建 | 本轮没有重跑旧版单元测试 | 60 项通过，0 失败/错误/跳过 |

manager 检查的前版与 Engine 检查的未修改上游是不同基线，不能混称原版。
定时场景通过反射替换测试列表，让复制行引用时抛出预先构造的 OOM，明确属于合成注入。
生产者的一秒等待和 Engine 的十秒超时只证明观察窗口内仍阻塞，不是测得的无限时长。
以上均没有真实数据库；没有提交、断电、进程被操作系统杀死或恢复后的字段对账验证。

干净构建包记录生产版本及全部 JAR 哈希；与 `b618ac3` 逐 class 比较，只有三个 manager
及其匿名内部类变化。完整 Engine 检查在构建完成后补充到脚本，脚本版本另以 SHA-256 保存。
新增检查已接入 CI；应核对对应推送的 [Actions](https://github.com/dangzitou/DataX/actions) 终态，
不能用本地结果或前一提交通过替代。

初版探针完成 12 项后台线程复现；随后增加分配点堆栈断言时，两次开发运行误把 StarRocks
重定位过的 Apache HTTP 类名当成未重定位类，夹具断言失败。修正匹配后完整重复 24 项。
这些失败的日志、源码和 pending_run 均保留；不作为产品新增错误或成功传输统计。

## 对回灌的意义和限制

修复减少“后台已经失败、前台却还在等”的路径，但没有限制所有响应、行缓存、队列或大字段
的内存占用。系统级内存耗尽、无法再分配异常对象、外部强杀等不保证能执行 Java 清理逻辑。
已经提交或正在提交的数据仍可能保留；任务失败后不能直接重跑追加写入。

这里没有测吞吐，也没有生产错误率样本。不得把更快报错换算成传输提速；
全场景 +25% / +50%、亿级无重复/遗漏/改值以及真实 SelectDB 服务端仍未验收。

## 复现与空间

按[构建指南](README.md)准备 JDK 8 和运行包；以下检查仅连接自身建立的回环模拟服务：

```sh
python3 benchmarks/stream_load_fatal_checks.py /tmp/datax-before /tmp/fatal-before --expect-stall
python3 benchmarks/stream_load_fatal_checks.py /tmp/datax-candidate /tmp/fatal-after
python3 benchmarks/stream_load_fatal_checks.py /tmp/datax-baseline /tmp/fatal-engine-before --engine --backends starrocks,doris --expect-stall
python3 benchmarks/stream_load_fatal_checks.py /tmp/datax-candidate /tmp/fatal-engine-after --engine --backends starrocks,doris
```

第一条 manager 对照需要有 `flushThread`/`abort` 的前版 fork（本轮为 `b618ac3`）；
第三条 Engine 对照可使用未修改上游 `80ec23d`。不要把两个构建基线混用。
没有创建数据库、容器或大型落盘夹具。保留基线与最新运行包，旧运行包在验证证据归档后清理。
[配置、逐次结果、完整日志、探针源码、构建指纹与空间记录](results/2026-09-27-stream-fatal/)
附逐文件 SHA-256。
