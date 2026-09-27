#!/usr/bin/env python3
"""Real PG affected-row checks, including the rewrite SUCCESS_NO_INFO limitation."""
import argparse
import json
from pathlib import Path
from postgresql_checks import job, sql
from mysql_querysql import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-unsafe', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Small real PG fixtures; not atomic recovery or production proof',
              'build': json.loads((args.runtime/'build-metadata.json').read_text()),
              'postgres': sql('SELECT version()'), 'results': []}
    cases = [
        ('normal', False, 'false', False),
        ('first-batch-skip', False, 'NEW.id=2', False),
        ('late-batch-skip', False, 'NEW.id=6', False),
        ('fallback-skip', False, 'NEW.id=2', True),
        ('rewrite-normal', True, 'false', False),
        ('rewrite-all-skipped', True, 'NEW.id<=4', False),
        ('rewrite-partial-unknown', True, 'NEW.id=2', False),
        ('rewrite-fallback-skip', True, 'NEW.id=2', True),
    ]
    for case, rewrite, condition, fallback in cases:
        for attempt in range(1, 5):
            name = '%s-%d' % (case, attempt)
            report['pending_run'] = name
            (out/'results.json').write_text(json.dumps(report, indent=2))
            setup = '''DROP TABLE IF EXISTS pg_row_count_target;
                CREATE TABLE pg_row_count_target(id bigint PRIMARY KEY, payload text);
                CREATE OR REPLACE FUNCTION pg_row_count_skip() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF %s THEN RETURN NULL; END IF; RETURN NEW; END $$;
                CREATE TRIGGER skip_row BEFORE INSERT ON pg_row_count_target
                FOR EACH ROW EXECUTE FUNCTION pg_row_count_skip();''' % condition
            if fallback:
                setup += "INSERT INTO pg_row_count_target VALUES(1,'中文😀-1');"
            (out/(name+'.sql')).write_text(setup)
            sql(setup)
            config = job(destination='pg_row_count_target',
                         query="SELECT i AS id,'中文😀-'||i AS payload FROM generate_series(1,8) i ORDER BY i")
            config['job']['setting']['errorLimit']['record'] = 100
            writer = config['job']['content'][0]['writer']['parameter']
            writer.update(column=['id', 'payload'], batchSize=4)
            writer['connection'][0]['jdbcUrl'] += '?reWriteBatchedInserts='+str(rewrite).lower()
            unknown = case == 'rewrite-partial-unknown'
            normal = condition == 'false'
            success = args.expect_unsafe or normal or unknown
            seconds = run(args.runtime.resolve(), config, out, name, expect_success=success)
            actual = json.loads(sql("SELECT coalesce(json_agg(id ORDER BY id),'[]') FROM pg_row_count_target"))
            different = int(sql("SELECT count(*) FROM pg_row_count_target WHERE payload IS DISTINCT FROM '中文😀-'||id"))
            if normal:
                expected = list(range(1, 9))
            elif args.expect_unsafe or unknown:
                expected = [i for i in range(1, 9) if i != (6 if case == 'late-batch-skip' else 2)]
                if case == 'rewrite-all-skipped': expected = [5, 6, 7, 8]
            else:
                expected = [1] if fallback else [1, 2, 3, 4] if case == 'late-batch-skip' else []
            assert actual == expected and different == 0, (name, actual, expected, different)
            log = (out/(name+'.log')).read_text()
            if not success:
                assert 'DBUtilErrorCode-26' in log, name
                assert ('采用每次写入一行' in log) == fallback, name
            entry = {'case': name, 'seconds': seconds, 'engine_success': success, 'ids': actual,
                     'mismatched_fields': different, 'unknown_rewrite_count_limitation': unknown}
            report['results'].append(entry); report.pop('pending_run')
            (out/'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
    sql('DROP TABLE pg_row_count_target; DROP FUNCTION pg_row_count_skip()')
    print('PASS', len(report['results']), 'real PG row-count checks', flush=True)


if __name__ == '__main__':
    main()
