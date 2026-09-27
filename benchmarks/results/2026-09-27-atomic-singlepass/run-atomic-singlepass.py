from pathlib import Path
import datetime,hashlib,json,os,subprocess,sys

root=Path('/tmp/datax-perf'); repo=Path('/Users/dengzitao/Developer/projects/DataX')
baseline=root/'candidate-calendar-final'; candidate=root/'candidate-atomic-singlepass'
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
meta=json.loads((candidate/'build-metadata.json').read_text())
assert meta['tracked_diff_sha256']==hashlib.sha256((root/'atomic-singlepass-prototype.patch').read_bytes()).hexdigest()
checks=[
 ('atomic-singlepass-regression',[sys.executable,'benchmarks/postgresql_atomic_checks.py',str(candidate),str(root/'atomic-singlepass-regression')]),
 ('atomic-singlepass-crash',[sys.executable,'benchmarks/postgresql_atomic_crash_checks.py',str(candidate),str(root/'atomic-singlepass-crash'),'--stage-persistence','p']),
 ('atomic-singlepass-calendar',[sys.executable,'benchmarks/postgresql_calendar_checks.py',str(candidate),str(root/'atomic-singlepass-calendar')]),
 ('atomic-singlepass-float',[sys.executable,'benchmarks/postgresql_float_checks.py',str(candidate),str(root/'atomic-singlepass-float'),'--atomic']),
 ('atomic-singlepass-upgrade',[sys.executable,str(root/'check-atomic-singlepass-upgrade.py')]),
]
for scenario in ['table-single','stream-to-pg','table-parallel']:
    name='atomic-singlepass-perf-'+scenario
    checks.append((name,[sys.executable,'benchmarks/postgresql_scenarios.py',str(baseline),str(candidate),str(root/name),
        '--scenario',scenario,'--rounds','5','--atomic']))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
    for name,command in checks:
        state['running']=name; (root/'atomic-singlepass-state.json').write_text(json.dumps(state,indent=2))
        with (root/(name+'.log')).open('w') as log:
            subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
        state['completed'].append(name); state.pop('running'); print(name+' passed',flush=True)
    state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
    state['error']=repr(error); raise
finally:
    (root/'atomic-singlepass-state.json').write_text(json.dumps(state,indent=2))
