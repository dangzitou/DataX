from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_checks import sql

out=Path('/tmp/datax-perf/atomic-hash-evaluations.json')
assert not out.exists()
sql('CREATE FUNCTION datax_hash_eval_probe(bytea) RETURNS bytea LANGUAGE plpgsql IMMUTABLE STRICT '
    'AS $$ BEGIN RETURN sha256($1); END $$')
rows=[]
try:
    for attempt in range(1,5):
        for mode,expected in [('previous',256),('materialized',64),('offset',64)]:
            materialized='MATERIALIZED ' if mode=='materialized' else ''
            fence=' OFFSET 0' if mode=='offset' else ''
            query='WITH hashes AS '+materialized+"(SELECT encode(datax_hash_eval_probe(record_send(ROW(id,tenant,amount,created,message,payload))),'hex') h FROM pg_perf_source WHERE id<=64"+fence+') SELECT count(*)'
            for start in [1,17,33,49]:query+=",coalesce(sum(('x'||substr(h,%d,16))::bit(64)::bigint),0)::text"%start
            query+=' FROM hashes'
            result=sql("BEGIN;SET LOCAL track_functions='pl';"+query+";SELECT calls FROM pg_stat_xact_user_functions WHERE funcid='datax_hash_eval_probe(bytea)'::regprocedure;ROLLBACK;").splitlines()
            assert len(result)==2 and int(result[1])==expected,(mode,result)
            rows.append({'mode':mode,'attempt':attempt,'records':64,'wrapped_hash_calls':int(result[1]),'fingerprint':result[0]})
    assert len({r['fingerprint'] for r in rows})==1
finally:
    sql('DROP FUNCTION datax_hash_eval_probe(bytea)')
out.write_text(json.dumps({'scope':'Real PG function-statistics probe using an immutable PL/pgSQL wrapper; not native sha256 timing or whole Engine performance','results':rows},indent=2))
print('PASS',len(rows),'evaluation-count checks')
