package com.alibaba.datax.plugin.rdbms.reader;

import com.alibaba.datax.common.element.BoolColumn;
import com.alibaba.datax.common.element.BytesColumn;
import com.alibaba.datax.common.element.DateColumn;
import com.alibaba.datax.common.element.DoubleColumn;
import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.common.statistics.PerfRecord;
import com.alibaba.datax.common.statistics.PerfTrace;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.reader.util.OriginalConfPretreatmentUtil;
import com.alibaba.datax.plugin.rdbms.reader.util.PreCheckTask;
import com.alibaba.datax.plugin.rdbms.reader.util.ReaderSplitUtil;
import com.alibaba.datax.plugin.rdbms.reader.util.SingleTableSplitUtil;
import com.alibaba.datax.plugin.rdbms.util.DBUtil;
import com.alibaba.datax.plugin.rdbms.util.DBUtilErrorCode;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import com.alibaba.datax.plugin.rdbms.util.RdbmsException;

import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.math.BigInteger;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Types;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.HashSet;
import java.util.Collections;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CommonRdbmsReader {

    public static class Job {
        private static final Logger LOG = LoggerFactory
                .getLogger(Job.class);

        public Job(DataBaseType dataBaseType) {
            OriginalConfPretreatmentUtil.DATABASE_TYPE = dataBaseType;
            SingleTableSplitUtil.DATABASE_TYPE = dataBaseType;
        }

        public void init(Configuration originalConfig) {

            OriginalConfPretreatmentUtil.doPretreatment(originalConfig);

            LOG.debug("After job init(), job config now is:[\n{}\n]",
                    originalConfig.toJSON());
        }

        public void preCheck(Configuration originalConfig,DataBaseType dataBaseType) {
            /*检查每个表是否有读权限，以及querySql跟splik Key是否正确*/
            Configuration queryConf = ReaderSplitUtil.doPreCheckSplit(originalConfig);
            String splitPK = queryConf.getString(Key.SPLIT_PK);
            List<Object> connList = queryConf.getList(Constant.CONN_MARK, Object.class);
            String username = queryConf.getString(Key.USERNAME);
            String password = queryConf.getString(Key.PASSWORD);
            ExecutorService exec;
            if (connList.size() < 10){
                exec = Executors.newFixedThreadPool(connList.size());
            }else{
                exec = Executors.newFixedThreadPool(10);
            }
            Collection<PreCheckTask> taskList = new ArrayList<PreCheckTask>();
            for (int i = 0, len = connList.size(); i < len; i++){
                Configuration connConf = Configuration.from(connList.get(i).toString());
                PreCheckTask t = new PreCheckTask(username,password,connConf,dataBaseType,splitPK);
                taskList.add(t);
            }
            try {
                java.util.concurrent.CompletionService<Boolean> completed =
                        new java.util.concurrent.ExecutorCompletionService<Boolean>(exec);
                for (PreCheckTask task : taskList) completed.submit(task);
                for (int i = 0; i < taskList.size(); i++) completed.take().get();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw DataXException.asDataXException(DBUtilErrorCode.SQL_EXECUTE_FAIL,
                        "Database precheck interrupted", e);
            } catch (ExecutionException e) {
                if (e.getCause() instanceof DataXException) throw (DataXException) e.getCause();
                throw DataXException.asDataXException(DBUtilErrorCode.SQL_EXECUTE_FAIL,
                        "Database precheck failed", e.getCause());
            } finally {
                exec.shutdownNow();
            }

        }


        public List<Configuration> split(Configuration originalConfig,
                                         int adviceNumber) {
            return ReaderSplitUtil.doSplit(originalConfig, adviceNumber);
        }

        public void post(Configuration originalConfig) {
            // do nothing
        }

        public void destroy(Configuration originalConfig) {
            // do nothing
        }

    }

    public static class Task {
        private static final Logger LOG = LoggerFactory
                .getLogger(Task.class);
        private static final boolean IS_DEBUG = LOG.isDebugEnabled();
        protected final byte[] EMPTY_CHAR_ARRAY = new byte[0];

        private DataBaseType dataBaseType;
        private int taskGroupId = -1;
        private int taskId=-1;

        private String username;
        private String password;
        private String jdbcUrl;
        private String mandatoryEncoding;
        private ResultSetMetaData cachedMetaData;
        private int[] columnTypes;
        private boolean[] yearColumns;
        private Set<String> binaryColumns = Collections.emptySet();

        // 作为日志显示信息时，需要附带的通用信息。比如信息所对应的数据库连接等信息，针对哪个表做的操作
        private String basicMsg;

        public Task(DataBaseType dataBaseType) {
            this(dataBaseType, -1, -1);
        }

        public Task(DataBaseType dataBaseType,int taskGropuId, int taskId) {
            this.dataBaseType = dataBaseType;
            this.taskGroupId = taskGropuId;
            this.taskId = taskId;
        }

        public void init(Configuration readerSliceConfig) {

			/* for database connection */

            this.username = readerSliceConfig.getString(Key.USERNAME);
            this.password = readerSliceConfig.getString(Key.PASSWORD);
            this.jdbcUrl = readerSliceConfig.getString(Key.JDBC_URL);

            //ob10的处理
            if (this.jdbcUrl.startsWith(com.alibaba.datax.plugin.rdbms.writer.Constant.OB10_SPLIT_STRING) && this.dataBaseType == DataBaseType.MySql) {
                String[] ss = this.jdbcUrl.split(com.alibaba.datax.plugin.rdbms.writer.Constant.OB10_SPLIT_STRING_PATTERN);
                if (ss.length != 3) {
                    throw DataXException
                            .asDataXException(
                                    DBUtilErrorCode.JDBC_OB10_ADDRESS_ERROR, "JDBC OB10格式错误，请联系askdatax");
                }
                LOG.info("this is ob1_0 jdbc url.");
                this.username = ss[1].trim() +":"+this.username;
                this.jdbcUrl = ss[2];
                LOG.info("this is ob1_0 jdbc url. user=" + this.username + " :url=" + this.jdbcUrl);
            }

            this.mandatoryEncoding = readerSliceConfig.getString(Key.MANDATORY_ENCODING, "");
            this.cachedMetaData = null;
            this.binaryColumns = Collections.emptySet();
            List<String> configuredBinary = readerSliceConfig.getList(Key.BINARY_COLUMNS, String.class);
            if (configuredBinary != null) {
                binaryColumns = new HashSet<>();
                for (String name : configuredBinary) {
                    if (name == null || name.isEmpty() || !binaryColumns.add(name))
                        throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                                "binaryColumns must contain distinct, nonempty result column labels");
                }
            }

            basicMsg = String.format("jdbcUrl:[%s]", this.jdbcUrl);

        }

        public void startRead(Configuration readerSliceConfig,
                              RecordSender recordSender,
                              TaskPluginCollector taskPluginCollector, int fetchSize) {
            String querySql = readerSliceConfig.getString(Key.QUERY_SQL);
            String table = readerSliceConfig.getString(Key.TABLE);

            PerfTrace.getInstance().addTaskDetails(taskId, table + "," + basicMsg);

            LOG.info("Begin to read record by Sql: [{}\n] {}.",
                    querySql, basicMsg);
            PerfRecord queryPerfRecord = new PerfRecord(taskGroupId,taskId, PerfRecord.PHASE.SQL_QUERY);
            queryPerfRecord.start();

            Connection conn = DBUtil.getConnection(this.dataBaseType, jdbcUrl,
                    username, password);

            int columnNumber = 0;
            ResultSet rs = null;
            Statement statement = null;
            try {
                // Keep session initialization within the connection cleanup scope.
                DBUtil.dealWithSessionConfig(conn, readerSliceConfig, this.dataBaseType, basicMsg);
                rs = DBUtil.query(conn, querySql, fetchSize,
                        readerSliceConfig.getInt(Key.QUERY_TIMEOUT,
                                com.alibaba.datax.plugin.rdbms.util.Constant.SOCKET_TIMEOUT_INSECOND));
                statement = rs.getStatement();
                queryPerfRecord.end();

                ResultSetMetaData metaData = rs.getMetaData();
                columnNumber = metaData.getColumnCount();
                cacheMetadata(metaData, columnNumber);

                //这个统计干净的result_Next时间
                PerfRecord allResultPerfRecord = new PerfRecord(taskGroupId, taskId, PerfRecord.PHASE.RESULT_NEXT_ALL);
                allResultPerfRecord.start();

                long rsNextUsedTime = 0;
                long lastTime = System.nanoTime();
                while (rs.next()) {
                    rsNextUsedTime += (System.nanoTime() - lastTime);
                    this.transportOneRecord(recordSender, rs,
                            metaData, columnNumber, mandatoryEncoding, taskPluginCollector);
                    lastTime = System.nanoTime();
                }

                allResultPerfRecord.end(rsNextUsedTime);
                //目前大盘是依赖这个打印，而之前这个Finish read record是包含了sql查询和result next的全部时间
                LOG.info("Finished read record by Sql: [{}\n] {}.",
                        querySql, basicMsg);

            } catch (DataXException e) {
                throw e;
            } catch (Exception e) {
                throw RdbmsException.asQueryException(this.dataBaseType, e, querySql, table, username);
            } finally {
                DBUtil.closeDBResources(rs, statement, conn);
            }
        }

        public void post(Configuration originalConfig) {
            // do nothing
        }

        public void destroy(Configuration originalConfig) {
            // do nothing
        }
        
        protected Record transportOneRecord(RecordSender recordSender, ResultSet rs, 
                ResultSetMetaData metaData, int columnNumber, String mandatoryEncoding, 
                TaskPluginCollector taskPluginCollector) {
            Record record = buildRecord(recordSender,rs,metaData,columnNumber,mandatoryEncoding,taskPluginCollector); 
            if (record != null) recordSender.sendToWriter(record);
            return record;
        }
        protected void cacheMetadata(ResultSetMetaData metaData, int columnNumber) throws SQLException {
            if (cachedMetaData != metaData) {
                columnTypes = new int[columnNumber];
                yearColumns = new boolean[columnNumber];
                Set<String> remaining = new HashSet<>(binaryColumns);
                for (int i = 0; i < columnNumber; i++) {
                    columnTypes[i] = metaData.getColumnType(i + 1);
                    yearColumns[i] = columnTypes[i] == Types.DATE
                            && "year".equalsIgnoreCase(metaData.getColumnTypeName(i + 1));
                    if (!binaryColumns.isEmpty() && binaryColumns.contains(metaData.getColumnLabel(i + 1))) {
                        String label = metaData.getColumnLabel(i + 1);
                        int type = columnTypes[i];
                        if (!remaining.remove(label) || !(type == Types.CHAR || type == Types.NCHAR
                                || type == Types.VARCHAR || type == Types.NVARCHAR || type == Types.LONGVARCHAR
                                || type == Types.LONGNVARCHAR || type == Types.BINARY || type == Types.VARBINARY
                                || type == Types.LONGVARBINARY || type == Types.BLOB))
                            throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                                    "binaryColumns label is ambiguous or not a string/binary field: " + label);
                        columnTypes[i] = Types.VARBINARY;
                    }
                }
                if (!remaining.isEmpty()) throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
                        "binaryColumns not found in query output: " + remaining);
                cachedMetaData = metaData;
            }
        }

        protected Record buildRecord(RecordSender recordSender,ResultSet rs, ResultSetMetaData metaData, int columnNumber, String mandatoryEncoding,
        		TaskPluginCollector taskPluginCollector) {
        	Record record = recordSender.createRecord();

            try {
                cacheMetadata(metaData, columnNumber);
                for (int i = 1; i <= columnNumber; i++) {
                    switch (columnTypes[i - 1]) {

                    case Types.CHAR:
                    case Types.NCHAR:
                    case Types.VARCHAR:
                    case Types.LONGVARCHAR:
                    case Types.NVARCHAR:
                    case Types.LONGNVARCHAR:
                        String rawData;
                        if(StringUtils.isBlank(mandatoryEncoding)){
                            rawData = rs.getString(i);
                        }else{
                            byte[] bytes = rs.getBytes(i);
                            rawData = bytes == null ? null : new String(bytes, mandatoryEncoding);
                        }
                        record.addColumn(new StringColumn(rawData));
                        break;

                    case Types.CLOB:
                    case Types.NCLOB:
                        record.addColumn(new StringColumn(rs.getString(i)));
                        break;

                    case Types.SMALLINT:
                    case Types.TINYINT:
                    case Types.INTEGER:
                    case Types.BIGINT:
                        if (dataBaseType == DataBaseType.PostgreSQL) {
                            // PostgreSQL integral types fit in long, including their binary wire format.
                            // Other drivers may expose unsigned BIGINT or padded text; keep their path.
                            long integer = rs.getLong(i);
                            record.addColumn(rs.wasNull() ? new LongColumn() :
                                    new LongColumn(BigInteger.valueOf(integer), Long.toString(integer).length()));
                        } else {
                            record.addColumn(new LongColumn(rs.getString(i)));
                        }
                        break;

                    case Types.NUMERIC:
                    case Types.DECIMAL:
                        record.addColumn(new DoubleColumn(rs.getString(i)));
                        break;

                    case Types.FLOAT:
                    case Types.REAL:
                    case Types.DOUBLE:
                        record.addColumn(new DoubleColumn(rs.getString(i)));
                        break;

                    case Types.TIME:
                        // java.sql.Time cannot retain PG microseconds, TIMETZ offsets or 24:00.
                        record.addColumn(dataBaseType == DataBaseType.PostgreSQL
                                ? new StringColumn(rs.getString(i)) : new DateColumn(rs.getTime(i)));
                        break;

                    // for mysql bug, see http://bugs.mysql.com/bug.php?id=35115
                    case Types.DATE:
                        if (yearColumns[i - 1]) {
                            int year = rs.getInt(i);
                            record.addColumn(rs.wasNull() ? new LongColumn() : new LongColumn(year));
                        } else {
                            record.addColumn(new DateColumn(rs.getDate(i)));
                        }
                        break;

                    case Types.TIMESTAMP:
                        record.addColumn(new DateColumn(rs.getTimestamp(i)));
                        break;

                    case Types.BINARY:
                    case Types.VARBINARY:
                    case Types.BLOB:
                    case Types.LONGVARBINARY:
                        record.addColumn(new BytesColumn(rs.getBytes(i)));
                        break;

                    // warn: bit(1) -> Types.BIT 可使用BoolColumn
                    // warn: bit(>1) -> Types.VARBINARY 可使用BytesColumn
                    case Types.BOOLEAN:
                    case Types.BIT:
                        boolean value = rs.getBoolean(i);
                        record.addColumn(rs.wasNull() ? new BoolColumn() : new BoolColumn(value));
                        break;

                    case Types.NULL:
                        String stringData = null;
                        if(rs.getObject(i) != null) {
                            stringData = rs.getObject(i).toString();
                        }
                        record.addColumn(new StringColumn(stringData));
                        break;

                    default:
                        throw DataXException
                                .asDataXException(
                                        DBUtilErrorCode.UNSUPPORTED_TYPE,
                                        String.format(
                                                "您的配置文件中的列配置信息有误. 因为DataX 不支持数据库读取这种字段类型. 字段名:[%s], 字段名称:[%s], 字段Java类型:[%s]. 请尝试使用数据库函数将其转换datax支持的类型 或者不同步该字段 .",
                                                metaData.getColumnName(i),
                                                metaData.getColumnType(i),
                                                metaData.getColumnClassName(i)));
                    }
                }
            } catch (SQLException e) {
                // JDBC access failures are task failures, never partially valid records.
                throw RdbmsException.asQueryException(dataBaseType, e, null, null, username);
            } catch (DataXException e) {
                throw e;
            } catch (Exception e) {
                if (IS_DEBUG) {
                    LOG.debug("read data " + record.toString()
                            + " occur exception:", e);
                }
                taskPluginCollector.collectDirtyRecord(record, e);
                return null;
            }
            return record;
        }
    }

}
