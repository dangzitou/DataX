from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_checks import sql
from postgresql_scenarios import disk_guard,COLUMNS

out=Path('/tmp/datax-perf/atomic-publication-plans');out.mkdir(exist_ok=True)
assert not (out/'results.json').exists()
columns=','.join(COLUMNS)
insert='INSERT INTO pg_group_probe ('+columns+') SELECT '+columns+' FROM pg_perf_source RETURNING '
previous=('WITH inserted AS ('+insert+columns+'), sent AS (SELECT record_send(ROW('+columns+')) b FROM pg_perf_source), '
          'written AS (SELECT record_send(ROW('+columns+')) b FROM inserted) SELECT count(*) FROM '
          '((SELECT b FROM sent EXCEPT ALL SELECT b FROM written) UNION ALL '
          '(SELECT b FROM written EXCEPT ALL SELECT b FROM sent)) differences')
grouped=('WITH inserted AS ('+insert+'record_send(ROW('+columns+')) b) SELECT count(*) FROM ('
         'SELECT b FROM (SELECT record_send(ROW('+columns+')) b,1 n FROM pg_perf_source '
         'UNION ALL SELECT b,-1 n FROM inserted) compared GROUP BY b HAVING sum(n)<>0) differences')
results=[]
try:
    for name,query,settings in [('previous',previous,''),('grouped',grouped,''),
                                ('grouped-sort',grouped,'SET LOCAL enable_hashagg=off;')]:
        storage=disk_guard(out)
        sql('DROP TABLE IF EXISTS pg_group_probe; CREATE TABLE pg_group_probe (LIKE pg_perf_source INCLUDING ALL)')
        script="BEGIN;SET LOCAL temp_file_limit='1536MB';"+settings+'EXPLAIN (ANALYZE,BUFFERS,TIMING OFF,FORMAT JSON) '+query+';ROLLBACK;'
        (out/(name+'.sql')).write_text(script)
        plan=json.loads(sql(script));(out/(name+'.json')).write_text(json.dumps(plan,indent=2))
        assert sql('SELECT count(*) FROM pg_group_probe')=='0'
        nodes=[]
        def walk(node):
            if node['Node Type'] in ['Aggregate','Sort','SetOp']:
                nodes.append({key:node[key] for key in ['Node Type','Strategy','Plan Rows','Actual Rows',
                    'HashAgg Batches','Disk Usage','Temp Read Blocks','Temp Written Blocks','Sort Method','Sort Space Used'] if key in node})
            for child in node.get('Plans',[]):walk(child)
        walk(plan[0]['Plan'])
        results.append({'case':name,'scope':'One native SQL diagnostic, not whole-Engine performance',
                        'execution_ms':plan[0]['Execution Time'],'nodes':nodes,'storage_before':storage})
        (out/'results.json').write_text(json.dumps(results,indent=2));print(results[-1],flush=True)
finally:
    sql('DROP TABLE IF EXISTS pg_group_probe')
