#!/usr/bin/env python3
"""Native PG COPY: real full-field validation and fail-closed transaction checks."""
import argparse
import json
from pathlib import Path
from postgresql_checks import sql, job, validate, COLUMNS
from mysql_querysql import run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime',type=Path); p.add_argument('output',type=Path)
    args=p.parse_args(); out=args.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    results=[]
    def record(e):
        results.append(e)
        (out/'results.json').write_text(json.dumps({'build':json.loads((args.runtime/'build-metadata.json').read_text()),
                                                   'results':results},indent=2))
        print(json.dumps(e),flush=True)
    def config(query='SELECT * FROM pg_copy_source ORDER BY id',destination='pg_copy_target'):
        c=job(destination=destination,query=query)
        c['job']['content'][0]['writer']['parameter']['useCopy']=True
        return c
    def reset():
        sql('DROP TABLE IF EXISTS pg_copy_target; CREATE TABLE pg_copy_target (LIKE pg_copy_source INCLUDING ALL)')
    def execute(name,c,success=True,zone='UTC'):
        return run(args.runtime.resolve(),c,out,name,expect_success=success,jvm_options=['-Duser.timezone='+zone])
    # Prior test creates pg_source; copy its fixture so this suite is independent
    # of the writer checks' trigger/fallback target and keeps their source intact.
    sql('''DROP TABLE IF EXISTS pg_copy_source; CREATE TABLE pg_copy_source (LIKE pg_source INCLUDING ALL);
        INSERT INTO pg_copy_source TABLE pg_source;
        INSERT INTO pg_copy_source SELECT 5,signed,amount,ts,tz,flag,chr(92)||'N',bin,day FROM pg_source WHERE id=2;
        INSERT INTO pg_copy_source SELECT 6,signed,amount,ts,tz,flag,
            repeat('中文😀,'||chr(34)||chr(92)||chr(10)||chr(13),10000),
            decode(string_agg(lpad(to_hex(i),2,'0'),''),'hex'),day
            FROM pg_source CROSS JOIN generate_series(0,255) i WHERE id=2
            GROUP BY signed,amount,ts,tz,flag,day;''')
    for zone in ['UTC','Asia/Shanghai','America/New_York']:
        for attempt in range(4):
            name='types-'+zone.replace('/','-')+'-'+str(attempt+1)
            reset(); seconds=execute(name,config(),zone=zone)
            check=validate('pg_copy_target','pg_copy_source'); assert check['exact_match'],check
            record({'case':name,'seconds':seconds,**check})
    for attempt in range(4):
        name='duplicate-fails-entire-batch-'+str(attempt+1)
        reset(); c=config('SELECT * FROM pg_copy_source UNION ALL SELECT * FROM pg_copy_source WHERE id=1 ORDER BY id')
        # Even with a nonzero dirty allowance COPY is strict, never silently skips.
        c['job']['setting']['errorLimit']['record']=100
        seconds=execute(name,c,False)
        assert sql('SELECT count(*) FROM pg_copy_target')=='0'
        record({'case':name,'seconds':seconds,'failed':True,'rolled_back_rows':True})
    for attempt in range(4):
        name='trigger-skip-detected-'+str(attempt+1)
        reset()
        sql('''CREATE OR REPLACE FUNCTION pg_copy_skip() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.id=2 THEN RETURN NULL; END IF; RETURN NEW; END $$;
            CREATE TRIGGER copy_skip BEFORE INSERT ON pg_copy_target FOR EACH ROW EXECUTE FUNCTION pg_copy_skip();''')
        seconds=execute(name,config(),False)
        assert sql('SELECT count(*) FROM pg_copy_target')=='0'
        assert 'COPY row count differs from input' in (out/(name+'.log')).read_text()
        record({'case':name,'seconds':seconds,'failed':True,'skipped_row_detected':True,'rolled_back_rows':True})
    # Qualified and quoted identifiers must round-trip through server resolution.
    sql('DROP TABLE IF EXISTS public."Copy quoted"; CREATE TABLE public."Copy quoted" ("Key id" bigint,"Text value" text)')
    c=config('SELECT id,txt FROM pg_copy_source ORDER BY id',destination='public."Copy quoted"')
    c['job']['content'][0]['writer']['parameter']['column']=['"Key id"','"Text value"']
    seconds=execute('quoted-identifiers',c)
    mismatch=sql('SELECT count(*) FROM ((SELECT id,txt FROM pg_copy_source EXCEPT ALL SELECT * FROM public."Copy quoted") '
                 'UNION ALL (SELECT * FROM public."Copy quoted" EXCEPT ALL SELECT id,txt FROM pg_copy_source)) d')
    assert mismatch=='0'; record({'case':'quoted-identifiers','seconds':seconds,'exact_match':True})
    sql('DROP TABLE IF EXISTS pg_copy_text; CREATE TABLE pg_copy_text(txt text)')
    for attempt in range(4):
        name='malformed-unicode-'+str(attempt+1)
        c=config(destination='pg_copy_text')
        c['job']['content'][0]['reader']={'name':'streamreader','parameter':{
            'sliceRecordCount':1,'column':[{'type':'string','value':'bad\ud800'}]}}
        c['job']['content'][0]['writer']['parameter']['column']=['txt']
        seconds=execute(name,c,False)
        assert sql('SELECT count(*) FROM pg_copy_text')=='0'
        assert 'MalformedInputException' in (out/(name+'.log')).read_text()
        record({'case':name,'seconds':seconds,'failed':True,'target_empty':True})
    print('PASS',len(results),'real COPY checks',flush=True)


if __name__=='__main__': main()
