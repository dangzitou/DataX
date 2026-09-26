# 二进制字段：旧版静默失真、显式编码与真实往返验证

2026-09-27。运行代码 `5b81262`，补充风险回归和 reader 文档 `d9b2dec`。本轮修复 StarRocks、Doris、SelectDB writer 把任意 BYTES 折算成 long 的问题，并提供显式 JDBC 二进制结果列读取。**不是性能提升验收，也不是亿级零差错承诺。StarRocks 原生二进制的默认 reader 路径仍有已复现的失真风险，必须显式配置。**

## 原版与此前 fork 的实际问题

未修改上游 `80ec23d` 和此前 fork 运行包 `a9f0db4`，在真实 PG→StarRocks/Doris 的 CSV、JSON 写入中都复现了以下行为，各组合重复 4 次：

- 空字节串、`00`、`0000` 全部成为文本 `"0"`。
- `01`、`0001`、`010000000000000000` 全部成为文本 `"1"`。
- 八个 `FF` 成为 `"-1"`；更长数据在 Java long 移位/溢出后失去原内容。
- 任务成功、行数仍为 11，不能通过只核对 count 发现错误。

根因是三个 writer 的基础序列化器将 BYTES 当作整数处理。现在复用 common 中的 `BinaryEncoding`，使用标准库 Base64 或逐字节 hex；默认 `reject`，非 NULL BYTES 未配置编码就明确失败，包括空字节串。NULL、空字节串、前导零和长度保持区别。普通字符串、整数、布尔字段沿用原有表示。

这是有意的兼容性变化：以前表面成功的危险作业现在可能报错，不能把更多明确报错等同于更多静默坏数据。MySQL 数值 BIT 如需保持数值语义，需在源 SQL 显式 CAST 成整数并匹配目标范围。`binaryEncoding` 不是目标 schema 自动校验器；随意选编码或列映射仍可能得到错误数据。

同时修正 Doris CSV 碰撞检查使用错误的行分隔符键，改为与上传端相同的 `line_delimiter`；SelectDB CSV 补上已有的字段/行分隔符与字面量 `\N` 碰撞拒绝。未增加依赖。

## 真实服务端暴露的另外两个边界

**StarRocks 原生二进制不能依赖隐式解码。**本次 4.1.4 实测直接把 hex `00` 发给 VARBINARY 会存为 `D3`，作业仍成功。当前[类型文档](https://docs.starrocks.io/docs/sql-reference/data-types/string-type/BINARY/)说 CSV 使用 hex，但该版本[转换器源码](https://github.com/StarRocks/starrocks/blob/4.1.4/be/src/formats/csv/varbinary_converter.cpp)实际先尝试 Base64，失败再保留原输入。最终测试使用显式转换：

```json
{
  "binaryEncoding": "hex",
  "loadProps": {
    "format": "csv",
    "strict_mode": true,
    "max_filter_ratio": 0,
    "columns": "id,encoded_payload,payload=to_binary(encoded_payload,'hex')"
  }
}
```

这是本轮两列夹具的 writer 参数片段，实际列映射必须随业务表调整。已验证原生 VARBINARY 往返，包括 NULL 和空字节串；未宣称任意版本或 JSON 原生二进制导入均可用。

**StarRocks reader 的默认类型推断仍不安全。**本次 MySQL JDBC 5.1 把原生 VARBINARY 报为 TEXT/String，结果元数据没有原表名或 catalog。默认 getString 会把部分高位字节变成字符，再写入 PG 时改变其字节。原版与当前候选各重复 4 次，均为 11 行中 3 行内容改变；双向 `EXCEPT ALL` 差异数为 6，作业成功、行数相同。该已知风险没有隐藏或计为“修复通过”。

新增 `reader.parameter.binaryColumns: ["payload"]`，按结果列标签精确匹配，缓存解析后用 JDBC getBytes 构造 BytesColumn。别名、大小写要匹配；重复配置、重名结果、缺失列、非文本/二进制类型都拒绝，即使结果为空或 errorLimit=100。未配置的字段维持旧行为，不能自动识别任意表达式的语义。

Doris 实际版本 **4.1.3-rc02-7126cf65d96** 的原生 VARBINARY 建表探针明确返回不支持本地建表。因此 Doris 测的是 STRING 中显式保存 hex/base64，再在读取 SQL 中恢复字节，**不是 Doris 原生二进制存储**。回读使用 `UNHEX` 或 `FROM_BASE64`，并设置 binaryColumns。SQL 显式写成 `CASE WHEN payload='' THEN '' ELSE FROM_BASE64(payload) END AS payload`，避免 StarRocks 的解码函数把空串变成 NULL；hex 同样处理。最终直接写入 PG bytea 对账，没有事后在 PG 改写结果来“修复”差异。

## 最终测试及计数口径

本机 macOS 27 / Apple M5 / 16 GiB、JDK 8、真实 DataX Engine；PG **17.11**，fsync/synchronous_commit/full_page_writes 开启。StarRocks **4.1.4-4a9848e** 与上述 Doris 顺序运行，各限制 4 CPU / 4 GiB；PG 限制 4 CPU / 2 GiB。镜像 digest、后端版本和运行包每个 JAR 的 SHA-256 均已存档。候选运行包来自 clean `5b81262`，最终脚本来自 `d9b2dec`；后者仅补 reader 风险测试和文档，不改运行代码。

二进制夹具只有 **11 行、2 列**：NULL、空字节、前导零、8 字节全 FF、9 字节不同零位置、00–FF 全字节范围、中文 emoji 的 UTF-8 字节。落库文本编码逐字段核对；回到 PG 后对源/目标做双向 `EXCEPT ALL`，比较原始 bytea 和行重数，无目标主键掩盖重复。

| 二进制检查 | Engine 次数 | 实际断言 |
|---|---:|---|
| 原版/此前 fork，两个 OLAP 后端，CSV/JSON，各 4 次 | 32 | 都成功但错误折算成 long，精确复现旧风险 |
| 当前候选不配置编码，两后端两格式各 4 次 | 16 | 明确拒绝非 NULL BYTES；本次小夹具目标为空 |
| hex/base64 × CSV/JSON × 两后端 × 4 次，写入及回读 | 64 | 编码表示正确；直接回写 PG bytea，差异 0 |
| StarRocks 原生 VARBINARY 显式转换及显式二进制读取，4 次 | 8 | 每次 11 行，字节差异 0；其中两次使用带空格别名 |
| binaryColumns 缺列/错误类型/重复配置/重名结果/空结果缺列 | 5 | 全部报错，即使 errorLimit=100；目标 0 行 |
| 两后端的非法编码及编码后分隔符冲突 | 8 | 全部拒绝，本次目标 0 行 |
| 原版及候选未配置 binaryColumns 的原生 StarRocks 读取，各 4 次 | 8 | 继续复现 3 行失真，计为已知未消除风险 |
| **合计** | **141** | **不是 141 次成功无损迁移** |

另有 **139 次既有 Engine 回归**：PG 原子追加/实际 TCP 回执丢失等 93 次、PG 字段保真与普通模式重复/部分提交风险 14 次、StarRocks/Doris 文本读写各 16 次。全部符合既有断言，包含预期失败和旧风险复现。总计 **280 次 Engine、268 个结果条目**；原子 owner 竞争和原生二进制往返有一个条目记录多个 Engine。

还通过 **52 项 JUnit 测试**及 **105 项纯编码器/工厂检查**。后者包括 SelectDB CSV/JSON、NULL、各种长度、编码配置和分隔符检查；**没有真实 SelectDB 服务端验收**。单元测试另覆盖最长 4096 字节的可逆编码，不等同大字段数据库传输测试。

不同表的功能回归有并行运行，所有日志中的 seconds 仅记录过程，不能用来算性能提升。本轮没有性能 A/B，也没有亿级运行。StarRocks 主套件最后一次完整运行是 53 条目 / 57 Engine；之后对最终原生夹具分别补跑原版和候选各 4 次无 hint 风险检查，独立结果保存，避免把新增测试冒充已包含在旧套件内。

## 仍不能承诺的事项

本修复减少了已知的静默字段损坏，但没有建立生产错误率统计，不能给出“比原版低多少”或“不会增加任何错误”的数字。默认拒绝在大作业遇到坏值前仍可能已经提交前面的批次；本次目标为空的断言只适用于这些小夹具。

OLAP 的提交回执丢失、重跑幂等、整作业原子性没有因此解决；现有 [PG 原子追加](REPORT-pg-atomic.zh-CN.md)只覆盖显式开启的 PG 单表追加，不覆盖这些 OLAP writer，也不能检测所有在 reader 转换阶段已经变坏的值。源快照、分片边界、目标唯一性、字段类型/精度与最终多重集合对账仍要按实际链路验收。随机/未知字段组合、外部修改、全部插件、真实 SelectDB、亿级锁等待/磁盘/恢复能力均未覆盖。

**全场景至少 +25%、争取 +50%，以及亿级零重复、零遗漏、零脏数据，仍未达成。**不能把这批正确性断言替换成上述验收。

## 复现、证据和磁盘

按[构建指南](README.md)准备 JDK 8，候选构建附加模块包括 postgresqlreader/postgresqlwriter、starrocksreader/starrockswriter、dorisreader/doriswriter、selectdbwriter。对专用测试容器执行：

```sh
python3 benchmarks/binary_codec_checks.py /tmp/datax-candidate /tmp/binary-codecs
python3 benchmarks/olap_binary_checks.py /tmp/datax-baseline /tmp/sr-binary-baseline --expect-legacy
python3 benchmarks/olap_binary_checks.py /tmp/datax-candidate /tmp/sr-binary-candidate
# 停止 StarRocks，再启动 Doris，端口和固定测试口令见 data-fidelity.yml。
python3 benchmarks/olap_binary_checks.py /tmp/datax-baseline /tmp/doris-binary-baseline --backend doris --expect-legacy
python3 benchmarks/olap_binary_checks.py /tmp/datax-candidate /tmp/doris-binary-candidate --backend doris
```

脚本会重建自己的 pg_binary_* 与 datax_bench.binary_target 夹具，只应用于专用测试库。新版主脚本包含候选无 hint 风险检查，仍预期复现失真。

[原始结果、每次配置/日志、脚本快照、构建哈希和存储记录](results/2026-09-27-binary/)保留了开发阶段失败：测试账号漏密码、隐式原生解码改变字节、空串解码变 NULL、编码器测试误用点号配置键。`development-*` 只作排障证据，最终统计以 `summary.json` 列出的套件为准。生产修复和测试已推送；具体 CI 结果按 `d9b2dec` 的运行页面核对，不把本地通过当作远端完成。

归档时 `d9b2dec` 的 [MySQL CI](https://github.com/dangzitou/DataX/actions/runs/36271845558) 已通过，[PG/StarRocks/Doris CI](https://github.com/dangzitou/DataX/actions/runs/36271845540) 仍在运行；状态 JSON 保留当时快照。此前 `2ae138f` 的两条 CI 均已通过，不能代替新提交的结果。

新二进制源仅 11 行。采样的 PG 数据目录 + 当时运行的 OLAP 可写层最高 **2,050,514,944 字节，约 1.91 GiB**；包含此前已有百万行 PG 源表，不包含缓存镜像/Maven/工作区，也不是全过程绝对峰值。收尾已移除本轮 StarRocks/Doris 容器及核对过归档哈希的旧候选运行包，保留原版与当前运行包。PG 约 1.34 GiB，无遗留原子暂存表或测试客户端连接；临时根目录约 773 MiB。没有全局 prune，也没有删除无关容器或镜像。
