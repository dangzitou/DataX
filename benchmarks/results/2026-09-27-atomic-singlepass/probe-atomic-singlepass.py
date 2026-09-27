"""Native PG probe for combined exact publication/fingerprint; not an Engine speed claim."""
from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_checks import sql,COLUMNS
from postgresql_atomic_checks import seed
from postgresql_scenarios import COLUMNS as PERF_COLUMNS, disk_guard

out=Path('/tmp/datax-perf/atomic-singlepass-probe'); out.mkdir(exist_ok=False)
report={'scope':__doc__,'postgres':sql('SELECT version()'),'checks':[],'plans':[]}
def save(): (out/'results.json').write_text(json.dumps(report,indent=2))
def queries(source,names):
    cols=','.join(names); src=','.join('s.'+c for c in names); dst=','.join('t.'+c for c in names)
    sums=''.join(",coalesce(sum(('x'||substr(h,%d,16))::bit(64)::bigint),0)::text"%i for i in [1,17,33,49])
    old="WITH hashes AS (SELECT encode(sha256(record_send(ROW("+cols+"))),'hex') h FROM "+source+") SELECT count(*)"+sums+' FROM hashes'
    combined=('WITH inserted AS (MERGE INTO probe_target AS t USING '+source+' AS s ON false '
        +'WHEN NOT MATCHED THEN INSERT ('+cols+') VALUES ('+src+') RETURNING '
        +"encode(sha256(record_send(ROW("+src+"))),'hex') h,"
        +'record_send(ROW('+src+')) IS NOT DISTINCT FROM record_send(ROW('+dst+')) AS exact) '
        +'SELECT count(*)'+sums+',count(*) FILTER (WHERE NOT exact) FROM inserted')
    return old,combined

for attempt in range(1,5):
    for case in ['duplicates-nulls-special','empty','numeric-rounding','timestamp-rounding','quoted-columns','partitioned']:
        seed(); names=COLUMNS
        if case=='quoted-columns':
            sql('ALTER TABLE pg_atomic_source RENAME txt TO "t,x"; ALTER TABLE pg_atomic_source RENAME bin TO "s""q"')
            names=['"t,x"' if n=='txt' else '"s""q"' if n=='bin' else n for n in COLUMNS]
        source='pg_atomic_source'
        if case=='empty': source='(SELECT * FROM pg_atomic_source WHERE false)'
        old,combined=queries(source,names)
        setup='CREATE TEMP TABLE probe_target (LIKE pg_atomic_source)'
        if case=='partitioned': setup+=' PARTITION BY RANGE(id); CREATE TEMP TABLE probe_partition PARTITION OF probe_target DEFAULT'
        setup+=';'
        if case=='numeric-rounding': setup+='ALTER TABLE probe_target ALTER COLUMN amount TYPE numeric(38,6);'
        if case=='timestamp-rounding': setup+='ALTER TABLE probe_target ALTER COLUMN ts TYPE timestamp(3);'
        script='BEGIN;'+setup+old+';'+combined+';ROLLBACK;'
        result=sql(script).splitlines(); expected=result[0].split('|'); actual=result[1].split('|')
        assert actual[:5]==expected, (case,expected,actual)
        changed=int(actual[5]); assert (changed>0)==case.endswith('rounding'),(case,actual)
        report['checks'].append({'case':case,'attempt':attempt,'old_count_and_fingerprint':expected,
                                'combined_count_and_fingerprint':actual[:5],'changed_rows':changed})
        (out/(case+'.sql')).write_text(script); save()

old,combined=queries('pg_perf_source',PERF_COLUMNS)
storage=disk_guard(out)
script="BEGIN;SET LOCAL temp_file_limit='1536MB';CREATE TEMP TABLE probe_target (LIKE pg_perf_source INCLUDING ALL);EXPLAIN (ANALYZE,BUFFERS,TIMING OFF,FORMAT JSON) "+combined+';ROLLBACK;'
plan=json.loads(sql(script)); (out/'combined-plan.json').write_text(json.dumps(plan,indent=2)); (out/'combined-plan.sql').write_text(script)
report['plans'].append({'scope':'Single SQL into a TEMP target, not whole Engine or logged-target throughput',
    'execution_ms':plan[0]['Execution Time'],'temp_read_blocks':plan[0]['Plan'].get('Temp Read Blocks',0),
    'temp_written_blocks':plan[0]['Plan'].get('Temp Written Blocks',0),'storage_before':storage})
save(); print('PASS',len(report['checks']),'native SQL checks;',report['plans'],flush=True)
