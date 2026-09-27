#!/usr/bin/env python3
"""HTTP clients + hashing sink, not real databases or Engine; bounded heap, no payload files."""
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
    parser.add_argument('--expect-oom', action='store_true')
    parser.add_argument('--modes', default='redirect,replay,empty,large')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A DEBUG wire logger would otherwise write the whole 64 MiB fixture to disk.
    logging = output / 'logback.xml'
    logging.write_text('<configuration><appender name="console" class="ch.qos.logback.core.ConsoleAppender">'
                       '<encoder><pattern>%level %msg%n</pattern></encoder></appender>'
                       '<root level="WARN"><appender-ref ref="console"/></root></configuration>')
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    classpath = os.pathsep.join([str(runtime / 'lib/*')] +
        [str(runtime / 'plugin/writer' / (backend + 'writer') / '*') for backend in args.backends.split(',')])
    source = Path(__file__).with_name('StreamLoadPayloadCheck.java')
    with (output / 'compile.log').open('w') as log:
        subprocess.run([str(java / 'javac'), '-encoding', 'UTF-8', '-cp', classpath,
                        '-d', str(output), str(source)], stdout=log, stderr=subprocess.STDOUT, check=True)
    report = {'scope': __doc__, 'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'results': []}
    try:
        for backend in args.backends.split(','):
            for format_name in ['csv', 'json']:
                for mode in args.modes.split(','):
                    for attempt in range(1, 5):
                        name = '%s-%s-%s-%d' % (backend, format_name, mode, attempt)
                        report['pending_run'] = name
                        (output / 'results.json').write_text(json.dumps(report, indent=2))
                        command = [str(java / 'java'), '-Xms32m', '-Xmx128m', '-XX:NewRatio=4',
                                   '-Dlogback.configurationFile=' + str(logging),
                                   '-cp', str(output) + os.pathsep + classpath,
                                   'StreamLoadPayloadCheck', backend, format_name, mode]
                        if args.expect_oom and mode == 'large': command.append('--expect-oom')
                        with (output / (name + '.log')).open('w') as log:
                            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=90)
                        assert result.returncode == 0, name
                        entries = [json.loads(line[7:]) for line in (output / (name + '.log')).read_text().splitlines()
                                   if line.startswith('RESULT ')]
                        assert len(entries) == 1, name
                        report['results'].append(dict(entries[0], attempt=attempt, command=command))
                        del report['pending_run']
                        (output / 'results.json').write_text(json.dumps(report, indent=2))
                        print(json.dumps(entries[0]), flush=True)
    finally:
        (output / 'results.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
