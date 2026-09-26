package com.alibaba.datax.plugin.rdbms.reader.util;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.reader.Key;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import org.junit.Test;
import java.math.BigInteger;
import java.util.Arrays;
import java.util.Collections;
import java.sql.ResultSetMetaData;
import java.sql.Types;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class QuerySqlSplitTest {
    @Test public void disjointRangesIncludeNullAndOpenEdges() {
        assertEquals(Arrays.asList("(`id` < 25 OR `id` IS NULL)", "`id` >= 25 AND `id` < 50",
                "`id` >= 50 AND `id` < 75", "`id` >= 75"),
                QuerySqlSplitUtil.predicates(BigInteger.ZERO, BigInteger.valueOf(100), 4, "`id`"));
        assertEquals(Collections.singletonList("1=1"),
                QuerySqlSplitUtil.predicates(BigInteger.TEN, BigInteger.TEN, 4, "`id`"));
    }

    @Test public void oneChannelRetainsOriginalQueryWithoutConnecting() {
        Configuration config = Configuration.newDefault();
        config.set(Key.QUERY_SQL_SPLIT_PK, "id");
        config.set(Key.QUERY_SQL, "SELECT id FROM source_data;");
        assertSame(config, QuerySqlSplitUtil.split(config, 1, DataBaseType.MySql).get(0));
        assertSame(config, QuerySqlSplitUtil.split(config, 1, DataBaseType.PostgreSQL).get(0));
    }

    @Test public void invalidSqlAndKeyFailBeforeConnecting() {
        for (String sql : Arrays.asList("SELECT 1; SELECT 2", "DELETE FROM t", "")) {
            for (DataBaseType db : Arrays.asList(DataBaseType.MySql, DataBaseType.PostgreSQL)) {
                try { QuerySqlSplitUtil.validateSql(sql, db); fail(sql); }
                catch (DataXException expected) { }
            }
        }
        for (String key : Arrays.asList("", "id; DROP TABLE t", "t.id", "id`")) {
            try { QuerySqlSplitUtil.validateKey(key, DataBaseType.MySql); fail(key); }
            catch (DataXException expected) { }
        }
        try { QuerySqlSplitUtil.validateKey("id", DataBaseType.SQLServer); fail(); }
        catch (DataXException expected) { }
    }

    @Test public void postgresDialectAndCaseSensitiveOutputNames() throws Exception {
        QuerySqlSplitUtil.validateKey("SplitID", DataBaseType.PostgreSQL);
        QuerySqlSplitUtil.validateSql("WITH s AS (SELECT 1::bigint AS \"SplitID\") SELECT * FROM s;",
                DataBaseType.PostgreSQL);
        ResultSetMetaData metadata = mock(ResultSetMetaData.class);
        when(metadata.getColumnCount()).thenReturn(1);
        when(metadata.getColumnLabel(1)).thenReturn("SplitID");
        when(metadata.getColumnType(1)).thenReturn(Types.BIGINT);
        QuerySqlSplitUtil.validateColumn(metadata, "SplitID", DataBaseType.PostgreSQL);
        QuerySqlSplitUtil.validateColumn(metadata, "splitid", DataBaseType.MySql);
        try { QuerySqlSplitUtil.validateColumn(metadata, "splitid", DataBaseType.PostgreSQL); fail(); }
        catch (DataXException expected) { }
        when(metadata.getColumnType(1)).thenReturn(Types.NUMERIC);
        try { QuerySqlSplitUtil.validateColumn(metadata, "SplitID", DataBaseType.PostgreSQL); fail(); }
        catch (DataXException expected) { }
    }
}
