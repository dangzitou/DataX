package com.alibaba.datax.common.util;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.SequenceInputStream;
import java.util.Enumeration;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Objects;

/** Repeatable framing over encoded rows; callers must not modify the row byte arrays. */
public final class BatchPayload {
    private final byte[][] rows;
    private final byte[] prefix, separator, suffix;
    private final long length;

    private BatchPayload(List<byte[]> rows, byte[] prefix, byte[] separator, byte[] suffix) {
        this.rows = rows.toArray(new byte[rows.size()][]);
        this.prefix = prefix;
        this.separator = separator;
        this.suffix = suffix;
        long size = Math.addExact((long) prefix.length, suffix.length);
        size = Math.addExact(size, Math.multiplyExact((long) Math.max(0, this.rows.length - 1), separator.length));
        for (byte[] row : this.rows) size = Math.addExact(size, Objects.requireNonNull(row, "row").length);
        this.length = size;
    }

    public static BatchPayload json(List<byte[]> rows) {
        return new BatchPayload(rows, new byte[]{'['}, new byte[]{','}, new byte[]{']'});
    }

    public static BatchPayload delimited(List<byte[]> rows, byte[] delimiter) {
        byte[] separator = delimiter.clone();
        return new BatchPayload(rows, new byte[0], separator, rows.isEmpty() ? new byte[0] : separator);
    }

    public long length() { return length; }

    public void writeTo(OutputStream output) throws IOException {
        Objects.requireNonNull(output, "output");
        output.write(prefix);
        for (int i = 0; i < rows.length; i++) {
            if (i > 0) output.write(separator);
            output.write(rows[i]);
        }
        output.write(suffix);
        output.flush();
    }

    public InputStream openStream() {
        return new SequenceInputStream(new Enumeration<InputStream>() {
            private int row = -1;
            private boolean delimiter, ending = true;

            public boolean hasMoreElements() { return row < rows.length || ending; }

            public InputStream nextElement() {
                if (row == -1) {
                    row = 0;
                    return new ByteArrayInputStream(prefix);
                }
                if (row < rows.length) {
                    if (delimiter) {
                        delimiter = false;
                        return new ByteArrayInputStream(separator);
                    }
                    byte[] bytes = rows[row++];
                    delimiter = row < rows.length;
                    return new ByteArrayInputStream(bytes);
                }
                if (ending) {
                    ending = false;
                    return new ByteArrayInputStream(suffix);
                }
                throw new NoSuchElementException();
            }
        });
    }
}
