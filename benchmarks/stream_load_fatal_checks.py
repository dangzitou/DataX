#!/usr/bin/env python3
"""Actual HTTP-response allocation OOM and manager failure propagation; no database/Engine."""
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
    parser.add_argument('--backends', default='starrocks,doris,selectdb')
    parser.add_argument('--expect-stall', action='store_true')
    parser.add_argument('--modes', default='response,timer')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logging = output / 'logback.xml'
    logging.write_text('<configuration><root level="OFF"/></configuration>')
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    classpath = os.pathsep.join([str(runtime / 'lib/*')] +
        [str(runtime / 'plugin/writer' / (backend + 'writer') / '*') for backend in args.backends.split(',')])
    sources = [Path(__file__).with_name(name) for name in
               ['StreamLoadQueueFailureCheck.java', 'StreamLoadFatalErrorCheck.java']]
    with (output / 'compile.log').open('w') as log:
        subprocess.run([str(java / 'javac'), '-encoding', 'UTF-8', '-cp', classpath,
                        '-d', str(output), *map(str, sources)], stdout=log, stderr=subprocess.STDOUT, check=True)
    report = {'scope': __doc__, 'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'sources': {s.name: hashlib.sha256(s.read_bytes()).hexdigest() for s in sources},
              'expect_stall': args.expect_stall, 'results': []}
    try:
        for backend, mode in [(b, m) for b in args.backends.split(',') for m in args.modes.split(',')]:
            for attempt in range(1, 5):
                name = '%s-%s-%d' % (backend, mode, attempt)
                report['pending_run'] = name
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                command = [str(java / 'java'), '-Xms32m', '-Xmx128m', '-XX:NewRatio=4',
                           '-Dlogback.configurationFile=' + str(logging), '-cp', str(output) + os.pathsep + classpath,
                           'StreamLoadFatalErrorCheck', backend, mode]
                if args.expect_stall: command.append('--expect-stall')
                with (output / (name + '.log')).open('w') as log:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=40)
                entries = [json.loads(line[7:]) for line in (output / (name + '.log')).read_text().splitlines()
                           if line.startswith('RESULT ')]
                assert result.returncode == 0 and len(entries) == 1, name
                report['results'].append(dict(entries[0], attempt=attempt, command=command))
                del report['pending_run']
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                print(json.dumps(report['results'][-1]), flush=True)
    finally:
        (output / 'results.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
