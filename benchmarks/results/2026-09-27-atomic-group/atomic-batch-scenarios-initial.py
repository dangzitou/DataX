#!/usr/bin/env python3
"""Paired real PostgreSQL benchmarks, including explicit native-driver controls."""
import argparse
import filecmp
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
from postgresql_checks import sql, job
from mysql_querysql import run, process_metrics
from mysql_scenarios import fingerprint
from performance_gate import evaluate

COLUMNS = ['id', 'tenant', 'amount', 'created', 'message', 'payload']
SCENARIOS = ['query-single', 'query-parallel', 'table-single', 'table-parallel', 'pg-to-file',
             'pg-integer-file', 'stream-to-pg']
INTEGER_COLUMNS = ['id', 'negated', 'tenant', 'large', 'negative_large', 'near_max', 'near_min', 'nullable']
INTEGER_QUERY = """SELECT id,-id AS negated,id%100 AS tenant,id*1000000000000 AS large,
    -id*1000000000000 AS negative_large,9223372036854775807::bigint-id AS near_max,
    '-9223372036854775808'::bigint+id AS near_min,
    CASE WHEN id%17=0 THEN NULL ELSE id*13 END AS nullable FROM pg_perf_source ORDER BY id"""


def seed(rows):
    assert 1 <= rows <= 1000000, 'Disposable fixture is capped at one million rows'
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
    channels = 4 if scenario in ['query-parallel', 'table-parallel', 'stream-to-pg'] else 1
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
    if scenario.endswith('-file'):
        content['writer'] = {'name': 'streamwriter', 'parameter': {
            'path': str(output), 'fileName': 'actual.tsv', 'print': False}}
        if scenario == 'pg-integer-file':
            reader['connection'][0]['querySql'] = [INTEGER_QUERY]
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


def disk_guard(output):
    pg_bytes = int(subprocess.check_output(['docker', 'exec', 'datax-perf-postgres', 'du', '-sk',
                                          '/var/lib/postgresql/data'], text=True).split()[0]) * 1024
    output_bytes = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    free = shutil.disk_usage(output).free
    # Keep 1 GiB of headroom inside a 6 GiB test-data budget; this is not a hard quota.
    if pg_bytes + output_bytes > 5 * 1024**3 or free < 8 * 1024**3:
        raise RuntimeError('PostgreSQL benchmark storage guard: clean owned test data before continuing')
    return {'postgres_bytes': pg_bytes, 'output_bytes': output_bytes, 'host_free_bytes': free}


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
    p.add_argument('--atomic', action='store_true',
                   help='Enable PG atomic append on both sides; include publication in elapsed time')
    p.add_argument('--baseline-manual-split', action='store_true',
                   help='query-parallel control: manually split the original into the same four ranges')
    args = p.parse_args()
    assert 1 <= args.rows <= 1000000 and args.rounds >= 1
    assert not args.baseline_manual_split or args.scenario == 'query-parallel'
    assert not args.atomic or not args.scenario.endswith('-file')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'results.json').exists(), 'Use a fresh evidence directory'
    storage_before = disk_guard(output)
    if args.seed: seed(args.rows)
    assert int(sql('SELECT count(*) FROM pg_perf_source')) == args.rows
    configs = {v: configuration(args.scenario, args.rows, output, getattr(args, v + '_rewrite'))[0]
               for v in ['baseline', 'candidate']}
    if args.scenario == 'query-parallel':
        configs['candidate']['job']['content'][0]['reader']['parameter'].update(
            querySqlSplitPk='id', consistentSnapshot=True)
        if args.baseline_manual_split:
            # Same boundaries as RangeSplitUtil for this static indexed fixture (id=1..rows).
            step, remainder = divmod(args.rows - 1, 4)
            bounds = [1 + step*i + min(i, remainder) for i in range(1, 4)]
            predicates = ['("id" < %d OR "id" IS NULL)' % bounds[0],
                          '"id" >= %d AND "id" < %d' % (bounds[0], bounds[1]),
                          '"id" >= %d AND "id" < %d' % (bounds[1], bounds[2]),
                          '"id" >= %d' % bounds[2]]
            connection = configs['baseline']['job']['content'][0]['reader']['parameter']['connection'][0]
            query = connection['querySql'][0]
            connection['querySql'] = ['SELECT * FROM (' + query + ') AS datax_query WHERE ' + pred
                                      for pred in predicates]
    if args.candidate_copy:
        assert not args.scenario.endswith('-file')
        configs['candidate']['job']['content'][0]['writer']['parameter']['useCopy'] = True
    if args.atomic:
        for config in configs.values():
            content = config['job']['content'][0]
            content['writer']['parameter'].update(atomicBatchId='benchmark-native-batch',
                session=["SET temp_file_limit='1536MB'"])
            if content['reader']['name'] == 'postgresqlreader':
                content['reader']['parameter']['consistentSnapshot'] = True
    report = {'scenario': args.scenario, 'rows': args.rows, 'runs': [], 'host': platform.platform(),
        'atomic': args.atomic,
        'storage_before': storage_before, 'baseline_manual_split': args.baseline_manual_split,
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
    if args.scenario.endswith('-file'):
        # This fixture has no tabs, backslashes or newlines in text fields.
        query = "COPY (SELECT id,coalesce(tenant::text,'null'),coalesce(amount::text,'null')," \
                "coalesce(created::text,'null'),coalesce(message,'null'),payload FROM pg_perf_source ORDER BY id) TO STDOUT"
        if args.scenario == 'pg-integer-file':
            query = "COPY (SELECT " + ','.join("coalesce("+c+"::text,'null')" for c in INTEGER_COLUMNS) \
                    + " FROM (" + INTEGER_QUERY + ") expected ORDER BY id) TO STDOUT"
        reference = output / 'reference.tsv'
        with reference.open('wb') as stream:
            subprocess.run(['docker','exec','-i','datax-perf-postgres','psql','-X','-q','-U','postgres',
                            '-d','datax_bench','-v','ON_ERROR_STOP=1','-c',query],stdout=stream,check=True)
        expected_file = fingerprint(reference)
        assert expected_file['rows'] == args.rows
        report['expected_file'] = expected_file
    for number in range(args.rounds + 1):
        for variant in (['candidate', 'baseline'] if number % 2 == 0 else ['baseline', 'candidate']):
            storage = disk_guard(output)
            name = ('warmup' if number == 0 else str(number)) + '-' + variant
            if not args.scenario.endswith('-file'):
                suffix = '' if args.scenario == 'stream-to-pg' else ' INCLUDING ALL'
                sql('DROP TABLE IF EXISTS pg_perf_target; CREATE TABLE pg_perf_target (LIKE pg_perf_source' + suffix + ')')
            seconds = run(getattr(args, variant).resolve(), configs[variant], output, name,
                          jvm_options=report['jvm_options'])
            if args.scenario.endswith('-file'):
                actual = fingerprint(output / 'actual.tsv')
                assert actual == expected_file, (actual, expected_file)
                # Both file fixtures have one ordered reader. Compare every byte as well as the fingerprint.
                filecmp.clear_cache()
                assert filecmp.cmp(reference, output / 'actual.tsv', shallow=False), 'File bytes differ from PG COPY'
                check = {'expected': args.rows, 'actual': actual['rows'], 'mismatched_rows': 0,
                         'exact_byte_equality': True, 'file': actual}
                (output / 'actual.tsv').unlink()
            else:
                check = validate(args.rows, args.scenario == 'stream-to-pg')
                if args.atomic:
                    published = int(sql("SELECT row_count FROM __datax_atomic_batches_v1 "
                        "WHERE target_oid='pg_perf_target'::regclass AND batch_id='benchmark-native-batch'"))
                    stages = int(sql("SELECT count(*) FROM pg_class WHERE relname LIKE '__datax_stage_%'"))
                    assert published == args.rows and stages == 0, (published, stages)
                    check.update(atomic_published_rows=published, remaining_stages=stages)
            entry = {'round': number, 'variant': variant, 'seconds': seconds,
                     'storage_before_run': storage,
                     'rows_per_second': args.rows / seconds, **check,
                     **process_metrics(output / (name + '.log'))}
            report['runs'].append(entry)
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
    if expected_file is not None:
        reference.unlink()
    medians = {v: statistics.median(r['seconds'] for r in report['runs'] if r['round'] and r['variant'] == v)
               for v in ['baseline', 'candidate']}
    report.update(median_seconds=medians, throughput_gain_percent=(medians['baseline']/medians['candidate']-1)*100,
                  elapsed_reduction_percent=(1-medians['candidate']/medians['baseline'])*100,
                  storage_after=disk_guard(output))
    (output/'results.json').write_text(json.dumps(report,indent=2))
    (output/'gate.json').write_text(json.dumps(evaluate(report,minimum_rounds=args.rounds),indent=2))
    print(json.dumps({'medians':medians,'throughput_gain_percent':report['throughput_gain_percent']}),flush=True)


if __name__ == '__main__':
    main()
