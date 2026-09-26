package com.alibaba.datax.plugin.writer.postgresqlwriter;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.Record;
import org.postgresql.core.BaseConnection;
import org.postgresql.core.Utils;
import org.postgresql.copy.CopyManager;
import org.postgresql.jdbc.TimestampUtils;
import org.postgresql.util.PGbytea;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.spi.Writer;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.util.DBUtilErrorCode;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import com.alibaba.datax.plugin.rdbms.writer.CommonRdbmsWriter;
import com.alibaba.datax.plugin.rdbms.writer.Key;

import java.util.List;

public class PostgresqlWriter extends Writer {
	private static final DataBaseType DATABASE_TYPE = DataBaseType.PostgreSQL;

	public static class Job extends Writer.Job {
		private Configuration originalConfig = null;
		private CommonRdbmsWriter.Job commonRdbmsWriterMaster;
		private PostgresqlAtomicBatch atomicBatch;

		@Override
		public void init() {
			this.originalConfig = super.getPluginJobConf();
			PostgresqlAtomicBatch.validate(this.originalConfig);

			// warn：not like mysql, PostgreSQL only support insert mode, don't use
			String writeMode = this.originalConfig.getString(Key.WRITE_MODE);
			if (null != writeMode) {
				throw DataXException.asDataXException(DBUtilErrorCode.CONF_ERROR,
					String.format("写入模式(writeMode)配置有误. 因为PostgreSQL不支持配置参数项 writeMode: %s, PostgreSQL仅使用insert sql 插入数据. 请检查您的配置并作出修改.", writeMode));
			}

			this.commonRdbmsWriterMaster = new CommonRdbmsWriter.Job(DATABASE_TYPE);
			this.commonRdbmsWriterMaster.init(this.originalConfig);
			if (this.originalConfig.getString(PostgresqlAtomicBatch.ID) != null)
				this.atomicBatch = new PostgresqlAtomicBatch(this.originalConfig);
		}

		@Override
		public void prepare() {
			this.commonRdbmsWriterMaster.prepare(this.originalConfig);
			if (atomicBatch != null) atomicBatch.prepare();
		}

		@Override
		public List<Configuration> split(int mandatoryNumber) {
			if (atomicBatch != null) atomicBatch.taskCount = mandatoryNumber;
			return this.commonRdbmsWriterMaster.split(this.originalConfig, mandatoryNumber);
		}

		@Override
		public void post() {
			if (atomicBatch != null) atomicBatch.publish(getJobPluginCollector().getMessage(originalConfig.getString(PostgresqlAtomicBatch.TOKEN)));
			else this.commonRdbmsWriterMaster.post(this.originalConfig);
		}

		@Override public boolean requiresCompleteTransfer() { return atomicBatch != null; }

		@Override
		public void destroy() {
			if (atomicBatch != null) atomicBatch.close();
			if (commonRdbmsWriterMaster != null) this.commonRdbmsWriterMaster.destroy(this.originalConfig);
		}

	}

    static String copyValue(Column column, int type, TimestampUtils dates) throws SQLException {
        if (column.getRawData() == null) return null;
        switch (type) {
            case Types.CHAR: case Types.NCHAR: case Types.CLOB: case Types.NCLOB:
            case Types.VARCHAR: case Types.LONGVARCHAR: case Types.NVARCHAR: case Types.LONGNVARCHAR:
            case Types.BOOLEAN: case Types.BIT:
                return column.asString();
            case Types.SMALLINT: case Types.INTEGER: case Types.BIGINT:
            case Types.NUMERIC: case Types.DECIMAL: case Types.FLOAT: case Types.REAL: case Types.DOUBLE:
                return column.asString();
            case Types.TINYINT:
                return column.asLong().toString();
            case Types.BINARY: case Types.VARBINARY: case Types.BLOB: case Types.LONGVARBINARY:
                return PGbytea.toPGString(column.asBytes());
            case Types.DATE:
                return dates.toString(null, new java.sql.Date(column.asDate().getTime()));
            case Types.TIME:
                if (column.getType() == Column.Type.STRING) return column.asString();
                return dates.toString(null, new java.sql.Time(column.asDate().getTime()));
            case Types.TIMESTAMP:
                java.util.Date date = column.asDate();
                return dates.toString(null, date instanceof java.sql.Timestamp
                        ? (java.sql.Timestamp) date : new java.sql.Timestamp(date.getTime()));
            default:
                throw new SQLException("Unsupported PostgreSQL COPY JDBC type: " + type, "0A000");
        }
    }

	public static class Task extends Writer.Task {
		private Configuration writerSliceConfig;
		private CommonRdbmsWriter.Task commonRdbmsWriterSlave;

		@Override
		public void init() {
			this.writerSliceConfig = super.getPluginJobConf();
			this.commonRdbmsWriterSlave = new CommonRdbmsWriter.Task(DATABASE_TYPE){
                private String copySql;

                @Override protected boolean allowBatchFallback() {
                    return writerSliceConfig.getString(PostgresqlAtomicBatch.TOKEN) == null;
                }

                @Override
                protected PreparedStatement fillPreparedStatementColumnType(PreparedStatement statement,
                        int index, int type, String typeName, Column column) throws SQLException {
                    if (type == Types.TIME && column.getType() == Column.Type.STRING) {
                        // calcValueHolder supplies the PG time/timetz cast; do not parse via java.sql.Time.
                        statement.setString(index + 1, column.asString());
                        return statement;
                    }
                    return super.fillPreparedStatementColumnType(statement, index, type, typeName, column);
                }

                @Override
                protected void doBatchInsert(Connection connection, List<Record> buffer) throws SQLException {
                    if (writerSliceConfig.getString(PostgresqlAtomicBatch.TOKEN) != null)
                        PostgresqlAtomicBatch.fence(connection, writerSliceConfig);
                    if (!writerSliceConfig.getBool("useCopy", false)) {
                        super.doBatchInsert(connection, buffer);
                        return;
                    }
                    try {
                        if (connection.getAutoCommit()) connection.setAutoCommit(false);
                        BaseConnection pg = connection.unwrap(BaseConnection.class);
                        if (copySql == null) {
                            // Resolve qualified/quoted table names using PostgreSQL's own parser.
                            String qualified;
                            try (PreparedStatement lookup = connection.prepareStatement(
                                    "SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) "
                                    + "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                    + "WHERE c.oid=?::regclass")) {
                                lookup.setString(1, table);
                                try (ResultSet result = lookup.executeQuery()) {
                                    if (!result.next()) throw new SQLException("COPY target not found", "42P01");
                                    qualified = result.getString(1);
                                }
                            }
                            StringBuilder statement = new StringBuilder("COPY ").append(qualified).append(" (");
                            for (int i = 0; i < columnNumber; i++) {
                                if (i > 0) statement.append(',');
                                Utils.escapeIdentifier(statement, resultSetMetaData.getLeft().get(i));
                            }
                            copySql = statement.append(") FROM STDIN WITH (FORMAT csv, ENCODING 'UTF8')").toString();
                        }
                        // ponytail: materialize one existing bounded batch; stream a record at a
                        // time if profiling shows large-field batch memory to be the bottleneck.
                        StringBuilder csv = new StringBuilder();
                        for (Record record : buffer) {
                            for (int i = 0; i < columnNumber; i++) {
                                if (i > 0) csv.append(',');
                                String value = copyValue(record.getColumn(i), resultSetMetaData.getMiddle().get(i),
                                        pg.getTimestampUtils());
                                int type = resultSetMetaData.getMiddle().get(i);
                                if (emptyAsNull && "".equals(value) && (type == Types.SMALLINT || type == Types.INTEGER
                                        || type == Types.BIGINT || type == Types.NUMERIC || type == Types.DECIMAL
                                        || type == Types.FLOAT || type == Types.REAL || type == Types.DOUBLE)) value = null;
                                // CSV's unquoted empty field is NULL; even empty strings are quoted.
                                if (value != null) csv.append('"').append(value.replace("\"", "\"\"")).append('"');
                            }
                            csv.append('\n');
                        }
                        // Encode once: CopyManager's Reader overload can split a UTF-16
                        // surrogate pair at its character-buffer boundary and corrupt emoji.
                        // CharsetEncoder rejects malformed UTF-16 instead of replacing it with '?'.
                        ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder().encode(CharBuffer.wrap(csv));
                        byte[] data = new byte[encoded.remaining()];
                        encoded.get(data);
                        long copied = new CopyManager(pg).copyIn(copySql, new ByteArrayInputStream(data));
                        if (copied != buffer.size()) {
                            throw new SQLException("COPY row count differs from input: " + copied
                                    + " vs " + buffer.size(), "21000");
                        }
                        connection.commit();
                    } catch (Exception failure) {
                        try { connection.rollback(); }
                        catch (SQLException rollback) { failure.addSuppressed(rollback); }
                        // Do not replay a COPY batch or silently filter rows on failure.
                        if (failure instanceof SQLException) throw (SQLException) failure;
                        throw new SQLException("PostgreSQL COPY failed", failure);
                    }
                }

				@Override
				public String calcValueHolder(String columnType){
					if("serial".equalsIgnoreCase(columnType)){
						return "?::int";
					}else if("bigserial".equalsIgnoreCase(columnType)){
						return "?::int8";
					}else if("bit".equalsIgnoreCase(columnType)){
						return "?::bit varying";
					}
					return "?::" + columnType;
				}
			};
			this.commonRdbmsWriterSlave.init(this.writerSliceConfig);
		}

		@Override
		public void prepare() {
			this.commonRdbmsWriterSlave.prepare(this.writerSliceConfig);
		}

		public void startWrite(RecordReceiver recordReceiver) {
			if (writerSliceConfig.getString(PostgresqlAtomicBatch.TOKEN) == null) {
				this.commonRdbmsWriterSlave.startWrite(recordReceiver, this.writerSliceConfig, super.getTaskPluginCollector());
				return;
			}
			final long[] received = {0};
			RecordReceiver counted = new RecordReceiver() {
				public Record getFromReader() {
					Record record = recordReceiver.getFromReader();
					if (record != null) received[0] = Math.addExact(received[0], 1);
					return record;
				}
				public void shutdown() { recordReceiver.shutdown(); }
			};
			this.commonRdbmsWriterSlave.startWrite(counted, this.writerSliceConfig, super.getTaskPluginCollector());
			super.getTaskPluginCollector().collectMessage(writerSliceConfig.getString(PostgresqlAtomicBatch.TOKEN), Long.toString(received[0]));
		}

		@Override
		public void post() {
			this.commonRdbmsWriterSlave.post(this.writerSliceConfig);
		}

		@Override
		public void destroy() {
			this.commonRdbmsWriterSlave.destroy(this.writerSliceConfig);
		}

	}

}
