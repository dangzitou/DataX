#!/usr/bin/env python3
"""Real PG -> OLAP binary fidelity, numeric-collision reproduction and PG round trips."""
import argparse
import base64
import json
import shutil
import subprocess
from pathlib import Path
from mysql_querysql import run
from postgresql_checks import job, sql
from starrocks_checks import sr

PAYLOADS = [None, b'', b'\0', b'\0\0', b'\1', b'\0\1', bytes([255])*8,
            b'\1'+bytes(8), bytes(8)+b'\1', bytes(range(256)), '中文😀'.encode()]


def legacy(value):
    if value is None:
        return None
    result = sum(b << ((i*8) & 63) for i,b in enumerate(reversed(value))) & ((1 << 64)-1)
    return str(result if result < 1 << 63 else result-(1 << 64))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--backend', choices=['starrocks','doris'], default='starrocks')
    p.add_argument('--expect-legacy', action='store_true')
    args = p.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    backend = args.backend
    container = 'datax-perf-'+backend
    jdbc_port, load_port = (29030,28040) if backend == 'starrocks' else (29031,28041)

    def execute(query):
        return sr(query, container)

    def storage():
        info = json.loads(subprocess.check_output(['docker','inspect','--size',container]))[0]
        pg = int(subprocess.check_output(['docker','exec','datax-perf-postgres',
                    'du','-sk','/var/lib/postgresql/data'],text=True).split()[0])*1024
        used = info['SizeRw']+pg
        assert used+1024**3 <= 6*1024**3, ('Generated test storage budget exceeded',used)
        assert shutil.disk_usage(output).free >= 8*1024**3
        return used

    report = {'scope':__doc__, 'backend':backend, 'backends':execute('SHOW BACKENDS'),
              'build':json.loads((runtime/'build-metadata.json').read_text()),
              'expect_legacy':args.expect_legacy, 'results':[]}
    def record(entry):
        report['results'].append(entry)
        (output/'results.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(entry),flush=True)

    sql('DROP TABLE IF EXISTS pg_binary_source; CREATE TABLE pg_binary_source(id bigint,payload bytea); INSERT INTO pg_binary_source VALUES '+
        ','.join('(%d,%s)' % (i,'NULL' if b is None else "decode('%s','hex')" % b.hex()) for i,b in enumerate(PAYLOADS)))
    execute('CREATE DATABASE IF NOT EXISTS datax_bench')
    execute("CREATE USER IF NOT EXISTS datax IDENTIFIED BY 'datax-local-benchmark'")
    execute("GRANT SELECT ON ALL TABLES IN DATABASE datax_bench TO USER datax" if backend=='starrocks'
            else "GRANT SELECT_PRIV ON datax_bench.* TO 'datax'")

    def create(kind='STRING'):
        execute('DROP TABLE IF EXISTS datax_bench.binary_target; CREATE TABLE datax_bench.binary_target '
                '(id BIGINT,payload '+kind+') DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")')

    def config(fmt,encoding=None):
        c = job(query='SELECT * FROM pg_binary_source ORDER BY id')
        w = {'username':'root','password':'','column':['id','payload'], 'loadUrl':['127.0.0.1:%d'%load_port],
             'loadProps':{'format':fmt,'strict_mode':True,'max_filter_ratio':0},
             'connection':[{'table':['binary_target'],'jdbcUrl':'jdbc:mysql://127.0.0.1:%d/datax_bench'%jdbc_port,
                            'selectedDatabase':'datax_bench'}]}
        if fmt=='json': w['loadProps']['strip_outer_array']=True
        if encoding is not None: w['binaryEncoding']=encoding
        c['job']['content'][0]['writer']={'name':backend+'writer','parameter':w}
        return c

    for fmt in ['csv','json']:
        for attempt in range(1,5):
            create()
            name=fmt+'-default-'+str(attempt)
            used=storage()
            seconds=run(runtime,config(fmt),output,name,expect_success=args.expect_legacy)
            # Prefix hex with x so an empty field remains distinguishable through CLI trimming.
            actual=execute("SELECT id,IFNULL(CONCAT('x',HEX(payload)),'NULL') FROM datax_bench.binary_target ORDER BY id")
            if args.expect_legacy:
                expected='\n'.join('%d\t%s'%(i,'NULL' if b is None else 'x'+legacy(b).encode().hex().upper()) for i,b in enumerate(PAYLOADS))
                assert actual==expected,(actual,expected)
                assert legacy(b'')==legacy(b'\0')==legacy(b'\0\0')
                assert legacy(b'\1')==legacy(b'\0\1')==legacy(b'\1'+bytes(8))
            else:
                assert actual=='',actual
                assert 'binaryEncoding' in (output/(name+'.log')).read_text()
            record(dict(case=name,seconds=seconds,legacy_corruption=args.expect_legacy,
                        unsafe_bytes_rejected=not args.expect_legacy,actual=actual,generated_bytes=used))
            if args.expect_legacy: continue
            for encoding in ['hex','base64']:
                create()
                name=fmt+'-'+encoding+'-'+str(attempt)
                seconds=run(runtime,config(fmt,encoding),output,name)
                values=[None if b is None else (b.hex() if encoding=='hex' else base64.b64encode(b).decode()) for b in PAYLOADS]
                expected='\n'.join('%d\t%s'%(i,'NULL' if b is None else 'x'+b.encode().hex().upper()) for i,b in enumerate(values))
                actual=execute("SELECT id,IFNULL(CONCAT('x',HEX(payload)),'NULL') FROM datax_bench.binary_target ORDER BY id")
                assert actual==expected,(actual,expected)
                record(dict(case=name,seconds=seconds,exact_encoded_bytes=True,rows=len(PAYLOADS),generated_bytes=storage()))

                # Store an explicit text representation, decode in the source query,
                # and read the resulting field as raw bytes despite ambiguous metadata.
                sql('DROP TABLE IF EXISTS pg_binary_encoded; CREATE TABLE pg_binary_encoded (LIKE pg_binary_source)')
                back=job(destination='pg_binary_encoded')
                back['job']['content'][0]['reader']={'name':backend+'reader','parameter':{
                    'username':'datax','password':'datax-local-benchmark','binaryColumns':['payload'],
                    'connection':[{'jdbcUrl':['jdbc:mysql://127.0.0.1:%d/datax_bench?characterEncoding=utf8'%jdbc_port],
                        'querySql':["SELECT id,CASE WHEN payload='' THEN '' ELSE %s(payload) END AS payload FROM binary_target ORDER BY id"%('UNHEX' if encoding=='hex' else 'FROM_BASE64')]}]}}
                back['job']['content'][0]['writer']['parameter']['column']=['id','payload']
                seconds=run(runtime,back,output,name+'-readback')
                differences=int(sql('SELECT count(*) FROM ((TABLE pg_binary_encoded EXCEPT ALL TABLE pg_binary_source) '
                                    'UNION ALL (TABLE pg_binary_source EXCEPT ALL TABLE pg_binary_encoded)) d'))
                assert differences==0,differences
                record(dict(case=name+'-readback',seconds=seconds,decoded_binary_differences=differences,
                            representation='encoded text; source query decodes to binaryColumns; PostgreSQL bytea compared directly',rows=len(PAYLOADS)))

    if not args.expect_legacy:
        create()
        cfg=config('csv','hex')
        cfg['job']['content'][0]['writer']['parameter']['loadProps']['row_delimiter' if backend=='starrocks' else 'line_delimiter']='f'
        run(runtime,cfg,output,'encoded-delimiter-collision',expect_success=False)
        assert 'Unsafe CSV' in (output/'encoded-delimiter-collision.log').read_text()
        assert execute('SELECT count(*) FROM datax_bench.binary_target')=='0'
        record(dict(case='encoded-delimiter-collision',rejected=True,target_rows=0))
        for bad in ['utf8','long','']:
            create()
            name='invalid-encoding-'+(bad or 'empty')
            run(runtime,config('json',bad),output,name,expect_success=False)
            assert 'binaryEncoding must be' in (output/(name+'.log')).read_text()
            assert execute('SELECT count(*) FROM datax_bench.binary_target')=='0'
            record(dict(case=name,rejected=True,target_rows=0))
        if backend=='starrocks':
            for attempt in range(1,5):
                create('VARBINARY')
                name='native-varbinary-'+str(attempt)
                cfg=config('csv','hex')
                # Explicit conversion avoids version-dependent implicit CSV binary decoding.
                cfg['job']['content'][0]['writer']['parameter']['loadProps']['columns']="id,encoded_payload,payload=to_binary(encoded_payload,'hex')"
                seconds=run(runtime,cfg,output,name)
                actual=execute("SELECT id,IFNULL(CONCAT('x',HEX(payload)),'NULL') FROM datax_bench.binary_target ORDER BY id")
                expected='\n'.join('%d\t%s'%(i,'NULL' if b is None else 'x'+b.hex().upper()) for i,b in enumerate(PAYLOADS))
                assert actual==expected,(actual,expected)
                sql('DROP TABLE IF EXISTS pg_binary_roundtrip; CREATE TABLE pg_binary_roundtrip (LIKE pg_binary_source)')
                back=job(destination='pg_binary_roundtrip')
                back['job']['content'][0]['reader']={'name':'starrocksreader','parameter':{'username':'datax','password':'datax-local-benchmark',
                    'binaryColumns':['payload'],
                    'connection':[{'jdbcUrl':['jdbc:mysql://127.0.0.1:%d/datax_bench'%jdbc_port],
                                   'querySql':['SELECT id,payload FROM binary_target ORDER BY id']}]}}
                back['job']['content'][0]['writer']['parameter']['column']=['id','payload']
                if attempt % 2 == 0:
                    back['job']['content'][0]['reader']['parameter']['binaryColumns']=['Binary Payload']
                    back['job']['content'][0]['reader']['parameter']['connection'][0]['querySql']=['SELECT id,payload AS `Binary Payload` FROM binary_target ORDER BY id']
                read_seconds=run(runtime,back,output,name+'-readback')
                differences=int(sql('SELECT count(*) FROM ((TABLE pg_binary_source EXCEPT ALL TABLE pg_binary_roundtrip) '
                                    'UNION ALL (TABLE pg_binary_roundtrip EXCEPT ALL TABLE pg_binary_source)) d'))
                assert differences==0,differences
                record(dict(case=name,write_seconds=seconds,read_seconds=read_seconds,native_binary_differences=0,
                            rows=len(PAYLOADS),generated_bytes=storage()))

            for case in ['missing-label','wrong-type','duplicate-config','duplicate-output','empty-result']:
                sql('TRUNCATE pg_binary_roundtrip')
                bad=json.loads(json.dumps(back))
                reader=bad['job']['content'][0]['reader']['parameter']
                reader['binaryColumns']=['id'] if case=='wrong-type' else ['payload']
                reader['connection'][0]['querySql']=['SELECT id,payload FROM binary_target']
                if case in ['missing-label','empty-result']: reader['binaryColumns']=['missing']
                if case=='empty-result': reader['connection'][0]['querySql']=['SELECT id,payload FROM binary_target WHERE false']
                if case=='duplicate-config': reader['binaryColumns']=['payload','payload']
                if case=='duplicate-output': reader['connection'][0]['querySql']=['SELECT payload,payload FROM binary_target']
                bad['job']['setting']['errorLimit']['record']=100
                name='binary-hint-'+case
                run(runtime,bad,output,name,expect_success=False)
                assert 'binaryColumns' in (output/(name+'.log')).read_text()
                assert sql('SELECT count(*) FROM pg_binary_roundtrip')=='0'
                record(dict(case=name,rejected=True,target_rows=0))


if __name__=='__main__':
    main()
