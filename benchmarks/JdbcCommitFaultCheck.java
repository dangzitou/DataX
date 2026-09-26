import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.core.transport.record.DefaultRecord;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import com.alibaba.datax.plugin.rdbms.writer.CommonRdbmsWriter;
import com.alibaba.fastjson2.JSON;
import org.apache.commons.lang3.tuple.Triple;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import java.sql.*;
import java.util.*;

/** Real PG/MySQL writes with a simulated JDBC commit acknowledgement failure, not a network test. */
public class JdbcCommitFaultCheck extends CommonRdbmsWriter.Task {
    JdbcCommitFaultCheck(boolean mysql) {
        super(mysql ? DataBaseType.MySql : DataBaseType.PostgreSQL);
        table = "pg_commit_fault_target";
        columnNumber = 2;
        writeRecordSql = "INSERT INTO pg_commit_fault_target(id,txt) VALUES "
                + (mysql ? "(?,?)" : "(?::bigint,?::text)");
        resultSetMetaData = Triple.of(Arrays.asList("id", "txt"), Arrays.asList(Types.BIGINT, Types.VARCHAR),
                Arrays.asList("bigint", "text"));
    }

    static List<Record> rows() {
        List<Record> rows = new ArrayList<>();
        for (long id = 1; id <= 2; id++) {
            Record row = new DefaultRecord();
            row.addColumn(new LongColumn(id));
            row.addColumn(new StringColumn("中文😀-" + id));
            rows.add(row);
        }
        return rows;
    }

    static Connection connect(boolean mysql) throws SQLException {
        return DriverManager.getConnection(mysql
                ? "jdbc:mysql://127.0.0.1:23306/datax_bench?useUnicode=true&characterEncoding=UTF-8&useSSL=false"
                : "jdbc:postgresql://127.0.0.1:25432/datax_bench", mysql ? "datax" : "postgres",
                "datax-local-benchmark");
    }

    public static void main(String[] args) throws Exception {
        boolean legacy = Arrays.asList(args).contains("--expect-legacy");
        boolean mysql = Arrays.asList(args).contains("--mysql");
        for (String point : new String[]{"after-commit", "before-commit", "batch-rejected", "normal"}) {
            for (int attempt = 1; attempt <= 4; attempt++) {
                try (Connection actual = connect(mysql); Statement setup = actual.createStatement()) {
                    setup.execute("DROP TABLE IF EXISTS pg_commit_fault_target");
                    setup.execute("CREATE TABLE pg_commit_fault_target(id bigint,txt text)"
                            + (mysql ? " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" : ""));
                    boolean[] injected = {false};
                    Connection wrapper = (Connection) Proxy.newProxyInstance(Connection.class.getClassLoader(),
                            new Class<?>[]{Connection.class}, (proxy, method, params) -> {
                        if (method.getName().equals("commit") && !injected[0] && point.endsWith("-commit")) {
                            injected[0] = true;
                            if (point.equals("after-commit")) actual.commit();
                            throw new SQLException("Injected JDBC commit acknowledgement failure: " + point, "40003");
                        }
                        try {
                            Object value = method.invoke(actual, params);
                            if (method.getName().equals("prepareStatement") && point.equals("batch-rejected")) {
                                return Proxy.newProxyInstance(PreparedStatement.class.getClassLoader(),
                                        new Class<?>[]{PreparedStatement.class}, (p, m, a) -> {
                                    if (m.getName().equals("executeBatch") && !injected[0]) {
                                        injected[0] = true;
                                        throw new SQLException("Injected pre-execution batch rejection", "23505");
                                    }
                                    try { return m.invoke(value, a); }
                                    catch (InvocationTargetException e) { throw e.getCause(); }
                                });
                            }
                            return value;
                        } catch (InvocationTargetException e) { throw e.getCause(); }
                    });
                    Throwable failure = null;
                    try { new JdbcCommitFaultCheck(mysql).doBatchInsert(wrapper, rows()); }
                    catch (Exception expected) { failure = expected; }
                    // Observe using a separate connection, so only committed rows are visible.
                    int count = 0;
                    try (Connection observer = connect(mysql); Statement query = observer.createStatement();
                         ResultSet result = query.executeQuery("SELECT id,txt FROM pg_commit_fault_target ORDER BY id")) {
                        while (result.next()) {
                            long id = result.getLong(1);
                            if (id < 1 || id > 2 || !result.getString(2).equals("中文😀-" + id))
                                throw new AssertionError("Unexpected field value");
                            count++;
                        }
                    }
                    Map<String, Object> result = new LinkedHashMap<>();
                    result.put("case", point + "-" + attempt);
                    result.put("legacy", legacy);
                    result.put("injected", injected[0]);
                    result.put("input_rows", 2);
                    result.put("visible_rows", count);
                    result.put("failed", failure != null);
                    result.put("error", failure == null ? null : failure.toString());
                    System.out.println("RESULT " + JSON.toJSONString(result));
                    int expected = point.equals("after-commit") ? (legacy ? 4 : 2)
                            : point.equals("before-commit") && !legacy ? 0 : 2;
                    boolean shouldFail = !legacy && point.endsWith("-commit");
                    if (count != expected || (failure != null) != shouldFail)
                        throw new AssertionError("Unexpected outcome: " + result);
                    if (shouldFail && !failure.toString().contains("DBUtilErrorCode-25"))
                        throw new AssertionError("Missing commit-outcome diagnostic");
                }
            }
        }
    }
}
