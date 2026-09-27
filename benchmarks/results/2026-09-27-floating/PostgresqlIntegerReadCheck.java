import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.core.transport.record.DefaultRecord;
import com.alibaba.datax.plugin.rdbms.reader.CommonRdbmsReader;
import com.alibaba.datax.plugin.rdbms.util.DBUtil;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import org.postgresql.core.Field;
import org.postgresql.jdbc.PgResultSet;
import java.sql.*;
import java.util.Objects;

/** Real reader/JDBC integration; reflection verifies the pinned driver's received wire format. */
public class PostgresqlIntegerReadCheck {
    static class Task extends CommonRdbmsReader.Task {
        Task() { super(DataBaseType.PostgreSQL); }
        Record read(ResultSet rs) throws Exception {
            return buildRecord(new RecordSender() {
                public Record createRecord() { return new DefaultRecord(); }
                public void sendToWriter(Record r) { }
                public void flush() { }
                public void terminate() { }
                public void shutdown() { }
            }, rs, rs.getMetaData(), 3, "", new TaskPluginCollector() {
                public void collectDirtyRecord(Record r, Throwable t, String m) { throw new AssertionError(t); }
                public void collectMessage(String k, String v) { }
            });
        }
    }

    public static void main(String[] args) throws Exception {
        java.lang.reflect.Field fields = PgResultSet.class.getDeclaredField("fields");
        fields.setAccessible(true);
        for (boolean binary : new boolean[]{false, true}) {
            String url = "jdbc:postgresql://127.0.0.1:25432/datax_bench?" + (binary
                    ? "prepareThreshold=-1&binaryTransfer=true&binaryTransferEnable=int2,int4,int8"
                    : "binaryTransfer=false");
            for (int attempt = 1; attempt <= 4; attempt++) {
                try (Connection conn = DriverManager.getConnection(url, "postgres", "datax-local-benchmark");
                     ResultSet rs = DBUtil.query(conn, "SELECT ni16,ni32,ni64 FROM pg_integer_source", 2);
                     Statement statement = rs.getStatement()) {
                    Task task = new Task();
                    int rows = 0, bytes = 0;
                    while (rs.next()) {
                        Field[] actualFields = (Field[]) fields.get(rs);
                        Record record = task.read(rs);
                        for (int i = 1; i <= 3; i++) {
                            if (actualFields[i - 1].getFormat() != (binary ? 1 : 0))
                                throw new AssertionError("Requested wire format not received");
                            LongColumn expected = new LongColumn(rs.getString(i));
                            Column actual = record.getColumn(i - 1);
                            if (actual.getType() != Column.Type.LONG
                                    || !Objects.equals(expected.getRawData(), actual.getRawData())
                                    || expected.getByteSize() != actual.getByteSize())
                                throw new AssertionError("Changed integer value or byte count");
                        }
                        bytes += record.getByteSize();
                        rows++;
                    }
                    if (rows != 9) throw new AssertionError("Missing or duplicated row");
                    System.out.println("RESULT {\"binary_received\":" + binary + ",\"attempt\":" + attempt
                            + ",\"rows\":" + rows + ",\"record_bytes\":" + bytes + "}");
                }
            }
        }
    }
}
