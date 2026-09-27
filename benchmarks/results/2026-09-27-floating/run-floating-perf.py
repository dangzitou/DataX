import datetime,json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
cases=[]
for label,reference in [('prior','candidate-stream-fatal'),('upstream','baseline-all')]:
 for scenario in ['table-single','table-parallel','stream-to-pg','pg-to-file']:
  cases.append((label,reference,scenario))
for scenario in ['table-single','pg-to-file']:cases.append(('driver-only','candidate-float-common',scenario))
plan={'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'candidate':'candidate-float-driver','cases':cases,'rows_per_job':1000000,'warmups_per_variant':1,'measured_pairs':5,
      'method':'Whole JVM elapsed, alternating AB/BA; identical configuration per pair, no rewrite/COPY enabled, 1 GiB heap, UTC. Validate all fields and counts, exact byte equality on ordered file output. No builds, sampling, compression or other local tests during timed groups.',
      'gate':'Report all paired +25% and +50% gates; do not cherry-pick medians. These repairs do not make performance success optional. If a regression is observed, retain its evidence and investigate; correctness alone is not whole-goal completion.',
      'storage':'Reuse existing million-row PG table, replace only owned target. Remove each verified TSV and final native reference. Existing 6 GiB data guard and 1 GiB headroom; no new containers.'}
(root/'floating-perf-plan.json').write_text(json.dumps(plan,indent=2))
if '--plan-only' in sys.argv:sys.exit(0)
assert 'finished_at' in json.loads((root/'floating-validation-state.json').read_text())
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
try:
 for label,reference,scenario in cases:
  name='floating-perf-'+label+'-'+scenario;state['running']=name
  (root/'floating-perf-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/postgresql_scenarios.py',str(root/reference),str(root/'candidate-float-driver'),str(root/name),'--scenario',scenario,'--rounds','5'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
  report=json.loads((root/name/'results.json').read_text())
  state['completed'].append({'name':name,'median_seconds':report['median_seconds'],'throughput_gain_percent':report['throughput_gain_percent']})
  del state['running'];(root/'floating-perf-state.json').write_text(json.dumps(state,indent=2));print(state['completed'][-1],flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:state['error']=repr(error);raise
finally:(root/'floating-perf-state.json').write_text(json.dumps(state,indent=2))
