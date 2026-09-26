#!/usr/bin/env python3
"""Isolated queue throughput only: reused records, no JDBC, serialization or storage."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("baseline", type=Path)
p.add_argument("candidate", type=Path)
p.add_argument("output", type=Path)
args = p.parse_args()
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=True)
java = Path(os.environ["JAVA_HOME"]) / "bin"
subprocess.run([str(java / "javac"), "-cp", str(args.baseline.resolve() / "lib/*"),
                "-d", str(args.output), str(Path(__file__).with_name("ChannelBenchmark.java"))], check=True)
report = {"scope": "Isolated bounded queue; reused records; no database or storage. Not end-to-end.",
          "warmup_rows_per_process": 1048576, "runs": []}
for i in range(5):
    for variant in (["baseline", "candidate"] if i % 2 == 0 else ["candidate", "baseline"]):
        cp = str(args.output) + os.pathsep + str(getattr(args, variant).resolve() / "lib/*")
        result = subprocess.check_output([str(java / "java"), "-Xms1g", "-Xmx1g", "-cp", cp,
                                          "ChannelBenchmark"], text=True, timeout=60)
        (args.output / ("%d-%s.txt" % (i + 1, variant))).write_text(result)
        entry = json.loads(next(line[10:] for line in result.splitlines() if line.startswith("BENCHMARK ")))
        entry.update(round=i + 1, variant=variant)
        report["runs"].append(entry)
        print(json.dumps(entry), flush=True)
        (args.output / "results.json").write_text(json.dumps(report, indent=2))
report["median_seconds"] = {v: statistics.median(r["seconds"] for r in report["runs"] if r["variant"] == v)
                            for v in ("baseline", "candidate")}
report["throughput_gain_percent"] = (report["median_seconds"]["baseline"] /
                                     report["median_seconds"]["candidate"] - 1) * 100
(args.output / "results.json").write_text(json.dumps(report, indent=2))
print(report["median_seconds"], report["throughput_gain_percent"])
