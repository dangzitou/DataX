#!/usr/bin/env python3
"""Real PG/MySQL writes with simulated driver acknowledgement failures; not a network/Engine test."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-legacy', action='store_true')
    parser.add_argument('--backend', choices=['postgresql', 'mysql'], default='postgresql')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name('JdbcCommitFaultCheck.java')
    classes = output / 'classes'
    classes.mkdir(exist_ok=True)
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    classpath = os.pathsep.join([str(runtime/'lib/*'), str(runtime/'plugin/writer'/
                            (args.backend+'writer')/'*')])
    with (output/'compile.log').open('w') as log:
        subprocess.run([str(java/'javac'), '-encoding', 'UTF-8', '-cp', classpath,
                        '-d', str(classes), str(source)], stdout=log, stderr=subprocess.STDOUT, check=True)
    with (output/'checks.log').open('w') as log:
        completed = subprocess.run([str(java/'java'), '-cp', str(classes)+os.pathsep+classpath,
                                   'JdbcCommitFaultCheck', *(['--expect-legacy'] if args.expect_legacy else []),
                                   *(['--mysql'] if args.backend == 'mysql' else [])],
                                  stdout=log, stderr=subprocess.STDOUT, timeout=120)
    results = [json.loads(line[7:]) for line in (output/'checks.log').read_text().splitlines()
               if line.startswith('RESULT ')]
    report = {'scope': __doc__, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'build': json.loads((runtime/'build-metadata.json').read_text()), 'results': results,
              'exit_code': completed.returncode, 'expect_legacy': args.expect_legacy, 'backend': args.backend}
    (output/'results.json').write_text(json.dumps(report, indent=2))
    assert completed.returncode == 0 and len(results) == 16, output/'checks.log'
    print('PASS', len(results), __doc__)


if __name__ == '__main__':
    main()
