# DataX StarRocksWriter


---


## 1 快速介绍

StarRocksWriter 插件实现了写入数据到 StarRocks 主库的目的表的功能。在底层实现上， StarRocksWriter 通过Streamload以csv格式导入数据至StarRocks。


## 2 实现原理

 StarRocksWriter 通过Streamload以csv格式导入数据至StarRocks， 内部将`reader`读取的数据进行缓存后批量导入至StarRocks，以提高写入性能。

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

* 这里使用一份从内存Mysql读取数据后导入至StarRocks。

```json
{
    "job": {
        "setting": {
            "speed": {
                 "channel": 1
            },
            "errorLimit": {
                "record": 0,
                "percentage": 0
            }
        },
        "content": [
            {
                "reader": {
                    "name": "mysqlreader",
                    "parameter": {
                        "username": "xxxx",
                        "password": "xxxx",
                        "column": [ "k1", "k2", "v1", "v2" ],
                        "connection": [
                            {
                                "table": [ "table1", "table2" ],
                                "jdbcUrl": [
                                     "jdbc:mysql://127.0.0.1:3306/datax_test1"
                                ]
                            },
                            {
                                "table": [ "table3", "table4" ],
                                "jdbcUrl": [
                                     "jdbc:mysql://127.0.0.1:3306/datax_test2"
                                ]
                            }
                        ]
                    }
                },
               "writer": {
                    "name": "starrockswriter",
                    "parameter": {
                        "username": "xxxx",
                        "password": "xxxx",
                        "column": ["k1", "k2", "v1", "v2"],
                        "preSql": [],
                        "postSql": [], 
                        "connection": [
                            {
                                "table": ["xxx"],
                                "jdbcUrl": "jdbc:mysql://172.28.17.100:9030/",
                                "selectedDatabase": "xxxx"
                            }
                        ],
                        "loadUrl": ["172.28.17.100:8030", "172.28.17.100:8030"],
                        "loadProps": {}
                    }
                }
            }
        ]
    }
}

```


### 3.2 参数说明

* **username**

	* 描述：StarRocks数据库的用户名 <br />

	* 必选：是 <br />

	* 默认值：无 <br />

* **password**

	* 描述：StarRocks数据库的密码 <br />

	* 必选：是 <br />

	* 默认值：无 <br />

* **selectedDatabase**

	* 描述：StarRocks表的数据库名称。

	* 必选：是 <br />

	* 默认值：无 <br />

* **table**

	* 描述：StarRocks表的表名称。

	* 必选：是 <br />

	* 默认值：无 <br />

* **loadUrl**

	* 描述：StarRocks FE的地址用于Streamload，可以为多个fe地址，`fe_ip:fe_http_port`。

	* 必选：是 <br />

	* 默认值：无 <br />

* **column**

	* 描述：目的表需要写入数据的字段,字段之间用英文逗号分隔。例如: "column": ["id","name","age"]。

			**column配置项必须指定，不能留空！**

               注意：我们强烈不推荐你这样配置，因为当你目的表字段个数、类型等有改动时，你的任务可能运行不正确或者失败

	* 必选：是 <br />

	* 默认值：否 <br />

* **preSql**

	* 描述：写入数据到目的表前，会先执行这里的标准语句。 <br />

	* 必选：否 <br />

	* 默认值：无 <br />

* **postSql**

	* 描述：写入数据到目的表后，会执行这里的标准语句。 <br />

	* 必选：否 <br />

	* 默认值：无 <br />

* **jdbcUrl**

	* 描述：目的数据库的 JDBC 连接信息，用于执行`preSql`及`postSql`。 <br />

 	* 必选：否 <br />

	* 默认值：无 <br />

* **maxBatchRows**

	* 描述：单次StreamLoad导入的最大行数 <br />

 	* 必选：否 <br />

	* 默认值：500000 (50W) <br />

* **maxBatchSize**

	* 描述：单次StreamLoad导入的最大字节数。 <br />

 	* 必选：否 <br />

	* 默认值：104857600 (100M)

* **flushInterval**

	* 描述：上一次StreamLoad结束至下一次开始的时间间隔（单位：ms）。 <br />

 	* 必选：否 <br />

	* 默认值：300000 (ms)

* **loadProps**

	* 描述：StreamLoad 的请求参数，详情参照StreamLoad介绍页面。 <br />

 	* 必选：否 <br />

	* 默认值：无 <br />


### 3.3 类型转换

默认传入的数据均会被转为字符串，并以`\t`作为列分隔符，`\n`作为行分隔符，组成`csv`文件进行StreamLoad导入操作。
如需更改列分隔符， 则正确配置 `loadProps` 即可：
```json
"loadProps": {
    "column_separator": "\\x01",
    "row_delimiter": "\\x02"
}
```

如需更改导入格式为`json`， 则正确配置 `loadProps` 即可：
```json
"loadProps": {
    "format": "json",
    "strip_outer_array": true
}
```

## 4 性能报告


## 5 约束限制


## FAQ

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

已验证的 StarRocks 4.1.4 原生 VARBINARY 方案：writer 设置 `binaryEncoding: "hex"`、CSV，
并在 `loadProps.columns` 显式配置 `id,encoded_payload,payload=to_binary(encoded_payload,'hex')`。
不要依赖隐式 CSV 二进制解码；该版本实际会尝试 Base64，和当前文档写的 hex 不一致。
输入为 NULL、空字节、00–FF 全字节范围时也必须保留区别。
版本源码见 [varbinary_converter.cpp](https://github.com/StarRocks/starrocks/blob/4.1.4/be/src/formats/csv/varbinary_converter.cpp)。
读取该 VARBINARY 列回 PG 时需上述 `binaryColumns`；默认文本推断在已测版本会改变非 ASCII 字节。
