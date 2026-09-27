# DataX PostgresqlWriter


---


## 1 快速介绍

PostgresqlWriter插件实现了写入数据到 PostgreSQL主库目的表的功能。在底层实现上，PostgresqlWriter通过JDBC连接远程 PostgreSQL 数据库，并执行相应的 insert into ... sql 语句将数据写入 PostgreSQL，内部会分批次提交入库。

PostgresqlWriter面向ETL开发工程师，他们使用PostgresqlWriter从数仓导入数据到PostgreSQL。同时 PostgresqlWriter亦可以作为数据迁移工具为DBA等用户提供服务。


## 2 实现原理

PostgresqlWriter通过 DataX 框架获取 Reader 生成的协议数据，根据你配置生成相应的SQL插入语句


* `insert into...`(当主键/唯一性索引冲突时会写不进去冲突的行)

<br />

    注意：
    1. 目的表所在数据库必须是主库才能写入数据；整个任务至少需具备 insert into...的权限，是否需要其他权限，取决于你任务配置中在 preSql 和 postSql 中指定的语句。
    2. PostgresqlWriter和MysqlWriter不同，不支持配置writeMode参数。


## 3 功能说明

### 3.1 配置样例

* 这里使用一份从内存产生到 PostgresqlWriter导入的数据。

```json
{
    "job": {
        "setting": {
            "speed": {
                "channel": 1
            }
        },
        "content": [
            {
                 "reader": {
                    "name": "streamreader",
                    "parameter": {
                        "column" : [
                            {
                                "value": "DataX",
                                "type": "string"
                            },
                            {
                                "value": 19880808,
                                "type": "long"
                            },
                            {
                                "value": "1988-08-08 08:08:08",
                                "type": "date"
                            },
                            {
                                "value": true,
                                "type": "bool"
                            },
                            {
                                "value": "test",
                                "type": "bytes"
                            }
                        ],
                        "sliceRecordCount": 1000
                    }
                },
                "writer": {
                    "name": "postgresqlwriter",
                    "parameter": {
                        "username": "xx",
                        "password": "xx",
                        "column": [
                            "id",
                            "name"
                        ],
                        "preSql": [
                            "delete from test"
                        ],
                        "connection": [
                            {
                                "jdbcUrl": "jdbc:postgresql://127.0.0.1:3002/datax",
                                "table": [
                                    "test"
                                ]
                            }
                        ]
                    }
                }
            }
        ]
    }
}

```


### 3.2 参数说明

* **jdbcUrl**

    * 描述：目的数据库的 JDBC 连接信息 ,jdbcUrl必须包含在connection配置单元中。

      注意：1、在一个数据库上只能配置一个值。
      2、jdbcUrl按照PostgreSQL官方规范，并可以填写连接附加参数信息。具体请参看PostgreSQL官方文档或者咨询对应 DBA。


  * 必选：是 <br />

  * 默认值：无 <br />

* **username**

  * 描述：目的数据库的用户名 <br />

  * 必选：是 <br />

  * 默认值：无 <br />

* **password**

  * 描述：目的数据库的密码 <br />

  * 必选：是 <br />

  * 默认值：无 <br />

* **table**

  * 描述：目的表的表名称。支持写入一个或者多个表。当配置为多张表时，必须确保所有表结构保持一致。

               注意：table 和 jdbcUrl 必须包含在 connection 配置单元中

  * 必选：是 <br />

  * 默认值：无 <br />

* **column**

  * 描述：目的表需要写入数据的字段,字段之间用英文逗号分隔。例如: "column": ["id","name","age"]。如果要依次写入全部列，使用\*表示, 例如: "column": ["\*"]

               注意：1、我们强烈不推荐你这样配置，因为当你目的表字段个数、类型等有改动时，你的任务可能运行不正确或者失败
                    2、此处 column 不能配置任何常量值

  * 必选：是 <br />

  * 默认值：否 <br />

* **preSql**

  * 描述：写入数据到目的表前，会先执行这里的标准语句。如果 Sql 中有你需要操作到的表名称，请使用 `@table` 表示，这样在实际执行 Sql 语句时，会对变量按照实际表名称进行替换。比如你的任务是要写入到目的端的100个同构分表(表名称为:datax_00,datax01, ... datax_98,datax_99)，并且你希望导入数据前，先对表中数据进行删除操作，那么你可以这样配置：`"preSql":["delete from @table"]`，效果是：在执行到每个表写入数据前，会先执行对应的 delete from 对应表名称 <br />

  * 必选：否 <br />

  * 默认值：无 <br />

* **postSql**

  * 描述：写入数据到目的表后，会执行这里的标准语句。（原理同 preSql ） <br />

  * 必选：否 <br />

  * 默认值：无 <br />

* **batchSize**

	* 描述：一次性批量提交的记录数大小，该值可以极大减少DataX与PostgreSql的网络交互次数，并提升整体吞吐量。但是该值设置过大可能会造成DataX运行进程OOM情况。<br />

	* 必选：否 <br />

	* 默认值：1024 <br />

### 可选 COPY 批量写入

`parameter.useCopy` 默认为 `false`，继续使用原来的 JDBC INSERT。设为 `true` 可使用 PostgreSQL 原生 `COPY FROM STDIN`，适合经过验收的批量导入表。例如：

```json
"useCopy": true,
"batchSize": 1024
```

COPY 复用原有记录缓冲、`batchSize` / `batchByteSize`、连接、preSql / postSql 和批次提交边界，不增加并发或关闭数据库持久化。非 NULL 字段均使用 CSV 引号；NULL 使用未引用的空字段，从而区分 NULL、空串及字面 `\N`。整批先严格编码为 UTF-8，保留跨传输缓冲区边界的 emoji，并拒绝不合法的 UTF-16 输入。时间与二进制格式使用现有 PostgreSQL JDBC 驱动的转换方法。

每批检查 COPY 返回行数必须等于输入行数，不一致会回滚并报错。COPY 发生错误时回滚当前批次、使任务失败；即使 `errorLimit` 非零，也不会跳过错误行或自动改为逐条重放。此前成功提交的批次仍然保留；COPY **不提供整任务原子性、共享源快照或重跑幂等性**。请使用隔离暂存目标与完整对账后发布的流程。

这是显式选择数据库的 COPY 语义，并非对所有 INSERT 作业透明替换：语句级触发器执行次数、规则、行级安全、默认/生成列等行为可能不同，需先检查目标表定义；参见 [PostgreSQL COPY 文档](https://www.postgresql.org/docs/17/sql-copy.html)。原有 `writeMode` 限制不变，其他数据库 writer 不使用这个选项。请勿把单个导入场景的性能结果当作全场景或生产速度保证。

### 可选 PG 批次原子追加（实验性）

本 fork 的 PG reader/writer 现使用 pgJDBC 42.7.13（Java 8）。本轮实测 PG 17.11，未完成
所有服务端版本回归；驱动官方不保证兼容 PG 9.1 以前版本。
见[浮点保真、驱动对照与性能](../../benchmarks/REPORT-floating.zh-CN.md)。

在 writer 的 `parameter` 中显式配置 `"atomicBatchId": "immutable-source-batch-20260927-001"`。
默认不启用；只实现 PG 单表追加，不是全量替换或 upsert。JDBC 与 `useCopy=true` 均可使用。
源为 PG 时，配合 reader 的 `consistentSnapshot=true`；批次范围和内容必须固定，重跑沿用同一个 ID。

数据先写入同 schema 的普通 logged 暂存表。完成后检查脏行/过滤行、任务接收行数和暂存行数，
把暂存数据追加、批次登记、删除暂存表放在同一 PG 事务中。发布前用 `INSERT ... RETURNING`
与暂存数据做二进制 `EXCEPT ALL` 双向比较；目标 numeric/timestamp 等精度缩窄导致数值变化时回滚。
同 ID 重跑核对列定义、行数和内容指纹；一致则不追加，不一致则失败。提交回执丢失时仍报告失败，
使用原 ID 重跑进行核对；不要换 ID 强行重跑。

限制与成本：

- 要求一个连接、一个 logged 原生表（含原生分区），拒绝目标及其分区的 RLS、用户触发器、写规则；不允许 preSql/postSql。
- 需要同 schema 的建表、删表和锁权限。`__datax_atomic_batches_v1` 是持久化批次账本；不要删除、修改或迁移它，
  也不要在批次期间重建/迁移目标表或改写暂存表。结构不符合要求的同名账本会被拒绝。
- 暂存保留一整批数据；发布事务需要 WAL 和二进制对账的临时空间，并阻塞目标并发写入。
  没有亿级容量/耗时验收；不要将亿级整批直接套用。连接断开后遗留的暂存由下一次同 ID 尝试回收。
- 指纹是四段 SHA-256 数值的无序累加，保留重复行重数，但依然是概率校验。发布时的双向二进制比较是精确的。
- 不会替你去重源数据，不防止不同 ID 的批次范围重叠，也不能证明 reader 未报告的源端丢失或类型转换不存在。
  仍需源端快照、明确边界及全字段对账。变更后的目标精度、字符编码、特殊数据类型需按真实表验证。
- 只在 PostgreSQL 17.11 做过本地真实 Engine 检查；不扩展到 MySQL、StarRocks、Doris 或其他 writer，
  不代表全场景 +25%/+50% 或生产零错误率。数据库持久化设置应保持启用。

可复现检查见 [postgresql_atomic_checks.py](../../benchmarks/postgresql_atomic_checks.py)。
PG 锁和返回行语义见 [显式锁](https://www.postgresql.org/docs/17/explicit-locking.html)、
[RETURNING](https://www.postgresql.org/docs/17/dml-returning.html)。

### 3.3 类型转换

目前 PostgresqlWriter支持大部分 PostgreSQL类型，但也存在部分没有支持的情况，请注意检查你的类型。

下面列出 PostgresqlWriter针对 PostgreSQL类型转换列表:

| DataX 内部类型| PostgreSQL 数据类型    |
| -------- | -----  |
| Long     |bigint, bigserial, integer, smallint, serial |
| Double   |double precision, money, numeric, real |
| String   |varchar, char, text, bit|
| Date     |date, time, timestamp |
| Boolean  |bool|
| Bytes    |bytea|

## 4 性能报告

### 4.1 环境准备

#### 4.1.1 数据特征
建表语句：

 create table pref_test(
     id serial,
     a_bigint bigint,
     a_bit bit(10),
     a_boolean boolean,
     a_char character(5),
     a_date date,
     a_double double precision,
     a_integer integer,
     a_money money,
     a_num numeric(10,2),
     a_real real,
     a_smallint smallint,
     a_text text,
     a_time time,
     a_timestamp timestamp
)

#### 4.1.2 机器参数

* 执行DataX的机器参数为:
	1. cpu: 16核 Intel(R) Xeon(R) CPU E5620  @ 2.40GHz
	2. mem: MemTotal: 24676836kB    MemFree: 6365080kB
	3. net: 百兆双网卡

* PostgreSQL数据库机器参数为:
	D12 24逻辑核  192G内存 12*480G SSD 阵列


### 4.2 测试报告

#### 4.2.1 单表测试报告

| 通道数|  批量提交batchSize | DataX速度(Rec/s)| DataX流量(M/s) | DataX机器运行负载
|--------|--------| --------|--------|--------|--------|
|1| 128 | 9259 | 0.55 | 0.3
|1| 512 | 10869 | 0.653 | 0.3
|1| 2048 | 9803 | 0.589 | 0.8
|4| 128 | 30303 | 1.82 | 1
|4| 512 | 36363 | 2.18 | 1
|4| 2048 | 36363 | 2.18 | 1
|8| 128 | 57142 | 3.43 | 2
|8| 512 | 66666 | 4.01 | 1.5
|8| 2048 | 66666 | 4.01 | 1.1
|16| 128 | 88888 | 5.34 | 1.8
|16| 2048 | 94117 | 5.65 | 2.5
|32| 512 | 76190 | 4.58 | 3

#### 4.2.2 性能测试小结
1. `channel数对性能影响很大`
2. `通常不建议写入数据库时，通道个数 > 32`


## FAQ

***

**Q: PostgresqlWriter 执行 postSql 语句报错，那么数据导入到目标数据库了吗?**

A: DataX 导入过程存在三块逻辑，pre 操作、导入操作、post 操作，其中任意一环报错，DataX 作业报错。由于 DataX 不能保证在同一个事务完成上述几个操作，因此有可能数据已经落入到目标端。

***

**Q: 按照上述说法，那么有部分脏数据导入数据库，如果影响到线上数据库怎么办?**

A: 目前有两种解法，第一种配置 pre 语句，该 sql 可以清理当天导入数据， DataX 每次导入时候可以把上次清理干净并导入完整数据。
第二种，向临时表导入数据，完成后再 rename 到线上表。

***
