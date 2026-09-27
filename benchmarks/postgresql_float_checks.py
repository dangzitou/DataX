#!/usr/bin/env python3
"""Real PG floating-point round trips; compare binary values and duplicate multiplicities."""
import argparse
import json
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import job, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-filter-failure', action='store_true')
    parser.add_argument('--expect-binary-numeric-failure', action='store_true')
    parser.add_argument('--jdbc-only', action='store_true', help='Untouched upstream has no COPY writer')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'results.json').exists(), 'Use a fresh evidence directory'
    sql("""DROP TABLE IF EXISTS pg_float_source;
        CREATE TABLE pg_float_source(id bigint, f4 real, f8 double precision, n numeric);
        INSERT INTO pg_float_source VALUES
        (0,NULL,NULL,NULL), (1,'0','0',0), (2,'-0','-0',0),
        (3,'NaN','NaN','NaN'), (4,'Infinity','Infinity','Infinity'),
        (5,'-Infinity','-Infinity','-Infinity'),
        (6,'1.401298464324817e-45','4.9406564584124654e-324',0.000000000000000001),
        (7,'-1.401298464324817e-45','-4.9406564584124654e-324',-0.000000000000000001),
        (8,'3.4028234663852886e38','1.7976931348623157e308',99999999999999999999.999999999999999999),
        (9,'-3.4028234663852886e38','-1.7976931348623157e308',-99999999999999999999.999999999999999999),
        (10,'1.0000001192092896','1.0000000000000002',1.234567890123456789),
        (11,'0.1','0.1',0.1);
        INSERT INTO pg_float_source SELECT * FROM pg_float_source WHERE id IN (0,2)""")
    projection = "id,encode(float4send(f4),'hex') AS f4_hex,encode(float8send(f8),'hex') AS f8_hex,n::text"
    source = 'SELECT ' + projection + ' FROM pg_float_source'
    target = 'SELECT ' + projection + ' FROM pg_float_target'
    report = {'scope': __doc__, 'postgres': sql('SELECT version()'),
              'build': json.loads((runtime / 'build-metadata.json').read_text()),
              'source': json.loads(sql('SELECT json_agg(row_to_json(s)) FROM (' + source + ' ORDER BY id) s')),
              'expect_filter_failure': args.expect_filter_failure,
              'expect_binary_numeric_failure': args.expect_binary_numeric_failure, 'results': []}
    for mode in (['jdbc'] if args.jdbc_only else ['jdbc', 'copy']):
        for wire in ['text', 'binary-requested']:
            for filtered in [False, True]:
                for attempt in range(1, 5):
                    name = '%s-%s-%s-%d' % (mode, wire, 'filter' if filtered else 'direct', attempt)
                    sql('DROP TABLE IF EXISTS pg_float_target; CREATE TABLE pg_float_target (LIKE pg_float_source)')
                    config = job(destination='pg_float_target', query='SELECT * FROM pg_float_source ORDER BY id')
                    content = config['job']['content'][0]
                    connection = content['reader']['parameter']['connection'][0]
                    connection['jdbcUrl'][0] += ('?binaryTransfer=false' if wire == 'text' else
                        '?prepareThreshold=-1&binaryTransfer=true&binaryTransferEnable=float4,float8')
                    content['writer']['parameter'].update(column=['id', 'f4', 'f8', 'n'], useCopy=mode == 'copy')
                    if filtered:
                        # No IEEE value is less than -Infinity; this filter must discard nothing.
                        content['transformer'] = [{'name': 'dx_filter', 'parameter': {
                            'columnIndex': 2, 'paras': ['<', '-Infinity']}}]
                    numeric_failure = wire == 'binary-requested' and args.expect_binary_numeric_failure
                    expected_failure = numeric_failure or (filtered and args.expect_filter_failure)
                    seconds = run(runtime, config, output, name, expect_success=not expected_failure)
                    differences = int(sql('SELECT count(*) FROM ((' + source + ' EXCEPT ALL ' + target
                        + ') UNION ALL (' + target + ' EXCEPT ALL ' + source + ')) d'))
                    actual = int(sql('SELECT count(*) FROM pg_float_target'))
                    entry = {'case': name, 'seconds_diagnostic_only': seconds, 'expected': 14,
                             'actual': actual, 'binary_multiset_differences': differences,
                             'expected_failure': expected_failure}
                    report['results'].append(entry)
                    (output / 'results.json').write_text(json.dumps(report, indent=2))
                    print(json.dumps(entry), flush=True)
                    if expected_failure:
                        log = (output / (name + '.log')).read_text()
                        if numeric_failure:
                            assert 'invalid sign in "numeric" value' in log, name
                        else:
                            assert 'Infinity' in log and '无法转换为Double' in log, name
                    else:
                        assert actual == 14 and differences == 0, entry


if __name__ == '__main__':
    main()
