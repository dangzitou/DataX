import datetime,json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
prior=json.loads((root/'floating-perf-state.json').read_text());assert 'finished_at' in prior and 'error' not in prior and len(prior['completed'])==10
plan=json.loads((root/'floating-perf-followup-plan.json').read_text())
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for label,reference in plan['cases']:
  name='floating-perf-'+label+'-stream-to-pg';state['running']=name
  (root/'floating-perf-followup-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/postgresql_scenarios.py',str(root/reference),str(root/'candidate-float-driver'),str(root/name),'--scenario','stream-to-pg','--rounds','5'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
  report=json.loads((root/name/'results.json').read_text())
  state['completed'].append({'name':name,'median_seconds':report['median_seconds'],'throughput_gain_percent':report['throughput_gain_percent']})
  del state['running'];(root/'floating-perf-followup-state.json').write_text(json.dumps(state,indent=2));print(state['completed'][-1],flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:state['error']=repr(error);raise
finally:(root/'floating-perf-followup-state.json').write_text(json.dumps(state,indent=2))
