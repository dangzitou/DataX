# StarRocks Reader 二进制字段

复用通用 JDBC reader，支持原有 table/column 和 querySql 配置。

## 显式二进制结果列

reader 的 `parameter.binaryColumns` 是按查询结果列标签精确匹配的可选列表，例如 `["payload"]`。它让该字段通过 JDBC `getBytes` 读取为 BytesColumn，避免驱动将实际字节错误报告为文本后的字符转换。默认空，不改变其他字段。重名、缺失、重复配置或非字符串/二进制字段会明确拒绝。需要以实际结果列别名配置，大小写精确匹配。

编码文本可以在 querySql 中用 `CASE WHEN payload='' THEN '' ELSE FROM_BASE64(payload) END AS payload`（hex 用 UNHEX）恢复，并将该结果列列入 binaryColumns；保留空字节串与 NULL 的区别。该选项不自动推断任意表达式的语义，也不提供整任务幂等。检查脚本见 [olap_binary_checks.py](../../benchmarks/olap_binary_checks.py)。

在已测的 StarRocks 4.1.4 + MySQL JDBC 5.1 路径中，原生 VARBINARY 被报告为 TEXT；默认读取会改变高位字节。回灌二进制必须显式设置 binaryColumns，或先用明确的文本编码搬运后解码。真实测试分别保留未配置时的错误复现和正确配置后的逐字节对账。
