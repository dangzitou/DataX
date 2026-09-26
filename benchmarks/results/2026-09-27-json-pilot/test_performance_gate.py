"""Synthetic checks for the gate itself, not performance measurements."""
from copy import deepcopy
from performance_gate import evaluate


report = {"rows": 100, "runs": [
    {"round": i, "variant": variant, "seconds": seconds,
     "expected": 100, "actual": 100, "mismatched_rows": 0}
    for i in range(6) for variant, seconds in [("baseline", 10.0), ("candidate", 5.0)]]}
assert evaluate(report)["passed"]
assert evaluate(report, "elapsed")["passed"]
one_slow = deepcopy(report)
one_slow["runs"][-1]["seconds"] = 8.0
assert not evaluate(one_slow)["passed"]  # Median still passes; slow pair must fail.
assert evaluate(report, threshold=100)["passed"]
assert not evaluate(report, "elapsed", threshold=51)["passed"]
for mutate in [lambda r: r.update(pending_run={"phase": "validating"}),
               lambda r: r["runs"].pop(),
               lambda r: r["runs"].append(r["runs"][-1]),
               lambda r: r["runs"][-1].update(mismatched_rows=1),
               lambda r: r["runs"][-1].update(seconds=float("nan")),
               lambda r: r["runs"][-1].update(seconds=0),
               lambda r: r["runs"].clear()]:
    invalid = deepcopy(report)
    mutate(invalid)
    try:
        evaluate(invalid)
    except ValueError:
        continue
    raise AssertionError("Invalid evidence accepted")
print("PASS performance gate checks (synthetic)")
