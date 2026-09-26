package com.alibaba.datax.plugin.reader.postgresqlreader;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.spi.Reader;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.reader.CommonRdbmsReader;
import com.alibaba.datax.plugin.rdbms.reader.Key;
import com.alibaba.datax.plugin.rdbms.util.DBUtil;
import com.alibaba.datax.plugin.rdbms.util.DBUtilErrorCode;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;

import java.util.List;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import org.postgresql.Driver;
import org.postgresql.PGProperty;

public class PostgresqlReader extends Reader {

    private static final DataBaseType DATABASE_TYPE = DataBaseType.PostgreSQL;

    public static class Job extends Reader.Job {

        private Configuration originalConfig;
        private CommonRdbmsReader.Job commonRdbmsReaderMaster;
        private Connection snapshotConnection;

        @Override
        public void init() {
            this.originalConfig = super.getPluginJobConf();
            if (this.originalConfig.get(Key.POSTGRESQL_SNAPSHOT) != null) {
                throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                        Key.POSTGRESQL_SNAPSHOT + " is reserved; use consistentSnapshot=true");
            }
            int fetchSize = this.originalConfig.getInt(com.alibaba.datax.plugin.rdbms.reader.Constant.FETCH_SIZE,
                    Constant.DEFAULT_FETCH_SIZE);
            if (fetchSize < 1) {
            	throw DataXException.asDataXException(DBUtilErrorCode.REQUIRED_VALUE,
					String.format("您配置的fetchSize有误，根据DataX的设计，fetchSize : [%d] 设置值不能小于 1.", fetchSize));
            }
            this.originalConfig.set(com.alibaba.datax.plugin.rdbms.reader.Constant.FETCH_SIZE, fetchSize);

            this.commonRdbmsReaderMaster = new CommonRdbmsReader.Job(DATABASE_TYPE);
            this.commonRdbmsReaderMaster.init(this.originalConfig);
        }

        @Override
        public void prepare() {
            if (!originalConfig.getBool("consistentSnapshot", false)) return;
            if (originalConfig.getList("connection", Object.class).size() != 1) {
                throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                        "consistentSnapshot requires one PostgreSQL connection entry");
            }
            String url = originalConfig.getString("connection[0].jdbcUrl");
            if (PGProperty.PG_HOST.get(Driver.parseURL(url, null)).contains(",")) {
                throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                        "consistentSnapshot requires a single PostgreSQL server, without JDBC host failover");
            }
            snapshotConnection = DBUtil.getConnection(DATABASE_TYPE, url,
                    originalConfig.getString(Key.USERNAME), originalConfig.getString(Key.PASSWORD));
            try {
                snapshotConnection.setReadOnly(true);
                snapshotConnection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
                snapshotConnection.setAutoCommit(false);
                try (Statement statement = snapshotConnection.createStatement()) {
                    statement.setQueryTimeout(originalConfig.getInt(Key.QUERY_TIMEOUT,
                            com.alibaba.datax.plugin.rdbms.util.Constant.SOCKET_TIMEOUT_INSECOND));
                    try (ResultSet result = statement.executeQuery("SELECT pg_export_snapshot()")) {
                        if (!result.next()) throw new SQLException("PostgreSQL did not export a snapshot");
                        originalConfig.set(Key.POSTGRESQL_SNAPSHOT, result.getString(1));
                    }
                }
            } catch (SQLException e) {
                DBUtil.closeDBResources(null, null, snapshotConnection);
                snapshotConnection = null;
                throw DataXException.asDataXException(DBUtilErrorCode.SET_SESSION_ERROR,
                        "Cannot export PostgreSQL snapshot", e);
            }
        }

        @Override
        public List<Configuration> split(int adviceNumber) {
            return this.commonRdbmsReaderMaster.split(this.originalConfig, adviceNumber);
        }

        @Override
        public void post() {
            this.commonRdbmsReaderMaster.post(this.originalConfig);
        }

        @Override
        public void destroy() {
            try {
                if (this.commonRdbmsReaderMaster != null) this.commonRdbmsReaderMaster.destroy(this.originalConfig);
            } finally {
                DBUtil.closeDBResources(null, null, snapshotConnection);
                snapshotConnection = null;
            }
        }

    }

    public static class Task extends Reader.Task {

        private Configuration readerSliceConfig;
        private CommonRdbmsReader.Task commonRdbmsReaderSlave;

        @Override
        public void init() {
            this.readerSliceConfig = super.getPluginJobConf();
            String url = this.readerSliceConfig.getString(Key.JDBC_URL);
            // PG JDBC 42.3.3 getString still converts binary TIME/TIMETZ through java.sql.Time.
            // Preserve existing disabled types while requesting lossless text for these two OIDs.
            String disabled = PGProperty.BINARY_TRANSFER_DISABLE.get(Driver.parseURL(url, null));
            disabled = (disabled == null || disabled.isEmpty() ? "" : disabled + ",") + "1083,1266";
            try {
                this.readerSliceConfig.set(Key.JDBC_URL, url + (url.contains("?") ? "&" : "?")
                        + "binaryTransferDisable=" + URLEncoder.encode(disabled, "UTF-8"));
            } catch (UnsupportedEncodingException e) {
                throw new IllegalStateException(e);
            }
            this.commonRdbmsReaderSlave = new CommonRdbmsReader.Task(DATABASE_TYPE,super.getTaskGroupId(), super.getTaskId());
            this.commonRdbmsReaderSlave.init(this.readerSliceConfig);
        }

        @Override
        public void startRead(RecordSender recordSender) {
            int fetchSize = this.readerSliceConfig.getInt(com.alibaba.datax.plugin.rdbms.reader.Constant.FETCH_SIZE);

            this.commonRdbmsReaderSlave.startRead(this.readerSliceConfig, recordSender,
                    super.getTaskPluginCollector(), fetchSize);
        }

        @Override
        public void post() {
            this.commonRdbmsReaderSlave.post(this.readerSliceConfig);
        }

        @Override
        public void destroy() {
            this.commonRdbmsReaderSlave.destroy(this.readerSliceConfig);
        }

    }

}
