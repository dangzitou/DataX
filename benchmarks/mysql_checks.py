#!/usr/bin/env python3
"""Real database correctness and fault checks, including DataX process exit codes."""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from mysql_querysql import sql, job, run, validate, QUERY


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runtime", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    # Catch fixture/validation corruption before testing DataX's Unicode path.
    assert sql("SELECT HEX('中文😀')") == "E4B8ADE69687F09F9880"
    results.append({"case": "mysql-client-utf8", "passed": True})
    sql("""DROP TABLE IF EXISTS edge_data;
        CREATE TABLE edge_data LIKE source_data;
        ALTER TABLE edge_data MODIFY id BIGINT UNSIGNED, MODIFY tenant BIGINT NULL;
        INSERT INTO edge_data (id, tenant, amount, created, optional_text, payload) VALUES
        (1, NULL, NULL, NULL, NULL, NULL),
        (2, -9223372036854775808, -1234.5678, '2024-01-01', '', 'a'),
        (3, -1, 0.0001, '2024-02-29', '中文', 'b'),
        (4, 0, 100, '2024-01-02', NULL, ''),
        (5, 0, 100, '2024-01-02', NULL, ''),
        (6, 9223372036854775807, 999999.1234, '2024-01-03', 'x', 'c'),
        (18446744073709551615, NULL, 1, '2024-01-04', 'y', 'd');""")
    cases = [
        ("nullable-duplicate-extreme-key", "SELECT * FROM edge_data", "tenant", "edge_data"),
        ("unsigned-bigint", "SELECT * FROM edge_data", "id", "edge_data"),
        ("empty", "SELECT * FROM edge_data WHERE id < 0", "tenant", "edge_data"),
        ("all-null-key", "SELECT * FROM edge_data WHERE tenant IS NULL", "tenant", "edge_data"),
        ("constant-key", "SELECT * FROM edge_data WHERE tenant=0", "tenant", "edge_data"),
        ("trailing-semicolon", "SELECT * FROM edge_data;", "id", "edge_data"),
        ("trailing-comment", "SELECT * FROM edge_data -- comment", "id", "edge_data"),
        ("join-filter-expression", "SELECT s.id,s.tenant,s.amount*2 AS amount,s.created,"
         "CONCAT(s.optional_text, '-joined') AS optional_text,SHA2(s.payload,256) AS payload "
         "FROM source_data s JOIN digits d ON MOD(s.id,10)=d.n WHERE s.id<=10000 AND d.n<5", "id", "source_data"),
        ("ordered-limit", QUERY + " ORDER BY id DESC LIMIT 101", "id", "source_data"),
        ("union-all", QUERY + " WHERE id<=50 UNION ALL " + QUERY + " WHERE id BETWEEN 100 AND 150", "id", "source_data"),
    ]
    for name, query, key, schema in cases:
        sql("DROP TABLE IF EXISTS check_target; CREATE TABLE check_target LIKE " + schema + ";")
        config = job(True, 4, "check_target", query)
        config["job"]["content"][0]["reader"]["parameter"]["querySqlSplitPk"] = key
        seconds = run(runtime, config, output, name)
        check_query = query.rstrip(";") + "\n"
        entry = {"case": name, "seconds": seconds, **validate("check_target", check_query)}
        results.append(entry)
        print(json.dumps(entry), flush=True)

    # A duplicate in the second batch must roll back before individual fallback;
    # the following batch must resume transactions without replaying stale rows.
    for attempt in range(4):
        name = "writer-batch-fallback-%d" % (attempt + 1)
        sql("DROP TABLE IF EXISTS check_target; CREATE TABLE check_target LIKE source_data;")
        query = QUERY + " WHERE id<=5 UNION ALL " + QUERY + " WHERE id=2 ORDER BY id"
        config = job(False, 1, "check_target", query)
        config["job"]["setting"]["errorLimit"]["record"] = 1
        config["job"]["content"][0]["writer"]["parameter"]["batchSize"] = 2
        seconds = run(runtime, config, output, name)
        check = validate("check_target", QUERY + " WHERE id<=5")
        results.append({"case": name, "seconds": seconds, **check})

    for attempt in range(4):
        name = "cached-partial-batch-%d" % (attempt + 1)
        sql("DROP TABLE IF EXISTS check_target; CREATE TABLE check_target LIKE source_data; "
            "ALTER TABLE check_target DROP PRIMARY KEY;")
        query = ("SELECT id,tenant,amount,IF(id=2,'not-a-date',"
                 "DATE_FORMAT(created,'%Y-%m-%d %H:%i:%s')) AS created,optional_text,payload "
                 "FROM source_data WHERE id<=6 ORDER BY id")
        config = job(False, 1, "check_target", query)
        config["job"]["setting"]["errorLimit"]["record"] = 1
        writer = config["job"]["content"][0]["writer"]["parameter"]
        writer["batchSize"] = 3
        writer["connection"][0]["jdbcUrl"] += "&useServerPrepStmts=true&cachePrepStmts=true&prepStmtCacheSqlLimit=65535"
        seconds = run(runtime, config, output, name)
        # No target PK: stale records must not be masked by duplicate-key fallback.
        check = validate("check_target", QUERY + " WHERE id<=6 AND id<>2")
        results.append({"case": name, "seconds": seconds, **check})

    sql("""DROP TABLE IF EXISTS type_source; CREATE TABLE type_source LIKE source_data;
        ALTER TABLE type_source MODIFY tenant BIT(1) NULL, MODIFY created TIMESTAMP(3) NULL,
            MODIFY payload BLOB, MODIFY optional_text TEXT;
        INSERT INTO type_source VALUES
        (1,NULL,NULL,NULL,NULL,NULL),
        (2,b'0',12.3400,'2024-02-29 12:34:56.123',
            CONCAT('中文😀',CHAR(0),CHAR(10),CHAR(92),CHAR(39),CHAR(34)),UNHEX('0001275C0A0DFF')),
        (3,b'1',-0.0001,'2024-01-01 00:00:01.987','',UNHEX('FF00'));""")
    for native in [True, False]:
        name = "types-native" if native else "types-client-override"
        sql("DROP TABLE IF EXISTS check_target; CREATE TABLE check_target LIKE type_source;")
        config = job(False, 1, "check_target", "SELECT * FROM type_source ORDER BY id")
        writer = config["job"]["content"][0]["writer"]["parameter"]
        writer["batchSize"] = 2
        if not native:
            writer["connection"][0]["jdbcUrl"] += "&useServerPrepStmts=false&cachePrepStmts=false"
        seconds = run(runtime, config, output, name)
        results.append({"case": name, "seconds": seconds, **validate("check_target", "SELECT * FROM type_source")})

    for mode in ["replace", "update"]:
        name = "native-write-mode-" + mode
        sql("DROP TABLE IF EXISTS check_target; CREATE TABLE check_target LIKE source_data; "
            "INSERT INTO check_target SELECT id,tenant,amount-999,created,'stale','stale' "
            "FROM source_data WHERE id<=3;")
        query = QUERY + " WHERE id<=6 ORDER BY id"
        config = job(False, 1, "check_target", query)
        writer = config["job"]["content"][0]["writer"]["parameter"]
        writer.update(writeMode=mode, batchSize=2)
        seconds = run(runtime, config, output, name)
        results.append({"case": name, "seconds": seconds, **validate("check_target", query)})

    # 1024 * 100 parameters exceed MySQL's 65535 server-prepare limit. The
    # driver's existing client fallback must preserve every column and row.
    columns = ["id"] + ["c%d" % i for i in range(1, 100)]
    sql("DROP TABLE IF EXISTS wide_target; CREATE TABLE wide_target (" +
        ",".join(c + " BIGINT" for c in columns) + ", PRIMARY KEY(id))")
    query = "SELECT id," + ",".join("id+%d AS c%d" % (i, i) for i in range(1, 100)) + " FROM source_data WHERE id<=1024"
    config = job(False, 1, "wide_target", query)
    config["job"]["content"][0]["writer"]["parameter"]["column"] = columns
    seconds = run(runtime, config, output, "wide-server-prepare-fallback")
    actual = int(sql("SELECT COUNT(*) FROM wide_target"))
    mismatched = int(sql("SELECT COUNT(*) FROM wide_target WHERE NOT (" +
                        " AND ".join("c%d <=> id+%d" % (i, i) for i in range(1, 100)) + ")"))
    assert actual == 1024 and mismatched == 0
    results.append({"case": "wide-server-prepare-fallback", "seconds": seconds,
                    "expected": 1024, "actual": actual, "mismatched_rows": mismatched})

    failures = [
        ("missing-table", "SELECT * FROM absent_datax_table", {}, "MYSQLErrCode-04"),
        ("missing-column", "SELECT nonexistent_column FROM source_data", {}, "MYSQLErrCode-06"),
        ("invalid-syntax", "SELECT FROM source_data", {}, "MYSQLErrCode-05"),
        ("query-timeout", "SELECT SLEEP(10)", {"queryTimeout": 1}, "DBUtilErrorCode-22"),
        ("invalid-split-key", QUERY, {"querySqlSplitPk": "id; DROP TABLE source_data"}, "DBUtilErrorCode-02"),
        ("missing-split-key", QUERY, {"querySqlSplitPk": "absent_key"}, "MYSQLErrCode-06"),
        ("noninteger-split-key", QUERY, {"querySqlSplitPk": "payload"}, "DBUtilErrorCode-04"),
        ("negative-timeout", QUERY, {"queryTimeout": -1}, "DBUtilErrorCode-02"),
        ("multiple-statements", QUERY + "; SELECT 1", {"querySqlSplitPk": "id"}, "DBUtilErrorCode-02"),
    ]
    for name, query, extra, code in failures:
        config = job(False, 4, "check_target", query)
        config["job"]["content"][0]["reader"]["parameter"].update(extra)
        config["job"]["content"][0]["writer"] = {"name": "streamwriter", "parameter": {"print": False}}
        seconds = run(runtime, config, output, name, expect_success=False)
        assert code in (output / (name + ".log")).read_text(), name
        if name == "query-timeout":
            assert seconds < 8, seconds
        results.append({"case": name, "seconds": seconds, "expected_error": code, "passed": True})

    for name, queries, success in [
            ("dryrun-valid", [QUERY, QUERY + " WHERE id<10"], True),
            ("dryrun-second-query-missing-column", [QUERY, "SELECT nonexistent FROM source_data"], False)]:
        config = job(False, 4, "check_target")
        config["job"]["setting"]["dryRun"] = True
        config["job"]["content"][0]["reader"]["parameter"]["connection"][0]["querySql"] = queries
        config["job"]["content"][0]["writer"] = {"name": "streamwriter", "parameter": {"print": False}}
        seconds = run(runtime, config, output, name, expect_success=success)
        if not success: assert "MYSQLErrCode-06" in (output / (name + ".log")).read_text()
        results.append({"case": name, "seconds": seconds, "passed": True})
    for key, success in [("id", True), ("missing_key", False), ("payload", False)]:
        name = "dryrun-split-key-" + key
        config = job(True, 4, "check_target", QUERY + " WHERE id<10")
        config["job"]["setting"]["dryRun"] = True
        config["job"]["content"][0]["reader"]["parameter"]["querySqlSplitPk"] = key
        config["job"]["content"][0]["writer"] = {"name": "streamwriter", "parameter": {"print": False}}
        seconds = run(runtime, config, output, name, expect_success=success)
        if not success: assert "DBUtilErrorCode-04" in (output / (name + ".log")).read_text()
        results.append({"case": name, "seconds": seconds, "passed": True})
    for attempt in range(4):
        name = "connection-killed-%d" % (attempt + 1)
        config = job(False, 4, "check_target", "SELECT /*datax_fault*/ id, SLEEP(0.02) FROM source_data")
        config["job"]["content"][0]["writer"] = {"name": "streamwriter", "parameter": {"print": False}}
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(run, runtime, config, output, name, False)
            deadline = time.monotonic() + 10
            connection = ""
            while time.monotonic() < deadline:
                connection = sql("SELECT ID FROM information_schema.PROCESSLIST "
                                 "WHERE USER='datax' AND INFO LIKE 'SELECT /*datax_fault*/%' LIMIT 1")
                if connection: break
                time.sleep(0.05)
            assert connection.isdigit(), "reader query was not observed"
            sql("KILL CONNECTION " + connection)
            seconds = future.result(timeout=10)
        assert "DBUtilErrorCode-21" in (output / (name + ".log")).read_text()
        results.append({"case": name, "seconds": seconds, "passed": True})
    (output / "results.json").write_text(json.dumps(results, indent=2))
    print("PASS", len(results), "real MySQL checks", flush=True)


if __name__ == "__main__":
    main()
