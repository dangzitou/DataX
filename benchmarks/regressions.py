#!/usr/bin/env python3
"""Run the same regression classes against a built baseline or candidate runtime."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("runtime", type=Path)
p.add_argument("output", type=Path)
p.add_argument("--repeat", type=int, default=4)
p.add_argument("--expect-failure", action="store_true", help="Reproduce failures in the upstream baseline")
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
args.output.mkdir(parents=True, exist_ok=True)
jars = list(args.runtime.rglob("*.jar"))
repo = Path.home() / ".m2/repository"
for pattern in ["junit/junit/4.13.1/*.jar", "org/mockito/mockito-all/1.9.5/*.jar"]:
    jars.extend(repo.glob(pattern))
cp = os.pathsep.join(map(str, jars))
tests = sorted(root.glob("*/src/test/java/**/*RegressionTest.java"))
java = Path(os.environ["JAVA_HOME"]) / "bin"
subprocess.run([str(java / "javac"), "-encoding", "UTF-8", "-cp", cp, "-d", str(args.output),
                *map(str, tests)], check=True)
classes = [str(t).split("/src/test/java/")[1][:-5].replace("/", ".") for t in tests]
unexpected = False
for i in range(args.repeat):
    with (args.output / ("round-%d.txt" % (i + 1))).open("w") as log:
        r = subprocess.run([str(java / "java"), "-cp", str(args.output) + os.pathsep + cp,
                            "org.junit.runner.JUnitCore", *classes],
                           stdout=log, stderr=subprocess.STDOUT, timeout=60)
    print("round", i + 1, "exit", r.returncode, flush=True)
    unexpected |= (r.returncode == 0) if args.expect_failure else (r.returncode != 0)
sys.exit(1 if unexpected else 0)
