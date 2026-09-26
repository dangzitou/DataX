package com.alibaba.datax.plugin.rdbms.reader;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
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
        Task(DataBaseType type) { super(type); }
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

    @Test public void postgresqlNativeIntegersPreserveValuesNullAndByteCounts() throws Exception {
        for (int type : new int[]{Types.SMALLINT, Types.INTEGER, Types.BIGINT}) {
            Task task = new Task(DataBaseType.PostgreSQL);
            ResultSetMetaData meta = metadata(type);
            for (Long value : new Long[]{null, 0L, 1L, -1L, -32768L, 32767L,
                    (long) Integer.MIN_VALUE, (long) Integer.MAX_VALUE, Long.MIN_VALUE, Long.MAX_VALUE}) {
                ResultSet rs = mock(ResultSet.class);
                when(rs.getLong(1)).thenReturn(value == null ? 0L : value);
                when(rs.wasNull()).thenReturn(value == null);
                Sender sender = new Sender();
                task.read(sender, rs, meta, "", new Collector());
                Column actual = sender.rows.get(0).getColumn(0);
                LongColumn expected = new LongColumn(value == null ? null : value.toString());
                assertEquals(expected.getRawData(), actual.getRawData());
                assertEquals(expected.getByteSize(), actual.getByteSize());
                verify(rs, never()).getString(1);
            }
        }
        ResultSet failing = mock(ResultSet.class);
        when(failing.getLong(1)).thenThrow(new SQLException("connection lost", "08006"));
        Sender sender = new Sender(); Collector collector = new Collector();
        try {
            new Task(DataBaseType.PostgreSQL).read(sender, failing, metadata(Types.BIGINT), "", collector);
            fail("JDBC failure was swallowed");
        } catch (DataXException expected) {
            assertEquals(0, collector.dirty);
            assertTrue(sender.rows.isEmpty());
        }
    }

    @Test public void explicitBinaryLabelsBypassLossyTextConversion() throws Exception {
        Configuration config=Configuration.newDefault();
        config.set(Key.JDBC_URL,"jdbc:mysql://test/db");
        config.set(Key.BINARY_COLUMNS,Collections.singletonList("payload"));
        Task task=new Task(); task.init(config);
        ResultSetMetaData meta=metadata(Types.LONGVARCHAR);
        when(meta.getColumnLabel(1)).thenReturn("payload");
        ResultSet rs=mock(ResultSet.class);
        byte[] binary=new byte[]{0,(byte)128,(byte)255};
        when(rs.getBytes(1)).thenReturn(binary);
        Sender sender=new Sender(); Collector collector=new Collector();
        task.read(sender,rs,meta,"UTF-8",collector);
        assertEquals(Column.Type.BYTES,sender.rows.get(0).getColumn(0).getType());
        assertArrayEquals(binary,sender.rows.get(0).getColumn(0).asBytes());
        verify(rs,never()).getString(1);
        when(rs.getBytes(1)).thenReturn(null);
        task.read(sender,rs,meta,"UTF-8",collector);
        assertNull(sender.rows.get(1).getColumn(0).getRawData());
        verify(meta,times(1)).getColumnType(1);
        for (boolean missing:new boolean[]{true,false}) {
            ResultSetMetaData invalid=metadata(missing ? Types.LONGVARCHAR : Types.BIGINT);
            when(invalid.getColumnLabel(1)).thenReturn(missing ? "other" : "payload");
            try { task.read(sender,rs,invalid,"",collector); fail("Invalid hint must fail"); }
            catch (DataXException expected) { assertTrue(expected.getMessage().contains("binaryColumns")); }
        }
        assertEquals(0,collector.dirty);
        assertEquals(2,sender.rows.size());
    }
}
