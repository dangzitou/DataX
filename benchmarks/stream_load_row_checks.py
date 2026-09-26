#!/usr/bin/env python3
"""Real PostgreSQL -> StarRocks/Doris row-loss detection, using only three source rows."""
import argparse
import copy
import json
import subprocess
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
    load_port = 28040 if args.backend == 'starrocks' else 28041
    metadata = {'scope': __doc__, 'backend': args.backend,
                'backends': execute('SHOW BACKENDS'), 'postgres': sql('SELECT version()'),
                'image': subprocess.check_output(['docker', 'inspect', '--format', '{{.Image}}',
                                                  container], text=True).strip(),
                'baseline': json.loads((args.baseline / 'build-metadata.json').read_text()),
                'candidate': json.loads((args.candidate / 'build-metadata.json').read_text())}
    results = []
    sql('DROP TABLE IF EXISTS pg_load_rows; CREATE TABLE pg_load_rows(id bigint, amount text); '
        "INSERT INTO pg_load_rows VALUES (1,'7'),(2,'bad'),(3,'9')")
    execute('CREATE DATABASE IF NOT EXISTS datax_bench; DROP TABLE IF EXISTS datax_bench.load_rows; '
            'CREATE TABLE datax_bench.load_rows(id BIGINT,amount BIGINT NULL) DUPLICATE KEY(id) '
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")')
    config = job(query='SELECT * FROM pg_load_rows ORDER BY id')
    config['job']['content'][0]['writer'] = {'name': args.backend + 'writer', 'parameter': {
        'username': 'root', 'password': '', 'column': ['id', 'amount'],
        'loadUrl': ['127.0.0.1:%d' % load_port],
        'connection': [{'selectedDatabase': 'datax_bench', 'table': ['load_rows']}]
    }}

    def check(variant, runtime, scenario, attempt):
        # Refuse further tests if this owned disposable layer unexpectedly grows.
        size = int(subprocess.check_output(['docker', 'inspect', '--size', '--format',
                                             '{{.SizeRw}}', container], text=True))
        if size > 4 * 1024**3:
            raise RuntimeError('Disposable OLAP layer exceeds 4 GiB; recycle it before continuing')
        execute('TRUNCATE TABLE datax_bench.load_rows')
        sql("UPDATE pg_load_rows SET amount='%s' WHERE id=2" %
            ('8' if scenario in ('unselected', 'complete') else 'bad'))
        candidate = copy.deepcopy(config)
        props = {'strict_mode': True, 'max_filter_ratio': 1 if scenario == 'filtered' else 0}
        if scenario == 'unselected':
            props['where'] = 'id <> 2'
        candidate['job']['content'][0]['writer']['parameter']['loadProps'] = props
        expected_success = scenario == 'complete' or (
            variant == 'baseline' and scenario in ('filtered', 'unselected'))
        name = '%s-%s-%d' % (variant, scenario, attempt)
        seconds = run(runtime.resolve(), candidate, output, name, expected_success)
        actual = execute('SELECT id,amount FROM datax_bench.load_rows ORDER BY id,amount')
        expected = {'filtered': '1\t7\n3\t9', 'unselected': '1\t7\n3\t9',
                    'complete': '1\t7\n2\t8\n3\t9', 'strict-reject': ''}[scenario]
        assert actual == expected, (name, actual, expected)
        log = (output / (name + '.log')).read_text()
        if not expected_success and scenario != 'strict-reject':
            assert 'Incomplete Stream Load' in log, name
            assert log.count('Executing stream load to:') == 1, 'Partial batch must not be retried'
        results.append({'case': name, 'seconds': seconds, 'expected_success': expected_success,
                        'source_rows': 3, 'actual': actual,
                        'target_rows': len(actual.splitlines()) if actual else 0,
                        'silent_row_loss_reproduced': expected_success and scenario != 'complete',
                        'partial_commit_remains': scenario in ('filtered', 'unselected'),
                        'container_bytes_before_run': size})
        (output / 'results.json').write_text(json.dumps({'metadata': metadata, 'results': results}, indent=2))
        print(json.dumps(results[-1]), flush=True)

    for variant, runtime in [('baseline', args.baseline), ('candidate', args.candidate)]:
        for scenario in ['filtered', 'unselected']:
            for attempt in range(1, 5):
                check(variant, runtime, scenario, attempt)
        check(variant, runtime, 'strict-reject', 1)
    for attempt in range(1, 5):
        check('candidate', args.candidate, 'complete', attempt)


if __name__ == '__main__':
    main()
