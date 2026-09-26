package com.alibaba.datax.plugin.rdbms.writer;

import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import org.junit.Test;
import java.sql.*;
import java.util.Arrays;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class RdbmsWriterRegressionTest {
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

    @Test public void reusesStatementButCommitsEveryBatchAndCloses() throws Exception {
        Connection connection = connection();
        PreparedStatement statement = mock(PreparedStatement.class);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(connection.getAutoCommit()).thenReturn(true, false, false);
        task().startWriteWithConnection(records(), mock(TaskPluginCollector.class), connection);
        verify(connection, times(1)).prepareStatement(anyString());
        verify(connection, times(1)).setAutoCommit(false);
        verify(connection, times(3)).commit();
        verify(statement, times(5)).addBatch();
        verify(statement, times(3)).executeBatch();
        verify(statement).close();
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
        verify(next).close();
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
}
