#!/usr/bin/env python3
"""Small real-Engine PG atomic append checks; not a scale or production guarantee."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import job, sql, URL, COLUMNS
from postgresql_commit_wire_checks import CommitDropProxy

SOURCE = 'pg_atomic_source'
TARGET = 'pg_atomic_target'
QUERY = 'SELECT * FROM ' + SOURCE


class PublicationDropProxy(CommitDropProxy):
    def should_drop_commit(self, inserted, tags):
        # Only the owner publishes the ledger INSERT and drops the stage together.
        return inserted > 0 and b'DROP TABLE' in tags


def seed():
    sql("""DROP TABLE IF EXISTS pg_atomic_target CASCADE;
        DROP TABLE IF EXISTS pg_atomic_source;
        CREATE TABLE pg_atomic_source (id bigint, signed bigint, amount numeric(38,18),
            ts timestamp(6), tz timestamptz(6), flag boolean, txt text, bin bytea, day date);
        INSERT INTO pg_atomic_source VALUES
        (1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
        (2,-9223372036854775808,-12345678901234567890.123456789012345678,
         '2024-02-29 12:34:56.123456','2024-02-29 12:34:56.123456+08',false,
         '中文😀' || chr(10) || chr(9) || chr(92) || chr(34),decode('0001275c0a0dff','hex'),'2024-02-29'),
        (3,9223372036854775807,0.000000000000000001,
         '1969-12-31 23:59:59.999999','1969-12-31 23:59:59.999999-05',true,'',decode('ff00','hex'),'1969-12-31'),
        (4,0,99999999999999999999.999999999999999999,
         '2024-01-01 00:00:00.000001','2024-01-01 00:00:00.000001+00',NULL,'plain',decode('','hex'),'2000-01-01');
        INSERT INTO pg_atomic_source SELECT * FROM pg_atomic_source;
        CREATE TABLE pg_atomic_target (LIKE pg_atomic_source);""")


def config(batch, copy=False, query=QUERY):
    result = job(destination=TARGET, query=query, channels=4)
    reader = result['job']['content'][0]['reader']['parameter']
    reader.update(consistentSnapshot=True, querySqlSplitPk='id')
    writer = result['job']['content'][0]['writer']['parameter']
    writer.update(atomicBatchId=batch, useCopy=copy)
    return result


def state():
    return json.loads(sql("""SELECT json_build_object(
        'rows',(SELECT count(*) FROM pg_atomic_target),
        'differences',(SELECT count(*) FROM (
          (TABLE pg_atomic_source EXCEPT ALL TABLE pg_atomic_target) UNION ALL
          (TABLE pg_atomic_target EXCEPT ALL TABLE pg_atomic_source)) d),
        'ledger_rows',(SELECT count(*) FROM __datax_atomic_batches_v1 WHERE target_oid='pg_atomic_target'::regclass),
        'stages',(SELECT count(*) FROM pg_class WHERE relname LIKE '__datax_stage_%'))"""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    runtime = args.runtime.resolve()
    report = {'scope': __doc__, 'postgres': sql('SELECT version()'),
              'build': json.loads((runtime/'build-metadata.json').read_text()), 'results': []}

    def record(entry):
        report['results'].append(entry)
        (output/'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(entry), flush=True)

    def check(name, cfg, rows, markers, success=True, diagnostic=None, **extra):
        seconds = run(runtime, cfg, output, name, expect_success=success)
        actual = state()
        assert actual['rows'] == rows and actual['ledger_rows'] == markers and actual['stages'] == 0, actual
        if rows == 8:
            assert actual['differences'] == 0, actual
        if diagnostic:
            assert diagnostic in (output/(name+'.log')).read_text(), name
        entry = dict(case=name, seconds=seconds, expected_success=success, **actual, **extra)
        record(entry)

    for copy in [False, True]:
        for attempt in range(1, 5):
            seed()
            prefix = ('copy' if copy else 'jdbc') + '-' + str(attempt)
            cfg = config(prefix, copy)
            check(prefix+'-first', cfg, 8, 1)
            check(prefix+'-rerun', cfg, 8, 1)
            changed = QUERY.replace('*', 'id,signed,amount,ts,tz,flag,coalesce(txt,\'\')||\'changed\',bin,day')
            check(prefix+'-changed', config(prefix, copy, changed), 8, 1, False, 'different data or columns')
            for column, kind in [('amount', 'numeric(38,6)'), ('ts', 'timestamp(3)')]:
                seed()
                sql('ALTER TABLE pg_atomic_target ALTER COLUMN '+column+' TYPE '+kind)
                check(prefix+'-narrow-'+column, config(prefix, copy), 0, 0, False,
                      'publication changed or omitted staged values')

    sql('DROP SCHEMA IF EXISTS datax_atomic_types CASCADE; CREATE SCHEMA datax_atomic_types; '
        'CREATE DOMAIN datax_atomic_types.rounded AS numeric(38,6); '
        'CREATE DOMAIN datax_atomic_types.nested AS datax_atomic_types.rounded; '
        'CREATE TYPE datax_atomic_types.wrapped AS (value datax_atomic_types.rounded); '
        "CREATE TYPE datax_atomic_types.enumerated AS ENUM ('one')")
    try:
        for kind in ['rounded', 'nested', 'rounded[]', 'wrapped', 'enumerated']:
            for copy in [False, True]:
                for attempt in range(1, 5):
                    seed()
                    sql('ALTER TABLE pg_atomic_source ALTER COLUMN amount TYPE datax_atomic_types.'+kind+' USING NULL')
                    sql('ALTER TABLE pg_atomic_target ALTER COLUMN amount TYPE datax_atomic_types.'
                        +kind+' USING NULL; INSERT INTO pg_atomic_target(id,txt) VALUES(0,\'existing\')')
                    name = 'custom-type-'+kind.replace('[]', '-array')+'-'+('copy' if copy else 'jdbc')+'-'+str(attempt)
                    check(name, config(name, copy), 1, 0, False,
                          'does not support domains or user-defined column types')
                    assert sql("SELECT count(*) FROM pg_atomic_target WHERE id=0 AND txt='existing' "
                               'AND signed IS NULL AND amount IS NULL AND ts IS NULL AND tz IS NULL '
                               'AND flag IS NULL AND bin IS NULL AND day IS NULL') == '1'
    finally:
        sql('DROP TABLE IF EXISTS pg_atomic_target; DROP SCHEMA datax_atomic_types CASCADE')

    for attempt in range(1, 5):
        for case in ['constraint', 'filtered', 'transformer-dirty', 'stage-commit-drop', 'publish-commit-drop']:
            seed()
            name = case+'-'+str(attempt)
            cfg = config(name)
            if case == 'constraint':
                sql('ALTER TABLE pg_atomic_target ADD CHECK (id < 4)')
                cfg['job']['setting']['errorLimit']['record'] = 100
                check(name, cfg, 0, 0, False, 'violates check constraint')
            elif case in ['filtered', 'transformer-dirty']:
                cfg['job']['setting']['errorLimit']['record'] = 100
                cfg['job']['content'][0]['transformer'] = [{'name': 'dx_filter', 'parameter': {
                    'columnIndex': 0, 'paras': ['=', '1' if case == 'filtered' else 'not-a-number']}}]
                check(name, cfg, 0, 0, False, 'dirty, filtered or missing records detected')
            else:
                cls = PublicationDropProxy if case == 'publish-commit-drop' else CommitDropProxy
                with cls() as proxy:
                    cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl'] = (
                        'jdbc:postgresql://127.0.0.1:%d/datax_bench?sslmode=disable' % proxy.port)
                    committed = case == 'publish-commit-drop'
                    check(name, cfg, 8 if committed else 0, 1 if committed else 0, False,
                          'DBUtilErrorCode-25', publication_committed=committed)
                    assert proxy.dropped.is_set(), 'No transaction COMMIT intercepted'
                cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl'] = URL
                check(name+'-recovery', cfg, 8, 1)

        seed()
        name = 'existing-target-preserved-'+str(attempt)
        sql('ALTER TABLE pg_atomic_target ADD CHECK (id < 4); INSERT INTO pg_atomic_target(id,txt) VALUES(0,\'existing\')')
        check(name, config(name), 1, 0, False, 'violates check constraint')
        assert sql("SELECT count(*) FROM pg_atomic_target WHERE id=0 AND txt='existing' "
                   'AND signed IS NULL AND amount IS NULL AND ts IS NULL AND tz IS NULL '
                   'AND flag IS NULL AND bin IS NULL AND day IS NULL') == '1'

    for case in ['empty', 'partitioned', 'preSql', 'postSql', 'reserved-token', 'blank-id', 'rls', 'trigger']:
        seed()
        cfg = config(case)
        writer = cfg['job']['content'][0]['writer']['parameter']
        rows, success, diagnostic = 0, False, None
        if case == 'empty':
            cfg = config(case, query=QUERY+' WHERE false'); success = True
        elif case == 'partitioned':
            sql('DROP TABLE pg_atomic_target; CREATE TABLE pg_atomic_target (LIKE pg_atomic_source) PARTITION BY RANGE(id); '
                'CREATE TABLE pg_atomic_target_p1 PARTITION OF pg_atomic_target FOR VALUES FROM (0) TO (3); '
                'CREATE TABLE pg_atomic_target_p2 PARTITION OF pg_atomic_target DEFAULT')
            rows, success = 8, True
        elif case in ['preSql', 'postSql']:
            writer[case] = ['INSERT INTO pg_atomic_target SELECT * FROM pg_atomic_source']
            diagnostic = 'does not allow '+case
        elif case == 'reserved-token':
            writer['_dataxAtomicToken'] = 'forged'; diagnostic = 'Reserved atomic configuration'
        elif case == 'blank-id':
            writer['atomicBatchId'] = ' '; diagnostic = 'must be valid, nonempty text'
        elif case == 'rls':
            sql('ALTER TABLE pg_atomic_target ENABLE ROW LEVEL SECURITY')
            diagnostic = 'without RLS, user triggers or write rules'
        elif case == 'trigger':
            sql("CREATE OR REPLACE FUNCTION pg_atomic_test_trigger() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$; "
                'CREATE TRIGGER skip BEFORE INSERT ON pg_atomic_target FOR EACH ROW EXECUTE FUNCTION pg_atomic_test_trigger()')
            diagnostic = 'without RLS, user triggers or write rules'
        check(case, cfg, rows, 1 if success else 0, success, diagnostic)

    seed()
    sql('DROP SCHEMA IF EXISTS datax_atomic_collision CASCADE; CREATE SCHEMA datax_atomic_collision; '
        'CREATE TABLE datax_atomic_collision.target (LIKE pg_atomic_source); '
        'CREATE TABLE datax_atomic_collision.__datax_atomic_batches_v1 (target_oid oid)')
    try:
        cfg = config('bad-ledger')
        cfg['job']['content'][0]['writer']['parameter']['connection'][0]['table'] = ['datax_atomic_collision.target']
        check('bad-ledger', cfg, 0, 0, False, 'ledger structure is unsafe')
        assert sql('SELECT count(*) FROM datax_atomic_collision.target') == '0'
    finally:
        sql('DROP SCHEMA datax_atomic_collision CASCADE')

    # Two real Engines: old worker resumes after the owner connection dies and a
    # replacement job has recreated its stage. Neither count checks nor an owner
    # advisory lock alone prevents the old worker from polluting the new stage.
    for attempt in range(1, 5):
        seed()
        name = 'generation-fence-'+str(attempt)
        holder = subprocess.Popen(['docker', 'exec', '-i', 'datax-perf-postgres',
            'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'datax_bench'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        locks = [918272610, 918272611]
        holder.stdin.write("SELECT 'locked',pg_advisory_lock(%d),pg_advisory_lock(%d);\n" % tuple(locks))
        holder.stdin.flush()
        assert holder.stdout.readline().startswith('locked|')

        def blocked_config(index):
            cfg = config(name, query=QUERY+' WHERE pg_advisory_xact_lock(%d) IS NOT NULL' % locks[index])
            reader = cfg['job']['content'][0]['reader']['parameter']
            del reader['querySqlSplitPk']
            reader['connection'][0]['jdbcUrl'] = [URL+'?ApplicationName='+name+'-reader-'+str(index)]
            cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl'] = URL+'?ApplicationName='+name+'-owner-'+str(index)
            return cfg

        def wait_blocked(index, future):
            deadline = time.monotonic()+30
            while sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='%s-reader-%d' "
                       "AND wait_event='advisory'" % (name,index)) == '0':
                if future.done():
                    future.result()
                    raise AssertionError('No source barrier reached')
                if time.monotonic() > deadline:
                    raise TimeoutError('No source barrier reached')
                time.sleep(.1)

        def release(index):
            holder.stdin.write("SELECT 'released',pg_advisory_unlock(%d);\n" % locks[index])
            holder.stdin.flush()
            assert holder.stdout.readline().startswith('released|t')

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            try:
                old = pool.submit(run, runtime, blocked_config(0), output, name+'-old', False)
                wait_blocked(0, old)
                # A live owner must reject a simultaneous same-batch job without cleanup damage.
                run(runtime, config(name), output, name+'-duplicate', False)
                assert 'already running' in (output/(name+'-duplicate.log')).read_text()
                assert state()['stages'] == 1
                killed = sql("SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a "
                             "WHERE a.application_name='%s-owner-0' AND EXISTS(SELECT 1 FROM pg_locks l "
                             "WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted)" % name)
                assert killed == 't', killed
                new = pool.submit(run, runtime, blocked_config(1), output, name+'-new', True)
                wait_blocked(1, new)
                release(0)
                old.result(timeout=30)
                assert 'stale writer stopped' in (output/(name+'-old.log')).read_text()
                assert state()['rows'] == 0 and state()['stages'] == 1
                release(1)
                new.result(timeout=30)
                actual = state()
                assert actual == {'rows': 8, 'differences': 0, 'ledger_rows': 1, 'stages': 0}, actual
                record(dict(case=name, stale_writer_rejected=True, simultaneous_owner_rejected=True, **actual))
            finally:
                holder.stdin.close()
                holder.wait(timeout=10)


if __name__ == '__main__':
    main()
