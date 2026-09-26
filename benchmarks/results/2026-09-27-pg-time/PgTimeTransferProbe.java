import java.sql.*;
import java.util.Arrays;
import org.postgresql.PGResultSetMetaData;
public class PgTimeTransferProbe {
  public static void main(String[] args) throws Exception {
    String url = "jdbc:postgresql://127.0.0.1:25432/datax_bench?prepareThreshold=-1&binaryTransferEnable=1083,1266&binaryTransferDisable=int4";
    for (boolean text : new boolean[] {false,true}) {
      for (int attempt=1; attempt<=4; attempt++) {
        try (Connection c=DriverManager.getConnection(url + (text ? "&binaryTransferDisable=int4%2C1083%2C1266" : ""), "postgres", "datax-local-benchmark")) {
          c.setAutoCommit(false);
          try (Statement s=c.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY)) {
            s.setFetchSize(128);
            try (ResultSet r=s.executeQuery("SELECT id,t,z,42::int4 i,t::text tx,z::text zx FROM pg_time_source ORDER BY id")) {
              PGResultSetMetaData meta=(PGResultSetMetaData)r.getMetaData();
              int[] formats = new int[4];
              for (int i=0; i<4; i++) formats[i]=meta.getFormat(i+1);
              int[] expected = text ? new int[] {1,0,0,0} : new int[] {1,1,1,0};
              if (!Arrays.equals(formats,expected)) throw new AssertionError(Arrays.toString(formats));
              int changed=0, rows=0;
              while (r.next()) {
                rows++;
                if (!java.util.Objects.equals(r.getString(2),r.getString(5))) changed++;
                if (!java.util.Objects.equals(r.getString(3),r.getString(6))) changed++;
              }
              if (rows!=7 || (text ? changed!=0 : changed==0)) throw new AssertionError("changed="+changed);
              System.out.println("text="+text+" attempt="+attempt+" formats="+Arrays.toString(formats)+" rows="+rows+" changedFields="+changed);
            }
          }
        }
      }
    }
  }
}
