package com.alibaba.datax.plugin.rdbms.reader;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.util.*;
import org.junit.Test;
import java.sql.*;
import java.util.Arrays;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class JdbcErrorsRegressionTest {
    @Test public void vendorCodesWorkWithoutEnglishMessages() {
        int[] numbers = {1146, 1054, 1064, 1142};
        String[] codes = {"MYSQLErrCode-04", "MYSQLErrCode-06", "MYSQLErrCode-05", "MYSQLErrCode-07"};
        for (int i = 0; i < numbers.length; i++) {
            SQLException error = new SQLException("本地化信息", "42000", numbers[i]);
            DataXException result = RdbmsException.asQueryException(DataBaseType.MySql, error, "SELECT x", null, "test");
            assertEquals(codes[i], result.getErrorCode().getCode());
            assertSame(error, result.getCause());
        }
    }

    @Test public void followsNextExceptionAndCause() {
        SQLException wrapper = new SQLException();
        wrapper.setNextException(new SQLException(null, "42S22", 1054));
        Exception error = new Exception("wrapper", wrapper);
        assertEquals("MYSQLErrCode-06", RdbmsException.asQueryException(DataBaseType.MySql,
                error, "SELECT x", null, "test").getErrorCode().getCode());
    }

    @Test public void nullMessageDoesNotHideFailure() {
        DataXException error = RdbmsException.asQueryException(DataBaseType.MySql,
                new SQLException(), "SELECT x", null, "test");
        assertEquals(DBUtilErrorCode.READ_RECORD_FAIL, error.getErrorCode());
    }

    @Test public void recognizesConnectionAndTimeout() {
        SQLException[] failures = {new SQLException("lost", "08S01"), new SQLTimeoutException("timeout")};
        String[] codes = {"DBUtilErrorCode-21", "DBUtilErrorCode-22"};
        for (int i = 0; i < failures.length; i++) {
            assertEquals(codes[i], RdbmsException.asQueryException(DataBaseType.MySql,
                    failures[i], "SELECT 1", null, "test").getErrorCode().getCode());
        }
    }

    @Test public void legacyMysqlTimeoutIsRecognized() {
        SQLException error = new com.mysql.jdbc.exceptions.MySQLTimeoutException("cancelled");
        assertEquals("DBUtilErrorCode-22", RdbmsException.asQueryException(DataBaseType.MySql,
                error, "SELECT SLEEP(10)", null, "test").getErrorCode().getCode());
    }

    @Test public void postgresCancellationIsNotAColumnErrorOrAssumedDeadline() {
        for (String message : new String[]{null, "本地化取消信息", "canceling statement due to user request"}) {
            SQLException cancelled = new SQLException(message, "57014");
            SQLException wrapper = new SQLException("wrapper");
            wrapper.setNextException(cancelled);
            DataXException result = RdbmsException.asQueryException(DataBaseType.PostgreSQL,
                    wrapper, "SELECT pg_sleep(3)", null, "test");
            assertEquals(DBUtilErrorCode.QUERY_CANCELLED, result.getErrorCode());
            assertTrue(result.getMessage().contains("SQLState=57014"));
            assertSame(wrapper, result.getCause());
        }
    }

    @Test public void failedQueryClosesOwnedStatement() throws Exception {
        Connection conn = mock(Connection.class);
        Statement stmt = mock(Statement.class);
        when(conn.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY)).thenReturn(stmt);
        SQLException cause = new SQLException("bad SQL", "42000");
        when(stmt.executeQuery("bad SQL")).thenThrow(cause);
        try { DBUtil.query(conn, "bad SQL", 1, 1); fail(); }
        catch (SQLException expected) { assertSame(cause, expected); }
        verify(stmt).close();
        verify(conn, never()).close();
    }

    @Test public void failedSessionClosesStatement() throws Exception {
        Connection conn = mock(Connection.class);
        Statement stmt = mock(Statement.class);
        when(conn.createStatement()).thenReturn(stmt);
        when(stmt.execute("invalid")).thenThrow(new SQLException("bad session"));
        Configuration config = Configuration.newDefault();
        config.set(Key.SESSION, Arrays.asList("invalid"));
        try { DBUtil.dealWithSessionConfig(conn, config, DataBaseType.MySql, "test"); fail(); }
        catch (DataXException expected) { assertEquals(DBUtilErrorCode.SET_SESSION_ERROR, expected.getErrorCode()); }
        verify(stmt).close();
    }

    @Test public void snapshotImportCannotSilentlyFallBack() throws Exception {
        for (String snapshot : new String[] {null, "", "bad' snapshot"}) {
            Connection conn = mock(Connection.class);
            Configuration config = Configuration.newDefault();
            config.set(Key.CONSISTENT_SNAPSHOT, true);
            if (snapshot != null) config.set(Key.POSTGRESQL_SNAPSHOT, snapshot);
            try { DBUtil.dealWithSessionConfig(conn, config, DataBaseType.PostgreSQL, "test"); fail(); }
            catch (DataXException expected) { assertEquals(DBUtilErrorCode.SET_SESSION_ERROR, expected.getErrorCode()); }
            verifyZeroInteractions(conn);
        }
        Connection conn = mock(Connection.class);
        Statement stmt = mock(Statement.class);
        when(conn.createStatement()).thenReturn(stmt);
        SQLException cause = new SQLException("invalid snapshot identifier", "22023");
        when(stmt.execute("SET TRANSACTION SNAPSHOT '00000001-00000001-1'")).thenThrow(cause);
        Configuration config = Configuration.newDefault();
        config.set(Key.CONSISTENT_SNAPSHOT, true);
        config.set(Key.POSTGRESQL_SNAPSHOT, "00000001-00000001-1");
        try { DBUtil.dealWithSessionConfig(conn, config, DataBaseType.PostgreSQL, "test"); fail(); }
        catch (DataXException expected) { assertSame(cause, expected.getCause()); }
        verify(stmt).close();
    }
}
