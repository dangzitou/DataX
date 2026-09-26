#!/usr/bin/env python3
"""Real PG -> OLAP with a response proxy: injected timeout status / lost commit reply.

Only the reply is altered: the real server parses, filters and commits the rows.
This does not induce an actual backend Publish Timeout or prove whole-job recovery.
"""
import argparse
import copy
import gzip
import hashlib
import http.client
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import sql, job
from starrocks_checks import sr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--backend', choices=['starrocks', 'doris'], default='starrocks')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    container = 'datax-perf-' + args.backend
    execute = lambda query: sr(query, container)
    load_port, state_port = (28040, 28030) if args.backend == 'starrocks' else (28041, 28031)
    requests, polls = [], []
    scenario = ''

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def reply(self, code, body):
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'application/json')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def forward(self, method, port, body=None):
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ('host', 'expect', 'accept-encoding', 'connection')}
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
            try:
                connection.request(method, self.path, body, headers)
                response = connection.getresponse()
                payload = response.read()
                if response.getheader('Content-Encoding') == 'gzip':
                    payload = gzip.decompress(payload)
                return response.status, payload
            finally:
                connection.close()

        def do_GET(self):
            if '/get_load_state?' not in self.path:
                self.reply(200, b'{}')
                return
            code, body = self.forward('GET', state_port)
            polls.append({'path': self.path, 'code': code, 'response': json.loads(body)})
            self.reply(code, body)

        def do_PUT(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            code, reply = self.forward('PUT', load_port, body)
            result = json.loads(reply)
            requests.append({'label': self.headers.get('label'), 'bytes': len(body),
                             'sha256': hashlib.sha256(body).hexdigest(),
                             'code': code, 'server_response': result})
            if scenario.startswith('ack-lost') and len(requests) == 1:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if scenario.startswith('timeout'):
                result = dict(result, Status='Publish Timeout')
                reply = json.dumps(result).encode()
            self.reply(code, reply)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'scope': __doc__, 'backend': args.backend, 'results': [],
              'backends': execute('SHOW BACKENDS'), 'postgres': sql('SELECT version()'),
              'image': subprocess.check_output(['docker', 'inspect', '--format', '{{.Image}}',
                                                container], text=True).strip(),
              'baseline': json.loads((args.baseline / 'build-metadata.json').read_text()),
              'candidate': json.loads((args.candidate / 'build-metadata.json').read_text())}

    def save():
        (output / 'results.json').write_text(json.dumps(report, indent=2))

    try:
        sql('DROP TABLE IF EXISTS pg_load_recovery; '
            'CREATE TABLE pg_load_recovery(id bigint, amount text); '
            "INSERT INTO pg_load_recovery VALUES (1,'7'),(2,'bad'),(3,'9')")
        execute('CREATE DATABASE IF NOT EXISTS datax_bench; '
                'DROP TABLE IF EXISTS datax_bench.load_recovery; '
                'CREATE TABLE datax_bench.load_recovery(id BIGINT,amount BIGINT NULL) DUPLICATE KEY(id) '
                'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")')
        config = job(query='SELECT * FROM pg_load_recovery ORDER BY id')
        config['job']['content'][0]['writer'] = {'name': args.backend + 'writer', 'parameter': {
            'username': 'root', 'password': '', 'column': ['id', 'amount'],
            'loadUrl': ['127.0.0.1:%d' % server.server_port],
            'loadProps': {'strict_mode': True, 'max_filter_ratio': 1},
            'connection': [{'selectedDatabase': 'datax_bench', 'table': ['load_recovery']}]}}
        for variant, runtime in [('baseline', args.baseline), ('candidate', args.candidate)]:
            for scenario in ['timeout-filtered', 'ack-lost-filtered', 'timeout-complete', 'ack-lost-complete']:
                for attempt in range(1, 5):
                    size = int(subprocess.check_output(['docker', 'inspect', '--size', '--format',
                                                        '{{.SizeRw}}', container], text=True))
                    if size > 4 * 1024**3:
                        raise RuntimeError('Disposable OLAP layer exceeds 4 GiB')
                    execute('TRUNCATE TABLE datax_bench.load_recovery')
                    filtered = scenario.endswith('filtered')
                    sql("UPDATE pg_load_recovery SET amount='%s' WHERE id=2" % ('bad' if filtered else '8'))
                    requests.clear()
                    polls.clear()
                    name = '%s-%s-%d' % (variant, scenario, attempt)
                    expected_success = variant == 'baseline' or scenario == 'timeout-complete'
                    report['pending_run'] = {'case': name, 'expected_success': expected_success}
                    save()
                    seconds = run(runtime.resolve(), copy.deepcopy(config), output, name, expected_success)
                    actual = execute('SELECT id,amount FROM datax_bench.load_recovery ORDER BY id,amount')
                    expected = '1\t7\n3\t9' if filtered else '1\t7\n2\t8\n3\t9'
                    assert actual == expected, (name, actual, expected)
                    assert len(requests) == (2 if scenario.startswith('ack-lost') else 1), requests
                    assert len({r['label'] for r in requests}) == 1, requests
                    assert len({r['sha256'] for r in requests}) == 1, requests
                    first = requests[0]['server_response']
                    assert first['Status'] == 'Success', requests
                    assert first['NumberTotalRows'] == 3, requests
                    assert first['NumberLoadedRows'] == (2 if filtered else 3), requests
                    assert first['NumberFilteredRows'] == (1 if filtered else 0), requests
                    if scenario.startswith('ack-lost'):
                        assert requests[1]['server_response']['Status'] == 'Label Already Exists', requests
                        assert len(polls) == 1, polls
                    else:
                        assert not polls, polls
                    if not expected_success:
                        diagnostic = 'Unverified Stream Load' if scenario.startswith('ack-lost') else 'Incomplete Stream Load'
                        assert diagnostic in (output / (name + '.log')).read_text(), name
                    report['results'].append({'case': name, 'seconds': seconds,
                        'expected_success': expected_success, 'source_rows': 3, 'actual': actual,
                        'silent_row_loss_reproduced': filtered and expected_success,
                        'committed_rows_remain_on_failure': not expected_success,
                        'requests': list(requests), 'state_polls': list(polls),
                        'container_bytes_before_run': size})
                    del report['pending_run']
                    save()
                    print(json.dumps(report['results'][-1]), flush=True)
    finally:
        if 'pending_run' in report:
            report['pending_run'].update(requests=requests, state_polls=polls)
            save()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
