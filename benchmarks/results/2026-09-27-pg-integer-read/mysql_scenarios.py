#!/usr/bin/env python3
"""Paired non-querySql benchmarks using the same disposable MySQL fixture.

Reuses mysql_querysql.py's runtime, settings, timing and database checks.
File output uses the existing streamwriter in file mode, not txtfilewriter.
The write-only case generates a constant record; it is an isolation experiment,
not a varied production source. No source or target durability is weakened.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess

from mysql_querysql import COLUMNS, CONTAINER, QUERY, job, process_metrics, run, sql, validate
from performance_gate import evaluate

SCENARIOS = ("table-single", "table-parallel", "mysql-to-file", "stream-to-mysql")


def fingerprint(file):
    digest = hashlib.sha256()
    rows = size = 0
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            rows += chunk.count(b"\n")
            size += len(chunk)
    return {"rows": rows, "bytes": size, "sha256": digest.hexdigest()}


def configuration(scenario, rows, output):
    channels = 4 if scenario in ("table-parallel", "stream-to-mysql") else 1
    config = job(False, channels, "scenario_target")
    content = config["job"]["content"][0]
    reader = content["reader"]["parameter"]
    reader["column"] = COLUMNS
    connection = reader["connection"][0]
    del connection["querySql"]
    connection["table"] = ["source_data"]
    if scenario == "table-parallel":
        reader["splitPk"] = "id"  # Keep the upstream default splitFactor on BOTH sides.
    if scenario == "mysql-to-file":
        content["writer"] = {"name": "streamwriter", "parameter": {
            "path": str(output), "fileName": "actual.tsv", "print": False}}
    if scenario == "stream-to-mysql":
        assert rows % channels == 0, "Rows must be divisible by the channel count"
        content["reader"] = {"name": "streamreader", "parameter": {
            "sliceRecordCount": rows // channels,
            "column": [{"type": "long", "value": "1"},
                       {"type": "long", "value": "7"},
                       {"type": "double", "value": "123.4500"},
                       {"type": "date", "value": "2024-01-01 00:00:00"},
                       {"type": "string", "value": "中文-constant"},
                       {"type": "string", "value": "abcd" * 64}]}}
    return config, channels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    assert args.rows > 0 and args.rounds >= 1
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / "results.json").exists(), "Choose a fresh evidence directory"
    assert int(sql("SELECT COUNT(*) FROM source_data")) == args.rows
    config, channels = configuration(args.scenario, args.rows, output)
    report = {"scenario": args.scenario, "host": platform.platform(),
              "database": sql("SELECT VERSION()"), "rows": args.rows, "channels": channels,
              "runs": [], "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "builds": {v: json.loads((getattr(args, v).resolve() / "build-metadata.json").read_text())
                         for v in ("baseline", "candidate")},
              "metric": "Whole JVM process; reset, source export and validation excluded.",
              "validation": "MySQL target: count and all fields; file: whole-file SHA256, bytes and row count.",
              "scope": "Generated data, fixed local resources; no querySql or querySqlSplitPk configuration."}
    expected_file = None
    if args.scenario == "mysql-to-file":
        # This fixture is scanned in PK order by both single-task readers. The
        # byte comparison also checks that observed order, not an API guarantee.
        expected_path = output / "expected.tsv"
        with expected_path.open("wb") as target:
            subprocess.run(["docker", "exec", CONTAINER, "mysql", "-uroot", "-N", "-B", "--raw",
                            "--default-character-set=utf8mb4", "datax_bench", "-e",
                            "SELECT id,tenant,amount,DATE_FORMAT(created,'%Y-%m-%d %H:%i:%s'),"
                            "IFNULL(optional_text,'null'),payload FROM source_data ORDER BY id"],
                           stdout=target, check=True)
        expected_file = fingerprint(expected_path)
        assert expected_file["rows"] == args.rows
        report["expected_file"] = expected_file
        expected_path.unlink()
    for round_number in range(args.rounds + 1):
        order = ("candidate", "baseline") if round_number % 2 == 0 else ("baseline", "candidate")
        for variant in order:
            name = (str(round_number) if round_number else "warmup") + "-" + variant
            if args.scenario != "mysql-to-file":
                sql("DROP TABLE IF EXISTS scenario_target; CREATE TABLE scenario_target LIKE source_data;")
                if args.scenario == "stream-to-mysql":
                    sql("ALTER TABLE scenario_target DROP PRIMARY KEY")
            seconds = run(getattr(args, variant).resolve(), config, output, name)
            if args.scenario == "mysql-to-file":
                actual_file = fingerprint(output / "actual.tsv")
                assert actual_file == expected_file, (actual_file, expected_file)
                check = {"expected": args.rows, "actual": actual_file["rows"],
                         "mismatched_rows": 0, "file": actual_file}
                (output / "actual.tsv").unlink()
            elif args.scenario == "stream-to-mysql":
                count, mismatches = map(int, sql(
                    "SELECT COUNT(*),SUM(NOT (id <=> 1 AND tenant <=> 7 AND amount <=> 123.4500 "
                    "AND created <=> '2024-01-01 00:00:00' AND optional_text <=> '中文-constant' "
                    "AND payload <=> REPEAT('abcd',64))) FROM scenario_target").split())
                assert count == args.rows and mismatches == 0, (count, mismatches)
                check = {"expected": args.rows, "actual": count, "mismatched_rows": mismatches}
            else:
                check = validate("scenario_target", QUERY)
            entry = {"round": round_number, "variant": variant, "seconds": seconds,
                     "rows_per_second": args.rows / seconds, **check,
                     **process_metrics(output / (name + ".log"))}
            report["runs"].append(entry)
            (output / "results.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
    medians = {v: statistics.median(r["seconds"] for r in report["runs"]
                                   if r["round"] > 0 and r["variant"] == v)
               for v in ("baseline", "candidate")}
    report.update(median_seconds=medians,
                  throughput_gain_percent=(medians["baseline"] / medians["candidate"] - 1) * 100,
                  elapsed_reduction_percent=(1 - medians["candidate"] / medians["baseline"]) * 100)
    (output / "results.json").write_text(json.dumps(report, indent=2))
    (output / "gate.json").write_text(json.dumps(evaluate(report), indent=2))
    print(json.dumps({"scenario": args.scenario, "median_seconds": medians,
                      "throughput_gain_percent": report["throughput_gain_percent"]}), flush=True)


if __name__ == "__main__":
    main()
