import java.sql.*;

public class PgAtomicOptionsCheck {
    public static void main(String[] args) throws Exception {
        Class.forName("org.postgresql.Driver");
        for (String suffix : new String[] {"?", "?reWriteBatchedInserts=true&"}) {
            String url = "jdbc:postgresql://127.0.0.1:25432/datax_bench" + suffix
                    + "options=-c%20temp_file_limit%3D1536MB";
            try (Connection conn = DriverManager.getConnection(url, "postgres", "datax-local-benchmark");
                 Statement statement = conn.createStatement();
                 ResultSet result = statement.executeQuery("SHOW temp_file_limit")) {
                if (!result.next() || !"1536MB".equals(result.getString(1)))
                    throw new AssertionError("Native startup limit was not applied");
                System.out.println("PASS " + suffix + " temp_file_limit=" + result.getString(1));
            }
        }
    }
}
