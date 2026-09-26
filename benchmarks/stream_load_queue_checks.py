#!/usr/bin/env python3
"""Real manager/HTTP-client queue failure checks; simulated server, not database/Engine."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-unsafe', action='store_true')
    parser.add_argument('--backends', default='starrocks,doris',
                        help='Comma-separated installed backends; optionally include selectdb')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    classpath = os.pathsep.join([str(runtime / 'lib/*'),
        str(runtime / 'plugin/writer/starrockswriter/*'), str(runtime / 'plugin/writer/doriswriter/*'),
        str(runtime / 'plugin/writer/selectdbwriter/*')])
    source = Path(__file__).with_name('StreamLoadQueueFailureCheck.java')
    with (output / 'compile.log').open('w') as log:
        subprocess.run([str(java / 'javac'), '-encoding', 'UTF-8', '-cp', classpath,
                        '-d', str(output), str(source)], stdout=log, stderr=subprocess.STDOUT, check=True)
    with (output / 'checks.log').open('w') as log:
        result = subprocess.run([str(java / 'java'), '-cp', str(output) + os.pathsep + classpath,
            'StreamLoadQueueFailureCheck', args.backends, *(['--expect-unsafe'] if args.expect_unsafe else [])],
            stdout=log, stderr=subprocess.STDOUT, timeout=240)
    entries = [json.loads(line[7:]) for line in (output / 'checks.log').read_text().splitlines()
               if line.startswith('RESULT ')]
    report = {'scope': __doc__, 'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'results': entries, 'exit_code': result.returncode, 'expect_unsafe': args.expect_unsafe}
    (output / 'results.json').write_text(json.dumps(report, indent=2))
    assert result.returncode == 0 and len(entries) == len(args.backends.split(',')) * 4 * (1 if args.expect_unsafe else 5), str(output / 'checks.log')
    print(json.dumps(entries), flush=True)


if __name__ == '__main__':
    main()
