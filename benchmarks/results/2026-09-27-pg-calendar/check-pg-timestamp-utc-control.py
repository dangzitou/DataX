import json
import sys
from pathlib import Path
sys.path.insert(0, '/Users/dengzitao/Developer/projects/DataX/benchmarks')
from postgresql_checks import sql, job
from mysql_querysql import run

out=Path('/tmp/datax-perf/pg-timestamp-dst-before'); out.mkdir(exist_ok=False)
sql("""DROP TABLE IF EXISTS pg_timestamp_dst_source;
CREATE TABLE pg_timestamp_dst_source(id bigint, ts timestamp(6), tz timestamptz(6), day date);
INSERT INTO pg_timestamp_dst_source VALUES
(1,NULL,NULL,NULL),
(2,'2024-03-10 02:30:00.123456','2024-03-10 03:30:00.123456-04','2024-03-10'),
(3,'2024-11-03 01:30:00.654321','2024-11-03 01:30:00.654321-04','2024-11-03'),
(4,'1991-04-14 02:30:00.123456','1991-04-14 03:30:00.123456+09','1991-04-14');
INSERT INTO pg_timestamp_dst_source SELECT * FROM pg_timestamp_dst_source WHERE id=2;""")
report={'source':json.loads(sql('SELECT json_agg(row_to_json(s)) FROM pg_timestamp_dst_source s')),'results':[]}
for variant,atomic in [('baseline-all',False),('candidate-atomic-group-final',False),('candidate-atomic-group-final',True)]:
    runtime=Path('/tmp/datax-perf')/variant
    report[variant]=json.loads((runtime/'build-metadata.json').read_text())
    for attempt in range(1,5):
        name=variant+('-atomic' if atomic else '')+'-'+str(attempt)
        report['pending_run']=name; (out/'results.json').write_text(json.dumps(report,indent=2))
        sql('DROP TABLE IF EXISTS pg_timestamp_dst_target; CREATE TABLE pg_timestamp_dst_target (LIKE pg_timestamp_dst_source)')
        cfg=job(destination='pg_timestamp_dst_target',query='SELECT * FROM pg_timestamp_dst_source ORDER BY id')
        item=cfg['job']['content'][0]; item['writer']['parameter']['column']=['id','ts','tz','day']
        if atomic:
            item['reader']['parameter']['consistentSnapshot']=True
            item['writer']['parameter']['atomicBatchId']=name
        seconds=run(runtime,cfg,out,name,jvm_options=['-Duser.timezone=America/New_York'])
        src='SELECT record_send(ROW(id,ts,tz,day)) FROM pg_timestamp_dst_source'
        dst='SELECT record_send(ROW(id,ts,tz,day)) FROM pg_timestamp_dst_target'
        differences=int(sql('SELECT count(*) FROM (('+src+' EXCEPT ALL '+dst+') UNION ALL ('+dst+' EXCEPT ALL '+src+')) d'))
        changed=json.loads(sql('SELECT json_agg(row_to_json(t)) FROM pg_timestamp_dst_target t'))
        entry={'case':name,'seconds':seconds,'differences':differences,'target':changed}
        report['results'].append(entry); report.pop('pending_run'); (out/'results.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(entry),flush=True)
