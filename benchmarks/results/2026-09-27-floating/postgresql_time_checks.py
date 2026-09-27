#!/usr/bin/env python3
"""Real PG TIME/TIMETZ round trips, including microseconds, offsets and 24:00."""
import argparse
import json
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import sql, job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-loss', action='store_true')
    parser.add_argument('--mode', choices=['jdbc', 'copy', 'all'], default='all')
    parser.add_argument('--binary', action='store_true', help='Request forced binary TIME/TIMETZ in the source URL')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sql("""DROP TABLE IF EXISTS pg_time_source;
        CREATE TABLE pg_time_source(id bigint PRIMARY KEY, t time(6), z timetz(6));
        INSERT INTO pg_time_source VALUES
        (1,NULL,NULL),
        (2,'12:34:56.123456','12:34:56.123456+05:45'),
        (3,'23:59:59.999999','23:59:59.999999-08'),
        (4,'00:00:00.000001','00:00:00.000001+00'),
        (5,'24:00:00','24:00:00+14'),
        (6,'01:02:03.000004','01:02:03.000004-15:59'),
        (7,'04:05:06','04:05:06+05:45:30');""")
    report = {'scope': __doc__, 'postgres': sql('SELECT version()'),
              'build': json.loads((args.runtime / 'build-metadata.json').read_text()),
              'expect_loss': args.expect_loss, 'request_binary': args.binary, 'results': []}
    modes = ['jdbc', 'copy'] if args.mode == 'all' else [args.mode]
    for mode in modes:
        for attempt, zone in enumerate(['UTC', 'Asia/Shanghai', 'America/Los_Angeles', 'UTC'], 1):
            name = '%s-%d' % (mode, attempt)
            sql('DROP TABLE IF EXISTS pg_time_target; '
                'CREATE TABLE pg_time_target (LIKE pg_time_source INCLUDING ALL)')
            config = job(destination='pg_time_target', query='SELECT * FROM pg_time_source ORDER BY id')
            config['common']['column']['timeZone'] = zone
            if args.binary:
                connection = config['job']['content'][0]['reader']['parameter']['connection'][0]
                connection['jdbcUrl'][0] += '?prepareThreshold=-1&binaryTransferEnable=1083,1266&binaryTransferDisable=int4'
            config['job']['content'][0]['writer']['parameter'].update(
                column=['id', 't', 'z'], useCopy=mode == 'copy')
            seconds = run(args.runtime.resolve(), config, output, name,
                          jvm_options=['-Duser.timezone=' + zone])
            # Compare text as well: normalizing an offset must not hide a changed representation.
            source = 'SELECT id,t::text,z::text FROM pg_time_source'
            target = 'SELECT id,t::text,z::text FROM pg_time_target'
            differences = int(sql('SELECT count(*) FROM ((' + source + ' EXCEPT ALL ' + target +
                                  ') UNION ALL (' + target + ' EXCEPT ALL ' + source + ')) d'))
            actual = int(sql('SELECT count(*) FROM pg_time_target'))
            entry = {'case': name, 'jvm_timezone': zone, 'seconds': seconds, 'actual': actual,
                     'differences': differences, 'exact_match': differences == 0,
                     'source': json.loads(sql('SELECT json_agg(row_to_json(s)) FROM (' + source + ' ORDER BY id) s')),
                     'target': json.loads(sql('SELECT json_agg(row_to_json(s)) FROM (' + target + ' ORDER BY id) s'))}
            report['results'].append(entry)
            (output / 'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(entry), flush=True)
            assert actual == 7, entry
            assert (differences > 0) if args.expect_loss else (differences == 0), entry


if __name__ == '__main__':
    main()
