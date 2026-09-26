#!/usr/bin/env python3
"""Real PG querySql splitting and read-only prechecks, with a nine-row fixture."""
import argparse
import json
import re
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import sql, job, URL

COLUMNS = ['row_id', 'id', 'payload', 'amount', 'ts', 'flag']
QUERY = 'SELECT * FROM pg_query_source'


def seed():
    sql("""DROP TABLE IF EXISTS pg_query_source,pg_query_expected,pg_query_target;
        CREATE TABLE pg_query_source(row_id bigint,id bigint,payload text,
            amount numeric(38,18),ts timestamp(6),flag boolean);
        INSERT INTO pg_query_source
        SELECT ord,id,CASE WHEN ord=1 THEN NULL ELSE '中文😀-'||ord END,
            -12345678901234567890.123456789012345678,
            timestamp '1969-12-31 23:59:59.999999', CASE WHEN ord=1 THEN NULL ELSE ord%2=0 END
        FROM unnest(ARRAY[NULL,-9223372036854775808,-1,0,0,1,1,9223372036854775806,
                         9223372036854775807]::bigint[]) WITH ORDINALITY AS v(id,ord);
        CREATE TABLE pg_query_target (LIKE pg_query_source);
        GRANT SELECT ON pg_query_source TO datax_snapshot_reader;""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sql("""DO $$ BEGIN CREATE ROLE datax_snapshot_reader LOGIN PASSWORD 'datax-local-benchmark';
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;""")
    report = {'scope': __doc__, 'postgres': sql('SELECT version()'),
              'build': json.loads((args.runtime/'build-metadata.json').read_text()), 'results': []}
    scenarios = ['edges-%d' % n for n in range(1, 5)]
    scenarios += ['cte-alias', 'multiple-queries', 'empty', 'all-null', 'constant', 'one-channel']
    scenarios += ['source-change-%d' % n for n in range(1, 5)]
    scenarios += ['missing-snapshot', 'bad-key', 'wrong-case', 'text-key', 'multiple-statements',
                  'dryrun-valid', 'dryrun-wrong-case', 'dryrun-text-key', 'dryrun-second-query',
                  'dryrun-write-cte', 'dryrun-write-function', 'dryrun-default-valid',
                  'dryrun-default-second-query', 'dryrun-table-valid', 'dryrun-table-missing',
                  'dryrun-timeout']
    for name in scenarios:
        seed()
        config = job(destination='pg_query_target', query=QUERY, channels=4)
        reader = config['job']['content'][0]['reader']['parameter']
        writer = config['job']['content'][0]['writer']['parameter']
        reader.update(username='datax_snapshot_reader', consistentSnapshot=True, querySqlSplitPk='id')
        app = 'datax-pg-query-' + name
        reader['connection'][0]['jdbcUrl'] = [URL + '?ApplicationName=' + app]
        writer['column'] = COLUMNS
        queries = [QUERY]
        expected_tasks = 4
        diagnostic = None
        dryrun = name.startswith('dryrun-')
        if name == 'cte-alias' or name in ('wrong-case', 'dryrun-wrong-case'):
            queries = ['WITH s AS (SELECT row_id,id::bigint AS "SplitID",payload,amount,ts,flag '
                       'FROM pg_query_source) SELECT * FROM s;']
            reader['querySqlSplitPk'] = 'SplitID' if name == 'cte-alias' else 'splitid'
            if name != 'cte-alias':
                diagnostic = 'splitid'
        elif name == 'multiple-queries':
            queries = [QUERY + ' WHERE row_id<=5', QUERY + ' WHERE row_id>5']
        elif name == 'empty':
            queries = [QUERY + ' WHERE false']
            expected_tasks = 1
        elif name in ('all-null', 'constant'):
            queries = ['SELECT row_id,%s::bigint AS id,payload,amount,ts,flag FROM pg_query_source'
                       % ('NULL' if name == 'all-null' else '7')]
            expected_tasks = 1
        elif name == 'one-channel':
            config['job']['setting']['speed']['channel'] = 1
            config['core']['container']['taskGroup']['channel'] = 1
            expected_tasks = 1
        elif name == 'missing-snapshot':
            reader['consistentSnapshot'] = False
            diagnostic = 'requires consistentSnapshot=true'
        elif name == 'bad-key':
            reader['querySqlSplitPk'] = 'id;bad'
            diagnostic = 'simple integer output column name'
        elif name in ('text-key', 'dryrun-text-key'):
            reader['querySqlSplitPk'] = 'payload'
            diagnostic = 'must expose an integer column'
        elif name == 'multiple-statements':
            queries = [QUERY + '; SELECT 1']
            diagnostic = 'requires one SELECT'
        elif name == 'dryrun-second-query':
            queries.append('SELECT no_such_column FROM pg_query_source')
            diagnostic = 'no_such_column'
        elif name.startswith('dryrun-default-') or name.startswith('dryrun-table-'):
            del reader['querySqlSplitPk']
            del reader['consistentSnapshot']
            if name == 'dryrun-default-second-query':
                queries.append('SELECT no_such_column FROM pg_query_source')
                diagnostic = 'no_such_column'
        elif name == 'dryrun-timeout':
            reader['queryTimeout'] = 1
            queries = ['SELECT 1::bigint AS id FROM pg_sleep(3)']
            diagnostic = 'SQLState=57014'
        elif name in ('dryrun-write-cte', 'dryrun-write-function'):
            # Use an owner account here so missing write privileges cannot mask a read-only failure.
            reader['username'] = 'postgres'
            if name == 'dryrun-write-cte':
                queries = ['WITH changed AS (DELETE FROM pg_query_source RETURNING *) SELECT * FROM changed']
            else:
                sql("""CREATE OR REPLACE FUNCTION pg_query_write_probe() RETURNS bigint LANGUAGE plpgsql AS $$
                    BEGIN DELETE FROM pg_query_source; RETURN 1; END $$""")
                queries = ['SELECT pg_query_write_probe() AS id']
            diagnostic = 'read-only transaction'
        reader['connection'][0]['querySql'] = queries
        if name.startswith('dryrun-table-'):
            del reader['connection'][0]['querySql']
            reader['connection'][0]['table'] = ['pg_query_source']
            reader['column'] = COLUMNS if name.endswith('valid') else ['no_such_column']
            if name.endswith('missing'):
                diagnostic = 'no_such_column'
        if dryrun:
            config['job']['setting']['dryRun'] = True
        elif diagnostic is None:
            sql('CREATE TABLE pg_query_expected AS ' + ' UNION ALL '.join(
                '(' + q.rstrip(';') + ')' for q in queries))
        if name.startswith('source-change-'):
            # Happens after exporting the reader snapshot, before discovering split boundaries.
            writer['preSql'] = ["UPDATE pg_query_source SET id=20,payload='after' WHERE row_id=2",
                                'DELETE FROM pg_query_source WHERE row_id=8',
                                "INSERT INTO pg_query_source VALUES (10,100,'new',NULL,NULL,NULL)"]
        seconds = run(args.runtime.resolve(), config, output, name, diagnostic is None)
        log = (output/(name+'.log')).read_text()
        count = int(sql('SELECT count(*) FROM pg_query_target'))
        if diagnostic:
            assert diagnostic in log, (name, diagnostic)
            if name == 'dryrun-timeout':
                assert 'DBUtilErrorCode-24' in log, name
        if dryrun or diagnostic:
            assert count == 0, (name, count)
            assert sql('SELECT count(*) FROM pg_query_source') == '9', name
            differences = None
            tasks = None
        else:
            differences = int(sql('SELECT count(*) FROM ((TABLE pg_query_expected EXCEPT ALL TABLE pg_query_target) '
                                  'UNION ALL (TABLE pg_query_target EXCEPT ALL TABLE pg_query_expected)) d'))
            assert differences == 0, (name, differences)
            tasks = int(re.search(r'Reader.Job \[postgresqlreader\] splits to \[(\d+)\] tasks', log)[1])
            assert tasks == expected_tasks, (name, tasks, expected_tasks)
        connections = int(sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='%s'" % app))
        assert connections == 0, (name, connections)
        entry = {'case': name, 'seconds': seconds, 'expected_success': diagnostic is None,
                 'target_rows': count, 'field_differences': differences, 'reader_tasks': tasks,
                 'remaining_source_connections': connections, 'diagnostic': diagnostic}
        report['results'].append(entry)
        (output/'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(entry), flush=True)


if __name__ == '__main__':
    main()
