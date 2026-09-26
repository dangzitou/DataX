#!/usr/bin/env python3
"""Real PostgreSQL -> StarRocks -> PostgreSQL text-fidelity regression."""
import argparse
import copy
import json
import subprocess
from pathlib import Path
from postgresql_checks import sql, job
from mysql_querysql import run


def sr(query):
    return subprocess.check_output(['docker', 'exec', '-i', 'datax-perf-starrocks', 'mysql',
        '--default-character-set=utf8mb4', '-uroot', '-h127.0.0.1', '-P9030', '-N'],
        input=query, text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    metadata = {'starrocks_backends': sr('SHOW BACKENDS'),
                'baseline': json.loads((args.baseline / 'build-metadata.json').read_text()),
                'candidate': json.loads((args.candidate / 'build-metadata.json').read_text())}
    def record(entry):
        results.append(entry)
        (output / 'results.json').write_text(json.dumps({'metadata': metadata, 'results': results}, indent=2))
        print(json.dumps(entry), flush=True)

    sql("""DROP TABLE IF EXISTS pg_sr_source;
        CREATE TABLE pg_sr_source(id bigint, txt text);
        INSERT INTO pg_sr_source VALUES (1,chr(92)||'N'),(2,NULL),(3,'plain');""")
    sr('CREATE DATABASE IF NOT EXISTS datax_bench; DROP TABLE IF EXISTS datax_bench.sr_target; '
       'CREATE TABLE datax_bench.sr_target(id BIGINT,txt STRING) DUPLICATE KEY(id) '
       'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1");')
    sr("CREATE USER IF NOT EXISTS datax IDENTIFIED BY 'datax-local-benchmark'; "
       "GRANT SELECT ON ALL TABLES IN DATABASE datax_bench TO USER datax;")
    config = job(query='SELECT * FROM pg_sr_source ORDER BY id')
    config['job']['content'][0]['writer'] = {'name': 'starrockswriter', 'parameter': {
        'username': 'root', 'password': '', 'column': ['id', 'txt'], 'loadUrl': ['127.0.0.1:28040'],
        'connection': [{'table': ['sr_target'], 'jdbcUrl': 'jdbc:mysql://127.0.0.1:29030/datax_bench',
                        'selectedDatabase': 'datax_bench'}]}}
    for variant, runtime in [('baseline', args.baseline), ('candidate', args.candidate)]:
        for attempt in range(4):
            name = '%s-csv-null-%d' % (variant, attempt + 1)
            sr('TRUNCATE TABLE datax_bench.sr_target;')
            seconds = run(runtime.resolve(), config, output, name, expect_success=variant == 'baseline')
            actual = sr("SELECT id,IFNULL(HEX(txt),'NULL') FROM datax_bench.sr_target ORDER BY id")
            if variant == 'baseline':
                assert actual == '1\tNULL\n2\tNULL\n3\t706C61696E', actual
                record({'case': name, 'seconds': seconds, 'silent_text_to_null_reproduced': True,
                        'actual': actual, 'expected': '1\t5C4E\n2\tNULL\n3\t706C61696E'})
            else:
                assert actual == '', actual
                assert 'Unsafe CSV value' in (output / (name + '.log')).read_text()
                record({'case': name, 'seconds': seconds, 'unsafe_csv_rejected': True, 'target_empty': True})

    # Reuse the existing JSON implementation: no custom escaping or dependencies.
    sql("INSERT INTO pg_sr_source VALUES (4,'中文😀'||chr(10)||chr(9)||chr(92)||chr(34)),(5,'')")
    expected = sql("SELECT id||chr(9)||coalesce(upper(encode(convert_to(txt,'UTF8'),'hex')),'NULL') "
                   "FROM pg_sr_source ORDER BY id")
    json_config = copy.deepcopy(config)
    json_config['job']['content'][0]['writer']['parameter']['loadProps'] = {
        'format': 'json', 'strip_outer_array': True, 'strict_mode': True, 'max_filter_ratio': 0}
    for attempt in range(4):
        name = 'candidate-json-text-%d' % (attempt + 1)
        sr('TRUNCATE TABLE datax_bench.sr_target;')
        seconds = run(args.candidate.resolve(), json_config, output, name)
        actual = sr("SELECT id,IFNULL(HEX(txt),'NULL') FROM datax_bench.sr_target ORDER BY id")
        assert actual == expected, (actual, expected)
        record({'case': name, 'seconds': seconds, 'exact_match': True, 'rows': 5})

    # Exercise the real starrocksreader, with duplicates visible (no target PK).
    for attempt in range(4):
        name = 'candidate-starrocksreader-pg-%d' % (attempt + 1)
        sql('DROP TABLE IF EXISTS pg_sr_target; CREATE TABLE pg_sr_target (LIKE pg_sr_source)')
        read_config = job(destination='pg_sr_target')
        read_config['job']['content'][0]['reader'] = {'name': 'starrocksreader', 'parameter': {
            'username': 'datax', 'password': 'datax-local-benchmark', 'connection': [{
                'jdbcUrl': ['jdbc:mysql://127.0.0.1:29030/datax_bench?characterEncoding=utf8'],
                'querySql': ['SELECT * FROM sr_target ORDER BY id']}]}}
        read_config['job']['content'][0]['writer']['parameter']['column'] = ['id', 'txt']
        seconds = run(args.candidate.resolve(), read_config, output, name)
        differences = int(sql('SELECT count(*) FROM ((TABLE pg_sr_source EXCEPT ALL TABLE pg_sr_target) '
                              'UNION ALL (TABLE pg_sr_target EXCEPT ALL TABLE pg_sr_source)) d'))
        assert differences == 0, differences
        record({'case': name, 'seconds': seconds, 'exact_match': True, 'rows': 5})


if __name__ == '__main__':
    main()
