#!/usr/bin/env python3
"""Small real-Engine integer reads: exact values and multiplicities, text/binary requests."""
import argparse
import json
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import job, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'results.json').exists(), 'Use a fresh evidence directory'
    columns = ['ni16', 'ni32', 'ni64', 'huge', 'fraction']
    sql("""DROP TABLE IF EXISTS pg_integer_source;
        CREATE TABLE pg_integer_source(ni16 smallint, ni32 integer, ni64 bigint,
            huge numeric(38,0), fraction numeric(38,18));
        INSERT INTO pg_integer_source VALUES
        (NULL,NULL,NULL,NULL,NULL),
        (0,0,0,0,0),
        (-1,-1,-1,-1,-0.000000000000000001),
        (1,1,1,1,0.000000000000000001),
        (-32768,-2147483648,'-9223372036854775808',
            -99999999999999999999999999999999999999,-12345678901234567890.123456789012345678),
        (32767,2147483647,9223372036854775807,
            99999999999999999999999999999999999999,12345678901234567890.123456789012345678),
        (7,NULL,9223372036854775806,18446744073709551615,123.450000000000000000);
        INSERT INTO pg_integer_source SELECT * FROM pg_integer_source WHERE ni16 IS NULL OR ni16=7""")
    logging = output / 'jdbc-logging.properties'
    logging.write_text('handlers=java.util.logging.ConsoleHandler\n.level=WARNING\n'
                       'java.util.logging.ConsoleHandler.level=FINEST\norg.postgresql.level=FINEST\n')
    report = {'scope': __doc__, 'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'postgres': sql('SELECT version()'), 'results': []}
    for wire in ['text', 'binary-requested']:
        for reader_mode in ['query', 'table']:
            for attempt in range(1, 5):
                name = wire + '-' + reader_mode + '-' + str(attempt)
                sql('DROP TABLE IF EXISTS pg_integer_target; '
                    'CREATE TABLE pg_integer_target (LIKE pg_integer_source)')
                config = job(destination='pg_integer_target', query='SELECT * FROM pg_integer_source')
                reader = config['job']['content'][0]['reader']['parameter']
                connection = reader['connection'][0]
                connection['jdbcUrl'][0] += ('?binaryTransfer=false' if wire == 'text' else
                    '?prepareThreshold=-1&binaryTransfer=true&binaryTransferEnable=int2,int4,int8')
                reader['fetchSize'] = 2
                if reader_mode == 'table':
                    del connection['querySql']
                    connection['table'] = ['pg_integer_source']
                    reader['column'] = columns
                config['job']['content'][0]['writer']['parameter']['column'] = columns
                seconds = run(runtime, config, output, name,
                    jvm_options=['-Djava.util.logging.config.file=' + str(logging)])
                differences = int(sql('SELECT count(*) FROM '
                    '((TABLE pg_integer_source EXCEPT ALL TABLE pg_integer_target) UNION ALL '
                    '(TABLE pg_integer_target EXCEPT ALL TABLE pg_integer_source)) d'))
                rows = int(sql('SELECT count(*) FROM pg_integer_target'))
                assert rows == 9 and differences == 0, (rows, differences)
                entry = {'case': name, 'seconds_diagnostic_only': seconds,
                         'rows': rows, 'multiset_differences': differences}
                report['results'].append(entry)
                (output / 'results.json').write_text(json.dumps(report, indent=2))
                print(json.dumps(entry), flush=True)


if __name__ == '__main__':
    main()
