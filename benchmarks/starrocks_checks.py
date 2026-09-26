#!/usr/bin/env python3
"""Real PostgreSQL -> StarRocks/Doris -> PostgreSQL text-fidelity regression."""
import argparse
import copy
import json
import subprocess
from pathlib import Path
from postgresql_checks import sql, job
from mysql_querysql import run


def sr(query, container="datax-perf-starrocks"):
    return subprocess.check_output(['docker', 'exec', '-i', container, 'mysql',
        '--default-character-set=utf8mb4', '-uroot', '-h127.0.0.1', '-P9030', '-N'],
        input=query, text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--backend', choices=['starrocks', 'doris'], default='starrocks')
    args = p.parse_args()
    backend = args.backend
    jdbc_port, load_port = (29030, 28040) if backend == 'starrocks' else (29031, 28041)
    def execute_sql(query):
        return sr(query, 'datax-perf-' + backend)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    metadata = {'backend': backend, 'backends': execute_sql('SHOW BACKENDS'),
                'baseline': json.loads((args.baseline / 'build-metadata.json').read_text()),
                'candidate': json.loads((args.candidate / 'build-metadata.json').read_text())}
    def record(entry):
        results.append(entry)
        (output / 'results.json').write_text(json.dumps({'metadata': metadata, 'results': results}, indent=2))
        print(json.dumps(entry), flush=True)

    sql("""DROP TABLE IF EXISTS pg_sr_source;
        CREATE TABLE pg_sr_source(id bigint, txt text);
        INSERT INTO pg_sr_source VALUES (1,chr(92)||'N'),(2,NULL),(3,'plain');""")
    execute_sql('CREATE DATABASE IF NOT EXISTS datax_bench; DROP TABLE IF EXISTS datax_bench.sr_target; '
       'CREATE TABLE datax_bench.sr_target(id BIGINT,txt STRING) DUPLICATE KEY(id) '
       'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1");')
    execute_sql("CREATE USER IF NOT EXISTS datax IDENTIFIED BY 'datax-local-benchmark';")
    execute_sql("GRANT SELECT ON ALL TABLES IN DATABASE datax_bench TO USER datax;" if backend == 'starrocks'
                else "GRANT SELECT_PRIV ON datax_bench.* TO 'datax';")
    config = job(query='SELECT * FROM pg_sr_source ORDER BY id')
    config['job']['content'][0]['writer'] = {'name': backend + 'writer', 'parameter': {
        'username': 'root', 'password': '', 'column': ['id', 'txt'], 'loadUrl': ['127.0.0.1:%d' % load_port],
        'connection': [{'table': ['sr_target'], 'jdbcUrl': 'jdbc:mysql://127.0.0.1:%d/datax_bench' % jdbc_port,
                        'selectedDatabase': 'datax_bench'}]}}
    for variant, runtime in [('baseline', args.baseline), ('candidate', args.candidate)]:
        for attempt in range(4):
            name = '%s-csv-null-%d' % (variant, attempt + 1)
            execute_sql('TRUNCATE TABLE datax_bench.sr_target;')
            seconds = run(runtime.resolve(), config, output, name, expect_success=variant == 'baseline')
            actual = execute_sql("SELECT id,IFNULL(HEX(txt),'NULL') FROM datax_bench.sr_target ORDER BY id")
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
        execute_sql('TRUNCATE TABLE datax_bench.sr_target;')
        seconds = run(args.candidate.resolve(), json_config, output, name)
        actual = execute_sql("SELECT id,IFNULL(HEX(txt),'NULL') FROM datax_bench.sr_target ORDER BY id")
        assert actual == expected, (actual, expected)
        record({'case': name, 'seconds': seconds, 'exact_match': True, 'rows': 5})

    # Exercise the real reader, with duplicates visible (no target PK).
    for attempt in range(4):
        name = 'candidate-%sreader-pg-%d' % (backend, attempt + 1)
        sql('DROP TABLE IF EXISTS pg_sr_target; CREATE TABLE pg_sr_target (LIKE pg_sr_source)')
        read_config = job(destination='pg_sr_target')
        read_config['job']['content'][0]['reader'] = {'name': backend + 'reader', 'parameter': {
            'username': 'datax', 'password': 'datax-local-benchmark', 'connection': [{
                'jdbcUrl': ['jdbc:mysql://127.0.0.1:%d/datax_bench?characterEncoding=utf8' % jdbc_port],
                'querySql': ['SELECT * FROM sr_target ORDER BY id']}]}}
        read_config['job']['content'][0]['writer']['parameter']['column'] = ['id', 'txt']
        seconds = run(args.candidate.resolve(), read_config, output, name)
        differences = int(sql('SELECT count(*) FROM ((TABLE pg_sr_source EXCEPT ALL TABLE pg_sr_target) '
                              'UNION ALL (TABLE pg_sr_target EXCEPT ALL TABLE pg_sr_source)) d'))
        assert differences == 0, differences
        record({'case': name, 'seconds': seconds, 'exact_match': True, 'rows': 5})


if __name__ == '__main__':
    main()
