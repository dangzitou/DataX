#!/usr/bin/env python3
"""Fail unless EVERY measured pair meets the requested gain and data checks.

This accepts only the measured workload. It cannot guarantee future runs or
other databases, queries, data distributions, concurrency limits or hardware.
"""
import argparse
import json
import math
from pathlib import Path


def evaluate(report, metric="throughput", threshold=50.0, minimum_rounds=5):
    if report.get("pending_run"):
        raise ValueError("A started run has not completed data validation")
    rounds = {}
    for run in report["runs"]:
        if run["round"] == 0:  # Exclude declared warmups only.
            continue
        if run["round"] < 1 or run["variant"] not in ("baseline", "candidate"):
            raise ValueError("Invalid round or variant")
        pair = rounds.setdefault(run["round"], {})
        if run["variant"] in pair:
            raise ValueError("Duplicate variant in a round")
        if not math.isfinite(run["seconds"]) or run["seconds"] <= 0:
            raise ValueError("Invalid duration")
        if not (run["expected"] == run["actual"] == report["rows"] > 0
                and run["mismatched_rows"] == 0):
            raise ValueError("Data validation failed or empty workload")
        pair[run["variant"]] = run["seconds"]
    if sorted(rounds) != list(range(1, len(rounds) + 1)) or len(rounds) < minimum_rounds:
        raise ValueError("Incomplete or insufficient measured rounds")
    pairs = []
    for number, pair in sorted(rounds.items()):
        if set(pair) != {"baseline", "candidate"}:
            raise ValueError("Missing paired run")
        gain = ((pair["baseline"] / pair["candidate"] - 1) if metric == "throughput"
                else (1 - pair["candidate"] / pair["baseline"])) * 100
        pairs.append({"round": number, "gain_percent": gain, "passed": gain >= threshold})
    return {"metric": metric, "required_gain_percent": threshold, "rounds": pairs,
            "minimum_gain_percent": min(pair["gain_percent"] for pair in pairs),
            "passed": all(pair["passed"] for pair in pairs),
            "scope": "Measured workload and rounds only; not a universal or future guarantee."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--metric", choices=["throughput", "elapsed"], default="throughput")
    parser.add_argument("--threshold", type=float, default=50)
    parser.add_argument("--minimum-rounds", type=int, default=5)
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or args.threshold < 0 or args.minimum_rounds < 1:
        parser.error("Use a finite nonnegative threshold and positive minimum rounds")
    results = {}
    for file in args.reports:
        try:
            results[str(file)] = evaluate(json.loads(file.read_text()), args.metric,
                                          args.threshold, args.minimum_rounds)
        except (KeyError, ValueError, TypeError, OSError) as error:
            results[str(file)] = {"passed": False, "error": str(error)}
    print(json.dumps(results, indent=2))
    return 0 if all(result["passed"] for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
