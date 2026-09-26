#!/usr/bin/env python3
"""Real PG concurrent-source snapshot checks on eight-row disposable tables."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import sql, job, URL


def seed():
    sql("""DROP TABLE IF EXISTS pg_snapshot_source, pg_snapshot_expected, pg_snapshot_target;
        CREATE TABLE pg_snapshot_source(id bigint PRIMARY KEY, shard bigint, payload text);
        INSERT INTO pg_snapshot_source SELECT n,n*10,'before-'||n FROM generate_series(1,8) n;
        CREATE TABLE pg_snapshot_expected AS TABLE pg_snapshot_source;
        CREATE TABLE pg_snapshot_target (LIKE pg_snapshot_source);
        GRANT SELECT ON pg_snapshot_source TO datax_snapshot_reader;""")


def check():
    return json.loads(sql("""SELECT json_build_object(
        'rows',(SELECT count(*) FROM pg_snapshot_target),
        'distinct_ids',(SELECT count(DISTINCT id) FROM pg_snapshot_target),
        'differences',(SELECT count(*) FROM (
            (TABLE pg_snapshot_expected EXCEPT ALL TABLE pg_snapshot_target) UNION ALL
            (TABLE pg_snapshot_target EXCEPT ALL TABLE pg_snapshot_expected)) d))"""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-inconsistent', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sql("""DO $$ BEGIN CREATE ROLE datax_snapshot_reader LOGIN PASSWORD 'datax-local-benchmark';
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;""")
    report = {'scope': __doc__, 'build': json.loads((args.runtime/'build-metadata.json').read_text()),
              'expect_inconsistent': args.expect_inconsistent, 'results': []}
    scenarios = ['query-concurrent', 'table-boundaries']
    if not args.expect_inconsistent:
        scenarios.append('query-expired')
    for scenario in scenarios:
        for attempt in range(1, 5):
            seed()
            name = scenario + '-' + str(attempt)
            config = job(destination='pg_snapshot_target', channels=1)
            reader = config['job']['content'][0]['reader']['parameter']
            writer = config['job']['content'][0]['writer']['parameter']
            reader['consistentSnapshot'] = True
            reader['username'] = 'datax_snapshot_reader'
            writer['column'] = ['id', 'shard', 'payload']
            app = 'datax-snapshot-' + name
            url = URL + '?ApplicationName=' + app
            if scenario == 'table-boundaries':
                reader.update(column=['id', 'shard', 'payload'], splitPk='shard', splitFactor=1,
                              connection=[{'jdbcUrl': [url], 'table': ['pg_snapshot_source']}])
                config['job']['setting']['speed']['channel'] = 4
                config['core']['container']['taskGroup']['channel'] = 4
                # Writer prepare runs after Reader prepare and before range discovery.
                writer['preSql'] = ["UPDATE pg_snapshot_source SET shard=100,payload='after' WHERE id=1"]
                seconds = run(args.runtime.resolve(), config, output, name)
            else:
                lock = 917202601
                reader['connection'] = [{'jdbcUrl': [url], 'querySql': [
                    'SELECT id,shard,payload FROM pg_snapshot_source '
                    'WHERE shard<50 AND pg_advisory_xact_lock(%d) IS NOT NULL ORDER BY id' % lock,
                    'SELECT id,shard,payload FROM pg_snapshot_source WHERE shard>=50 ORDER BY id']}]
                holder = subprocess.Popen(['docker', 'exec', '-i', 'datax-perf-postgres',
                    'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'datax_bench'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    try:
                        holder.stdin.write("SELECT 'locked',pg_advisory_lock(%d);\n" % lock)
                        holder.stdin.flush()
                        assert holder.stdout.readline().startswith('locked|')
                        future = pool.submit(run, args.runtime.resolve(), config, output, name,
                                             scenario != 'query-expired')
                        deadline = time.monotonic() + 30
                        while sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='%s' "
                                   "AND wait_event='advisory'" % app) == '0':
                            if future.done():
                                future.result()
                                raise AssertionError('Reader never waited on the barrier')
                            if time.monotonic() > deadline:
                                raise TimeoutError('Reader barrier was not reached')
                            time.sleep(.1)
                        if scenario == 'query-expired':
                            killed = sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                         "WHERE application_name='%s' AND query='SELECT pg_export_snapshot()'" % app)
                            assert killed == 't', killed
                        else:
                            sql("""BEGIN; UPDATE pg_snapshot_source SET payload='after-'||id;
                                UPDATE pg_snapshot_source SET shard=70 WHERE id=2;
                                DELETE FROM pg_snapshot_source WHERE id=6;
                                INSERT INTO pg_snapshot_source VALUES (9,90,'after-9'); COMMIT;""")
                    finally:
                        holder.stdin.close()
                        holder.wait(timeout=10)
                    seconds = future.result(timeout=30)
            result = check()
            remaining = int(sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='%s'" % app))
            entry = {'case': name, 'seconds': seconds, **result, 'remaining_source_connections': remaining,
                     'expected_job_failure': scenario == 'query-expired'}
            report['results'].append(entry)
            (output/'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
            assert remaining == 0, entry
            if scenario == 'query-expired':
                assert 'Cannot import PostgreSQL snapshot' in (output/(name+'.log')).read_text()
                assert result['rows'] == result['distinct_ids'] == 4, entry
            elif args.expect_inconsistent:
                assert result['differences'] > 0, entry
            else:
                assert result == {'rows': 8, 'distinct_ids': 8, 'differences': 0}, entry


if __name__ == '__main__':
    main()
