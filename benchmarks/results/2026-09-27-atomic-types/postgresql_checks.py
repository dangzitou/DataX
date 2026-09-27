#!/usr/bin/env python3
"""Real PostgreSQL field fidelity and failed-job audit; never a production proof."""
import argparse
import json
import subprocess
from pathlib import Path
from mysql_querysql import run

URL = "jdbc:postgresql://127.0.0.1:25432/datax_bench"
COLUMNS = ["id", "signed", "amount", "ts", "tz", "flag", "txt", "bin", "day"]


def sql(query):
    return subprocess.check_output([
        "docker", "exec", "-i", "datax-perf-postgres", "psql", "-X", "-q", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "datax_bench"],
        input=query, text=True).strip()


def job(destination="pg_target", query="SELECT * FROM pg_source ORDER BY id", channels=1):
    credentials = {"username": "postgres", "password": "datax-local-benchmark"}
    return {"common": {"column": {"timeZone": "UTC"}},
        "core": {"container": {"job": {"sleepInterval": 200}, "taskGroup": {"channel": channels}}},
        "job": {"setting": {"speed": {"channel": channels}, "errorLimit": {"record": 0}},
            "content": [{"reader": {"name": "postgresqlreader", "parameter": {
                **credentials, "fetchSize": 128,
                "connection": [{"jdbcUrl": [URL], "querySql": [query]}]}},
                "writer": {"name": "postgresqlwriter", "parameter": {
                    **credentials, "column": COLUMNS, "batchSize": 2,
                    "connection": [{"jdbcUrl": URL, "table": [destination]}]}}}]}}


def validate(destination="pg_target", source="pg_source"):
    # Compare multiplicities in both directions, including NULL and every field.
    # Unlike counts/hashes alone, EXCEPT ALL detects duplicated, missing and changed rows.
    columns = ",".join(COLUMNS)
    result = json.loads(sql("SELECT json_build_object('expected', (SELECT count(*) FROM " + source +
        "), 'actual', (SELECT count(*) FROM " + destination +
        "), 'missing_or_changed', (SELECT count(*) FROM (SELECT " + columns + " FROM " + source +
        " EXCEPT ALL SELECT " + columns + " FROM " + destination +
        ") d), 'extra_or_changed', (SELECT count(*) FROM (SELECT " + columns + " FROM " + destination +
        " EXCEPT ALL SELECT " + columns + " FROM " + source + ") d))"))
    result["exact_match"] = (result["missing_or_changed"] == 0 and result["extra_or_changed"] == 0)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sql("""DROP TABLE IF EXISTS pg_source CASCADE;
        CREATE TABLE pg_source (id bigint PRIMARY KEY, signed bigint, amount numeric(38,18),
            ts timestamp(6), tz timestamptz(6), flag boolean, txt text, bin bytea, day date);
        INSERT INTO pg_source VALUES
        (1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
        (2,-9223372036854775808,-12345678901234567890.123456789012345678,
         '2024-02-29 12:34:56.123456','2024-02-29 12:34:56.123456+08',false,
         '中文😀' || chr(10) || chr(9) || chr(92) || chr(34),decode('0001275c0a0dff','hex'),'2024-02-29'),
        (3,9223372036854775807,0.000000000000000001,
         '1969-12-31 23:59:59.999999','1969-12-31 23:59:59.999999-05',true,'',decode('ff00','hex'),'1969-12-31'),
        (4,0,99999999999999999999.999999999999999999,
         '2024-01-01 00:00:00.000001','2024-01-01 00:00:00.000001+00',NULL,'plain',decode('','hex'),'2000-01-01');""")
    metadata = {"postgres": sql("SELECT version()"), "server_encoding": sql("SHOW server_encoding"),
        "fsync": sql("SHOW fsync"), "synchronous_commit": sql("SHOW synchronous_commit"),
        "baseline": json.loads((args.baseline / "build-metadata.json").read_text()),
        "candidate": json.loads((args.candidate / "build-metadata.json").read_text())}
    results = []
    def record(entry):
        results.append(entry)
        (output / "results.json").write_text(json.dumps({"metadata": metadata, "results": results}, indent=2))
        print(json.dumps(entry), flush=True)

    for variant, runtime in [("baseline", args.baseline), ("candidate", args.candidate)]:
        for attempt in range(4):
            name = "%s-types-%d" % (variant, attempt + 1)
            sql("DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source INCLUDING ALL)")
            seconds = run(runtime.resolve(), job(), output, name)
            check = validate()
            record({"case": name, "seconds": seconds, **check})
            if variant == "candidate":
                assert check["exact_match"], check
            else:
                assert not check["exact_match"], "Historical baseline regression no longer reproduced"

    # No target PK: a full rerun appends the same source again. Record this hazard,
    # rather than claiming that a successful job has exactly-once semantics.
    sql("DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source)")
    for attempt in range(2):
        name = "candidate-append-rerun-%d" % (attempt + 1)
        seconds = run(args.candidate.resolve(), job(), output, name)
        check = validate()
        record({"case": name, "seconds": seconds, **check})
        assert check["actual"] == 4 * (attempt + 1)
        assert check["extra_or_changed"] == 4 * attempt

    # A duplicate after the first committed batch must fail with errorLimit=0.
    # Successful earlier batches remain; a failed job is not an atomic rollback.
    for attempt in range(4):
        name = "candidate-failed-job-partial-commit-%d" % (attempt + 1)
        sql("DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source INCLUDING ALL)")
        query = "SELECT * FROM pg_source UNION ALL SELECT * FROM pg_source WHERE id=4 ORDER BY id"
        seconds = run(args.candidate.resolve(), job(query=query), output, name, expect_success=False)
        check = validate()
        record({"case": name, "seconds": seconds, "expected_job_failure": True, **check})
        assert check["actual"] > 0, "Expected previously committed rows to remain"
        assert "脏" in (output / (name + ".log")).read_text()


if __name__ == "__main__":
    main()
