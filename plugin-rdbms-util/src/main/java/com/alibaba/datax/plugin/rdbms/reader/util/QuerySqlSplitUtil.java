package com.alibaba.datax.plugin.rdbms.reader.util;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.common.util.RangeSplitUtil;
import com.alibaba.datax.plugin.rdbms.reader.Key;
import com.alibaba.datax.plugin.rdbms.util.*;
import com.alibaba.druid.sql.SQLUtils;
import com.alibaba.druid.sql.ast.SQLStatement;
import com.alibaba.druid.sql.ast.statement.SQLSelectStatement;

import java.math.BigInteger;
import java.sql.*;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Opt-in splitting of a repeatable MySQL SELECT by an integer output column.
 * ponytail: independent snapshots; use a frozen source or exported snapshot for mutable data.
 */
public final class QuerySqlSplitUtil {
    private QuerySqlSplitUtil() { }

    public static List<Configuration> split(Configuration config, int count, DataBaseType database) {
        String key = config.getString(Key.QUERY_SQL_SPLIT_PK);
        validateKey(key, database);
        String sql = config.getString(Key.QUERY_SQL).trim();
        validateSql(sql);
        if (count <= 1) return Collections.singletonList(config);
        if (sql.endsWith(";")) sql = sql.substring(0, sql.length() - 1);
        String table = "(\n" + sql + "\n) AS datax_query";
        String column = "`" + key + "`";
        String rangeSql = SingleTableSplitUtil.genPKSql(column, table, null);
        String min, max;
        try (Connection conn = DBUtil.getConnection(database, config.getString(Key.JDBC_URL),
                config.getString(Key.USERNAME), config.getString(Key.PASSWORD))) {
            DBUtil.dealWithSessionConfig(conn, config, database, "querySql range discovery");
            try (ResultSet rs = DBUtil.query(conn, rangeSql, 1,
                    config.getInt(Key.QUERY_TIMEOUT, com.alibaba.datax.plugin.rdbms.util.Constant.SOCKET_TIMEOUT_INSECOND));
                 Statement statement = rs.getStatement()) {
                validateType(rs.getMetaData().getColumnType(1));
                if (!rs.next()) return Collections.singletonList(config);
                min = rs.getString(1);
                max = rs.getString(2);
            }
        } catch (SQLException e) {
            throw RdbmsException.asQueryException(database, e, rangeSql, null, config.getString(Key.USERNAME));
        }
        if (min == null || max == null) return Collections.singletonList(config);
        List<Configuration> result = new ArrayList<Configuration>();
        for (String predicate : predicates(new BigInteger(min), new BigInteger(max), count, column)) {
            Configuration slice = config.clone();
            slice.set(Key.QUERY_SQL, "SELECT * FROM " + table + " WHERE " + predicate);
            result.add(slice);
        }
        return result;
    }

    public static void validateKey(String key, DataBaseType database) {
        if (database != DataBaseType.MySql || key == null || !key.matches("[A-Za-z_][A-Za-z0-9_]*")) {
            throw DataXException.asDataXException(DBUtilErrorCode.ILLEGAL_VALUE,
                    "querySqlSplitPk requires MySQL and a simple integer output column name.");
        }
    }

    public static void validateSql(String sql) {
        try {
            List<SQLStatement> statements = SQLUtils.parseStatements(sql, "mysql");
            if (statements.size() != 1 || !(statements.get(0) instanceof SQLSelectStatement)) {
                throw new IllegalArgumentException("expected exactly one SELECT");
            }
        } catch (RuntimeException e) {
            throw DataXException.asDataXException(DBUtilErrorCode.ILLEGAL_VALUE,
                    "querySqlSplitPk requires one SELECT supported by the SQL parser.", e);
        }
    }

    static void validateColumn(ResultSetMetaData metadata, String key) throws SQLException {
        for (int i = 1; i <= metadata.getColumnCount(); i++) {
            if (key.equalsIgnoreCase(metadata.getColumnLabel(i))) {
                validateType(metadata.getColumnType(i));
                return;
            }
        }
        throw DataXException.asDataXException(DBUtilErrorCode.ILLEGAL_SPLIT_PK,
                "querySqlSplitPk is not an output column: " + key);
    }

    private static void validateType(int type) {
        if (type != Types.BIGINT && type != Types.INTEGER && type != Types.SMALLINT && type != Types.TINYINT) {
            throw DataXException.asDataXException(DBUtilErrorCode.ILLEGAL_SPLIT_PK,
                    "querySqlSplitPk must expose an integer column (including unsigned BIGINT).");
        }
    }

    static List<String> predicates(BigInteger min, BigInteger max, int count, String column) {
        BigInteger[] bounds = RangeSplitUtil.doBigIntegerSplit(min, max, count);
        if (bounds.length == 2) return Collections.singletonList("1=1");
        List<String> result = new ArrayList<String>();
        for (int i = 0; i < bounds.length - 1; i++) {
            if (i == 0) result.add("(" + column + " < " + bounds[i + 1] + " OR " + column + " IS NULL)");
            else if (i == bounds.length - 2) result.add(column + " >= " + bounds[i]);
            else result.add(column + " >= " + bounds[i] + " AND " + column + " < " + bounds[i + 1]);
        }
        return result;
    }
}
