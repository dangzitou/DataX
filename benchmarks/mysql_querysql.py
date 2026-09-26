#!/usr/bin/env python3
"""Real MySQL -> DataX -> MySQL benchmark. Only uses a dedicated test container.

Fixed public test credentials; never point this script at an existing database.
Reports whole process wall time, validates every output cell, and alternates AB/BA.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import time

CONTAINER = "datax-perf-mysql"
URL = "jdbc:mysql://127.0.0.1:23306/datax_bench?useSSL=false&useUnicode=true&characterEncoding=utf8&socketTimeout=120000"
COLUMNS = ["id", "tenant", "amount", "created", "optional_text", "payload"]
QUERY = "SELECT id, tenant, amount, created, optional_text, payload FROM source_data"


def sql(statement):
    return subprocess.check_output(
        ["docker", "exec", "-i", CONTAINER, "mysql", "-uroot", "-N", "-B",
         "--default-character-set=utf8mb4", "datax_bench"],
        input=statement, text=True).strip()


def seed(rows):
    sql("""CREATE TABLE IF NOT EXISTS digits (n INT PRIMARY KEY);
        INSERT IGNORE INTO digits VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9);
        DROP TABLE IF EXISTS source_data;
        CREATE TABLE source_data (
            id BIGINT PRIMARY KEY, tenant INT, amount DECIMAL(20,4), created DATETIME,
            optional_text VARCHAR(80), payload VARCHAR(256)) ENGINE=InnoDB;
        INSERT INTO source_data
        SELECT n, MOD(n, 1000), (n-500000)/100.0,
            TIMESTAMPADD(SECOND, MOD(n,31536000), '2024-01-01'),
            IF(MOD(n,17)=0,NULL,CONCAT('中文-',n)), REPEAT(MD5(n),8)
        FROM (SELECT 1+a.n+10*b.n+100*c.n+1000*d.n+10000*e.n+100000*f.n+1000000*g.n AS n
              FROM digits a CROSS JOIN digits b CROSS JOIN digits c CROSS JOIN digits d
              CROSS JOIN digits e CROSS JOIN digits f CROSS JOIN digits g) numbers
        WHERE n <= %d ORDER BY n;
        ANALYZE TABLE source_data;""" % rows)


def job(split, channels, destination, query=QUERY):
    reader = {"username": "datax", "password": "datax-local-benchmark",
              "connection": [{"jdbcUrl": [URL], "querySql": [query]}]}
    if split:
        reader["querySqlSplitPk"] = "id"
    writer = {"username": "datax", "password": "datax-local-benchmark", "writeMode": "insert",
              "column": COLUMNS, "batchSize": 1024,
              "connection": [{"jdbcUrl": URL, "table": [destination]}]}
    return {"core": {"container": {"job": {"sleepInterval": 200}, "taskGroup": {"channel": channels}}},
            "job": {"setting": {"speed": {"channel": channels}, "errorLimit": {"record": 0}},
                    "content": [{"reader": {"name": "mysqlreader", "parameter": reader},
                                 "writer": {"name": "mysqlwriter", "parameter": writer}}]}}


def run(runtime, config, output, name, expect_success=True):
    config_file = output / (name + ".json")
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2))
    java = str(Path(os.environ["JAVA_HOME"]) / "bin/java")
    command = [java, "-Xms1g", "-Xmx1g", "-Dfile.encoding=UTF-8", "-Ddatax.home=" + str(runtime),
               "-Dlogback.configurationFile=" + str(runtime / "conf/logback.xml"),
               "-cp", str(runtime / "lib/*"), "com.alibaba.datax.core.Engine",
               "-mode", "standalone", "-jobid", "-1", "-job", str(config_file)]
    if platform.system() == "Darwin":
        command = ["/usr/bin/time", "-l", *command]
    start = time.perf_counter()
    with (output / (name + ".log")).open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    seconds = time.perf_counter() - start
    if expect_success and result.returncode:
        raise RuntimeError("DataX failed: " + str(output / (name + ".log")))
    if not expect_success and result.returncode == 0:
        raise AssertionError("Invalid job unexpectedly succeeded: " + name)
    return seconds


def process_metrics(log):
    text = log.read_text()
    resident = re.search(r"(\d+)\s+maximum resident set size", text)
    cpu = re.search(r"([\d.]+) real\s+([\d.]+) user\s+([\d.]+) sys", text)
    metrics = {}
    if resident:
        metrics["peak_rss_mib"] = int(resident[1]) / 1024**2
    if cpu:
        metrics.update(user_cpu_seconds=float(cpu[2]), system_cpu_seconds=float(cpu[3]))
    return metrics


def validate(table, query):
    expected = int(sql("SELECT COUNT(*) FROM (" + query + ") q"))
    actual = int(sql("SELECT COUNT(*) FROM " + table))
    equal = " AND ".join("q.`%s` <=> t.`%s`" % (c, c) for c in COLUMNS)
    mismatch = int(sql("SELECT COUNT(*) FROM (" + query + ") q LEFT JOIN " + table +
                       " t ON q.id=t.id WHERE t.id IS NULL OR NOT (" + equal + ")"))
    assert expected == actual and mismatch == 0, (expected, actual, mismatch)
    return {"expected": expected, "actual": actual, "mismatched_rows": mismatch}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--rows", type=int, default=1000000)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--channels", type=int, default=4)
    p.add_argument("--seed", action="store_true")
    p.add_argument("--candidate-no-split", action="store_true", help="Same-config comparison without querySql splitting")
    p.add_argument("--baseline-manual-split", action="store_true", help="Compare with manually partitioned upstream querySql")
    args = p.parse_args()
    assert 1 <= args.rows <= 10000000 and args.rounds >= 1 and args.channels >= 1
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.seed:
        seed(args.rows)
    assert int(sql("SELECT COUNT(*) FROM source_data")) == args.rows
    report = {"host": platform.platform(), "database": sql("SELECT VERSION()"),
              "rows": args.rows, "channels": args.channels, "query": QUERY, "runs": [],
              "validation": "All cells compared with NULL-safe equality; target has a primary key.",
              "metric": "Process start to successful exit; validation and table reset excluded."}
    report["candidate_split"] = not args.candidate_no_split
    report["baseline_manual_split"] = args.baseline_manual_split
    report["builds"] = {v: json.loads((getattr(args, v).resolve() / "build-metadata.json").read_text())
                        for v in ["baseline", "candidate"]}
    for round_number in range(-1, args.rounds):
        order = ["baseline", "candidate"] if round_number % 2 == 0 else ["candidate", "baseline"]
        for variant in order:
            runtime = getattr(args, variant).resolve()
            table = "target_" + variant
            sql("DROP TABLE IF EXISTS " + table + "; CREATE TABLE " + table + " LIKE source_data;")
            name = ("warmup" if round_number < 0 else str(round_number + 1)) + "-" + variant
            config = job(variant == "candidate" and not args.candidate_no_split, args.channels, table)
            if variant == "baseline" and args.baseline_manual_split:
                boundaries = [1 + args.rows * i // args.channels for i in range(args.channels + 1)]
                config["job"]["content"][0]["reader"]["parameter"]["connection"][0]["querySql"] = [
                    QUERY + " WHERE id >= %d AND id < %d" % (boundaries[i], boundaries[i + 1])
                    for i in range(args.channels)]
            seconds = run(runtime, config, output, name)
            check = validate(table, QUERY)
            entry = {"round": round_number + 1, "variant": variant, "seconds": seconds,
                     "rows_per_second": args.rows / seconds, **check,
                     **process_metrics(output / (name + ".log"))}
            report["runs"].append(entry)
            print(json.dumps(entry), flush=True)
            (output / "results.json").write_text(json.dumps(report, indent=2))
    medians = {v: statistics.median(r["seconds"] for r in report["runs"]
                                   if r["round"] > 0 and r["variant"] == v)
               for v in ["baseline", "candidate"]}
    report["median_seconds"] = medians
    report["throughput_gain_percent"] = (medians["baseline"] / medians["candidate"] - 1) * 100
    report["elapsed_reduction_percent"] = (1 - medians["candidate"] / medians["baseline"]) * 100
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
