import java.sql.*;
import java.util.Arrays;

public class RowCountProbe {
    public static void main(String[] args) throws Exception {
        Class.forName("org.postgresql.Driver");
        for (boolean rewrite : new boolean[]{false, true}) {
            String url = "jdbc:postgresql://127.0.0.1:25432/datax_bench?reWriteBatchedInserts=" + rewrite;
            try (Connection c = DriverManager.getConnection(url, "postgres", "datax-local-benchmark")) {
                c.setAutoCommit(false);
                for (int attempt = 1; attempt <= 4; attempt++) {
                    for (String condition : new String[]{"false", "NEW.id=2", "true"}) {
                        try (Statement s = c.createStatement()) {
                            s.execute("CREATE TEMP TABLE row_count_probe(id bigint)");
                            s.execute("CREATE FUNCTION pg_temp.row_count_skip() RETURNS trigger LANGUAGE plpgsql AS $$ "
                                    + "BEGIN IF " + condition + " THEN RETURN NULL; END IF; RETURN NEW; END $$");
                            s.execute("CREATE TRIGGER skip BEFORE INSERT ON row_count_probe FOR EACH ROW EXECUTE FUNCTION pg_temp.row_count_skip()");
                        }
                        try (PreparedStatement p = c.prepareStatement("INSERT INTO row_count_probe VALUES(?::bigint)")) {
                            for (int i = 1; i <= 4; i++) { p.setLong(1, i); p.addBatch(); }
                            int[] counts = p.executeBatch();
                            int[] expected = condition.equals("true") ? new int[]{0, 0, 0, 0}
                                    : rewrite ? new int[]{-2, -2, -2, -2}
                                    : condition.equals("false") ? new int[]{1, 1, 1, 1} : new int[]{1, 0, 1, 1};
                            if (!Arrays.equals(counts, expected)) throw new AssertionError(Arrays.toString(counts));
                            try (Statement s = c.createStatement(); ResultSet r = s.executeQuery("SELECT count(*) FROM row_count_probe")) {
                                r.next(); int rows = r.getInt(1);
                                if (rows != (condition.equals("true") ? 0 : condition.equals("false") ? 4 : 3)) throw new AssertionError(rows);
                                System.out.println("rewrite=" + rewrite + " attempt=" + attempt + " skip=" + condition
                                        + " counts=" + Arrays.toString(counts) + " actual=" + rows);
                            }
                        }
                        c.rollback();
                    }
                }
            }
        }
    }
}
