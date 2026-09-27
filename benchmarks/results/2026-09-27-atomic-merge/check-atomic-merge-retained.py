"""Run the new boundary fixtures against the retained production runtime."""
from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_atomic_checks import seed,config,state,SOURCE,TARGET,COLUMNS,sql,run

root=Path('/tmp/datax-perf');runtime=root/'candidate-atomic-group-final'
out=root/'atomic-merge-retained';out.mkdir(exist_ok=True);assert not (out/'results.json').exists()
report={'scope':__doc__,'build':json.loads((runtime/'build-metadata.json').read_text()),'results':[]}
for copy in [False,True]:
    for attempt in range(1,5):
        for case in ['quoted-columns','duplicate-pk','partitioned-insert']:
            seed();name=case+'-'+('copy' if copy else 'jdbc')+'-'+str(attempt);cfg=config(name,copy)
            if case=='quoted-columns':
                for table in [SOURCE,TARGET]:
                    sql('ALTER TABLE '+table+' RENAME txt TO "t,x"; ALTER TABLE '+table+' RENAME bin TO "s""q"')
                cfg['job']['content'][0]['writer']['parameter']['column']=[
                    '"t,x"' if c=='txt' else '"s""q"' if c=='bin' else c for c in COLUMNS]
            elif case=='duplicate-pk':sql('ALTER TABLE pg_atomic_target ADD PRIMARY KEY(id)')
            else:
                sql('DROP TABLE pg_atomic_target; CREATE TABLE pg_atomic_target (LIKE pg_atomic_source) PARTITION BY RANGE(id); '
                    'CREATE TABLE pg_atomic_target_p1 PARTITION OF pg_atomic_target FOR VALUES FROM (0) TO (3); '
                    'CREATE TABLE pg_atomic_target_p2 PARTITION OF pg_atomic_target DEFAULT')
            success=case!='duplicate-pk';run(runtime,cfg,out,name,expect_success=success);actual=state()
            assert actual=={'rows':8 if success else 0,'differences':0 if success else 8,
                            'ledger_rows':1 if success else 0,'stages':0},actual
            if not success:assert 'violates unique constraint' in (out/(name+'.log')).read_text()
            report['results'].append(dict(case=name,expected_success=success,**actual))
            (out/'results.json').write_text(json.dumps(report,indent=2))
print('PASS',len(report['results']),'retained-runtime boundary checks')
