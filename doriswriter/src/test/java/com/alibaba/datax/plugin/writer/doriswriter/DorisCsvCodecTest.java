package com.alibaba.datax.plugin.writer.doriswriter;

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

public class DorisCsvCodecTest {
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
        DorisCsvCodec csv = new DorisCsvCodec(null);
        DorisJsonCodec json = new DorisJsonCodec(Collections.singletonList("txt"));
        for (String value : new String[] {"\\N", "a\tb", "a\nb"}) {
            Record row = row(value);
            try {
                csv.codec(row);
                fail("Ambiguous CSV must not succeed");
            } catch (IllegalArgumentException expected) {
                assertTrue(expected.getMessage().contains("format=json"));
            }
            assertEquals(value, JSON.parseObject(json.codec(row)).getString("txt"));
        }
        Record row = row(null);
        assertEquals("\\N", csv.codec(row));
        row = row("a|b");
        assertEquals("a|b", csv.codec(row));
        try {
            new DorisCsvCodec("\t", "|").codec(row);
            fail("Configured row delimiter must be checked");
        } catch (IllegalArgumentException expected) { }
    }

    @Test public void utf8BytesPreserveJsonTextAndNull() throws Exception {
        DorisJsonCodec json = new DorisJsonCodec(Collections.singletonList("txt"));
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 10000; i++) longText.append("中文😀\n\t\"\\");
        for (String value : new String[] {null, "", "\\N", "a\r\nb", longText.toString()}) {
            byte[] bytes = json.codecBytes(row(value));
            String decoded = java.nio.charset.StandardCharsets.UTF_8.newDecoder()
                    .decode(java.nio.ByteBuffer.wrap(bytes)).toString();
            assertEquals(value, JSON.parseObject(decoded).getString("txt"));
            assertArrayEquals(json.codec(row(value)).getBytes(java.nio.charset.StandardCharsets.UTF_8), bytes);
        }
        // Sample every Unicode plane, excluding unpaired UTF-16 surrogate code points.
        for (int cp = 0; cp <= Character.MAX_CODE_POINT; cp += 257) {
            if (cp >= Character.MIN_SURROGATE && cp <= Character.MAX_SURROGATE) continue;
            String value = new String(Character.toChars(cp));
            assertEquals(value, JSON.parseObject(json.codecBytes(row(value))).getString("txt"));
        }
    }

    @Test public void binaryFieldsAreExplicitAndReversible() {
        byte[] binary = new byte[]{0,0,1,2,3,4,5,6,7,8,(byte)255};
        Record row = columnRow(new BytesColumn(binary));
        for (BinaryEncoding encoding : new BinaryEncoding[]{BinaryEncoding.HEX, BinaryEncoding.BASE64}) {
            String expected = encoding.encode(binary);
            assertEquals(expected, new DorisCsvCodec(null, null, encoding).codec(row));
            assertEquals(expected, JSON.parseObject(new DorisJsonCodec(Collections.singletonList("payload"), encoding).codec(row)).getString("payload"));
        }
        try { new DorisCsvCodec(null).codec(row); fail("Raw bytes must not become an integer"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("binaryEncoding")); }
        try { new DorisJsonCodec(Collections.singletonList("payload")).codec(row); fail("Raw bytes must not become an integer"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("binaryEncoding")); }
        try { new DorisCsvCodec("0", null, BinaryEncoding.HEX).codec(row); fail("Encoding must not bypass delimiter guards"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("Unsafe CSV")); }
    }
}
