from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_atomic_checks import seed,config,state,COLUMNS
from postgresql_checks import sql
from mysql_querysql import run

root=Path('/tmp/datax-perf'); out=root/'atomic-singlepass-upgrade'; out.mkdir(exist_ok=False)
versions=['candidate-calendar-final','candidate-atomic-singlepass']
report={'scope':'Real Engines: same-ID retries across old/new publishers in BOTH directions',
    'builds':{v:json.loads((root/v/'build-metadata.json').read_text()) for v in versions},'results':[]}
def save(): (out/'results.json').write_text(json.dumps(report,indent=2))
for reverse in [False,True]:
    for copy in [False,True]:
        for attempt in range(1,5):
            seed(); name=('new-to-old' if reverse else 'old-to-new')+('-copy-' if copy else '-jdbc-')+str(attempt)
            cfg=config('singlepass-'+name,copy); report['pending_run']=name; save(); checks=[]
            for version in (list(reversed(versions)) if reverse else versions):
                run(root/version,cfg,out,name+'-'+version)
                actual=state(); assert actual=={'rows':8,'differences':0,'ledger_rows':1,'stages':0},actual
                source='SELECT record_send(ROW('+','.join(COLUMNS)+')) FROM pg_atomic_source'
                target='SELECT record_send(ROW('+','.join(COLUMNS)+')) FROM pg_atomic_target'
                differences=int(sql('SELECT count(*) FROM (('+source+' EXCEPT ALL '+target+') UNION ALL ('+target+' EXCEPT ALL '+source+')) d'))
                assert differences==0,(name,version,differences)
                checks.append(dict(version=version,binary_multiset_differences=differences,**actual))
            report['results'].append(dict(case=name,checks=checks)); report.pop('pending_run'); save()
            print(name+' passed',flush=True)
print('PASS',len(report['results']),'bidirectional same-ID upgrade pairs')
