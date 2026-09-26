package com.alibaba.datax.plugin.writer.doriswriter;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.util.BinaryEncoding;

public class DorisCsvCodec extends DorisBaseCodec implements DorisCodec {

    private static final long serialVersionUID = 1L;

    private final String columnSeparator;
    private final String rowDelimiter;

    public DorisCsvCodec(String sp) {
        this(sp, null);
    }

    public DorisCsvCodec(String sp, String rowDelimiter) {
        this(sp, rowDelimiter, BinaryEncoding.REJECT);
    }

    public DorisCsvCodec(String sp, String rowDelimiter, BinaryEncoding binaryEncoding) {
        super(binaryEncoding);
        this.columnSeparator = DelimiterParser.parse(sp, "\t");
        this.rowDelimiter = DelimiterParser.parse(rowDelimiter, "\n");
    }

    @Override
    public String codec( Record row) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < row.getColumnNumber(); i++) {
            String value = convertionField(row.getColumn(i));
            // This wire format has no quoting. Reject ambiguous values instead of
            // silently turning text into NULL, extra columns or extra records.
            if (value != null && ("\\N".equals(value) || value.contains(columnSeparator)
                    || value.contains(rowDelimiter))) {
                throw new IllegalArgumentException("Unsafe CSV value at column " + i
                        + "; use loadProps.format=json and strip_outer_array=true to preserve text");
            }
            sb.append(null == value ? "\\N" : value);
            if (i < row.getColumnNumber() - 1) {
                sb.append(columnSeparator);
            }
        }
        return sb.toString();
    }
}
