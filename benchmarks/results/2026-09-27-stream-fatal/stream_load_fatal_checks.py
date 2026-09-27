#!/usr/bin/env python3
"""Actual HTTP-response allocation OOM and manager failure propagation; no database/Engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def engine_checks(args, runtime, output, java, logging):
    """Run standalone Engine; timeout is an expected baseline failure, never a pass for the candidate."""
    assert set(args.backends.split(',')) <= {'starrocks', 'doris'}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

        def do_PUT(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            requests.append({'label': self.headers.get('label'), 'bytes': len(body),
                             'sha256': hashlib.sha256(body).hexdigest()})
            length = 80 * 1024 * 1024
            self.send_response(200)
            self.send_header('Content-Length', str(length))
            self.end_headers()
            chunk = b' ' * 8192
            try:
                for _ in range(length // len(chunk)):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'scope': 'Standalone DataX Engine with actual HTTP response allocation OOM; simulated HTTP, no database.',
              'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'expect_stall': args.expect_stall, 'results': []}
    try:
        for backend in args.backends.split(','):
            for attempt in range(1, 5):
                name = '%s-%d' % (backend, attempt)
                requests.clear()
                config = {'core': {'container': {'job': {'sleepInterval': 200}}},
                          'job': {'setting': {'speed': {'channel': 1}, 'errorLimit': {'record': 0}},
                                  'content': [{'reader': {'name': 'streamreader', 'parameter': {
                                      'sliceRecordCount': 8, 'column': [{'type': 'long', 'value': 1},
                                                                      {'type': 'string', 'value': '中文😀'}]}},
                                      'writer': {'name': backend + 'writer', 'parameter': {
                                          'username': 'test', 'password': '', 'column': ['id', 'txt'],
                                          'maxBatchRows': 1, 'flushQueueLength': 2,
                                          'loadUrl': ['127.0.0.1:%d' % server.server_port],
                                          'connection': [{'selectedDatabase': 'test', 'table': ['target']}]}}}]}}
                config_file = output / (name + '.json')
                config_file.write_text(json.dumps(config, indent=2))
                command = [str(java / 'java'), '-Xms32m', '-Xmx128m', '-XX:NewRatio=4',
                           '-Dfile.encoding=UTF-8', '-Ddatax.home=' + str(runtime),
                           '-Dlogback.configurationFile=' + str(logging), '-cp', str(runtime / 'lib/*'),
                           'com.alibaba.datax.core.Engine', '-mode', 'standalone', '-jobid', '-1',
                           '-job', str(config_file)]
                report['pending_run'] = name
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                timed_out, code = False, None
                start = time.monotonic()
                with (output / (name + '.log')).open('w') as log:
                    try:
                        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=10).returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True  # subprocess.run killed and reaped this directly launched JVM.
                log_text = (output / (name + '.log')).read_text()
                assert timed_out == args.expect_stall, name
                assert timed_out or code != 0, name
                assert 'OutOfMemoryError' in log_text and 'CharArrayBuffer' in log_text, name
                assert len(requests) == 1, (name, requests)
                report['results'].append({'backend': backend, 'attempt': attempt, 'timed_out': timed_out,
                                          'exit_code': code, 'seconds': time.monotonic() - start,
                                          'requests': list(requests), 'command': command})
                del report['pending_run']
                (output / 'results.json').write_text(json.dumps(report, indent=2))
    finally:
        (output / 'results.json').write_text(json.dumps(report, indent=2))
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--backends', default='starrocks,doris,selectdb')
    parser.add_argument('--expect-stall', action='store_true')
    parser.add_argument('--modes', default='response,timer')
    parser.add_argument('--engine', action='store_true', help='Standalone Engine, StarRocks/Doris only')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logging = output / 'logback.xml'
    logging.write_text('<configuration><appender name="console" class="ch.qos.logback.core.ConsoleAppender">'
                       '<encoder><pattern>%level %msg%n</pattern></encoder></appender>'
                       '<root level="ERROR"><appender-ref ref="console"/></root></configuration>')
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    if args.engine:
        engine_checks(args, runtime, output, java, logging)
        return
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
