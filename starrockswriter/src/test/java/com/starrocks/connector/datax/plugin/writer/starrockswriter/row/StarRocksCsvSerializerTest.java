package com.starrocks.connector.datax.plugin.writer.starrockswriter.row;

import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.element.Record;
import java.lang.reflect.Proxy;
import com.alibaba.fastjson2.JSON;
import org.junit.Test;
import java.util.Collections;
import static org.junit.Assert.*;

public class StarRocksCsvSerializerTest {
    private Record row(String value) {
        return (Record) Proxy.newProxyInstance(Record.class.getClassLoader(),
                new Class<?>[] {Record.class}, (proxy, method, args) -> {
                    if (method.getName().equals("getColumnNumber")) return 1;
                    if (method.getName().equals("getColumn")) return new StringColumn(value);
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
}
