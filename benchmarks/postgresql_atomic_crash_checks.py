#!/usr/bin/env python3
"""Destructive crash checks ONLY for the disposable datax-perf-postgres container."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path
from postgresql_atomic_checks import seed, config, PublicationDropProxy, COLUMNS, QUERY, URL
from postgresql_checks import sql
from mysql_querysql import run

CONTAINER = 'datax-perf-postgres'
GATE = 918272680


def holder(query):
    process = subprocess.Popen(['docker', 'exec', '-i', CONTAINER, 'psql', '-X', '-q', '-A', '-t',
        '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'datax_bench'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process.stdin.write(query+";SELECT 'held';\n"); process.stdin.flush()
    assert process.stdout.readline().strip() == 'held'
    return process


def crash():
    before = sql('SELECT pg_postmaster_start_time()')
    subprocess.run(['docker', 'kill', '--signal=KILL', CONTAINER], capture_output=True, check=True)
    subprocess.run(['docker', 'start', CONTAINER], capture_output=True, check=True)
    deadline = time.monotonic()+45
    while subprocess.run(['docker', 'exec', CONTAINER, 'pg_isready', '-U', 'postgres', '-d', 'datax_bench'],
                         capture_output=True).returncode != 0:
        if time.monotonic() > deadline: raise TimeoutError('Disposable PostgreSQL did not recover')
        time.sleep(.1)
    after = sql('SELECT pg_postmaster_start_time()')
    assert before != after, (before, after)
    return {'before': before, 'after': after}


def staged():
    tables = json.loads(sql("SELECT coalesce(json_agg(json_build_object('table',quote_ident(n.nspname)||'.'||quote_ident(c.relname),"
        "'persistence',c.relpersistence)),'[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname LIKE '__datax_stage_%' AND c.relkind='r'"))
    for table in tables: table['rows'] = int(sql('SELECT count(*) FROM '+table['table']))
    return tables


def exact_rows(table, predicate='true'):
    return sql("SELECT coalesce(json_agg(b ORDER BY b),'[]') FROM (SELECT encode(record_send(ROW("
               +','.join(COLUMNS)+")),'hex') b FROM "+table+' WHERE '+predicate+') rows')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stage-persistence', choices=['p','u'], required=True)
    parser.add_argument('--rounds', type=int, default=4)
    args = parser.parse_args(); assert 1 <= args.rounds <= 4
    runtime = args.runtime.resolve(); out = args.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    assert not (out/'results.json').exists(), 'Use a fresh output directory'
    assert sql("SELECT count(*) FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','datax_bench')") == '0'
    assert sql("SELECT count(*) FROM pg_stat_activity WHERE datname='datax_bench' AND pid<>pg_backend_pid()") == '0'
    assert sql("SELECT bool_and(setting='on') FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes')") == 't'
    report = {'scope': __doc__, 'stage_persistence': args.stage_persistence, 'postgres': sql('SELECT version()'),
        'build': json.loads((runtime/'build-metadata.json').read_text()), 'results': []}
    def save(): (out/'results.json').write_text(json.dumps(report,indent=2))

    # The CHECK supplies a deterministic barrier during publication, not a production trigger.
    sql("DROP SCHEMA IF EXISTS datax_atomic_crash CASCADE; CREATE SCHEMA datax_atomic_crash; "
        "CREATE SEQUENCE datax_atomic_crash.progress; "
        "CREATE FUNCTION datax_atomic_crash.gate(value bigint) RETURNS boolean LANGUAGE plpgsql AS $$ "
        "BEGIN PERFORM nextval('datax_atomic_crash.progress'); "
        "IF value=4 THEN PERFORM pg_advisory_xact_lock("+str(GATE)+"); END IF; RETURN true; END $$")
    try:
        for copy in [False,True]:
            for attempt in range(1,args.rounds+1):
                for phase in ['staged','publishing','committed-reply-lost']:
                    name=phase+'-'+('copy' if copy else 'jdbc')+'-'+str(attempt)
                    report['pending_run']=name; save()
                    barrier=None
                    seed(); sql("INSERT INTO pg_atomic_target(id,txt) VALUES(0,'existing')")
                    source=exact_rows('pg_atomic_source'); existing=exact_rows('pg_atomic_target','id=0')
                    cfg=config(name,copy,QUERY+' ORDER BY id')
                    cfg['job']['setting']['speed']['channel']=1
                    cfg['core']['container']['taskGroup']['channel']=1
                    reader=cfg['job']['content'][0]['reader']['parameter']; del reader['querySqlSplitPk']
                    cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl']=URL+'?ApplicationName='+name

                    def verify(published):
                        assert exact_rows('pg_atomic_source')==source
                        assert exact_rows('pg_atomic_target','id=0')==existing
                        actual=exact_rows('pg_atomic_target','id IS DISTINCT FROM 0')
                        assert actual==(source if published else '[]'), (name,actual)
                        rows=int(sql('SELECT count(*) FROM pg_atomic_target'))
                        assert rows==(9 if published else 1), (name,rows)
                        markers=int(sql("SELECT count(*) FROM __datax_atomic_batches_v1 WHERE target_oid='pg_atomic_target'::regclass"))
                        assert markers==int(published), (name,markers)
                        return {'rows':rows,'ledger_rows':markers,
                                'source_and_existing_exact':True,'new_rows_exact':published,'unpublished_target_unchanged':not published}

                    if not report.get('validator_rejects_extra_null_key'):
                        sql('INSERT INTO pg_atomic_target(id) VALUES(NULL)')
                        try:
                            verify(False)
                        except AssertionError:
                            report['validator_rejects_extra_null_key']=True
                        else:
                            raise AssertionError('Validator accepted an extra NULL-key row')
                        finally:
                            sql('DELETE FROM pg_atomic_target WHERE id IS NULL')
                        save()

                    if phase=='committed-reply-lost':
                        with PublicationDropProxy() as proxy:
                            cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl']=(
                                'jdbc:postgresql://127.0.0.1:%d/datax_bench?sslmode=disable' % proxy.port)
                            run(runtime,cfg,out,name+'-interrupted',False)
                            assert proxy.dropped.is_set()
                            assert 'DBUtilErrorCode-25' in (out/(name+'-interrupted.log')).read_text()
                        before_state=verify(True); assert staged()==[]
                        restarted=crash(); after_stage=staged(); assert after_stage==[]
                        after_state=verify(True)
                    else:
                        if phase=='publishing':
                            sql("ALTER SEQUENCE datax_atomic_crash.progress RESTART WITH 1; "
                                "ALTER TABLE pg_atomic_target ADD CHECK(datax_atomic_crash.gate(id)) NOT VALID")
                            lock=holder('DO $$ BEGIN PERFORM pg_advisory_lock('+str(GATE)+'); END $$')
                            waiting="wait_event='advisory' AND query LIKE 'WITH inserted AS%'"
                        else:
                            lock=holder('BEGIN;LOCK TABLE pg_atomic_target IN SHARE MODE')
                            waiting="wait_event_type='Lock' AND query LIKE 'LOCK TABLE%pg_atomic_target%'"
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            future=pool.submit(run,runtime,cfg,out,name+'-interrupted',False)
                            try:
                                deadline=time.monotonic()+30
                                while sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='"+name+"' AND "+waiting)=='0':
                                    if future.done(): future.result(); raise AssertionError('Engine never reached crash barrier')
                                    if time.monotonic()>deadline: raise TimeoutError('Engine never reached crash barrier')
                                    time.sleep(.1)
                                before_stage=staged(); assert len(before_stage)==1
                                assert before_stage[0]['persistence']==args.stage_persistence and before_stage[0]['rows']==8,before_stage
                                before_state=verify(False)
                                progress=int(sql('SELECT last_value FROM datax_atomic_crash.progress')) if phase=='publishing' else None
                                if progress is not None: assert progress>=2, progress
                                restarted=crash(); future.result(timeout=30)
                                after_stage=staged(); assert len(after_stage)==1,after_stage
                                assert after_stage[0]['rows']==(0 if args.stage_persistence=='u' else 8),after_stage
                                after_state=verify(False)
                            finally:
                                lock.stdin.close()
                                if lock.poll() is None: lock.terminate()
                                lock.wait(timeout=10)
                        barrier={'stage':before_stage,'constraint_calls':progress}

                    cfg['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl']=URL
                    recovery=[]
                    for suffix in ['recover','rerun']:
                        run(runtime,cfg,out,name+'-'+suffix); recovery.append(verify(True)); assert staged()==[]
                    report['results'].append({'case':name,'abrupt_server_restart':restarted,'before':before_state,
                        'after':after_state,'stage_after_crash':after_stage,'barrier':barrier,'recovery':recovery,
                        'same_id_recovered_and_reran_without_duplicates':True})
                    report.pop('pending_run'); save(); print(name+' passed',flush=True)
    finally:
        sql('DROP SCHEMA datax_atomic_crash CASCADE')
    print('PASS',len(report['results']),'abrupt-restart scenarios')


if __name__=='__main__': main()
