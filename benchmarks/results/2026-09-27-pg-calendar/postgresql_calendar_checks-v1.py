#!/usr/bin/env python3
"""PG local DATE/TIMESTAMP fidelity across DST, skipped days and calendar boundaries."""
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
    runtime, out = args.runtime.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out/'results.json').exists(), 'Use a fresh evidence directory'
    sql("""DROP TABLE IF EXISTS pg_calendar_source;
        CREATE TABLE pg_calendar_source(id bigint, ts timestamp(6), tz timestamptz(6), day date);
        INSERT INTO pg_calendar_source VALUES
        (1,NULL,NULL,NULL),
        (2,'2024-03-10 02:30:00.123456','2024-03-10 01:30:00.123456-05','2024-03-10'),
        (3,'2024-11-03 01:30:00.654321','2024-11-03 01:30:00.654321-04','2024-11-03'),
        (4,'2024-11-03 01:30:00.654321','2024-11-03 01:30:00.654321-05','2024-11-03'),
        (5,'1991-04-14 02:30:00.123456','1991-04-14 03:30:00.123456+09','1991-04-14'),
        (6,'2011-12-30 12:34:56.123456','2011-12-30 12:34:56.123456+00','2011-12-30'),
        (7,'1582-10-10 12:34:56.123456','1582-10-10 12:34:56.123456+00','1582-10-10'),
        (8,'0001-01-01 12:34:56.123456 BC','0001-01-01 12:34:56.123456+00 BC','0001-01-01 BC'),
        (9,'infinity','infinity','infinity'),
        (10,'-infinity','-infinity','-infinity'),
        (11,'294276-12-31 00:00:00.123456',NULL,'5874897-12-31'),
        (12,'4713-01-01 00:00:00.123456 BC',NULL,'4713-01-01 BC'),
        (13,'1969-12-31 23:59:59.999999','1969-12-31 23:59:59.999999+00','1969-12-31');
        INSERT INTO pg_calendar_source SELECT * FROM pg_calendar_source WHERE id IN (1,2,8)""")
    source = 'SELECT record_send(ROW(id,ts,tz,day)) FROM pg_calendar_source'
    target = 'SELECT record_send(ROW(id,ts,tz,day)) FROM pg_calendar_target'
    report = {'scope': __doc__, 'postgres': sql('SELECT version()'),
              'build': json.loads((runtime/'build-metadata.json').read_text()),
              'source': json.loads(sql('SELECT json_agg(row_to_json(s)) FROM pg_calendar_source s')),
              'results': []}
    def save(): (out/'results.json').write_text(json.dumps(report, indent=2))
    for zone in ['UTC', 'America/New_York', 'Asia/Shanghai', 'Pacific/Apia']:
        for binary in [False, True]:
            for copy in [False, True]:
                for atomic in [False, True]:
                    name = zone.replace('/', '-')+('-binary' if binary else '-text')
                    name += ('-copy' if copy else '-jdbc')+('-atomic' if atomic else '-append')
                    report['pending_run'] = name; save()
                    sql('DROP TABLE IF EXISTS pg_calendar_target; CREATE TABLE pg_calendar_target (LIKE pg_calendar_source)')
                    cfg = job(destination='pg_calendar_target', query='SELECT * FROM pg_calendar_source ORDER BY id')
                    cfg['common']['column']['timeZone'] = zone
                    content = cfg['job']['content'][0]
                    content['reader']['parameter']['connection'][0]['jdbcUrl'][0] += (
                        '?prepareThreshold=-1&binaryTransferEnable=1082,1114,1184&binaryTransferDisable=int4'
                        if binary else '?binaryTransfer=false')
                    content['writer']['parameter'].update(column=['id','ts','tz','day'], useCopy=copy)
                    if atomic:
                        content['reader']['parameter']['consistentSnapshot'] = True
                        content['writer']['parameter']['atomicBatchId'] = name
                    checks = []
                    for suffix in (['publish','same-id'] if atomic else ['append']):
                        case = name+'-'+suffix
                        seconds = run(runtime, cfg, out, case, jvm_options=['-Duser.timezone='+zone])
                        assert 'JVM TimeZone: '+zone+',' in (out/(case+'.log')).read_text(), case
                        differences = int(sql('SELECT count(*) FROM (('+source+' EXCEPT ALL '+target+') UNION ALL ('
                                              +target+' EXCEPT ALL '+source+')) d'))
                        rows = int(sql('SELECT count(*) FROM pg_calendar_target'))
                        assert rows == 16 and differences == 0, (case, rows, differences)
                        check = {'case':case, 'seconds_diagnostic_only':seconds, 'rows':rows,
                                 'binary_multiset_differences':differences}
                        if atomic:
                            ledger = int(sql("SELECT count(*) FROM __datax_atomic_batches_v1 WHERE target_oid='pg_calendar_target'::regclass"))
                            assert ledger == 1, (case, ledger)
                            check['ledger_rows'] = ledger
                        checks.append(check)
                    report['results'].append({'case':name,'checks':checks})
                    report.pop('pending_run'); save(); print(name+' passed', flush=True)
    print('PASS', len(report['results']), 'calendar scenarios')


if __name__ == '__main__':
    main()
