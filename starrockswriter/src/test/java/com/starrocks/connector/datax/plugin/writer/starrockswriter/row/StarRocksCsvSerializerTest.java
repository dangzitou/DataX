package com.starrocks.connector.datax.plugin.writer.starrockswriter.row;

import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.element.BytesColumn;
import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.util.BinaryEncoding;
import com.alibaba.datax.common.element.Record;
import java.lang.reflect.Proxy;
import com.alibaba.fastjson2.JSON;
import org.junit.Test;
import java.util.Collections;
import static org.junit.Assert.*;

public class StarRocksCsvSerializerTest {
    private Record row(String value) { return columnRow(new StringColumn(value)); }

    private Record columnRow(Column value) {
        return (Record) Proxy.newProxyInstance(Record.class.getClassLoader(),
                new Class<?>[] {Record.class}, (proxy, method, args) -> {
                    if (method.getName().equals("getColumnNumber")) return 1;
                    if (method.getName().equals("getColumn")) return value;
                    throw new UnsupportedOperationException(method.getName());
                });
    }

    @Test public void ambiguousCsvFailsAndJsonPreservesText() {
        StarRocksCsvSerializer csv = new StarRocksCsvSerializer(null);
        StarRocksJsonSerializer json = new StarRocksJsonSerializer(Collections.singletonList("txt"));
        for (String value : new String[] {"\\N", "a\tb", "a\nb"}) {
            Record row = row(value);
            try {
                csv.serialize(row);
                fail("Ambiguous CSV must not succeed");
            } catch (IllegalArgumentException expected) {
                assertTrue(expected.getMessage().contains("format=json"));
            }
            assertEquals(value, JSON.parseObject(json.serialize(row)).getString("txt"));
        }
        Record row = row(null);
        assertEquals("\\N", csv.serialize(row));
        row = row("a|b");
        assertEquals("a|b", csv.serialize(row));
        try {
            new StarRocksCsvSerializer("\t", "|").serialize(row);
            fail("Configured row delimiter must be checked");
        } catch (IllegalArgumentException expected) { }
    }

    @Test public void utf8BytesPreserveJsonTextAndNull() throws Exception {
        StarRocksJsonSerializer json = new StarRocksJsonSerializer(Collections.singletonList("txt"));
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 10000; i++) longText.append("中文😀\n\t\"\\");
        for (String value : new String[] {null, "", "\\N", "a\r\nb", longText.toString()}) {
            byte[] bytes = json.serializeBytes(row(value));
            String decoded = java.nio.charset.StandardCharsets.UTF_8.newDecoder()
                    .decode(java.nio.ByteBuffer.wrap(bytes)).toString();
            assertEquals(value, JSON.parseObject(decoded).getString("txt"));
            assertArrayEquals(json.serialize(row(value)).getBytes(java.nio.charset.StandardCharsets.UTF_8), bytes);
        }
        // Sample every Unicode plane, excluding unpaired UTF-16 surrogate code points.
        for (int cp = 0; cp <= Character.MAX_CODE_POINT; cp += 257) {
            if (cp >= Character.MIN_SURROGATE && cp <= Character.MAX_SURROGATE) continue;
            String value = new String(Character.toChars(cp));
            assertEquals(value, JSON.parseObject(json.serializeBytes(row(value))).getString("txt"));
        }
    }

    @Test public void binaryFieldsAreExplicitAndReversible() {
        byte[] binary = new byte[]{0,0,1,2,3,4,5,6,7,8,(byte)255};
        Record row = columnRow(new BytesColumn(binary));
        for (BinaryEncoding encoding : new BinaryEncoding[]{BinaryEncoding.HEX, BinaryEncoding.BASE64}) {
            String expected = encoding.encode(binary);
            assertEquals(expected, new StarRocksCsvSerializer(null, null, encoding).serialize(row));
            assertEquals(expected, JSON.parseObject(new StarRocksJsonSerializer(Collections.singletonList("payload"), encoding).serialize(row)).getString("payload"));
        }
        try { new StarRocksCsvSerializer(null).serialize(row); fail("Raw bytes must not become an integer"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("binaryEncoding")); }
        try { new StarRocksJsonSerializer(Collections.singletonList("payload")).serialize(row); fail("Raw bytes must not become an integer"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("binaryEncoding")); }
        try { new StarRocksCsvSerializer("0", null, BinaryEncoding.HEX).serialize(row); fail("Encoding must not bypass delimiter guards"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("Unsafe CSV")); }
    }
    @Test public void jsonBytesMatchMapAcrossFieldOrdersAndSpecialKeys() {
        java.util.List<java.util.List<String>> layouts = new java.util.ArrayList<>();
        layouts.add(java.util.Arrays.asList("id", "null", "bool", "decimal", "中文😀\"\n", "bytes"));
        layouts.add(java.util.Arrays.asList("repeat", "repeat", null, "bool", "decimal", "bytes"));
        layouts.add(Collections.<String>emptyList());
        java.util.List<String> wide = new java.util.ArrayList<>();
        // Many colliding keys exercise HashMap's tree buckets and iteration order.
        for (int i = 0; i < 128; i++) {
            StringBuilder key = new StringBuilder();
            for (int bit = 0; bit < 7; bit++) key.append((i & (1 << bit)) == 0 ? "Aa" : "BB");
            wide.add(key.toString());
        }
        layouts.add(wide);
        final Column[] values = {new com.alibaba.datax.common.element.LongColumn("18446744073709551615"),
            new StringColumn(), new com.alibaba.datax.common.element.BoolColumn(true),
            new com.alibaba.datax.common.element.DoubleColumn("12345678901234567890.123456789012345678"),
            new StringColumn("中文😀\"\n\t\\"), new BytesColumn(new byte[]{0,1,(byte)255})};
        Record row = (Record) Proxy.newProxyInstance(Record.class.getClassLoader(),new Class<?>[]{Record.class},
            (proxy, method, args) -> {
                if (method.getName().equals("getColumn")) return values[(Integer) args[0] % values.length];
                throw new UnsupportedOperationException(method.getName());
            });
        for (java.util.List<String> layout : layouts) {
            java.util.List<String> fields = new java.util.ArrayList<>(layout);
            StarRocksJsonSerializer codec = new StarRocksJsonSerializer(fields, BinaryEncoding.HEX);
            assertArrayEquals(codec.serialize(row).getBytes(java.nio.charset.StandardCharsets.UTF_8), codec.serializeBytes(row));
            if (!fields.isEmpty()) {
                fields.set(0,"changed after construction");
                assertArrayEquals(codec.serialize(row).getBytes(java.nio.charset.StandardCharsets.UTF_8), codec.serializeBytes(row));
            }
        }
    }

}
