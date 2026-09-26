package com.alibaba.datax.common.util;

import org.junit.Test;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import static org.junit.Assert.*;

public class BatchPayloadTest {
    @Test
    public void preservesFramingAndFreshStreams() throws Exception {
        for (List<byte[]> rows : Arrays.asList(Collections.<byte[]>emptyList(),
                Collections.singletonList(new byte[0]), Arrays.asList(new byte[0], new byte[0]),
                Arrays.asList("null".getBytes("UTF-8"), "\"中文😀\\n\"".getBytes("UTF-8"), new byte[0]))) {
            for (String delimiter : Arrays.asList("", "\n", "\r\n", "中文😀")) {
                ByteArrayOutputStream old = new ByteArrayOutputStream();
                for (byte[] row : rows) {
                    old.write(row);
                    old.write(delimiter.getBytes(StandardCharsets.UTF_8));
                }
                check(BatchPayload.delimited(rows, delimiter.getBytes(StandardCharsets.UTF_8)), old.toByteArray());
            }
            ByteArrayOutputStream old = new ByteArrayOutputStream();
            old.write('[');
            for (int i = 0; i < rows.size(); i++) {
                if (i > 0) old.write(',');
                old.write(rows.get(i));
            }
            old.write(']');
            check(BatchPayload.json(rows), old.toByteArray());
        }
        List<byte[]> mutable = new ArrayList<byte[]>();
        mutable.add(new byte[]{'x'});
        byte[] separator = new byte[]{'!'};
        BatchPayload payload = BatchPayload.delimited(mutable, separator);
        mutable.clear(); separator[0] = '?';
        check(payload, new byte[]{'x', '!'});
        try {
            payload.writeTo(new OutputStream() {
                public void write(int value) throws IOException { throw new IOException("sink failed"); }
            });
            fail("Swallowed the output failure");
        } catch (IOException expected) { assertEquals("sink failed", expected.getMessage()); }
    }

    private void check(BatchPayload payload, byte[] expected) throws Exception {
        assertEquals(expected.length, payload.length());
        for (int size : new int[]{1, 3, 8192}) {
            for (int attempt = 0; attempt < 2; attempt++) {
                ByteArrayOutputStream actual = new ByteArrayOutputStream();
                payload.writeTo(actual);
                assertArrayEquals(expected, actual.toByteArray());
                actual.reset();
                try (InputStream input = payload.openStream()) {
                    byte[] buffer = new byte[size];
                    assertEquals(0, input.read(buffer, 0, 0));
                    int count;
                    while ((count = input.read(buffer)) != -1) actual.write(buffer, 0, count);
                    assertEquals(-1, input.read());
                }
                assertArrayEquals(expected, actual.toByteArray());
            }
        }
    }

    @Test
    public void countsMoreThanTwoGiBWithoutAllocatingAContiguousBody() throws Exception {
        // Repeated immutable references exercise long framing, not a real 2 GiB dataset.
        List<byte[]> rows = Collections.nCopies(4096, new byte[512 * 1024]);
        for (BatchPayload payload : Arrays.asList(BatchPayload.json(rows), BatchPayload.delimited(rows, new byte[]{'\n'}))) {
            final long[] written = {0};
            payload.writeTo(new OutputStream() {
                public void write(int value) { written[0]++; }
                public void write(byte[] bytes, int offset, int length) { written[0] += length; }
            });
            assertTrue(payload.length() > Integer.MAX_VALUE);
            assertEquals(payload.length(), written[0]);
        }
    }
}
