package com.alibaba.datax.plugin.rdbms.reader.util;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.reader.Key;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import org.junit.Test;
import java.math.BigInteger;
import java.util.Arrays;
import java.util.Collections;
import static org.junit.Assert.*;

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
    }

    @Test public void invalidSqlAndKeyFailBeforeConnecting() {
        for (String sql : Arrays.asList("SELECT 1; SELECT 2", "DELETE FROM t", "")) {
            try { QuerySqlSplitUtil.validateSql(sql); fail(sql); }
            catch (DataXException expected) { }
        }
        for (String key : Arrays.asList("", "id; DROP TABLE t", "t.id", "id`")) {
            try { QuerySqlSplitUtil.validateKey(key, DataBaseType.MySql); fail(key); }
            catch (DataXException expected) { }
        }
        try { QuerySqlSplitUtil.validateKey("id", DataBaseType.PostgreSQL); fail(); }
        catch (DataXException expected) { }
    }
}
