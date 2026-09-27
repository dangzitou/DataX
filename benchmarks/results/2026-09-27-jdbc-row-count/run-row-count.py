from pathlib import Path
import datetime,json,os,subprocess,sys
root=Path('/tmp/datax-perf'); repo=Path('/Users/dengzitao/Developer/projects/DataX')
candidate=root/'candidate-row-count'; baseline=root/'candidate-calendar-final'
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
checks=[
 ('row-count-unit-full',['mvn','-B','-pl','core,plugin-rdbms-util,postgresqlreader,postgresqlwriter','-am','test']),
 ('row-count-fidelity',[sys.executable,'benchmarks/postgresql_checks.py',str(root/'baseline-all'),str(candidate),str(root/'row-count-fidelity')]),
 ('row-count-writer',[sys.executable,'benchmarks/postgresql_writer_checks.py',str(candidate),str(root/'row-count-writer')]),
 ('row-count-wire',[sys.executable,'benchmarks/postgresql_commit_wire_checks.py',str(candidate),str(root/'row-count-wire')]),
 ('row-count-atomic',[sys.executable,'benchmarks/postgresql_atomic_checks.py',str(candidate),str(root/'row-count-atomic')]),
]
for scenario in ['table-single','table-parallel','stream-to-pg']:
 name='row-count-perf-'+scenario
 checks.append((name,[sys.executable,'benchmarks/postgresql_scenarios.py',str(baseline),str(candidate),str(root/name),'--scenario',scenario,'--rounds','5']))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for name,cmd in checks:
  state['running']=name;(root/'row-count-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run(cmd,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
  state['completed'].append(name);state.pop('running');print(name+' passed',flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
 state['error']=repr(error);raise
finally:
 (root/'row-count-state.json').write_text(json.dumps(state,indent=2))
