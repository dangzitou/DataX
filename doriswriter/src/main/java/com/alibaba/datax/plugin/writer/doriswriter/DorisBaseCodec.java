package com.alibaba.datax.plugin.writer.doriswriter;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.util.BinaryEncoding;

public class DorisBaseCodec {
    private final BinaryEncoding binaryEncoding;

    protected DorisBaseCodec() { this(BinaryEncoding.REJECT); }
    protected DorisBaseCodec(BinaryEncoding binaryEncoding) { this.binaryEncoding = binaryEncoding; }

    protected String convertionField( Column col) {
        if (null == col.getRawData() || Column.Type.NULL == col.getType()) {
            return null;
        }
        if ( Column.Type.BOOL == col.getType()) {
            return String.valueOf(col.asLong());
        }
        if ( Column.Type.BYTES == col.getType()) {
            return binaryEncoding.encode(col.asBytes());
        }
        return col.asString();
    }
}
