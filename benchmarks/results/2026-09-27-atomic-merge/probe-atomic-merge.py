"""Real PG SQL probe; all inserted rows are rolled back, not an Engine benchmark."""
from pathlib import Path
import json, sys
sys.path.insert(0, 'benchmarks')
from postgresql_checks import sql
from postgresql_scenarios import disk_guard, COLUMNS

out = Path('/tmp/datax-perf/atomic-merge-probe'); out.mkdir(exist_ok=True)
assert not (out/'results.json').exists()

def queries(target, stage, names):
    columns = ','.join(names)
    source = ','.join('s.'+name for name in names)
    written = ','.join('t.'+name for name in names)
    grouped = ('WITH inserted AS (INSERT INTO '+target+' ('+columns+') SELECT '+columns+' FROM '+stage
        +' RETURNING record_send(ROW('+columns+')) b) SELECT count(*) FROM (SELECT b FROM ('
        +'SELECT record_send(ROW('+columns+')) b,1 n FROM '+stage
        +' UNION ALL SELECT b,-1 n FROM inserted) compared GROUP BY b HAVING sum(n)<>0) differences')
    merge = ('WITH inserted AS (MERGE INTO '+target+' AS t USING '+stage+' AS s ON false '
        +'WHEN NOT MATCHED THEN INSERT ('+columns+') VALUES ('+source+') RETURNING '
        +'record_send(ROW('+source+')) IS NOT DISTINCT FROM record_send(ROW('+written+')) AS exact) '
        +'SELECT count(*),count(*) FILTER (WHERE NOT exact) FROM inserted')
    return grouped, merge

cases = [
    ('duplicates-nulls-special', 'id bigint,n numeric,f float8,b bytea,txt text', None,
     "(1,1.2300,'-0',decode('000aff','hex'),'中文😀'),(1,1.2300,'-0',decode('000aff','hex'),'中文😀'),"
     "(NULL,NULL,NULL,NULL,NULL),(2,'NaN','Infinity',decode('','hex'),''),(3,'Infinity','NaN',NULL,'x')",
     ['id','n','f','b','txt'], 5, True, ''),
    ('numeric-rounding', 'n numeric', 'n numeric(9,2)', '(1.2345),(1.2300),(NULL)', ['n'], 3, False, ''),
    ('timestamp-rounding', 'ts timestamp,tz timestamptz', 'ts timestamp(3),tz timestamptz(3)',
     "('2024-01-01 00:00:00.123456','2024-01-01 00:00:00.999999+08')", ['ts','tz'], 1, False, ''),
    ('quoted-identifiers', '"comma,name" text,"quote""name" bigint,"t" text,"s" text', None,
     "('hi',1,'x','y'),(NULL,NULL,NULL,NULL)", ['"comma,name"','"quote""name"','"t"','"s"'], 2, True, ''),
    ('empty', 'id bigint', None, None, ['id'], 0, True, ''),
    ('existing-default', 'id bigint', 'id bigint,extra text DEFAULT \'default\'', '(1),(1),(NULL)', ['id'], 3, True,
     "INSERT INTO probe_target(id,extra) VALUES(99,'existing');"),
    ('partitioned', 'id bigint,txt text', None, "(1,'x'),(2,NULL),(1,'x')", ['id','txt'], 3, True,
     'partitioned'),
]
report = {'postgres': sql('SELECT version()'), 'scope': __doc__, 'checks': [], 'plans': []}
def save(): (out/'results.json').write_text(json.dumps(report, indent=2))
for name, source_type, target_type, values, names, count, exact, setup in cases:
    for attempt in range(1,5):
        for variant, query in zip(['grouped','merge'], queries('probe_target','probe_stage',names)):
            create = 'CREATE TEMP TABLE probe_stage ('+source_type+');'
            create += 'CREATE TEMP TABLE probe_target ('+(target_type or source_type)+')'
            if setup == 'partitioned':
                create += ' PARTITION BY RANGE(id); CREATE TEMP TABLE probe_partition PARTITION OF probe_target DEFAULT;'
            else:
                create += ';'+setup
            if values: create += 'INSERT INTO probe_stage VALUES '+values+';'
            script = 'BEGIN;'+create+query+';ROLLBACK;'
            actual = sql(script).split('|')
            assert (int(actual[-1]) == 0) == exact, (name,variant,actual)
            if variant == 'merge': assert int(actual[0]) == count, (name,actual)
            report['checks'].append(dict(case=name,attempt=attempt,variant=variant,actual=actual,exact=exact))
            (out/(name+'-'+variant+'.sql')).write_text(script)
            save()

for variant, query in zip(['grouped','merge'], queries('probe_target','pg_perf_source',COLUMNS)):
    storage = disk_guard(out)
    script = ("BEGIN;SET LOCAL temp_file_limit='1536MB';CREATE TEMP TABLE probe_target (LIKE pg_perf_source INCLUDING ALL);"
              +'EXPLAIN (ANALYZE,BUFFERS,TIMING OFF,FORMAT JSON) '+query+';ROLLBACK;')
    plan = json.loads(sql(script)); (out/(variant+'-plan.json')).write_text(json.dumps(plan,indent=2))
    (out/(variant+'-plan.sql')).write_text(script)
    # Temp-table writes differ from the production logged target; this is only a query-plan diagnostic.
    report['plans'].append({'variant': variant,'scope':'Single native SQL with temporary target, not Engine performance',
        'execution_ms':plan[0]['Execution Time'],
        'temp_read_blocks':plan[0]['Plan'].get('Temp Read Blocks',0),
        'temp_written_blocks':plan[0]['Plan'].get('Temp Written Blocks',0), 'storage_before':storage})
    save()
print('PASS',len(report['checks']),'SQL checks; plans:',report['plans'])
