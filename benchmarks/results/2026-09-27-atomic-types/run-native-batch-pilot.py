import datetime,json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
plan={'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runtime':'candidate-float-driver','driver':'42.7.13',
 'evidence':'Two full-JVM diagnostic JFRs show writer native socket reads and flushIfDeadlockRisk stacks dominating samples. Counts do not equal wall-time proportions.',
 'comparison':'Same code/runtime, same configuration except candidate reWriteBatchedInserts=true; configuration experiment, not source-only performance.',
 'scenarios':['table-single','stream-to-pg'],'rows':1000000,'warmups_each':1,'pairs':5,
 'retention':'No production change in this pilot. Consider a controlled atomic-staging path only if both medians gain >=5% and all correctness checks pass; ordinary user table semantics cannot be assumed equivalent.',
 'scope':'All-scenario +25/+50 and billion-row safety remain mandatory and unproven.',
 'storage':'Reuse existing source. Fixed test table, existing 6 GiB guard, no new containers. No builds, profiling or compression while timing.'}
(root/'native-batch-pilot-plan.json').write_text(json.dumps(plan,indent=2))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for scenario in plan['scenarios']:
  name='native-batch-pilot-'+scenario;state['running']=name
  (root/'native-batch-pilot-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/postgresql_scenarios.py',str(root/'candidate-float-driver'),str(root/'candidate-float-driver'),str(root/name),'--scenario',scenario,'--rounds','5','--candidate-rewrite'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
  r=json.loads((root/name/'results.json').read_text())
  state['completed'].append({'name':name,'median_seconds':r['median_seconds'],'gain':r['throughput_gain_percent']});del state['running']
  (root/'native-batch-pilot-state.json').write_text(json.dumps(state,indent=2));print(state['completed'][-1],flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as e:state['error']=repr(e);raise
finally:(root/'native-batch-pilot-state.json').write_text(json.dumps(state,indent=2))
