package com.alibaba.datax.plugin.writer.postgresqlwriter;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.plugin.rdbms.util.DBUtil;
import com.alibaba.datax.plugin.rdbms.util.DBUtilErrorCode;
import com.alibaba.datax.plugin.rdbms.util.DataBaseType;
import com.alibaba.datax.plugin.rdbms.writer.Constant;
import com.alibaba.datax.plugin.rdbms.writer.Key;
import com.alibaba.datax.plugin.rdbms.writer.util.WriterUtil;
import org.postgresql.core.Utils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;

/** PG-native staging and atomic append. The batch ID names one immutable logical batch. */
final class PostgresqlAtomicBatch {
    static final String ID = "atomicBatchId";
    static final String TOKEN = "_dataxAtomicToken";
    private static final Logger LOG = LoggerFactory.getLogger(PostgresqlAtomicBatch.class);
    private final Configuration config;
    private final String batchId;
    private final String token = UUID.randomUUID().toString();
    private Connection connection;
    private String target, stage, ledger, columns, layout, ownership, stageComment;
    private long targetOid;
    int taskCount;

    static void validate(Configuration config) {
        if (config.get(TOKEN) != null) fail("Reserved atomic configuration: " + TOKEN);
        String id = config.getString(ID);
        if (id == null) return;
        if (id.trim().isEmpty() || id.length() > 256 || id.indexOf('\0') >= 0
                || !StandardCharsets.UTF_8.newEncoder().canEncode(id)) fail("atomicBatchId must be valid, nonempty text, at most 256 UTF-16 units");
        for (String key : new String[]{Key.PRE_SQL, Key.POST_SQL}) {
            List<String> statements = config.getList(key, String.class);
            if (statements != null && !statements.isEmpty()) fail("atomicBatchId does not allow " + key);
        }
    }

    PostgresqlAtomicBatch(Configuration config) {
        this.config = config;
        this.batchId = config.getString(ID);
        if (config.getInt(Constant.TABLE_NUMBER_MARK) != 1
                || config.getList(Constant.CONN_MARK).size() != 1) fail("atomicBatchId requires one destination table and connection");
    }

    private static String quote(String identifier) throws SQLException {
        return Utils.escapeIdentifier(null, identifier).toString();
    }

    private static void fail(String message) {
        throw DataXException.asDataXException(DBUtilErrorCode.WRITE_DATA_ERROR, message);
    }

    private void execute(String sql) throws SQLException {
        try (Statement statement = connection.createStatement()) { statement.execute(sql); }
    }

    private String comment(String table) throws SQLException {
        try (PreparedStatement query = connection.prepareStatement("SELECT obj_description(to_regclass(?),'pg_class')")) {
            query.setString(1, table);
            try (ResultSet result = query.executeQuery()) { result.next(); return result.getString(1); }
        }
    }

    void prepare() {
        try {
            connection = DBUtil.getConnection(DataBaseType.PostgreSQL, config.getString(Key.JDBC_URL),
                    config.getString(Key.USERNAME), config.getString(Key.PASSWORD));
            DBUtil.dealWithSessionConfig(connection, config, DataBaseType.PostgreSQL, "atomic staging");
            connection.setAutoCommit(false);
            String schema;
            try (PreparedStatement query = connection.prepareStatement(
                    "SELECT c.oid,n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=?::regclass")) {
                query.setString(1, config.getString(Key.TABLE));
                try (ResultSet result = query.executeQuery()) {
                    if (!result.next()) throw new SQLException("Atomic target not found");
                    targetOid = result.getLong(1); schema = result.getString(2);
                    if (result.getString(3).startsWith("__datax_")) fail("Atomic target uses a reserved __datax_ name");
                    target = quote(schema) + "." + quote(result.getString(3));
                }
            }
            checkTarget();
            List<String> names = new ArrayList<>();
            List<String> stageExpressions = new ArrayList<>();
            StringBuilder definition = new StringBuilder();
            for (String configured : config.getList(Key.COLUMN, String.class)) {
                String name;
                try (PreparedStatement parse = connection.prepareStatement("SELECT parse_ident(?,true)")) {
                    parse.setString(1, configured);
                    try (ResultSet result = parse.executeQuery()) {
                        result.next(); String[] parsed = (String[]) result.getArray(1).getArray();
                        if (parsed.length != 1) throw new SQLException("Atomic columns must be simple identifiers");
                        name = parsed[0];
                    }
                }
                if (names.contains(quote(name))) fail("Repeated atomic column: " + name);
                names.add(quote(name));
                try (PreparedStatement attr = connection.prepareStatement(
                        "SELECT a.atttypid,a.atttypmod,a.attcollation,n.nspname,t.typname,t.typtype FROM pg_attribute a "
                        + "JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace n ON n.oid=t.typnamespace "
                        + "WHERE a.attrelid=?::oid AND a.attname=? AND a.attnum>0 AND NOT a.attisdropped")) {
                    attr.setLong(1, targetOid); attr.setString(2, name);
                    try (ResultSet result = attr.executeQuery()) {
                        if (!result.next()) throw new SQLException("Atomic column not found: " + name);
                        // Domains retain typmods inside the type, including in arrays/composites.
                        // Comparing stage to target cannot detect values already rounded on staging.
                        if (!"pg_catalog".equals(result.getString(4)) || "d".equals(result.getString(6)))
                            fail("Atomic append does not support domains or user-defined column types: " + name);
                        definition.append(name.length()).append(':').append(name).append(':')
                                .append(result.getString(1)).append(':').append(result.getString(2)).append(':')
                                .append(result.getString(3)).append(';');
                        // Preserve incoming precision in staging; detect target rounding via RETURNING.
                        stageExpressions.add("CAST(" + quote(name) + " AS " + quote(result.getString(4))
                                + "." + quote(result.getString(5)) + ") AS " + quote(name));
                    }
                }
            }
            columns = String.join(",", names); layout = definition.toString();
            byte[] key = MessageDigest.getInstance("SHA-256").digest((targetOid + "\0" + batchId).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte value : key) hex.append(String.format(Locale.ROOT, "%02x", value & 255));
            ownership = "datax.atomic.v1:" + hex + ":";
            stageComment = ownership + token;
            stage = quote(schema) + "." + quote("__datax_stage_" + hex.substring(0, 32));
            ledger = quote(schema) + "." + quote("__datax_atomic_batches_v1");
            // ponytail: one bootstrap lock; per-schema locks if simultaneous job initialization contends.
            execute("SELECT pg_advisory_xact_lock(4918300359238505550)");
            execute("CREATE TABLE IF NOT EXISTS " + ledger + " (target_oid oid NOT NULL,batch_id text NOT NULL,"
                    + "layout text NOT NULL,row_count bigint NOT NULL,fingerprint text NOT NULL,"
                    + "committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(target_oid,batch_id))");
            checkLedger();
            connection.commit();
            try (PreparedStatement lock = connection.prepareStatement("SELECT pg_try_advisory_lock(?)")) {
                lock.setLong(1, ByteBuffer.wrap(key).getLong());
                try (ResultSet result = lock.executeQuery()) {
                    result.next(); if (!result.getBoolean(1)) fail("This atomic batch is already running: " + batchId);
                }
            }
            String previous = comment(stage);
            if (previous != null && !previous.startsWith(ownership)) fail("Refusing to replace an unowned staging table: " + stage);
            // A pre-existing unmarked table is not ours, even when the name happens to match.
            try (PreparedStatement exists = connection.prepareStatement("SELECT to_regclass(?) IS NOT NULL")) {
                exists.setString(1, stage);
                try (ResultSet result = exists.executeQuery()) {
                    result.next(); if (result.getBoolean(1) && previous == null) fail("Unmarked staging table already exists: " + stage);
                }
            }
            execute("DROP TABLE IF EXISTS " + stage);
            execute("CREATE TABLE " + stage + " AS SELECT " + String.join(",", stageExpressions) + " FROM " + target + " WITH NO DATA");
            execute("COMMENT ON TABLE " + stage + " IS '" + stageComment + "'");
            connection.commit();
            config.set(Key.TABLE, stage);
            config.set(Key.COLUMN, names);
            config.set(TOKEN, stageComment);
            config.set(Constant.INSERT_OR_REPLACE_TEMPLATE_MARK, WriterUtil.getWriteTemplate(names,
                    Collections.nCopies(names.size(), "?"), "INSERT", DataBaseType.PostgreSQL, false));
        } catch (Exception error) {
            throw DataXException.asDataXException(DBUtilErrorCode.WRITE_DATA_ERROR, "Atomic staging preparation failed", error);
        }
    }

    private void checkTarget() throws SQLException {
        try (PreparedStatement check = connection.prepareStatement(
                "WITH RECURSIVE tables(oid) AS (SELECT ?::oid UNION SELECT i.inhrelid FROM pg_inherits i JOIN tables p ON i.inhparent=p.oid) "
                + "SELECT count(*) FROM pg_class c JOIN tables t ON t.oid=c.oid WHERE c.relkind NOT IN ('r','p') OR c.relpersistence<>'p' OR c.relrowsecurity "
                + "OR EXISTS(SELECT 1 FROM pg_trigger x WHERE x.tgrelid=c.oid AND NOT x.tgisinternal) "
                + "OR EXISTS(SELECT 1 FROM pg_rewrite x WHERE x.ev_class=c.oid AND x.ev_type<>'1')")) {
            check.setLong(1, targetOid);
            try (ResultSet result = check.executeQuery()) {
                result.next(); if (result.getLong(1) != 0) fail("Atomic append requires logged native tables without RLS, user triggers or write rules (including partitions)");
            }
        }
    }

    private void checkLedger() throws SQLException {
        execute("LOCK TABLE " + ledger + " IN ROW EXCLUSIVE MODE");
        try (PreparedStatement check = connection.prepareStatement(
                "SELECT c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity "
                + "AND NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal) "
                + "AND NOT EXISTS(SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid AND r.ev_type<>'1') "
                + "AND EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' "
                + "AND k.conkey=ARRAY[1,2]::smallint[] AND NOT k.condeferrable AND k.convalidated) "
                + "AND (SELECT array_agg(a.attname||':'||a.atttypid||':'||a.attnotnull ORDER BY a.attnum) "
                + "FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) "
                + "= ARRAY['target_oid:26:true','batch_id:25:true','layout:25:true','row_count:20:true',"
                + "'fingerprint:25:true','committed_at:1184:true'] FROM pg_class c WHERE c.oid=?::regclass")) {
            check.setString(1, ledger);
            try (ResultSet result = check.executeQuery()) {
                if (!result.next() || !result.getBoolean(1)) fail("Atomic batch ledger structure is unsafe: " + ledger);
            }
        }
    }

    static void fence(Connection connection, Configuration config) throws SQLException {
        if (connection.getAutoCommit()) connection.setAutoCommit(false);
        try (Statement lock = connection.createStatement()) {
            lock.execute("LOCK TABLE " + config.getString(Key.TABLE) + " IN ROW EXCLUSIVE MODE");
        }
        try (PreparedStatement query = connection.prepareStatement("SELECT obj_description(?::regclass,'pg_class')")) {
            query.setString(1, config.getString(Key.TABLE));
            try (ResultSet result = query.executeQuery()) {
                result.next();
                if (!config.getString(TOKEN).equals(result.getString(1))) fail("Atomic staging generation changed; stale writer stopped");
            }
        }
    }

    void publish(List<String> taskRows) {
        try {
            if (taskRows == null || taskRows.size() != taskCount) fail("Missing atomic task row counts");
            long expected = 0;
            for (String count : taskRows) expected = Math.addExact(expected, Long.parseLong(count));
            execute("LOCK TABLE " + stage + " IN SHARE MODE");
            if (!stageComment.equals(comment(stage))) fail("Atomic staging generation changed before publication");
            // Sum all four signed SHA-256 limbs, preserving multiplicity (XOR would cancel duplicates).
            // PostgreSQL numeric sums do not overflow; binary record_send preserves typed field boundaries.
            StringBuilder hashQuery = new StringBuilder("WITH hashes AS (SELECT encode(sha256(record_send(ROW(")
                    .append(columns).append("))),'hex') h FROM ").append(stage).append(") SELECT count(*)");
            for (int offset : new int[]{1,17,33,49}) hashQuery.append(",coalesce(sum(('x'||substr(h,").append(offset)
                    .append(",16))::bit(64)::bigint),0)::text");
            hashQuery.append(" FROM hashes");
            long count;
            String fingerprint;
            try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(hashQuery.toString())) {
                result.next(); count = result.getLong(1);
                fingerprint = result.getString(2) + ":" + result.getString(3) + ":" + result.getString(4) + ":" + result.getString(5);
            }
            if (count != expected) fail("Atomic stage count differs from received records: " + count + " vs " + expected);
            checkLedger();
            boolean published = false;
            try (PreparedStatement prior = connection.prepareStatement("SELECT layout,row_count,fingerprint FROM " + ledger + " WHERE target_oid=?::oid AND batch_id=?")) {
                prior.setLong(1, targetOid); prior.setString(2, batchId);
                try (ResultSet result = prior.executeQuery()) {
                    if (result.next()) {
                        if (!layout.equals(result.getString(1)) || count != result.getLong(2) || !fingerprint.equals(result.getString(3)))
                            fail("Atomic batch ID was already committed with different data or columns: " + batchId);
                        published = true;
                    }
                }
            }
            if (!published) {
                execute("LOCK TABLE " + target + " IN SHARE ROW EXCLUSIVE MODE");
                try (PreparedStatement identity = connection.prepareStatement("SELECT ?::regclass::oid=?::oid")) {
                    identity.setString(1, target); identity.setLong(2, targetOid);
                    try (ResultSet result = identity.executeQuery()) { result.next(); if (!result.getBoolean(1)) fail("Atomic target was replaced"); }
                }
                checkTarget();
                // Compare exact binary multisets inside the publication transaction, before committing.
                String inserted = "WITH inserted AS (INSERT INTO " + target + " (" + columns + ") SELECT " + columns + " FROM " + stage
                        + " RETURNING " + columns + "), sent AS (SELECT record_send(ROW(" + columns + ")) b FROM " + stage
                        + "), written AS (SELECT record_send(ROW(" + columns + ")) b FROM inserted) "
                        + "SELECT count(*) FROM ((SELECT b FROM sent EXCEPT ALL SELECT b FROM written) UNION ALL (SELECT b FROM written EXCEPT ALL SELECT b FROM sent)) differences";
                try (Statement statement = connection.createStatement(); ResultSet result = statement.executeQuery(inserted)) {
                    result.next(); if (result.getLong(1) != 0) fail("Atomic publication changed or omitted staged values");
                }
                execute("SET CONSTRAINTS ALL IMMEDIATE");
                try (PreparedStatement marker = connection.prepareStatement("INSERT INTO " + ledger
                        + "(target_oid,batch_id,layout,row_count,fingerprint) VALUES(?::oid,?,?,?,?)")) {
                    marker.setLong(1, targetOid); marker.setString(2, batchId); marker.setString(3, layout);
                    marker.setLong(4, count); marker.setString(5, fingerprint);
                    if (marker.executeUpdate() != 1) fail("Atomic batch marker was not inserted");
                }
            }
            execute("DROP TABLE " + stage);
            try { connection.commit(); }
            catch (SQLException uncertain) {
                throw DataXException.asDataXException(DBUtilErrorCode.WRITE_COMMIT_UNCERTAIN,
                        "Atomic publication acknowledgement missing; retry only with the SAME atomicBatchId: " + batchId, uncertain);
            }
            LOG.info("Atomic batch {}: {} rows, {}", batchId, count, published ? "already published; no rows appended" : "published once");
        } catch (Exception error) {
            throw DataXException.asDataXException(DBUtilErrorCode.WRITE_DATA_ERROR, "Atomic publication failed", error);
        }
    }

    void close() {
        if (connection == null) return;
        try {
            if (!connection.isClosed()) {
                connection.rollback();
                // Never reconnect for cleanup: losing the session also loses our advisory lock.
                if (stage != null && stageComment != null && stageComment.equals(comment(stage))) {
                    execute("SET LOCAL lock_timeout='5s'");
                    execute("DROP TABLE " + stage);
                    connection.commit();
                }
            }
        } catch (SQLException error) {
            LOG.warn("Atomic stage cleanup incomplete; the next same-ID attempt can reclaim its stage: " + stage, error);
        } finally {
            DBUtil.closeDBResources(null, null, connection);
            connection = null;
        }
    }
}
