from pathlib import Path
import datetime,os,json,subprocess,sys,hashlib

root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
candidate=root/'candidate-atomic-merge';baseline=root/'candidate-atomic-group-final'
meta=json.loads((candidate/'build-metadata.json').read_text())
assert meta['tracked_diff_sha256']==hashlib.sha256((root/'atomic-merge-prototype.patch').read_bytes()).hexdigest()
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
checks=[
    ('atomic-merge-regression',[sys.executable,'benchmarks/postgresql_atomic_checks.py',str(candidate),str(root/'atomic-merge-regression')]),
    ('atomic-merge-float',[sys.executable,'benchmarks/postgresql_float_checks.py',str(candidate),str(root/'atomic-merge-float'),'--atomic']),
    ('atomic-merge-upgrade',[sys.executable,str(root/'check-atomic-merge-upgrade.py')]),
]
for scenario in ['table-single','stream-to-pg','table-parallel']:
    name='atomic-merge-perf-'+scenario
    checks.append((name,[sys.executable,'benchmarks/postgresql_scenarios.py',str(baseline),str(candidate),str(root/name),
                        '--scenario',scenario,'--rounds','5','--atomic']))
try:
    for name,command in checks:
        state['running']=name;(root/'atomic-merge-state.json').write_text(json.dumps(state,indent=2))
        with (root/(name+'.log')).open('w') as log:
            subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
        state['completed'].append(name);del state['running']
        (root/'atomic-merge-state.json').write_text(json.dumps(state,indent=2));print(name+' passed',flush=True)
    state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
    state['error']=repr(error);raise
finally:
    (root/'atomic-merge-state.json').write_text(json.dumps(state,indent=2))
