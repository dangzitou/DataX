from pathlib import Path
import datetime,os,json,subprocess,sys
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for variant,runtime,kind in [('baseline','candidate-atomic-group-final','p'),('candidate','candidate-atomic-unlogged','u')]:
  name='atomic-unlogged-crash-v2-'+variant;state['running']=name;(root/'atomic-unlogged-crash-v2-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/postgresql_atomic_crash_checks.py',str(root/runtime),str(root/name),'--stage-persistence',kind],
                  cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900,check=True)
  state['completed'].append(name);del state['running'];print(name+' passed',flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
 state['error']=repr(error);raise
finally:(root/'atomic-unlogged-crash-v2-state.json').write_text(json.dumps(state,indent=2))
