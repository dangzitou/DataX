#!/usr/bin/env python3
"""Real PostgreSQL JDBC trace: nullable integer binding must not reparse each row."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import job, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-churn', action='store_true')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logging = output / 'jdbc-logging.properties'
    logging.write_text('handlers=java.util.logging.ConsoleHandler\n.level=WARNING\n'
                       'java.util.logging.ConsoleHandler.level=FINEST\norg.postgresql.level=FINEST\n')
    sql("""DROP TABLE IF EXISTS pg_bind_source;
        CREATE TABLE pg_bind_source(id bigint, small_value smallint, int_value integer, large_value bigint);
        INSERT INTO pg_bind_source SELECT i,
            CASE WHEN i%2=0 THEN NULL ELSE (i-64)::smallint END,
            CASE WHEN i%2=0 THEN NULL ELSE (2147483647-i)::integer END,
            CASE WHEN i%3=0 THEN NULL WHEN i%3=1 THEN '-9223372036854775808'::bigint
                 ELSE 9223372036854775807::bigint END
        FROM generate_series(1,128) i""")
    report = {'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'postgres': sql('SELECT version()'), 'expect_churn': args.expect_churn, 'results': []}
    for attempt in range(1,5):
        sql('DROP TABLE IF EXISTS pg_bind_target; CREATE TABLE pg_bind_target (LIKE pg_bind_source)')
        config = job(destination='pg_bind_target', query='SELECT * FROM pg_bind_source ORDER BY id')
        config['job']['content'][0]['writer']['parameter'].update(
            column=['id','small_value','int_value','large_value'], batchSize=128)
        name = 'nullable-integer-' + str(attempt)
        seconds = run(runtime, config, output, name,
                      jvm_options=['-Djava.util.logging.config.file=' + str(logging)])
        differences = int(sql('SELECT count(*) FROM ((TABLE pg_bind_source EXCEPT ALL TABLE pg_bind_target) '
                              'UNION ALL (TABLE pg_bind_target EXCEPT ALL TABLE pg_bind_source)) d'))
        rows = int(sql('SELECT count(*) FROM pg_bind_target'))
        assert rows == 128 and differences == 0, (rows,differences)
        trace = (output / (name + '.log')).read_text()
        parses = [line for line in trace.splitlines()
                  if 'FE=> Parse(' in line and 'INSERT INTO pg_bind_target' in line]
        types = Counter(re.search(r'oids=([^)]*)', line).group(1) for line in parses)
        entry = {'case':name, 'seconds_diagnostic_only':seconds, 'rows':rows,
                 'binary_multiset_differences':differences, 'insert_parse_messages':len(parses),
                 'parameter_oid_vectors':dict(types)}
        report['results'].append(entry)
        (output / 'results.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(entry),flush=True)
        if args.expect_churn:
            assert len(parses) >= 64 and len(types) > 1, entry
        else:
            assert 1 <= len(parses) <= 4 and len(types) == 1, entry

    for target, value in [('smallint',32768), ('smallint',-32769),
                          ('integer',2147483648), ('integer',-2147483649)]:
        for attempt in range(1,5):
            sql('DROP TABLE IF EXISTS pg_bind_overflow; CREATE TABLE pg_bind_overflow(value '+target+')')
            config = job(destination='pg_bind_overflow', query='SELECT '+str(value)+'::bigint AS value')
            config['job']['content'][0]['writer']['parameter']['column'] = ['value']
            name = 'overflow-'+target+'-'+str(value)+'-'+str(attempt)
            run(runtime, config, output, name, expect_success=False)
            assert sql('SELECT count(*) FROM pg_bind_overflow') == '0'
            assert '22003' in (output/(name+'.log')).read_text()
            entry = {'case':name, 'input':value, 'target_type':target, 'rejected_without_truncation':True,
                     'target_rows':0, 'sqlstate':'22003'}
            report['results'].append(entry)
            (output/'results.json').write_text(json.dumps(report,indent=2))
            print(json.dumps(entry),flush=True)


if __name__ == '__main__':
    main()
