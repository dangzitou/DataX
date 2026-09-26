#!/usr/bin/env python3
"""Real DataX Engine against a loopback HTTP fault simulator, NOT a database test."""
import argparse
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mysql_querysql import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-unsafe', action='store_true',
                        help='Reproduce historical acceptance of unknown/null/empty status')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    response, requests, polls = {}, [], []
    state = 'VISIBLE'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if '/get_load_state?' in self.path:
                polls.append(self.path)
            self.reply({'state': state, 'data': state})

        def do_PUT(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            requests.append({'label': self.headers.get('label'), 'bytes': len(body),
                             'sha256': hashlib.sha256(body).hexdigest()})
            self.reply(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    report = {'scope': __doc__, 'expect_unsafe': args.expect_unsafe,
              'build': json.loads((args.runtime / 'build-metadata.json').read_text()),
              'results': results}
    cases = [('unknown-%d' % i, {'Status': 'UnexpectedFailure'}, False, True)
             for i in range(1, 5)]
    cases += [('null', {'Status': None}, False, True),
              ('empty', {'Status': ''}, False, True),
              ('missing', {}, False, False),
              ('fail', {'Status': 'Fail', 'Message': 'Injected rejection'}, False, False),
              ('success', {'Status': 'Success'}, True, False),
              ('publish-timeout', {'Status': 'Publish Timeout'}, True, False),
              ('visible', {'Status': 'Label Already Exists'}, True, False),
              ('committed', {'Status': 'Label Already Exists'}, True, False)]
    try:
        for backend in ['starrocks', 'doris']:
            for case, response, success, historically_unsafe in cases:
                state = 'COMMITTED' if case == 'committed' else 'VISIBLE'
                requests.clear()
                polls.clear()
                name = backend + '-' + case
                config = {'core': {'container': {'job': {'sleepInterval': 200}}},
                          'job': {'setting': {'speed': {'channel': 1}, 'errorLimit': {'record': 0}},
                                  'content': [{'reader': {'name': 'streamreader', 'parameter': {
                                      'sliceRecordCount': 3, 'column': [{'type': 'long', 'value': 1},
                                                                      {'type': 'string', 'value': '中文😀'}]}},
                                      'writer': {'name': backend + 'writer', 'parameter': {
                                          'username': 'test', 'password': '', 'column': ['id', 'txt'],
                                          'loadUrl': ['127.0.0.1:%d' % server.server_port],
                                          'connection': [{'selectedDatabase': 'test', 'table': ['target']}]
                                      }}}]}}
                expected_success = success or (args.expect_unsafe and historically_unsafe)
                seconds = run(args.runtime.resolve(), config, output, name, expected_success)
                assert requests and len({r['sha256'] for r in requests}) == 1, requests
                assert requests[0]['sha256'] == hashlib.sha256(('1\t中文😀\n' * 3).encode()).hexdigest()
                # Recovery must reuse a label; Publish Timeout must never replay the batch.
                assert len({r['label'] for r in requests}) == 1, requests
                if expected_success:
                    assert len(requests) == 1, requests
                elif historically_unsafe:
                    assert 'unknown result status' in (output / (name + '.log')).read_text()
                assert len(polls) == (1 if case in ('visible', 'committed') else 0), polls
                results.append({'case': name, 'response': response, 'seconds': seconds,
                                'expected_success': expected_success, 'requests': list(requests),
                                'state_polls': list(polls),
                                'unsafe_success_reproduced': historically_unsafe and args.expect_unsafe})
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                print(json.dumps(results[-1]), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
