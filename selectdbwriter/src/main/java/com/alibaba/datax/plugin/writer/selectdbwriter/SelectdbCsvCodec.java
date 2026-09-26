package com.alibaba.datax.plugin.writer.selectdbwriter;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.util.BinaryEncoding;

public class SelectdbCsvCodec extends SelectdbBaseCodec implements SelectdbCodec {

    private static final long serialVersionUID = 1L;

    private final String columnSeparator;
    private final String rowDelimiter;

    public SelectdbCsvCodec ( String sp) {
        this(sp, null, BinaryEncoding.REJECT);
    }

    public SelectdbCsvCodec(String sp, String rowDelimiter, BinaryEncoding binaryEncoding) {
        super(binaryEncoding);
        this.rowDelimiter = DelimiterParser.parse(rowDelimiter, "\n");
        this.columnSeparator = DelimiterParser.parse(sp, "\t");
    }

    @Override
    public String codec( Record row) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < row.getColumnNumber(); i++) {
            String value = convertionField(row.getColumn(i));
            if (value != null && ("\\N".equals(value) || value.contains(columnSeparator)
                    || value.contains(rowDelimiter))) {
                throw new IllegalArgumentException("Unsafe CSV value at column " + i
                        + "; use loadProps.file.type=json and file.strip_outer_array=true to preserve text");
            }
            sb.append(null == value ? "\\N" : value);
            if (i < row.getColumnNumber() - 1) {
                sb.append(columnSeparator);
            }
        }
        return sb.toString();
    }
}
