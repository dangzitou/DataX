import datetime,json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
plan={'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runtime':'candidate-atomic-types','driver':'42.7.13',
 'evidence':'Two full-JVM diagnostic JFRs show writer native socket reads and flushIfDeadlockRisk stacks dominating samples. Counts do not equal wall-time proportions.',
 'comparison':'Same atomic runtime, same configuration except candidate reWriteBatchedInserts=true. Complete source-to-stage, fingerprint, binary multiset publication, ledger and commit are timed. Both sides use PG shared snapshot for PG sources and JDBC startup options for temp_file_limit=1536MB (verified via real JDBC SHOW).',
 'scenarios':['table-single','stream-to-pg'],'rows':1000000,'warmups_each':1,'pairs':5,
 'retention':'No production change during pilot. Consider default native rewrite only for our own atomic staging if both whole-job medians gain >=5%, all pairs avoid regression, and all correctness checks pass. Global +25/+50 acceptance stays unchanged.',
 'scope':'All-scenario +25/+50 and billion-row safety remain mandatory and unproven.',
 'storage':'Reuse existing source. Fixed test table, existing 6 GiB guard, no new containers. No builds, profiling or compression while timing.'}
(root/'atomic-batch-pilot-v2-plan.json').write_text(json.dumps(plan,indent=2))
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for scenario in plan['scenarios']:
  name='atomic-batch-pilot-v2-'+scenario;state['running']=name
  (root/'atomic-batch-pilot-v2-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/postgresql_scenarios.py',str(root/'candidate-atomic-types'),str(root/'candidate-atomic-types'),str(root/name),'--scenario',scenario,'--rounds','5','--candidate-rewrite','--atomic'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
  r=json.loads((root/name/'results.json').read_text())
  state['completed'].append({'name':name,'median_seconds':r['median_seconds'],'gain':r['throughput_gain_percent']});del state['running']
  (root/'atomic-batch-pilot-v2-state.json').write_text(json.dumps(state,indent=2));print(state['completed'][-1],flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as e:state['error']=repr(e);raise
finally:(root/'atomic-batch-pilot-v2-state.json').write_text(json.dumps(state,indent=2))
