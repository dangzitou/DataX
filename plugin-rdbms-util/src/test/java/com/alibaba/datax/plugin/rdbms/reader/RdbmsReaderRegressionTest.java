package com.alibaba.datax.plugin.rdbms.reader;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import org.junit.Test;
import java.sql.*;
import java.util.*;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class RdbmsReaderRegressionTest {
    private static class Row implements Record {
        final List<Column> columns = new ArrayList<Column>();
        public void addColumn(Column c) { columns.add(c); }
        public void setColumn(int i, Column c) { columns.set(i, c); }
        public Column getColumn(int i) { return columns.get(i); }
        public int getColumnNumber() { return columns.size(); }
        public int getByteSize() { return 0; }
        public int getMemorySize() { return 0; }
        public void setMeta(Map<String, String> meta) { }
        public Map<String, String> getMeta() { return null; }
    }
    private static class Sender implements RecordSender {
        final List<Record> rows = new ArrayList<Record>();
        public Record createRecord() { return new Row(); }
        public void sendToWriter(Record r) { rows.add(r); }
        public void flush() { }
        public void terminate() { }
        public void shutdown() { }
    }
    private static class Collector extends TaskPluginCollector {
        int dirty;
        public void collectDirtyRecord(Record r, Throwable t, String message) { dirty++; }
        public void collectMessage(String key, String value) { }
    }
    private static class Task extends CommonRdbmsReader.Task {
        Task() { super(DataBaseType.MySql); }
        void read(Sender sender, ResultSet rs, ResultSetMetaData meta, String encoding, Collector collector) {
            transportOneRecord(sender, rs, meta, 1, encoding, collector);
        }
    }
    private ResultSetMetaData metadata(int type) throws Exception {
        ResultSetMetaData meta = mock(ResultSetMetaData.class);
        when(meta.getColumnType(1)).thenReturn(type);
        when(meta.getColumnTypeName(1)).thenReturn(type == Types.DATE ? "YEAR" : "test");
        return meta;
    }

    @Test public void sqlNullBooleanAndYearStayNull() throws Exception {
        for (int type : new int[] {Types.BOOLEAN, Types.DATE}) {
            ResultSet rs = mock(ResultSet.class);
            when(rs.wasNull()).thenReturn(true);
            Sender sender = new Sender();
            new Task().read(sender, rs, metadata(type), "", new Collector());
            assertNull(sender.rows.get(0).getColumn(0).getRawData());
        }
    }

    @Test public void mandatoryEncodingPreservesNullAndReadsBytesOnce() throws Exception {
        ResultSet rs = mock(ResultSet.class);
        Sender sender = new Sender();
        Task task = new Task();
        ResultSetMetaData meta = metadata(Types.VARCHAR);
        task.read(sender, rs, meta, "UTF-8", new Collector());
        assertNull(sender.rows.get(0).getColumn(0).getRawData());
        when(rs.getBytes(1)).thenReturn("中文".getBytes("UTF-8"));
        task.read(sender, rs, meta, "UTF-8", new Collector());
        assertEquals("中文", sender.rows.get(1).getColumn(0).asString());
        verify(rs, times(2)).getBytes(1);
    }

    @Test public void connectionFailureIsFatalInsteadOfDirtyPartialRow() throws Exception {
        ResultSet rs = mock(ResultSet.class);
        when(rs.getString(1)).thenThrow(new SQLException("connection lost", "08S01", 2013));
        Sender sender = new Sender();
        Collector collector = new Collector();
        try {
            new Task().read(sender, rs, metadata(Types.VARCHAR), "", collector);
            fail("lost connection was swallowed");
        } catch (DataXException expected) {
            assertEquals(0, collector.dirty);
            assertTrue(sender.rows.isEmpty());
        }
    }

    @Test public void dirtyConversionDoesNotSendPartialRecord() throws Exception {
        ResultSet rs = mock(ResultSet.class);
        when(rs.getTime(1)).thenThrow(new IllegalArgumentException("invalid time"));
        Sender sender = new Sender();
        Collector collector = new Collector();
        new Task().read(sender, rs, metadata(Types.TIME), "", collector);
        assertEquals(1, collector.dirty);
        assertTrue(sender.rows.isEmpty());
    }

    @Test public void metadataIsReadOncePerResultSet() throws Exception {
        ResultSet rs = mock(ResultSet.class);
        when(rs.getString(1)).thenReturn("9223372036854775808");
        ResultSetMetaData meta = metadata(Types.BIGINT);
        Sender sender = new Sender();
        Task task = new Task();
        for (int i = 0; i < 100; i++) task.read(sender, rs, meta, "", new Collector());
        verify(meta, times(1)).getColumnType(1);
        assertEquals("9223372036854775808", sender.rows.get(0).getColumn(0).asString());
    }
}
