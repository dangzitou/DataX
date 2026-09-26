#!/usr/bin/env python3
"""Paired real PostgreSQL benchmarks, including explicit native-driver controls."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
from postgresql_checks import sql, job
from mysql_querysql import run, process_metrics
from mysql_scenarios import fingerprint
from performance_gate import evaluate

COLUMNS = ['id', 'tenant', 'amount', 'created', 'message', 'payload']
SCENARIOS = ['query-single', 'table-single', 'table-parallel', 'pg-to-file', 'stream-to-pg']


def seed(rows):
    sql('''DROP TABLE IF EXISTS pg_perf_source;
        CREATE TABLE pg_perf_source (id bigint PRIMARY KEY, tenant integer,
            amount numeric(20,4), created timestamp(0), message text, payload text);
        INSERT INTO pg_perf_source
        SELECT i, CASE WHEN i%%17=0 THEN NULL ELSE i%%100 END,
            CASE WHEN i%%23=0 THEN NULL ELSE i::numeric*12345/10000 END,
            CASE WHEN i%%29=0 THEN NULL ELSE timestamp '2024-01-01' + (i%%86400)*interval '1 second' END,
            CASE WHEN i%%31=0 THEN NULL ELSE '中文😀-'||i END,
            repeat(md5(i::text),8)
        FROM generate_series(1,%d) i;
        ANALYZE pg_perf_source;''' % rows)


def configuration(scenario, rows, output, rewrite):
    channels = 4 if scenario in ['table-parallel', 'stream-to-pg'] else 1
    config = job(destination='pg_perf_target', query='SELECT * FROM pg_perf_source ORDER BY id', channels=channels)
    content = config['job']['content'][0]
    reader, writer = content['reader']['parameter'], content['writer']['parameter']
    reader['fetchSize'] = 1024
    writer.update(column=COLUMNS, batchSize=1024)
    if rewrite:
        writer['connection'][0]['jdbcUrl'] += '?reWriteBatchedInserts=true'
    if scenario.startswith('table-'):
        connection = reader['connection'][0]
        del connection['querySql']
        connection['table'] = ['pg_perf_source']
        reader['column'] = COLUMNS
        if scenario == 'table-parallel':
            reader['splitPk'] = 'id'
    if scenario == 'pg-to-file':
        content['writer'] = {'name': 'streamwriter', 'parameter': {
            'path': str(output), 'fileName': 'actual.tsv', 'print': False}}
    if scenario == 'stream-to-pg':
        assert rows % channels == 0
        content['reader'] = {'name': 'streamreader', 'parameter': {
            'sliceRecordCount': rows // channels, 'column': [
                {'type': 'long', 'value': '1'}, {'type': 'long', 'value': '7'},
                {'type': 'double', 'value': '123.4500'},
                {'type': 'date', 'value': '2024-01-01 00:00:00'},
                {'type': 'string', 'value': '中文😀-constant'},
                {'type': 'string', 'value': 'abcd' * 64}]}}
    return config, channels


def validate(rows, constant=False):
    actual = int(sql('SELECT count(*) FROM pg_perf_target'))
    if constant:
        different = int(sql("SELECT count(*) FROM pg_perf_target WHERE ROW(id,tenant,amount,created,message,payload) "
            "IS DISTINCT FROM ROW(1::bigint,7::integer,123.4500::numeric,timestamp '2024-01-01','中文😀-constant',repeat('abcd',64))"))
    else:
        # Both tables have a PK; equal count plus a full field comparison for each
        # source key proves no missing/extra/duplicated/changed row in this fixture.
        equal = ','.join('s.' + c for c in COLUMNS)
        target = ','.join('t.' + c for c in COLUMNS)
        different = int(sql('SELECT count(*) FROM pg_perf_source s LEFT JOIN pg_perf_target t USING(id) '
                            'WHERE ROW(' + equal + ') IS DISTINCT FROM ROW(' + target + ')'))
    assert actual == rows and different == 0, (actual, different)
    return {'expected': rows, 'actual': actual, 'mismatched_rows': different}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--scenario', choices=SCENARIOS, required=True)
    p.add_argument('--rows', type=int, default=1000000)
    p.add_argument('--rounds', type=int, default=5)
    p.add_argument('--seed', action='store_true')
    p.add_argument('--baseline-rewrite', action='store_true')
    p.add_argument('--candidate-rewrite', action='store_true')
    p.add_argument('--candidate-copy', action='store_true')
    args = p.parse_args()
    assert args.rows > 0 and args.rounds >= 1
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'results.json').exists(), 'Use a fresh evidence directory'
    if args.seed: seed(args.rows)
    assert int(sql('SELECT count(*) FROM pg_perf_source')) == args.rows
    configs = {v: configuration(args.scenario, args.rows, output, getattr(args, v + '_rewrite'))[0]
               for v in ['baseline', 'candidate']}
    if args.candidate_copy:
        assert args.scenario != 'pg-to-file'
        configs['candidate']['job']['content'][0]['writer']['parameter']['useCopy'] = True
    report = {'scenario': args.scenario, 'rows': args.rows, 'runs': [], 'host': platform.platform(),
        'database': sql('SELECT version()'), 'jvm_options': ['-Duser.timezone=UTC'],
        'fsync': sql('SHOW fsync'), 'synchronous_commit': sql('SHOW synchronous_commit'),
        'full_page_writes': sql('SHOW full_page_writes'),
        'configs': configs, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'builds': {v: json.loads((getattr(args, v).resolve() / 'build-metadata.json').read_text())
                   for v in ['baseline', 'candidate']},
        'metric': 'Whole JVM elapsed time, excluding seed/reset/validation; static synthetic source.',
        'driver_control': {'baseline_rewrite': args.baseline_rewrite, 'candidate_rewrite': args.candidate_rewrite,
                           'candidate_copy': args.candidate_copy}}
    expected_file = None
    if args.scenario == 'pg-to-file':
        # This fixture has no tabs, backslashes or newlines in text fields.
        import subprocess
        query = "COPY (SELECT id,coalesce(tenant::text,'null'),coalesce(amount::text,'null')," \
                "coalesce(created::text,'null'),coalesce(message,'null'),payload FROM pg_perf_source ORDER BY id) TO STDOUT"
        reference = output / 'reference.tsv'
        with reference.open('wb') as stream:
            subprocess.run(['docker','exec','-i','datax-perf-postgres','psql','-X','-q','-U','postgres',
                            '-d','datax_bench','-v','ON_ERROR_STOP=1','-c',query],stdout=stream,check=True)
        expected_file = fingerprint(reference); reference.unlink()
        assert expected_file['rows'] == args.rows
        report['expected_file'] = expected_file
    for number in range(args.rounds + 1):
        for variant in (['candidate', 'baseline'] if number % 2 == 0 else ['baseline', 'candidate']):
            name = ('warmup' if number == 0 else str(number)) + '-' + variant
            if args.scenario != 'pg-to-file':
                suffix = '' if args.scenario == 'stream-to-pg' else ' INCLUDING ALL'
                sql('DROP TABLE IF EXISTS pg_perf_target; CREATE TABLE pg_perf_target (LIKE pg_perf_source' + suffix + ')')
            seconds = run(getattr(args, variant).resolve(), configs[variant], output, name,
                          jvm_options=report['jvm_options'])
            if args.scenario == 'pg-to-file':
                actual = fingerprint(output / 'actual.tsv')
                assert actual == expected_file, (actual, expected_file)
                check = {'expected': args.rows, 'actual': actual['rows'], 'mismatched_rows': 0, 'file': actual}
                (output / 'actual.tsv').unlink()
            else:
                check = validate(args.rows, args.scenario == 'stream-to-pg')
            entry = {'round': number, 'variant': variant, 'seconds': seconds,
                     'rows_per_second': args.rows / seconds, **check,
                     **process_metrics(output / (name + '.log'))}
            report['runs'].append(entry)
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
    medians = {v: statistics.median(r['seconds'] for r in report['runs'] if r['round'] and r['variant'] == v)
               for v in ['baseline', 'candidate']}
    report.update(median_seconds=medians, throughput_gain_percent=(medians['baseline']/medians['candidate']-1)*100,
                  elapsed_reduction_percent=(1-medians['candidate']/medians['baseline'])*100)
    (output/'results.json').write_text(json.dumps(report,indent=2))
    (output/'gate.json').write_text(json.dumps(evaluate(report,minimum_rounds=args.rounds),indent=2))
    print(json.dumps({'medians':medians,'throughput_gain_percent':report['throughput_gain_percent']}),flush=True)


if __name__ == '__main__':
    main()
