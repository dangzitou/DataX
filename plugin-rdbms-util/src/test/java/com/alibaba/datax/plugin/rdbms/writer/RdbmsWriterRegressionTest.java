package com.alibaba.datax.plugin.rdbms.writer;

import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.element.BoolColumn;
import com.alibaba.datax.common.element.DateColumn;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import com.alibaba.datax.plugin.rdbms.util.DBUtilErrorCode;
import org.junit.Test;
import java.sql.*;
import java.util.Arrays;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class RdbmsWriterRegressionTest {
    @Test public void postgresBatchCountMismatchRollsBackWithoutReplayOrDirtyRows() throws Exception {
        for (int[] counts : new int[][] {null, {}, {1}, {1, 0}, {1, 2}, {1, Statement.EXECUTE_FAILED}, {1, -1}}) {
            CommonRdbmsWriter.Task task = task();
            task.dataBaseType = DataBaseType.PostgreSQL;
            Connection connection = connection();
            PreparedStatement statement = mock(PreparedStatement.class);
            when(connection.prepareStatement(anyString())).thenReturn(statement);
            when(statement.executeBatch()).thenReturn(counts);
            TaskPluginCollector collector = mock(TaskPluginCollector.class);
            try {
                task.startWriteWithConnection(records(), collector, connection);
                fail("Invalid affected-row counts must fail");
            } catch (DataXException expected) {
                assertEquals(DBUtilErrorCode.WRITE_ROW_COUNT_MISMATCH, expected.getErrorCode());
            }
            verify(connection).rollback();
            verify(connection, never()).commit();
            verify(connection, times(1)).prepareStatement(anyString());
            verify(statement, never()).execute();
            verifyZeroInteractions(collector);
            verify(statement).close();
            verify(connection).close();
        }
    }

    @Test public void postgresKeepsUnknownBatchCountsWithoutClaimingAnExactCount() throws Exception {
        CommonRdbmsWriter.Task task = task();
        task.dataBaseType = DataBaseType.PostgreSQL;
        Connection connection = connection();
        PreparedStatement statement = mock(PreparedStatement.class);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(statement.executeBatch()).thenReturn(new int[]{1, 1},
                new int[]{Statement.SUCCESS_NO_INFO, Statement.SUCCESS_NO_INFO}, new int[]{1});
        task.startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
        verify(connection, times(3)).commit();
        verify(connection, never()).rollback();
    }

    @Test public void postgresSingleRowFallbackCannotHideSkippedRows() throws Exception {
        for (int count : new int[] {0, 2, -1, Statement.SUCCESS_NO_INFO}) {
            CommonRdbmsWriter.Task task = task();
            task.dataBaseType = DataBaseType.PostgreSQL;
            task.resultSetMetaData = org.apache.commons.lang3.tuple.Triple.of(
                    Arrays.asList("id"), Arrays.asList(Types.BIGINT), Arrays.asList("int8"));
            Connection connection = connection();
            PreparedStatement statement = mock(PreparedStatement.class);
            when(connection.prepareStatement(anyString())).thenReturn(statement);
            when(statement.getUpdateCount()).thenReturn(count);
            TaskPluginCollector collector = mock(TaskPluginCollector.class);
            task.taskPluginCollector = collector;
            try {
                task.doOneInsert(connection, Arrays.asList(records().getFromReader(), records().getFromReader()));
                fail("Skipped fallback row must fail even if dirty rows are allowed");
            } catch (DataXException expected) {
                assertEquals(DBUtilErrorCode.WRITE_ROW_COUNT_MISMATCH, expected.getErrorCode());
            }
            verify(statement, times(1)).execute();
            verify(statement).clearParameters();
            verifyZeroInteractions(collector);
            verify(statement).close();
        }
    }

    private CommonRdbmsWriter.Task task() {
        CommonRdbmsWriter.Task task = new CommonRdbmsWriter.Task(DataBaseType.MySql);
        task.table = "target";
        task.columns = Arrays.asList("id");
        task.columnNumber = 1;
        task.batchSize = 2;
        task.batchByteSize = 1024;
        task.writeRecordSql = "INSERT INTO target(id) VALUES(?)";
        return task;
    }

    private Connection connection() throws Exception {
        Connection connection = mock(Connection.class);
        Statement statement = mock(Statement.class);
        ResultSet result = mock(ResultSet.class);
        ResultSetMetaData metadata = mock(ResultSetMetaData.class);
        when(connection.createStatement()).thenReturn(statement);
        when(statement.executeQuery(anyString())).thenReturn(result);
        when(result.getMetaData()).thenReturn(metadata);
        when(metadata.getColumnCount()).thenReturn(1);
        when(metadata.getColumnName(1)).thenReturn("id");
        when(metadata.getColumnType(1)).thenReturn(Types.BIGINT);
        when(metadata.getColumnTypeName(1)).thenReturn("BIGINT");
        return connection;
    }

    private RecordReceiver records() {
        Record record = mock(Record.class);
        when(record.getColumnNumber()).thenReturn(1);
        when(record.getColumn(0)).thenReturn(new LongColumn(7L));
        when(record.getMemorySize()).thenReturn(16);
        RecordReceiver receiver = mock(RecordReceiver.class);
        when(receiver.getFromReader()).thenReturn(record, record, record, record, record, null);
        return receiver;
    }

    @Test public void avoidsRedundantAutocommitButCommitsEveryBatchAndCloses() throws Exception {
        Connection connection = connection();
        PreparedStatement statement = mock(PreparedStatement.class);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(connection.getAutoCommit()).thenReturn(true, false, false);
        task().startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
        verify(connection, times(3)).prepareStatement(anyString());
        verify(connection, times(1)).setAutoCommit(false);
        verify(connection, times(3)).commit();
        verify(statement, times(5)).addBatch();
        verify(statement, times(3)).executeBatch();
        verify(statement, times(3)).close();
        verify(connection).close();
    }

    @Test public void failedBatchUsesFreshStatementAndResumesTransactions() throws Exception {
        Connection connection = connection();
        PreparedStatement failed = mock(PreparedStatement.class);
        PreparedStatement single = mock(PreparedStatement.class);
        PreparedStatement next = mock(PreparedStatement.class);
        when(connection.prepareStatement(anyString())).thenReturn(failed, single, next);
        when(connection.getAutoCommit()).thenReturn(true, true, false);
        when(failed.executeBatch()).thenThrow(new SQLException("duplicate", "23000", 1062));
        task().startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
        verify(connection).rollback();
        verify(single, times(2)).execute();
        verify(next, times(2)).executeBatch();
        verify(connection).setAutoCommit(true);
        verify(connection, times(2)).setAutoCommit(false);
        verify(connection, times(2)).commit();
        verify(failed).close();
        verify(single).close();
        verify(next, times(2)).close();
        verify(connection).close();
    }

    @Test public void metadataFailureStillClosesConnection() throws Exception {
        Connection connection = connection();
        when(connection.createStatement()).thenThrow(new SQLException("table disappeared"));
        try {
            task().startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
            fail();
        } catch (DataXException expected) {
            verify(connection).close();
        }
    }

    @Test public void commitFailureNeverReplaysRegardlessOfSqlState() throws Exception {
        for (String state : new String[] {"08006", "40003", "40001", null}) {
            Connection connection = connection();
            PreparedStatement statement = mock(PreparedStatement.class);
            when(connection.prepareStatement(anyString())).thenReturn(statement);
            SQLException failure = new SQLException("commit acknowledgement missing", state);
            doThrow(failure).when(connection).commit();
            TaskPluginCollector collector = mock(TaskPluginCollector.class);
            try {
                task().startWriteWithConnection(records(), collector, connection);
                fail("Commit failure must not become successful row replay");
            } catch (DataXException expected) {
                assertEquals(DBUtilErrorCode.WRITE_COMMIT_UNCERTAIN, expected.getErrorCode());
                assertSame(failure, expected.getCause());
            }
            verify(connection, times(1)).prepareStatement(anyString());
            verify(statement, times(1)).executeBatch();
            verify(statement, never()).execute();
            verify(connection, never()).setAutoCommit(true);
            verify(connection).rollback();
            verify(connection).close();
            verifyZeroInteractions(collector);
        }
    }

    @Test public void failedRollbackPreservesTheOriginalFailureAndStopsReplay() throws Exception {
        for (boolean duringCommit : new boolean[] {false, true}) {
            Connection connection = connection();
            PreparedStatement statement = mock(PreparedStatement.class);
            when(connection.prepareStatement(anyString())).thenReturn(statement);
            SQLException first = new SQLException("original failure", "40003");
            SQLException rollback = new SQLException("rollback failed", "08006");
            if (duringCommit) doThrow(first).when(connection).commit();
            else when(statement.executeBatch()).thenThrow(first);
            doThrow(rollback).when(connection).rollback();
            try {
                task().startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
                fail();
            } catch (DataXException expected) {
                assertSame(first, expected.getCause());
                assertArrayEquals(new Throwable[]{rollback}, first.getSuppressed());
            }
            verify(connection, times(1)).prepareStatement(anyString());
            verify(statement, never()).execute();
            verify(connection).close();
        }
    }

    @Test public void integerBindingPreservesSignedUnsignedNullAndStringInputs() throws Exception {
        CommonRdbmsWriter.Task task = task();
        PreparedStatement statement = mock(PreparedStatement.class);
        task.fillPreparedStatementColumnType(statement, 0, Types.BIGINT, "BIGINT", new LongColumn(Long.MIN_VALUE));
        task.fillPreparedStatementColumnType(statement, 1, Types.BIGINT, "BIGINT", new LongColumn(Long.MAX_VALUE));
        task.fillPreparedStatementColumnType(statement, 2, Types.BIGINT, "BIGINT", new LongColumn());
        task.fillPreparedStatementColumnType(statement, 3, Types.BIGINT, "BIGINT", new LongColumn("18446744073709551615"));
        task.fillPreparedStatementColumnType(statement, 4, Types.INTEGER, "INT", new StringColumn("1.25"));
        verify(statement).setLong(1, Long.MIN_VALUE);
        verify(statement).setLong(2, Long.MAX_VALUE);
        verify(statement).setNull(3, Types.BIGINT);
        verify(statement).setString(4, "18446744073709551615");
        verify(statement).setString(5, "1.25");
    }

    @Test public void booleanAndBitPreserveNullAndFalse() throws Exception {
        CommonRdbmsWriter.Task task = task();
        for (int type : new int[] {Types.BOOLEAN, Types.BIT}) {
            PreparedStatement statement = mock(PreparedStatement.class);
            task.fillPreparedStatementColumnType(statement, 0, type, "BIT", new BoolColumn());
            task.fillPreparedStatementColumnType(statement, 1, type, "BIT", new BoolColumn(false));
            verify(statement).setNull(1, type);
            verify(statement).setBoolean(2, false);
        }
    }

    @Test public void narrowIntegerBindingMatchesNullTypesWithoutTruncation() throws Exception {
        for (int type : new int[]{Types.SMALLINT, Types.INTEGER}) {
            long low = type == Types.SMALLINT ? Short.MIN_VALUE : Integer.MIN_VALUE;
            long high = type == Types.SMALLINT ? Short.MAX_VALUE : Integer.MAX_VALUE;
            for (long value : new long[]{low, high, low-1, high+1, 4294967295L}) {
                PreparedStatement statement = mock(PreparedStatement.class);
                task().fillPreparedStatementColumnType(statement, 0, type, "integer", new LongColumn(value));
                if (value < low || value > high) verify(statement).setLong(1, value);
                else if (type == Types.SMALLINT) verify(statement).setShort(1, (short)value);
                else verify(statement).setInt(1, (int)value);
                task().fillPreparedStatementColumnType(statement, 0, type, "integer", new LongColumn());
                verify(statement).setNull(1, type);
                verifyNoMoreInteractions(statement);
            }
        }
    }

    @Test public void mysqlWriterDefaultsRespectExplicitDriverOptions() {
        String url = "jdbc:mysql://localhost/test";
        String defaults = DataBaseType.MySql.appendJDBCSuffixForWriter(url);
        assertTrue(defaults.contains("useServerPrepStmts=true"));
        assertTrue(defaults.contains("cachePrepStmts=true"));
        assertTrue(defaults.contains("prepStmtCacheSqlLimit=65535"));
        String explicit = DataBaseType.MySql.appendJDBCSuffixForWriter(url
                + "?useServerPrepStmts=false&cachePrepStmts=false&prepStmtCacheSqlLimit=123");
        assertFalse(explicit.contains("useServerPrepStmts=true"));
        assertFalse(explicit.contains("cachePrepStmts=true"));
        assertFalse(explicit.contains("prepStmtCacheSqlLimit=65535"));
        assertFalse(DataBaseType.MySql.appendJDBCSuffixForReader(url).contains("useServerPrepStmts"));
    }

    @Test public void timestampBindingPreservesFractionAndNull() throws Exception {
        PreparedStatement statement = mock(PreparedStatement.class);
        CommonRdbmsWriter.Task task = task();
        Timestamp timestamp = Timestamp.valueOf("1969-12-31 23:59:59.123456789");
        task.fillPreparedStatementColumnType(statement, 0, Types.TIMESTAMP, "timestamp", new DateColumn(timestamp));
        task.fillPreparedStatementColumnType(statement, 1, Types.TIMESTAMP, "timestamp", new DateColumn(123L));
        task.fillPreparedStatementColumnType(statement, 2, Types.TIMESTAMP, "timestamp", new DateColumn());
        verify(statement).setTimestamp(1, timestamp);
        verify(statement).setTimestamp(2, new Timestamp(123L));
        verify(statement).setTimestamp(3, null);
    }
}
