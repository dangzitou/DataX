from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from postgresql_atomic_checks import seed, config, state
from mysql_querysql import run

root=Path('/tmp/datax-perf');out=root/'atomic-merge-upgrade';out.mkdir(exist_ok=True)
assert not (out/'results.json').exists()
report={'scope':'Real Engines: previous atomic publisher followed by same-ID candidate retry',
        'builds':{name:json.loads((root/name/'build-metadata.json').read_text())
                  for name in ['candidate-atomic-group-final','candidate-atomic-merge']},'results':[]}
for copy in [False,True]:
    for attempt in range(1,5):
        seed();name=('copy' if copy else 'jdbc')+'-'+str(attempt);cfg=config('merge-upgrade-'+name,copy)
        for runtime in ['candidate-atomic-group-final','candidate-atomic-merge']:
            run(root/runtime,cfg,out,name+'-'+runtime)
            actual=state()
            assert actual=={'rows':8,'differences':0,'ledger_rows':1,'stages':0},actual
        report['results'].append(dict(case=name,**actual,previous_publish_and_candidate_retry=True))
        (out/'results.json').write_text(json.dumps(report,indent=2))
print('PASS',len(report['results']),'same-ID upgrade pairs')
