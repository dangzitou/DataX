#!/usr/bin/env python3
"""Real manager/HTTP-client queue failure checks; simulated server, not database/Engine."""
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
    parser.add_argument('--expect-unsafe', action='store_true')
    parser.add_argument('--backends', default='starrocks,doris',
                        help='Comma-separated installed backends; optionally include selectdb')
    parser.add_argument('--real-database', action='store_true',
                        help='Disposable real StarRocks/Doris with a proxy replaying the first server rejection')
    args = parser.parse_args()
    if args.real_database and args.backends not in ('starrocks', 'doris'):
        parser.error('--real-database requires exactly one StarRocks/Doris backend')
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
            'StreamLoadQueueFailureCheck', args.backends, *(['--expect-unsafe'] if args.expect_unsafe else []),
            *(['--real-database'] if args.real_database else [])],
            stdout=log, stderr=subprocess.STDOUT, timeout=240)
    entries = [json.loads(line[7:]) for line in (output / 'checks.log').read_text().splitlines()
               if line.startswith('RESULT ')]
    report = {'scope': __doc__, 'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'results': entries, 'exit_code': result.returncode, 'expect_unsafe': args.expect_unsafe,
              'java_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if args.real_database:
        report['scope'] = ('Real manager/HTTP client to disposable real database; proxy replays the first '
                           'server rejection on retries. No PG reader or full Engine in this check.')
        report['container'] = json.loads(subprocess.check_output(['docker', 'inspect', '--size',
            '--format', '{{json .}}', 'datax-perf-' + args.backends], text=True))
    (output / 'results.json').write_text(json.dumps(report, indent=2))
    assert result.returncode == 0 and len(entries) == len(args.backends.split(',')) * 4 * (1 if args.expect_unsafe or args.real_database else 5), str(output / 'checks.log')
    print(json.dumps({'checks': len(entries), 'exit_code': result.returncode, 'report': str(output / 'results.json')}), flush=True)


if __name__ == '__main__':
    main()
