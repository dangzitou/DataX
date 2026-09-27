from pathlib import Path
import json,sys,time
sys.path.insert(0,'benchmarks')
from postgresql_checks import sql
from postgresql_scenarios import disk_guard,COLUMNS

out=Path('/tmp/datax-perf/atomic-hash-probe');out.mkdir(exist_ok=True)
assert not (out/'results.json').exists()

def query(mode,source='pg_perf_source',columns=COLUMNS):
    marker='MATERIALIZED ' if mode=='materialized' else ''
    fence=' OFFSET 0' if mode=='offset' else ''
    body='WITH hashes AS '+marker+'(SELECT encode(sha256(record_send(ROW('+','.join(columns)+"))),'hex') h FROM "+source+fence+') SELECT count(*)'
    for start in [1,17,33,49]:
        body+=",coalesce(sum(('x'||substr(h,%d,16))::bit(64)::bigint),0)::text"%start
    return body+' FROM hashes'

report={'scope':'Real PG fingerprint equality and native SQL diagnostics, not whole Engine performance',
        'postgres':sql('SELECT version()'),'plans':{},'results':[]}
for mode in ['previous','materialized','offset']:
    disk_guard(out)
    script="BEGIN;SET LOCAL temp_file_limit='1536MB';EXPLAIN (ANALYZE,BUFFERS,VERBOSE,TIMING OFF,FORMAT JSON) "+query(mode)+';ROLLBACK;'
    (out/(mode+'.sql')).write_text(script)
    plan=json.loads(sql(script));(out/(mode+'-plan.json')).write_text(json.dumps(plan,indent=2))
    nodes=[]
    def walk(node):
        nodes.append({k:node[k] for k in ['Node Type','Strategy','Actual Rows','Output','Temp Read Blocks','Temp Written Blocks'] if k in node})
        for child in node.get('Plans',[]):walk(child)
    walk(plan[0]['Plan'])
    report['plans'][mode]={'execution_ms':plan[0]['Execution Time'],'nodes':nodes}
    (out/'results.json').write_text(json.dumps(report,indent=2))
    print(mode,plan[0]['Execution Time'],'ms',flush=True)

for source,columns in [('pg_perf_source',COLUMNS),('pg_perf_source WHERE false',COLUMNS),
                       ('pg_atomic_source',['id','signed','amount','ts','tz','flag','txt','bin','day'])]:
    for attempt in range(1,5):
        outputs={mode:sql("BEGIN;SET LOCAL temp_file_limit='1536MB';"+query(mode,source,columns)+';ROLLBACK;')
                 for mode in ['previous','materialized','offset']}
        assert len(set(outputs.values()))==1,outputs
        if 'WHERE false' in source:assert outputs['previous']=='0|0|0|0|0',outputs
        report['results'].append({'source':source,'attempt':attempt,'outputs':outputs})
        (out/'results.json').write_text(json.dumps(report,indent=2))
print('PASS',len(report['results']),'three-way fingerprint comparisons',flush=True)
