#!/usr/bin/env python3
"""Real DataX Engine against a loopback HTTP fault simulator, NOT a database test."""
import argparse
import hashlib
import json
import socket
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
    parser.add_argument('--expect-incomplete', action='store_true',
                        help='Reproduce historical acceptance of incomplete Success responses')
    parser.add_argument('--expect-unverified', action='store_true',
                        help='Reproduce unchecked Publish Timeout and existing-label recovery')
    parser.add_argument('--suite', choices=['all', 'status', 'rows', 'recovery'], default='all')
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
            if case.startswith('recovery-ack-lost') and len(requests) == 1:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            # A retry after a committed partial load can return a visible label.
            # The client must keep the original count failure, not recover to success.
            self.reply({'Status': 'Label Already Exists'}
                       if case.startswith('rows-') and len(requests) > 1 else response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    report = {'scope': __doc__, 'expect_unsafe': args.expect_unsafe,
              'expect_incomplete': args.expect_incomplete, 'suite': args.suite,
              'expect_unverified': args.expect_unverified,
              'build': json.loads((args.runtime / 'build-metadata.json').read_text()),
              'results': results}
    cases = [('unknown-%d' % i, {'Status': 'UnexpectedFailure'}, False, True)
             for i in range(1, 5)]
    cases += [('null', {'Status': None}, False, True),
              ('empty', {'Status': ''}, False, True),
              ('missing', {}, False, False),
              ('fail', {'Status': 'Fail', 'Message': 'Injected rejection'}, False, False),
              ('success', {'Status': 'Success', 'NumberTotalRows': 3, 'NumberLoadedRows': 3,
                           'NumberFilteredRows': 0, 'NumberUnselectedRows': 0}, True, False)]
    if args.suite in ('rows', 'recovery'):
        cases = []
    if args.suite in ('all', 'rows'):
        complete = {'Status': 'Success', 'NumberTotalRows': 3, 'NumberLoadedRows': 3,
                    'NumberFilteredRows': 0, 'NumberUnselectedRows': 0}
        changes = [('filtered-%d' % i, {'NumberLoadedRows': 2, 'NumberFilteredRows': 1})
                   for i in range(1, 5)]
        changes += [('unselected', {'NumberLoadedRows': 2, 'NumberUnselectedRows': 1}),
                    ('loaded', {'NumberLoadedRows': 2}),
                    ('extra-total', {'NumberTotalRows': 4}),
                    ('extra-loaded', {'NumberLoadedRows': 4}),
                    ('empty', {'NumberTotalRows': 0, 'NumberLoadedRows': 0}),
                    ('missing', {'NumberLoadedRows': 'REMOVE'}),
                    ('null', {'NumberFilteredRows': None}),
                    ('fractional', {'NumberFilteredRows': 0.1}),
                    ('overflow', {'NumberLoadedRows': 18446744073709551619}),
                    ('negative', {'NumberUnselectedRows': -1})]
        for name, change in changes:
            result = dict(complete, **change)
            if result.get('NumberLoadedRows') == 'REMOVE':
                del result['NumberLoadedRows']
            cases.append(('rows-' + name, result, False, True))
        cases += [('rows-complete', complete, True, False),
                  ('rows-numeric-strings', {k: str(v) for k, v in complete.items()}, True, False)]
    if args.suite in ('all', 'status', 'recovery'):
        complete = {'Status': 'Publish Timeout', 'NumberTotalRows': 3, 'NumberLoadedRows': 3,
                    'NumberFilteredRows': 0, 'NumberUnselectedRows': 0}
        for attempt in range(1, 5):
            for name, result, success in [
                ('timeout-complete', complete, True),
                ('timeout-filtered', dict(complete, NumberLoadedRows=2, NumberFilteredRows=1), False),
                ('timeout-missing', {'Status': 'Publish Timeout'}, False),
                ('visible', {'Status': 'Label Already Exists'}, False),
                ('committed', {'Status': 'Label Already Exists'}, False),
                ('ack-lost', {'Status': 'Label Already Exists'}, False),
            ]:
                cases.append(('recovery-%s-%d' % (name, attempt), result, success, not success))
    try:
        for backend in ['starrocks', 'doris']:
            for case, response, success, historically_unsafe in cases:
                state = 'COMMITTED' if case.startswith('recovery-committed') else 'VISIBLE'
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
                row_case = case.startswith('rows-')
                recovery_case = case.startswith('recovery-')
                expect_unsafe = (args.expect_unverified if recovery_case else
                                 args.expect_incomplete if row_case else args.expect_unsafe)
                expected_success = success or (expect_unsafe and historically_unsafe)
                seconds = run(args.runtime.resolve(), config, output, name, expected_success)
                assert requests and len({r['sha256'] for r in requests}) == 1, requests
                assert requests[0]['sha256'] == hashlib.sha256(('1\t中文😀\n' * 3).encode()).hexdigest()
                # Recovery must reuse a label; Publish Timeout must never replay the batch.
                assert len({r['label'] for r in requests}) == 1, requests
                expected_requests = 2 if case.startswith('recovery-ack-lost') else 1
                if expected_success:
                    assert len(requests) == expected_requests, requests
                elif historically_unsafe:
                    diagnostic = ('Unverified Stream Load' if recovery_case and 'timeout' not in case else
                                  'Incomplete Stream Load' if row_case or recovery_case else
                                  'unknown result status')
                    assert diagnostic in (output / (name + '.log')).read_text()
                    if row_case or recovery_case:
                        assert len(requests) == expected_requests, requests
                assert len(polls) == (1 if recovery_case and 'timeout' not in case else 0), polls
                results.append({'case': name, 'response': response, 'seconds': seconds,
                                'expected_success': expected_success, 'requests': list(requests),
                                'state_polls': list(polls),
                                'unsafe_success_reproduced': historically_unsafe and expect_unsafe})
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                print(json.dumps(results[-1]), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
