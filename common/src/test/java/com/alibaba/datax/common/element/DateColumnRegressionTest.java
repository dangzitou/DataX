package com.alibaba.datax.common.element;

import com.alibaba.datax.common.util.Configuration;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Date;
import static org.junit.Assert.*;

public class DateColumnRegressionTest {
    @Before @After public void restoreFormats() {
        Configuration config = Configuration.newDefault();
        config.set("common.column.datetimeFormat", "yyyy-MM-dd HH:mm:ss");
        config.set("common.column.timeZone", "GMT+8");
        ColumnCast.bind(config);
    }

    @Test public void timestampsPreserveFractionsThroughDateAndDefaultText() {
        Configuration config = Configuration.newDefault();
        config.set("common.column.timeZone", "UTC");
        ColumnCast.bind(config);
        for (String input : new String[] {"2024-01-01T12:34:56.123456789Z",
                "2024-01-01T12:34:56.000001Z", "1969-12-31T23:59:59.999999Z",
                "2024-01-01T12:34:56Z"}) {
            Timestamp timestamp = Timestamp.from(Instant.parse(input));
            DateColumn column = new DateColumn(timestamp);
            assertEquals(timestamp, column.asDate());
            assertEquals(timestamp, new DateColumn((Date) timestamp).asDate());
            assertEquals(input.replace('T', ' ').replace("Z", ""), column.asString());
            ((Timestamp) column.asDate()).setNanos(0);
            assertEquals(timestamp, column.asDate()); // Returned dates must not mutate the record.
        }
        assertNull(new DateColumn((Timestamp) null).asDate());
        assertNull(new DateColumn((Timestamp) null).asString());
    }

    @Test public void legacyMillisAndExplicitFormatsRemainCompatible() {
        Configuration config = Configuration.newDefault();
        config.set("common.column.timeZone", "UTC");
        ColumnCast.bind(config);
        assertEquals(new Date(-1L), new DateColumn(-1L).asDate());
        assertEquals("1969-12-31 23:59:59", new DateColumn(-1L).asString());
        config.set("common.column.datetimeFormat", "yyyy-MM-dd HH:mm:ss.SSS");
        ColumnCast.bind(config);
        assertEquals("2024-01-01 12:34:56.123", new DateColumn(
                Timestamp.from(Instant.parse("2024-01-01T12:34:56.123456Z"))).asString());
    }
}
