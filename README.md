![Datax-logo](https://github.com/alibaba/DataX/blob/master/images/DataX-logo.jpg)

# DataX

> 当前目标为所有场景吞吐至少 +25%，继续争取 +50%；**全场景目标和亿级零重复、零遗漏、零脏数据均未验收**。见[全部已测对照](benchmarks/REPORT-25pct.zh-CN.md)。
> 最新[文件完整性修复](benchmarks/REPORT-file.zh-CN.md)解决并发输出行内容交错、分隔符截断及取消被吞掉；百万行 PG→文件中位吞吐 +9.47%，仍未达到 +25%。
> PG 已增加显式开启的[共享快照与 querySql 自动分片](benchmarks/REPORT-pg-query.zh-CN.md)，已测单查询→四分片吞吐 +85.48%；同任务数对照未达标。普通写入仍有重跑重复与失败残留已提交批次的问题。
> [JDBC 提交异常修复](benchmarks/REPORT-commit.zh-CN.md)：提交确认异常时禁止自动重放批次，避免在已提交后重复插入；PG/MySQL 故障注入及真实 PG 断连证据分别记录。普通模式的完整作业重跑仍不具备幂等保证。
> PG writer 另有实验性、显式开启的[批次原子追加](benchmarks/REPORT-pg-atomic.zh-CN.md)：暂存后事务发布，同一不可变批次 ID 重跑核对内容。限 PG 单表追加，未验收亿级容量，也不覆盖其他 writer。
> 最新[二进制字段实测](benchmarks/REPORT-binary.zh-CN.md)：修复 StarRocks/Doris/SelectDB writer 将 BYTES 折算成 long 的静默失真，默认拒绝未指定编码的 BYTES；已测 PG↔StarRocks/Doris 的显式编码往返。StarRocks 原生二进制 reader 仍须配置 `binaryColumns`，SelectDB 仅测编码器，未实测服务端。
> [整数路径与 PG 参数类型修复](benchmarks/REPORT-integer.zh-CN.md)：消除本 fork 可空整数反复准备语句的性能回退；最终百万行普通表相对原版单路中位 +8.08%、四路 +7.78%，仍未达到每轮 +25%。完整回退结果与修复前后对照均保留。

> [PG 原生整数读取](benchmarks/REPORT-pg-integer-read.zh-CN.md)：减少字符串中转，保留 NULL、整数值及字节统计；百万行整数/混合字段导出相对原版中位 +19.30% / +17.65%，普通表单路/四路 +9.39% / +1.51%。8 组每轮 +25% 均未通过，变慢配对及全部原始结果保留。

> [失败后的排队写入修复](benchmarks/REPORT-stream-queue.zh-CN.md)：SR/Doris/SelectDB 终止失败后停止后续排队批次，并关闭任务线程；真实 SR/Doris 对照确认原版失败后仍写入两行尾部数据，新版停止这些写入。已提交/在途数据仍不能自动回滚，亿级零差错尚未验证。
> [提交回执与不完整导入](benchmarks/REPORT-stream-recovery.zh-CN.md)：SR/Doris 的 Publish Timeout 同样校验行数；标签已提交但原始计数未知时明确失败，防止把可能少行的批次判成功。完整导入丢失回执也可能需要人工对账，不能盲目重跑。
> [上传正文内存与完整性](benchmarks/REPORT-stream-payload.zh-CN.md)：SR/SelectDB 消除整批正文的第二份内存复制，受限堆下验证重定向和重放字节一致；Doris 试验有性能回退，已恢复原实现。百万行吞吐目标仍未通过，不代表亿级回灌安全保证。
> [异步内存错误传播](benchmarks/REPORT-stream-fatal.zh-CN.md)：修复 SR/Doris/SelectDB 后台 OOM 未传回任务、满队列生产者继续等待的问题。原版 SR/Doris 的 8 次 Engine 检查超时未退出，新版 8 次明确失败退出；不回滚已提交数据，不构成零错误率保证。

> 本 fork 增加 MySQL `querySql` 自动并行、内存通道优化及 JDBC 错误/资源管理修复。
> 100 万行真实 MySQL 同步，单条 querySql 开启自动四路并行：七轮吞吐提升均超过 50%，最低 **62.86%**，按耗时中位数计算提升 **72.66%**，逐字段校验零差异。
> 该结果限于已测负载；不开分片、原版已手工并行的对照分别为 +4.80%、+3.72%，不代表所有任务普遍提升 50%。
> [最终实测报告与完整对照数据](benchmarks/REPORT-final.zh-CN.md) · [构建、配置及复现指南](benchmarks/README.md)（使用本 fork 的改进请按此构建，下面的上游下载包不包含改动）
> [非 querySql 补测与字符集勘误](benchmarks/REPORT-scenarios.zh-CN.md)：普通表单路/四路分别 +9.49%/+4.78%，文件输出约持平，生成数据写入 MySQL +7.83%。旧样本文本并非预期中文，说明已更正；PostgreSQL 亿级回灌的正确性和性能尚未验收。
> [PostgreSQL 百万行实测](benchmarks/REPORT-pg.zh-CN.md)：六组中位吞吐变化为 -1.40% 到 +26.32%，全部未达到逐轮 +50%。新增 COPY 默认关闭，不提供整作业原子性或幂等重跑。
> [字段保真与回灌风险](benchmarks/REPORT-fidelity.zh-CN.md) · [真实 Doris 读写检查](benchmarks/REPORT-doris.zh-CN.md)：部分静默失真已修复，但无唯一键重跑重复、失败留下已提交批次的问题仍未解决。PG 共享快照默认关闭，需要显式配置。全场景 +50% 与亿级零差错均未验收。
> [StarRocks / Doris 百万行性能与磁盘约束](benchmarks/REPORT-olap.zh-CN.md)：十组完整对照均未达到逐轮 +50%；另有一组因服务器内存不足未完成。成功的 131 次百万行传输均完成数据校验，失败记录保留。

[![Leaderboard](https://img.shields.io/badge/DataX-%E6%9F%A5%E7%9C%8B%E8%B4%A1%E7%8C%AE%E6%8E%92%E8%A1%8C%E6%A6%9C-orange)](https://opensource.alibaba.com/contribution_leaderboard/details?projectValue=datax)

DataX 是阿里云 [DataWorks数据集成](https://www.aliyun.com/product/bigdata/ide) 的开源版本，在阿里巴巴集团内被广泛使用的离线数据同步工具/平台。DataX 实现了包括 MySQL、Oracle、OceanBase、SqlServer、Postgre、HDFS、Hive、ADS、HBase、TableStore(OTS)、MaxCompute(ODPS)、Hologres、DRDS, databend 等各种异构数据源之间高效的数据同步功能。

# DataX 商业版本
阿里云DataWorks数据集成是DataX团队在阿里云上的商业化产品，致力于提供复杂网络环境下、丰富的异构数据源之间高速稳定的数据移动能力，以及繁杂业务背景下的数据同步解决方案。目前已经支持云上近3000家客户，单日同步数据超过3万亿条。DataWorks数据集成目前支持离线50+种数据源，可以进行整库迁移、批量上云、增量同步、分库分表等各类同步解决方案。2020年更新实时同步能力，支持10+种数据源的读写任意组合。提供MySQL，Oracle等多种数据源到阿里云MaxCompute，Hologres等大数据引擎的一键全增量同步解决方案。

商业版本参见：  https://www.aliyun.com/product/bigdata/ide


# Features

DataX本身作为数据同步框架，将不同数据源的同步抽象为从源头数据源读取数据的Reader插件，以及向目标端写入数据的Writer插件，理论上DataX框架可以支持任意数据源类型的数据同步工作。同时DataX插件体系作为一套生态系统, 每接入一套新数据源该新加入的数据源即可实现和现有的数据源互通。



# DataX详细介绍

##### 请参考：[DataX-Introduction](https://github.com/alibaba/DataX/blob/master/introduction.md)



# Quick Start

##### Download [DataX下载地址](https://datax-opensource.oss-cn-hangzhou.aliyuncs.com/202308/datax.tar.gz)


##### 请点击：[Quick Start](https://github.com/alibaba/DataX/blob/master/userGuid.md)



# Support Data Channels 

DataX目前已经有了比较全面的插件体系，主流的RDBMS数据库、NOSQL、大数据计算系统都已经接入，目前支持数据如下图，详情请点击：[DataX数据源参考指南](https://github.com/alibaba/DataX/wiki/DataX-all-data-channels)

| 类型               | 数据源                          | Reader(读) | Writer(写) |                                                                                                                       文档                                                                                                                       |
|--------------|---------------------------|:---------:|:---------:|:----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
| RDBMS 关系型数据库 | MySQL                           |     √      |     √      |                                       [读](https://github.com/alibaba/DataX/blob/master/mysqlreader/doc/mysqlreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/mysqlwriter/doc/mysqlwriter.md)                                       |
|                    | Oracle                          |     √      |     √      |                                     [读](https://github.com/alibaba/DataX/blob/master/oraclereader/doc/oraclereader.md) 、[写](https://github.com/alibaba/DataX/blob/master/oraclewriter/doc/oraclewriter.md)                                     |
|                    | OceanBase                       |     √      |     √      | [读](https://github.com/alibaba/DataX/blob/master/oceanbasev10reader/doc/oceanbasev10reader.md) 、[写](https://github.com/alibaba/DataX/blob/master/oceanbasev10writer/doc/oceanbasev10writer.md) |
|                    | SQLServer                       |     √      |     √      |                               [读](https://github.com/alibaba/DataX/blob/master/sqlserverreader/doc/sqlserverreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/sqlserverwriter/doc/sqlserverwriter.md)                               |
|                    | PostgreSQL                      |     √      |     √      |                             [读](https://github.com/alibaba/DataX/blob/master/postgresqlreader/doc/postgresqlreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/postgresqlwriter/doc/postgresqlwriter.md)                             |
|                    | DRDS                            |     √      |     √      |                                         [读](https://github.com/alibaba/DataX/blob/master/drdsreader/doc/drdsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/drdswriter/doc/drdswriter.md)                                         |
|                    | Kingbase                        |     √      |     √      |                                         [读](https://github.com/alibaba/DataX/blob/master/drdsreader/doc/drdsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/drdswriter/doc/drdswriter.md)                                         |
|                    | 通用RDBMS(支持所有关系型数据库) |     √      |     √      |                                       [读](https://github.com/alibaba/DataX/blob/master/rdbmsreader/doc/rdbmsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/rdbmswriter/doc/rdbmswriter.md)                                       |
| 阿里云数仓数据存储 | ODPS                            |     √      |     √      |                                         [读](https://github.com/alibaba/DataX/blob/master/odpsreader/doc/odpsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/odpswriter/doc/odpswriter.md)                                         |
|                    | ADB                             |            |     √      |                                                                             [写](https://github.com/alibaba/DataX/blob/master/adbmysqlwriter/doc/adbmysqlwriter.md)                                                                             |
|                    | ADS                             |            |     √      |                                                                                  [写](https://github.com/alibaba/DataX/blob/master/adswriter/doc/adswriter.md)                                                                                  |
|                    | OSS                             |     √      |     √      |                                           [读](https://github.com/alibaba/DataX/blob/master/ossreader/doc/ossreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/osswriter/doc/osswriter.md)                                           |
|                    | OCS                             |            |     √      |                                                                                  [写](https://github.com/alibaba/DataX/blob/master/ocswriter/doc/ocswriter.md)                                                                                  |
|                    | Hologres                        |            |     √      |                                                                         [写](https://github.com/alibaba/DataX/blob/master/hologresjdbcwriter/doc/hologresjdbcwriter.md)                                                                         |
|                    | AnalyticDB For PostgreSQL       |            |     √      |                                                                                                                       写                                                                                                                        |
| 阿里云中间件       | datahub                         |     √      |     √      |                                                                                                                      读 、写                                                                                                                      |
|                    | SLS                             |     √      |     √      |                                                                                                                      读 、写                                                                                                                      |
| 图数据库           | 阿里云 GDB                      |     √      |     √      |                                           [读](https://github.com/alibaba/DataX/blob/master/gdbreader/doc/gdbreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/gdbwriter/doc/gdbwriter.md)                                           |
|                    | Neo4j                           |            |     √      |                                                                                [写](https://github.com/alibaba/DataX/blob/master/neo4jwriter/doc/neo4jwriter.md)                                                                                |
| NoSQL数据存储      | OTS                             |     √      |     √      |                                           [读](https://github.com/alibaba/DataX/blob/master/otsreader/doc/otsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/otswriter/doc/otswriter.md)                                           |
|                    | Hbase0.94                       |     √      |     √      |                               [读](https://github.com/alibaba/DataX/blob/master/hbase094xreader/doc/hbase094xreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hbase094xwriter/doc/hbase094xwriter.md)                               |
|                    | Hbase1.1                        |     √      |     √      |                                 [读](https://github.com/alibaba/DataX/blob/master/hbase11xreader/doc/hbase11xreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hbase11xwriter/doc/hbase11xwriter.md)                                 |
|                    | Phoenix4.x                      |     √      |     √      |                           [读](https://github.com/alibaba/DataX/blob/master/hbase11xsqlreader/doc/hbase11xsqlreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hbase11xsqlwriter/doc/hbase11xsqlwriter.md)                           |
|                    | Phoenix5.x                      |     √      |     √      |                           [读](https://github.com/alibaba/DataX/blob/master/hbase20xsqlreader/doc/hbase20xsqlreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hbase20xsqlwriter/doc/hbase20xsqlwriter.md)                           |
|                    | MongoDB                         |     √      |     √      |                                   [读](https://github.com/alibaba/DataX/blob/master/mongodbreader/doc/mongodbreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/mongodbwriter/doc/mongodbwriter.md)                                   |
|                    | Cassandra                       |     √      |     √      |                               [读](https://github.com/alibaba/DataX/blob/master/cassandrareader/doc/cassandrareader.md) 、[写](https://github.com/alibaba/DataX/blob/master/cassandrawriter/doc/cassandrawriter.md)                               |
| 数仓数据存储       | StarRocks                       |     √      |     √      |                                                                          读 、[写](https://github.com/alibaba/DataX/blob/master/starrockswriter/doc/starrockswriter.md)                                                                           |
|                    | ApacheDoris                     |     √      |     √      | [读](dorisreader/doc/dorisreader.md)、[写](doriswriter/doc/doriswriter.md) |
|                    | ClickHouse                      |     √      |     √      |                              [读](https://github.com/alibaba/DataX/blob/master/clickhousereader/doc/clickhousereader.md) 、[写](https://github.com/alibaba/DataX/blob/master/clickhousewriter/doc/clickhousewriter.md)                               |
|                    | Databend                        |            |     √      |                                                                             [写](https://github.com/alibaba/DataX/blob/master/databendwriter/doc/databendwriter.md)                                                                             |
|                    | Hive                            |     √      |     √      |                                         [读](https://github.com/alibaba/DataX/blob/master/hdfsreader/doc/hdfsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hdfswriter/doc/hdfswriter.md)                                         |
|                    | kudu                            |            |     √      |                                                                                 [写](https://github.com/alibaba/DataX/blob/master/hdfswriter/doc/hdfswriter.md)                                                                                 |
|                    | selectdb                        |            |     √      |                                                                             [写](https://github.com/alibaba/DataX/blob/master/selectdbwriter/doc/selectdbwriter.md)                                                                             |
| 无结构化数据存储   | TxtFile                         |     √      |     √      |                                   [读](https://github.com/alibaba/DataX/blob/master/txtfilereader/doc/txtfilereader.md) 、[写](https://github.com/alibaba/DataX/blob/master/txtfilewriter/doc/txtfilewriter.md)                                   |
|                    | FTP                             |     √      |     √      |                                           [读](https://github.com/alibaba/DataX/blob/master/ftpreader/doc/ftpreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/ftpwriter/doc/ftpwriter.md)                                           |
|                    | HDFS                            |     √      |     √      |                                         [读](https://github.com/alibaba/DataX/blob/master/hdfsreader/doc/hdfsreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/hdfswriter/doc/hdfswriter.md)                                         |
|                    | Elasticsearch                   |            |     √      |                                                                        [写](https://github.com/alibaba/DataX/blob/master/elasticsearchwriter/doc/elasticsearchwriter.md)                                                                        |
| 时间序列数据库     | OpenTSDB                        |     √      |            |                                                                             [读](https://github.com/alibaba/DataX/blob/master/opentsdbreader/doc/opentsdbreader.md)                                                                             |
|                    | TSDB                            |     √      |     √      |                                       [读](https://github.com/alibaba/DataX/blob/master/tsdbreader/doc/tsdbreader.md) 、[写](https://github.com/alibaba/DataX/blob/master/tsdbwriter/doc/tsdbhttpwriter.md)                                       |
|                    | TDengine                        |     √      |     √      |                              [读](https://github.com/alibaba/DataX/blob/master/tdenginereader/doc/tdenginereader-CN.md) 、[写](https://github.com/alibaba/DataX/blob/master/tdenginewriter/doc/tdenginewriter-CN.md)                              |

# 阿里云DataWorks数据集成

目前DataX的已有能力已经全部融和进阿里云的数据集成，并且比DataX更加高效、安全，同时数据集成具备DataX不具备的其它高级特性和功能。可以理解为数据集成是DataX的全面升级的商业化用版本，为企业可以提供稳定、可靠、安全的数据传输服务。与DataX相比，数据集成主要有以下几大突出特点：

支持实时同步：

- 功能简介：https://help.aliyun.com/document_detail/181912.html
- 支持的数据源：https://help.aliyun.com/document_detail/146778.html
- 支持数据处理：https://help.aliyun.com/document_detail/146777.html

离线同步数据源种类大幅度扩充：

- 新增比如：DB2、Kafka、Hologres、MetaQ、SAPHANA、达梦等等，持续扩充中
- 离线同步支持的数据源：https://help.aliyun.com/document_detail/137670.html
- 具备同步解决方案：
    - 解决方案系统：https://help.aliyun.com/document_detail/171765.html
    - 一键全增量：https://help.aliyun.com/document_detail/175676.html
    - 整库迁移：https://help.aliyun.com/document_detail/137809.html
    - 批量上云：https://help.aliyun.com/document_detail/146671.html
    - 更新更多能力请访问：https://help.aliyun.com/document_detail/137663.html
    -

# 我要开发新的插件

请点击：[DataX插件开发宝典](https://github.com/alibaba/DataX/blob/master/dataxPluginDev.md)

# 重要版本更新说明

DataX 后续计划月度迭代更新，也欢迎感兴趣的同学提交 Pull requests，月度更新内容如下。

- [datax_v202309]（https://github.com/alibaba/DataX/releases/tag/datax_v202309)
  - 支持Phoenix 同步数据添加 where条件
  - 支持华为 GuassDB读写插件
  - 修复ClickReader 插件运行报错 Can't find bundle for base name
  - 增加 DataX调试模块
  - 修复 orc空文件报错问题
  - 优化obwriter性能
  - txtfilewriter 增加导出为insert语句功能支持
  - HdfsReader/HdfsWriter 支持parquet读写能力
  
- [datax_v202308]（https://github.com/alibaba/DataX/releases/tag/datax_v202308)
  - OTS 插件更新
  - databend 插件更新
  - Oceanbase驱动修复


- [datax_v202306]（https://github.com/alibaba/DataX/releases/tag/datax_v202306)
  - 精简代码
  - 新增插件（neo4jwriter、clickhousewriter）
  - 优化插件、修复问题（oceanbase、hdfs、databend、txtfile）


- [datax_v202303]（https://github.com/alibaba/DataX/releases/tag/datax_v202303)
  - 精简代码
  - 新增插件（adbmysqlwriter、databendwriter、selectdbwriter）
  - 优化插件、修复问题（sqlserver、hdfs、cassandra、kudu、oss）
  - fastjson 升级到 fastjson2

- [datax_v202210]（https://github.com/alibaba/DataX/releases/tag/datax_v202210)
  - 涉及通道能力更新（OceanBase、Tdengine、Doris等）

- [datax_v202209]（https://github.com/alibaba/DataX/releases/tag/datax_v202209)
    - 涉及通道能力更新（MaxCompute、Datahub、SLS等）、安全漏洞更新、通用打包更新等

- [datax_v202205]（https://github.com/alibaba/DataX/releases/tag/datax_v202205)
    - 涉及通道能力更新（MaxCompute、Hologres、OSS、Tdengine等）、安全漏洞更新、通用打包更新等


# 项目成员

核心Contributions: 言柏 、枕水、秋奇、青砾、一斅、云时

感谢天烬、光戈、祁然、巴真、静行对DataX做出的贡献。

# License

This software is free to use under the Apache License [Apache license](https://github.com/alibaba/DataX/blob/master/license.txt).

# 
请及时提出issue给我们。请前往：[DataxIssue](https://github.com/alibaba/DataX/issues)

# 开源版DataX企业用户

![Datax-logo](https://github.com/alibaba/DataX/blob/master/images/datax-enterprise-users.jpg)

```
长期招聘 联系邮箱：datax@alibabacloud.com
【JAVA开发职位】
职位名称：JAVA资深开发工程师/专家/高级专家
工作年限 : 2年以上
学历要求 : 本科（如果能力靠谱，这些都不是条件）
期望层级 : P6/P7/P8

岗位描述：
    1. 负责阿里云大数据平台（数加）的开发设计。 
    2. 负责面向政企客户的大数据相关产品开发；
    3. 利用大规模机器学习算法挖掘数据之间的联系，探索数据挖掘技术在实际场景中的产品应用 ；
    4. 一站式大数据开发平台
    5. 大数据任务调度引擎
    6. 任务执行引擎
    7. 任务监控告警
    8. 海量异构数据同步

岗位要求：
    1. 拥有3年以上JAVA Web开发经验；
    2. 熟悉Java的基础技术体系。包括JVM、类装载、线程、并发、IO资源管理、网络；
    3. 熟练使用常用Java技术框架、对新技术框架有敏锐感知能力；深刻理解面向对象、设计原则、封装抽象；
    4. 熟悉HTML/HTML5和JavaScript；熟悉SQL语言；
    5. 执行力强，具有优秀的团队合作精神、敬业精神；
    6. 深刻理解设计模式及应用场景者加分；
    7. 具有较强的问题分析和处理能力、比较强的动手能力，对技术有强烈追求者优先考虑；
    8. 对高并发、高稳定可用性、高性能、大数据处理有过实际项目及产品经验者优先考虑；
    9. 有大数据产品、云产品、中间件技术解决方案者优先考虑。
````

用户咨询支持：

钉钉群目前暂时受到了一些管控策略影响，建议大家有问题优先在这里提交问题 Issue，DataX研发和社区会定期回答Issue中的问题，知识库丰富后也能帮助到后来的使用者。
