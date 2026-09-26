#!/usr/bin/env python3
"""Build a small, real DataX distribution without the legacy assembly plugin.

Use the same script and JDK 8 for both the untouched upstream and the candidate.
No source files or dependency versions are changed by this script.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    source, runtime = args.source.resolve(), args.runtime.resolve()
    modules = ["core", "mysqlreader", "mysqlwriter", "rdbmsreader", "streamreader", "streamwriter"]
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "build.log").open("w") as log:
        def maven(*arguments):
            subprocess.run(["mvn", "-B", *arguments], cwd=source, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        maven("-pl", ",".join(modules), "-am", "install", "-Dmaven.test.skip=true",
              "-Dassembly.skipAssembly=true")
        shutil.copytree(source / "core/src/main/conf", runtime / "conf", dirs_exist_ok=True)
        for module in modules:
            kind = "reader" if module.endswith("reader") else "writer"
            dest = runtime / "lib" if module == "core" else runtime / "plugin" / kind / module
            dest.mkdir(parents=True, exist_ok=True)
            for jar in dest.glob("*.jar"):
                jar.unlink()
            for jar in (source / module / "target").glob("*-0.0.1-SNAPSHOT.jar"):
                shutil.copy2(jar, dest)
            if module != "core":
                for resource in (source / module / "src/main/resources").glob("*.json"):
                    shutil.copy2(resource, dest)
            maven("-pl", module, "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:copy-dependencies",
                  "-DincludeScope=runtime", "-DoutputDirectory=" + str(dest))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    (runtime / "revision.txt").write_text(revision + "\n")
    (runtime / "build-metadata.json").write_text(json.dumps({
        "revision": revision,
        "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"], cwd=source)).hexdigest(),
        "jars": {str(p.relative_to(runtime)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(runtime.rglob("*.jar"))},
        "java": subprocess.check_output([str(Path(os.environ["JAVA_HOME"]) / "bin/java"), "-version"],
                                         stderr=subprocess.STDOUT, text=True)
    }, indent=2))
    print(runtime)


if __name__ == "__main__":
    main()
