#!/usr/bin/env python3
"""Real DataX Engine + PostgreSQL: drop the TCP connection after server COMMIT completion."""
import argparse
import json
from pathlib import Path
import socket
import struct
import threading
from mysql_querysql import run
from postgresql_checks import job, sql


class CommitDropProxy:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(.2)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.dropped = threading.Event()
        self.dropped_insert_rows = 0
        self.connections = []
        self.workers = []
        self.errors = []

    @staticmethod
    def exact(stream, count):
        data = bytearray()
        while len(data) < count:
            chunk = stream.recv(count-len(data))
            if not chunk:
                raise EOFError()
            data.extend(chunk)
        return bytes(data)

    def relay(self, client):
        server = socket.create_connection(('127.0.0.1', 25432), timeout=10)
        self.connections.extend([client, server])

        def requests():
            try:
                while not self.stopped.is_set():
                    data = client.recv(65536)
                    if not data:
                        break
                    server.sendall(data)
            except OSError:
                pass
            finally:
                try: server.shutdown(socket.SHUT_WR)
                except OSError: pass

        sender = threading.Thread(target=requests, daemon=True)
        sender.start()
        inserted = 0
        try:
            while not self.stopped.is_set():
                kind = self.exact(server, 1)
                length = self.exact(server, 4)
                size = struct.unpack('!I', length)[0]
                assert 4 <= size <= 64 * 1024 * 1024, size
                body = self.exact(server, size-4)
                if kind == b'C' and body.startswith(b'INSERT '):
                    inserted += int(body.rstrip(b'\0').split()[-1])
                # JDBC startup also commits. Only interrupt a transaction that inserted rows.
                if kind == b'C' and body == b'COMMIT\0' and inserted and not self.dropped.is_set():
                    self.dropped_insert_rows = inserted
                    self.dropped.set()
                    break  # Suppress COMMIT completion and close the connection.
                client.sendall(kind+length+body)
        except (EOFError, OSError):
            pass
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            for stream in [client, server]:
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                stream.close()
            sender.join(2)

    def accept(self):
        while not self.stopped.is_set():
            try: client, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            worker = threading.Thread(target=self.relay, args=(client,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def __enter__(self):
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.listener.close()
        for stream in self.connections:
            try: stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            stream.close()
        self.acceptor.join(2)
        for worker in self.workers:
            worker.join(2)
        assert not self.errors, self.errors
        assert not any(worker.is_alive() for worker in self.workers)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--expect-legacy', action='store_true')
    args = p.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    report = {'scope': __doc__, 'expect_legacy': args.expect_legacy,
              'build': json.loads((args.runtime/'build-metadata.json').read_text()), 'results': []}
    for attempt in range(1, 5):
        sql('DROP TABLE IF EXISTS pg_commit_wire_target; CREATE TABLE pg_commit_wire_target(id bigint,txt text)')
        name = 'commit-connection-drop-%d' % attempt
        with CommitDropProxy() as proxy:
            config = job(destination='pg_commit_wire_target',
                         query="SELECT i::bigint AS id, '中文😀-'||i AS txt FROM generate_series(1,4) i ORDER BY i")
            writer = config['job']['content'][0]['writer']['parameter']
            writer['column'] = ['id', 'txt']
            writer['connection'][0]['jdbcUrl'] = (
                'jdbc:postgresql://127.0.0.1:%d/datax_bench?sslmode=disable&ApplicationName=datax-commit-wire' % proxy.port)
            seconds = run(args.runtime.resolve(), config, output, name, expect_success=False)
            assert proxy.dropped.is_set(), 'COMMIT completion was never intercepted'
            assert proxy.dropped_insert_rows == 2, proxy.dropped_insert_rows
        data = json.loads(sql("SELECT coalesce(json_agg(t ORDER BY id),'[]') FROM pg_commit_wire_target t"))
        assert data == [{'id': i, 'txt': '中文😀-'+str(i)} for i in [1, 2]], data
        connections = int(sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='datax-commit-wire'"))
        assert connections == 0, connections
        diagnostic = 'DBUtilErrorCode-25' in (output/(name+'.log')).read_text()
        assert diagnostic != args.expect_legacy
        entry = {'case': name, 'seconds': seconds, 'commit_completion_dropped': True,
                 'acknowledged_inserts_before_drop': proxy.dropped_insert_rows,
                 'failed': True, 'input_rows': 4, 'committed_rows': data,
                 'remaining_writer_connections': connections, 'uncertain_commit_diagnostic': diagnostic}
        report['results'].append(entry)
        (output/'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(entry), flush=True)


if __name__ == '__main__':
    main()
