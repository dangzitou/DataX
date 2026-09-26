# DorisWriter 插件文档

## 1 快速介绍
DorisWriter支持将大批量数据写入Doris中。

## 2 实现原理
DorisWriter 通过Doris原生支持Stream load方式导入数据， DorisWriter会将`reader`读取的数据进行缓存在内存中，拼接成Json文本，然后批量导入至Doris。

本 fork 对 `Status=Success` 的每批响应强制检查行数：`NumberTotalRows`、`NumberLoadedRows`
都必须等于发送行数，`NumberFilteredRows`、`NumberUnselectedRows` 必须为 0；缺失或格式错误的
计数也会使作业失败。此类错误不会自动重试，避免已提交的不完整批次经 `Label Already Exists`
被重新判成成功。配置 `where` 丢弃行或允许过滤坏数据的作业因此可能从成功变为明确失败。

这是提交后的检测，不能回滚已写入的行。应同时设置 `loadProps.strict_mode=true`、
`loadProps.max_filter_ratio=0`，避免服务端容忍坏数据；这些参数也不能替代字段校验。
`Publish Timeout` 和通过标签确认的网络重试仍沿用原有恢复行为，不能据此证明逐行完整性。
目标表的主键合并、字段转换、跨作业重跑及整作业原子发布均不由这项计数检查保证。

## 3 功能说明

### 3.1 配置样例

这里是一份从Stream读取数据后导入至Doris的配置文件。

```
{
    "job": {
        "content": [
            {
                "reader": {
                    "name": "mysqlreader",
                    "parameter": {
                        "column": ["emp_no", "birth_date", "first_name","last_name","gender","hire_date"],
                        "connection": [
                            {
                                "jdbcUrl": ["jdbc:mysql://localhost:3306/demo"],
                                "table": ["employees_1"]
                            }
                        ],
                        "username": "root",
                        "password": "xxxxx",
                        "where": ""
                    }
                },
                "writer": {
                    "name": "doriswriter",
                    "parameter": {
                        "loadUrl": ["172.16.0.13:8030"],
                        "column": ["emp_no", "birth_date", "first_name","last_name","gender","hire_date"],
                        "username": "root",
                        "password": "xxxxxx",
                        "postSql": ["select count(1) from all_employees_info"],
                        "preSql": [],
                        "flushInterval":30000,
                        "connection": [
                          {
                            "jdbcUrl": "jdbc:mysql://172.16.0.13:9030/demo",
                            "selectedDatabase": "demo",
                            "table": ["all_employees_info"]
                          }
                        ],
                        "loadProps": {
                            "format": "json",
                            "strip_outer_array": true
                        }
                    }
                }
            }
        ],
        "setting": {
            "speed": {
                "channel": "1"
            }
        }
    }
}
```

### 3.2 参数说明

* **jdbcUrl**

  - 描述：Doris 的 JDBC 连接串，用户执行 preSql 或 postSQL。
  - 必选：是
  - 默认值：无

* **loadUrl**

  - 描述：作为 Stream Load 的连接目标。格式为 "ip:port"。其中 IP 是 FE 节点 IP，port 是 FE 节点的 http_port。可以填写多个，多个之间使用英文状态的分号隔开:`;`，doriswriter 将以轮询的方式访问。
  - 必选：是
  - 默认值：无

* **username**

  - 描述：访问Doris数据库的用户名
  - 必选：是
  - 默认值：无

* **password**

  - 描述：访问Doris数据库的密码
  - 必选：否
  - 默认值：空

* **connection.selectedDatabase**
  - 描述：需要写入的Doris数据库名称。
  - 必选：是
  - 默认值：无

* **connection.table**
  - 描述：需要写入的Doris表名称。
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
  - 默认值：104857600

* **maxRetries**

  - 描述：每批次导入数据失败后的重试次数。
  - 必选：否
  - 默认值：0

* **labelPrefix**

  - 描述：每批次导入任务的 label 前缀。最终的 label 将有 `labelPrefix + UUID` 组成全局唯一的 label，确保数据不会重复导入
  - 必选：否
  - 默认值：`datax_doris_writer_`

* **loadProps**

  - 描述：StreamLoad 的请求参数，详情参照StreamLoad介绍页面。[Stream load - Apache Doris](https://doris.apache.org/zh-CN/docs/data-operate/import/import-way/stream-load-manual)

    这里包括导入的数据格式：format等，导入数据格式默认我们使用csv，支持JSON，具体可以参照下面类型转换部分，也可以参照上面Stream load 官方信息

  - 必选：否

  - 默认值：无

### 类型转换

默认传入的数据均会被转为字符串，并以`\t`作为列分隔符，`\n`作为行分隔符，组成`csv`文件进行StreamLoad导入操作。

默认是csv格式导入，如需更改列分隔符， 则正确配置 `loadProps` 即可：

```json
"loadProps": {
  "column_separator": "\\x01",
  "line_delimiter": "\\x02"
}
```

如需更改导入格式为`json`， 则正确配置 `loadProps` 即可：
```json
"loadProps": {
  "format": "json",
  "strip_outer_array": true
}
```

更多信息请参照 Doris 官网：[Stream load - Apache Doris](https://doris.apache.org/zh-CN/docs/data-operate/import/import-way/stream-load-manual)
