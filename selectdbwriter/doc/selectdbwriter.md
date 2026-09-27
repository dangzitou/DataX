# SelectdbWriter 插件文档

## 1 快速介绍
SelectdbWriter支持将大批量数据写入SELECTDB中。

## 2 实现原理
SelectdbWriter 通过调用selectdb api （/copy/upload），返回一个重定向的S3地址，使用Http向S3地址发送字节流，设置参数达到要求时执行copy into

本 fork 上传时将已编码行组合成可读取的流，避免额外拼接整批 byte[]；保留原有 chunked 上传方式。
已验证实际 HTTP 客户端的重定向、重放和正文哈希，尚未验证真实 SelectDB 服务端。
编码行和队列仍占内存，这不解决整作业原子性或重跑重复。见[验证报告](../../benchmarks/REPORT-stream-payload.zh-CN.md)。

## 3 编译

1. 运行 init-env.sh

2. 编译 selectdbwriter：

i. 单独编译 selectdbwriter 插件:

   ```text
     mvn clean install -pl plugin-rdbms-util,selectdbwriter -DskipTests
   ```


ii.编译整个 DataX 项目:

   ```text
     mvn package assembly:assembly -Dmaven.test.skip=true
   ```
产出在 target/datax/datax/.
hdfsreader, hdfswriter and oscarwriter 这三个插件需要额外的jar包。如果你并不需要这些插件，可以在 DataX/pom.xml 中删除这些插件的模块。
   
 
iii.编译错误

如遇到如下编译错误： 
  ```text
  Could not find artifact com.alibaba.datax:datax-all:pom:0.0.1-SNAPSHOT 
  ```

可尝试以下方式解决：

a.下载 alibaba-datax-maven-m2-20210928.tar.gz

b.解压后，将得到的 alibaba/datax/ 目录，拷贝到所使用的 maven 对应的 .m2/repository/com/alibaba/ 下。

c.再次尝试编译。

## 3 功能说明

### 3.1 配置样例

这里是一份从Stream读取数据后导入至selectdb的配置文件。

```
{
  "job":{
    "content":[
      {
        "reader":{
          "name":"streamreader",
          "parameter":{
            "column":[
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"long",
                "random":"0,5"
              },
              {
                "type":"string",
                "random":"0,10"
              },
              {
                "type":"string",
                "random":"0,5"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"string",
                "random":"0,21"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"long",
                "random":"0,20"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"string",
                "random":"0,10"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"long",
                "random":"0,10"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"string",
                "random":"0,100"
              },
              {
                "type":"string",
                "random":"0,1"
              },
              {
                "type":"long",
                "random":"0,1"
              },
              {
                "type":"string",
                "random":"0,64"
              },
              {
                "type":"string",
                "random":"0,20"
              },
              {
                "type":"string",
                "random":"0,31"
              },
              {
                "type":"long",
                "random":"0,3"
              },
              {
                "type":"long",
                "random":"0,3"
              },
              {
                "type":"long",
                "random":"0,19"
              },
              {
                "type":"date",
                "random":"2022-01-01 12:00:00,2023-01-01 12:00:00"
              },
              {
                "type":"string",
                "random":"0,1"
              }
            ],
            "sliceRecordCount":10
          }
        },
        "writer":{
          "name":"selectdbwriter",
          "parameter":{
            "loadUrl":[
              "xxx:47150"
            ],
            "loadProps":{
              "file.type":"json",
              "file.strip_outer_array":"true"
            },
            "column":[
              "id",
              "table_id",
              "table_no",
              "table_name",
              "table_status",
              "no_disturb",
              "dinner_type",
              "member_id",
              "reserve_bill_no",
              "pre_order_no",
              "queue_num",
              "person_num",
              "open_time",
              "open_time_format",
              "order_time",
              "order_time_format",
              "table_bill_id",
              "offer_time",
              "offer_time_format",
              "confirm_bill_time",
              "confirm_bill_time_format",
              "bill_time",
              "bill_time_format",
              "clear_time",
              "clear_time_format",
              "table_message",
              "bill_close",
              "table_type",
              "pad_mac",
              "company_id",
              "shop_id",
              "is_sync",
              "table_split_no",
              "ts",
              "ts_format",
              "dr"
            ],
            "username":"admin",
            "password":"SelectDB2022",
            "postSql":[

            ],
            "preSql":[

            ],
            "connection":[
              {
                "jdbcUrl":"jdbc:mysql://xxx:34142/cl_test",
                "table":[
                  "ods_pos_pro_table_dynamic_delta_v4"
                ],
                "selectedDatabase":"cl_test"
              }
            ],
            "maxBatchRows":1000000,
            "maxBatchByteSize":536870912000
          }
        }
      }
    ],
    "setting":{
      "errorLimit":{
        "percentage":0.02,
        "record":0
      },
      "speed":{
        "channel":5
      }
    }
  }
}

```

### 3.2 参数说明

```text
 **jdbcUrl**

    - 描述：selectdb 的 JDBC 连接串，用户执行 preSql 或 postSQL。
    - 必选：是
    - 默认值：无

* **loadUrl**

    - 描述：作为 selecdb 的连接目标。格式为 "ip:port"。其中 IP 是 selectdb的private-link，port 是selectdb 集群的 http_port
    - 必选：是
    - 默认值：无

* **username**

    - 描述：访问selectdb数据库的用户名
    - 必选：是
    - 默认值：无

* **password**

    - 描述：访问selectdb数据库的密码
    - 必选：否
    - 默认值：空

* **connection.selectedDatabase**
    - 描述：需要写入的selectdb数据库名称。
    - 必选：是
    - 默认值：无

* **connection.table**
    - 描述：需要写入的selectdb表名称。
        - 必选：是
        - 默认值：无

* **column**

    - 描述：目的表**需要写入数据**的字段，这些字段将作为生成的 Json 数据的字段名。字段之间用英文逗号分隔。例如: "column": ["id","name","age"]。
    - 必选：是
    - 默认值：否

* **preSql**

    - 描述：写入数据到目的表前，会先执行这里的标准语句。
    - 必选：否
    - 默认值：无

* **postSql**

    - 描述：写入数据到目的表后，会执行这里的标准语句。
    - 必选：否
    - 默认值：无


* **maxBatchRows**

    - 描述：每批次导入数据的最大行数。和 **batchSize** 共同控制每批次的导入数量。每批次数据达到两个阈值之一，即开始导入这一批次的数据。
    - 必选：否
    - 默认值：500000

* **batchSize**

    - 描述：每批次导入数据的最大数据量。和 **maxBatchRows** 共同控制每批次的导入数量。每批次数据达到两个阈值之一，即开始导入这一批次的数据。
    - 必选：否
    - 默认值：90M

* **maxRetries**

    - 描述：每批次导入数据失败后的重试次数。
    - 必选：否
    - 默认值：3

* **labelPrefix**

    - 描述：每批次上传文件的 label 前缀。最终的 label 将有 `labelPrefix + UUID` 组成全局唯一的 label，确保数据不会重复导入
    - 必选：否
    - 默认值：`datax_selectdb_writer_`

* **loadProps**

    - 描述：COPY INOT 的请求参数

      这里包括导入的数据格式：file.type等，导入数据格式默认我们使用csv，支持JSON，具体可以参照下面类型转换部分

    - 必选：否

    - 默认值：无

* **clusterName**

  - 描述：selectdb could 集群名称

  - 必选：否

  - 默认值：无

* **flushQueueLength**

  - 描述：队列长度

  - 必选：否

  - 默认值：1

* **flushInterval**

  - 描述：数据写入批次的时间间隔，如果maxBatchRows 和 batchSize 参数设置的有很大，那么很可能达不到你这设置的数据量大小，会执行导入。

  - 必选：否

  - 默认值：30000ms
```

### 类型转换

默认传入的数据均会被转为字符串，并以`\t`作为列分隔符，`\n`作为行分隔符，组成`csv`文件进行Selectdb导入操作。

默认是csv格式导入，如需更改列分隔符， 则正确配置 `loadProps` 即可：

```json
"loadProps": {
    "file.column_separator": "\\x01",
    "file.line_delimiter": "\\x02"
}
```

如需更改导入格式为`json`， 则正确配置 `loadProps` 即可：
```json
"loadProps": {
    "file.type": "json",
    "file.strip_outer_array": true
}
```
## 二进制字段的显式传输

`parameter.binaryEncoding` 默认 `reject`，遇到非 NULL 的 DataX BYTES 时明确失败；不再将任意字节折算成 long。
可选 `hex` 或 `base64`，保留每个字节、前导零和长度，空字节串与 NULL 分开。该设置作用于所有 BYTES 字段，
不改变普通字符串、整数或布尔字段。数值型 MySQL BIT 应在源 querySql 中显式 `CAST(bit_col AS UNSIGNED)`，
并选择能容纳其范围的目标数值类型；不要把二进制内容和整数混用。

编码是传输表示，必须和目标列/导入表达式相匹配。文本目标可用 STRING/VARCHAR 存储编码后的值；
不能把 hex/base64 文本直接当作任意数值类型或任意版本的原生二进制类型。字段长度、loadProps 列映射、
目标表达式仍需检查，成功行数相同并不能证明字段相同。请保持严格导入与零过滤，并逐字节对账。

源/目标不支持原生二进制存储时，编码文本是明确的替代表示，不是声称目标具有 bytea/VARBINARY 类型。
这也不提供整任务原子提交：错误前已提交的批次仍可能存在。

对 JDBC 将二进制列误报为文本的 reader，可配置 `binaryColumns: ["payload"]`。按查询结果列标签精确匹配，
以 `ResultSet.getBytes` 构造 BytesColumn；未列出的字段保留原有转换。重名、缺失、重复配置或非字符串/二进制类型会拒绝。
该选项不是自动类型推断；已知案例是 StarRocks 4.1.4 VARBINARY 被 JDBC 报成 TEXT。

读取编码文本并恢复二进制时，可在源 querySql 使用
`CASE WHEN payload='' THEN '' ELSE FROM_BASE64(payload) END AS payload`（hex 用 `UNHEX`），
同时设置 reader `binaryColumns: ["payload"]`。显式处理空串避免解码函数把空二进制变成 NULL；需按目标版本验证。

本 fork 的真实数据库检查见 [olap_binary_checks.py](../../benchmarks/olap_binary_checks.py)，
工厂/编码器检查见 [binary_codec_checks.py](../../benchmarks/binary_codec_checks.py)。

SelectDB 的检查目前只覆盖编码器、配置工厂与编译，没有真实 SelectDB 服务验收。
CSV 使用 `file.column_separator` 与 `file.line_delimiter` 检查碰撞；有分隔字符或字面 \N 的字符串需要 JSON。
