package com.starrocks.connector.datax.plugin.writer.starrockswriter.row;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.util.BinaryEncoding;
import com.alibaba.datax.common.element.Column.Type;

public class StarRocksBaseSerializer {
    private final BinaryEncoding binaryEncoding;

    protected StarRocksBaseSerializer() { this(BinaryEncoding.REJECT); }
    protected StarRocksBaseSerializer(BinaryEncoding binaryEncoding) { this.binaryEncoding = binaryEncoding; }


    protected String fieldConvertion(Column col) {
        if (null == col.getRawData() || Type.NULL == col.getType()) {
            return null;
        }
        if (Type.BOOL == col.getType()) {
            return String.valueOf(col.asLong());
        }
        if (Type.BYTES == col.getType()) {
            return binaryEncoding.encode(col.asBytes());
        }
        return col.asString();
    }

}