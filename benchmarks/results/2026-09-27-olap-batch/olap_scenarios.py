#!/usr/bin/env python3
"""Paired real StarRocks/Doris reader/writer benchmarks on a static PG fixture."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
from mysql_querysql import run, process_metrics
from mysql_scenarios import fingerprint
from performance_gate import evaluate
from postgresql_checks import sql, job
from postgresql_scenarios import COLUMNS, seed, validate as validate_pg
from starrocks_checks import sr


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--backend', choices=['starrocks', 'doris'], required=True)
    p.add_argument('--scenario', choices=['writer-csv', 'writer-json', 'writer-parallel',
                                         'reader-pg', 'reader-file'], required=True)
    p.add_argument('--rows', type=int, default=1000000)
    p.add_argument('--rounds', type=int, default=5)
    p.add_argument('--seed', action='store_true')
    p.add_argument('--baseline-batch-mib', type=int, default=5)
    p.add_argument('--candidate-batch-mib', type=int, default=5)
    p.add_argument('--max-generated-gib', type=float, default=6,
                   help='Stop before another run when active test database storage reaches this limit')
    args = p.parse_args()
    assert 0 < args.rows <= 1000000 and args.rounds > 0, 'Local fixtures are capped at one million rows'
    assert 0 < args.max_generated_gib <= 6, 'Keep the local test storage limit at or below 6 GiB'
    assert all(1 <= value <= 32 for value in [args.baseline_batch_mib, args.candidate_batch_mib]), \
        'Local batch-size controls are capped at 32 MiB'
    assert args.scenario.startswith('writer-') or (args.baseline_batch_mib == args.candidate_batch_mib == 5), \
        'Batch-size controls apply only to writer scenarios'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'results.json').exists(), 'Use a fresh evidence directory'
    backend, scenario = args.backend, args.scenario
    jdbc_port, load_port = (29030, 28040) if backend == 'starrocks' else (29031, 28041)
    def storage():
        container = json.loads(subprocess.check_output(
            ['docker', 'inspect', '--size', 'datax-perf-' + backend]))[0]
        pg_bytes = int(subprocess.check_output(['docker', 'exec', 'datax-perf-postgres',
            'du', '-sk', '/var/lib/postgresql/data'], text=True).split()[0]) * 1024
        used = container['SizeRw'] + pg_bytes
        # Reserve a full input/output batch of fixtures before starting a JVM.
        # This is a between-run guard, not a filesystem quota or production setting.
        assert used + 1024**3 <= args.max_generated_gib * 1024**3, (
            'Generated test storage limit reached; recreate only the disposable OLAP container', used)
        assert shutil.disk_usage(output).free >= 8 * 1024**3, 'Less than 8 GiB host disk headroom'
        return used
    def execute(query):
        return sr(query, 'datax-perf-' + backend)
    def create(table):
        execute('DROP TABLE IF EXISTS datax_bench.' + table + '; CREATE TABLE datax_bench.' + table +
                '(id BIGINT NOT NULL, tenant INT, amount DECIMAL(20,4), created DATETIME, '
                'message STRING, payload STRING) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 4 '
                'PROPERTIES("replication_num"="1");')
    def validate_olap(table):
        # DUPLICATE KEY preserves repeated rows. Exact distinct/range checks and
        # independently computed values detect duplicates, omissions and corruption.
        expected = ["IF(id%17=0,NULL,id%100)",
                    "IF(id%23=0,NULL,CAST(id*1.2345 AS DECIMAL(20,4)))",
                    "IF(id%29=0,NULL,TIMESTAMPADD(SECOND,id%86400,CAST('2024-01-01' AS DATETIME)))",
                    "IF(id%31=0,NULL,CONCAT('中文😀-',CAST(id AS STRING)))",
                    "REPEAT(MD5(CAST(id AS STRING)),8)"]
        equal = ' AND '.join('(`' + c + '` <=> ' + e + ')' for c, e in zip(COLUMNS[1:], expected))
        counts = execute('SELECT COUNT(*),COUNT(DISTINCT id),'
            'SUM(IF(id BETWEEN 1 AND %d AND %s,0,1)) FROM datax_bench.%s' % (args.rows, equal, table))
        actual, distinct, different = map(int, counts.split('\t'))
        assert actual == distinct == args.rows and different == 0, counts
        return {'expected': args.rows, 'actual': actual, 'distinct_keys': distinct, 'mismatched_rows': different}
    def write_config(table, format_name='json', channels=1):
        config = job(query='SELECT * FROM pg_perf_source ORDER BY id', channels=channels)
        content = config['job']['content'][0]
        reader = content['reader']['parameter']
        reader['fetchSize'] = 1024
        if channels > 1:
            del reader['connection'][0]['querySql']
            reader['connection'][0]['table'] = ['pg_perf_source']
            reader.update(column=COLUMNS, splitPk='id')
        content['writer'] = {'name': backend + 'writer', 'parameter': {
            'username': 'root', 'password': '', 'column': COLUMNS,
            'loadUrl': ['127.0.0.1:%d' % load_port],
            'connection': [{'table': [table], 'jdbcUrl': 'jdbc:mysql://127.0.0.1:%d/datax_bench' % jdbc_port,
                            'selectedDatabase': 'datax_bench'}],
            'maxBatchRows': 500000, ('maxBatchSize' if backend == 'starrocks' else 'batchSize'): 5*1024*1024,
            'flushQueueLength': 1,
            'loadProps': {'format': format_name, 'strict_mode': True, 'max_filter_ratio': 0,
                          **({'strip_outer_array': True} if format_name == 'json' else {})}}}
        return config

    initial_storage = storage()
    if args.seed:
        seed(args.rows)
    assert int(sql('SELECT count(*) FROM pg_perf_source')) == args.rows
    assert sql("SELECT encode(convert_to(message,'UTF8'),'hex') FROM pg_perf_source WHERE id=1") == 'e4b8ade69687f09f98802d31'
    execute('CREATE DATABASE IF NOT EXISTS datax_bench;')
    jvm_options = ['-Duser.timezone=UTC']
    if scenario.startswith('writer-'):
        create('olap_perf_target')
        config = write_config('olap_perf_target', 'csv' if scenario == 'writer-csv' else 'json',
                              4 if scenario == 'writer-parallel' else 1)
    else:
        create('olap_perf_source')
        source_config = write_config('olap_perf_source')
        run(args.candidate.resolve(), source_config, output, 'seed-olap', jvm_options=jvm_options)
        validate_olap('olap_perf_source')
        execute("CREATE USER IF NOT EXISTS datax IDENTIFIED BY 'datax-local-benchmark';")
        execute("GRANT SELECT ON ALL TABLES IN DATABASE datax_bench TO USER datax;" if backend == 'starrocks'
                else "GRANT SELECT_PRIV ON datax_bench.* TO 'datax';")
        config = job(destination='pg_perf_target')
        content = config['job']['content'][0]
        content['reader'] = {'name': backend + 'reader', 'parameter': {
            'username': 'datax', 'password': 'datax-local-benchmark', 'connection': [{
                'jdbcUrl': ['jdbc:mysql://127.0.0.1:%d/datax_bench?characterEncoding=utf8' % jdbc_port],
                'querySql': ['SELECT * FROM olap_perf_source ORDER BY id']}]}}
        content['writer']['parameter'].update(column=COLUMNS, batchSize=1024)
        if scenario == 'reader-file':
            content['writer'] = {'name': 'streamwriter', 'parameter': {
                'path': str(output), 'fileName': 'actual.tsv', 'print': False}}
    configs = {variant: copy.deepcopy(config) for variant in ['baseline', 'candidate']}
    if scenario.startswith('writer-'):
        for variant, size in [('baseline', args.baseline_batch_mib), ('candidate', args.candidate_batch_mib)]:
            configs[variant]['job']['content'][0]['writer']['parameter'][
                'maxBatchSize' if backend == 'starrocks' else 'batchSize'] = size * 1024**2
    report = {'backend': backend, 'scenario': scenario, 'rows': args.rows, 'runs': [],
        'config': configs['baseline'] if configs['baseline'] == configs['candidate'] else None,
        'host': platform.platform(), 'backends': execute('SHOW BACKENDS'),
        'configs_by_variant': configs,
        'batch_mib': {'baseline': args.baseline_batch_mib, 'candidate': args.candidate_batch_mib},
        'postgres': sql('SELECT version()'), 'jvm_options': jvm_options,
        'pg_durability': {key: sql('SHOW ' + key) for key in ['fsync', 'synchronous_commit', 'full_page_writes']},
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'builds': {v: json.loads((getattr(args, v).resolve() / 'build-metadata.json').read_text())
                   for v in ['baseline', 'candidate']},
        'metric': 'Whole JVM elapsed time, excluding seed/reset/validation; static synthetic source.'}
    report['storage_limit_gib'] = args.max_generated_gib
    report['initial_database_storage_bytes'] = initial_storage
    if scenario == 'reader-file':
        reference = output / 'reference.tsv'
        query = "COPY (SELECT id,coalesce(tenant::text,'null'),coalesce(amount::text,'null')," \
                "coalesce(created::text,'null'),coalesce(message,'null'),payload FROM pg_perf_source ORDER BY id) TO STDOUT"
        with reference.open('wb') as stream:
            subprocess.run(['docker', 'exec', '-i', 'datax-perf-postgres', 'psql', '-X', '-q', '-U', 'postgres',
                            '-d', 'datax_bench', '-v', 'ON_ERROR_STOP=1', '-c', query], stdout=stream, check=True)
        report['expected_file'] = fingerprint(reference)
        reference.unlink()
        assert report['expected_file']['rows'] == args.rows
    for number in range(args.rounds + 1):
        for variant in (['candidate', 'baseline'] if number % 2 == 0 else ['baseline', 'candidate']):
            name = ('warmup' if number == 0 else str(number)) + '-' + variant
            storage_before = storage()
            if scenario.startswith('writer-'):
                execute('TRUNCATE TABLE datax_bench.olap_perf_target;')
            elif scenario == 'reader-pg':
                sql('DROP TABLE IF EXISTS pg_perf_target; CREATE TABLE pg_perf_target (LIKE pg_perf_source INCLUDING ALL)')
            report['pending_run'] = {'round': number, 'variant': variant, 'name': name, 'phase': 'running'}
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            seconds = run(getattr(args, variant).resolve(), configs[variant], output, name, jvm_options=jvm_options)
            report['pending_run'].update(phase='validating', seconds=seconds,
                                         **process_metrics(output / (name + '.log')))
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            if scenario.startswith('writer-'):
                check = validate_olap('olap_perf_target')
                sizes = [int(value) for value in re.findall(
                    r"Executing stream load to:.*size: '(\d+)'", (output / (name + '.log')).read_text())]
                assert sizes, 'Missing Stream Load request-size evidence'
                check.update(stream_load_attempts=len(sizes), max_request_body_bytes=max(sizes),
                             attempted_body_bytes=sum(sizes))
            elif scenario == 'reader-pg':
                check = validate_pg(args.rows)
            else:
                actual = fingerprint(output / 'actual.tsv')
                assert actual == report['expected_file'], (actual, report['expected_file'])
                check = {'expected': args.rows, 'actual': actual['rows'], 'mismatched_rows': 0, 'file': actual}
                (output / 'actual.tsv').unlink()
            entry = {'round': number, 'variant': variant, 'seconds': seconds,
                     'rows_per_second': args.rows / seconds, **check,
                     'database_storage_before_bytes': storage_before,
                     **process_metrics(output / (name + '.log'))}
            report['runs'].append(entry)
            del report['pending_run']
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
    medians = {v: statistics.median(r['seconds'] for r in report['runs'] if r['round'] and r['variant'] == v)
               for v in ['baseline', 'candidate']}
    report.update(median_seconds=medians, throughput_gain_percent=(medians['baseline']/medians['candidate']-1)*100,
                  elapsed_reduction_percent=(1-medians['candidate']/medians['baseline'])*100)
    (output / 'results.json').write_text(json.dumps(report, indent=2))
    (output / 'gate.json').write_text(json.dumps(evaluate(report, minimum_rounds=args.rounds), indent=2))
    print(json.dumps({'medians': medians, 'throughput_gain_percent': report['throughput_gain_percent']}), flush=True)


if __name__ == '__main__':
    main()
