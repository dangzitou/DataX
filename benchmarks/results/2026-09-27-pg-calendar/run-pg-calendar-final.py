import datetime,json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/datax-perf'); repo=Path('/Users/dengzitao/Developer/projects/DataX')
runtime=root/'candidate-calendar-final'
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
checks=[
 ('pg-calendar-regression',[sys.executable,'benchmarks/postgresql_atomic_checks.py',str(runtime),str(root/'pg-calendar-regression')]),
 ('pg-calendar-crash',[sys.executable,'benchmarks/postgresql_atomic_crash_checks.py',str(runtime),str(root/'pg-calendar-crash'),'--stage-persistence','p']),
 ('pg-calendar-fidelity',[sys.executable,'benchmarks/postgresql_checks.py',str(root/'baseline-all'),str(runtime),str(root/'pg-calendar-fidelity')]),
]
for scenario in ['table-single','table-parallel']:
    name='pg-calendar-perf-'+scenario
    checks.append((name,[sys.executable,'benchmarks/postgresql_scenarios.py',str(root/'candidate-atomic-group-final'),str(runtime),str(root/name),'--scenario',scenario,'--rounds','5']))
try:
    for name,command in checks:
        state['running']=name; (root/'pg-calendar-final-state.json').write_text(json.dumps(state,indent=2))
        with (root/(name+'.log')).open('w') as log:
            subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
        state['completed'].append(name); state.pop('running')
        print(name+' passed',flush=True)
    state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
    state['error']=repr(error); raise
finally:
    (root/'pg-calendar-final-state.json').write_text(json.dumps(state,indent=2))
