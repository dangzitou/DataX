import datetime,json,os,subprocess,sys,time
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
candidate=str(root/'candidate-float-driver');baseline=str(root/'baseline-all')
checks=[
 ('float','postgresql_float_checks.py',[candidate]),
 ('fidelity','postgresql_checks.py',[baseline,candidate]),
 ('binding','postgresql_binding_checks.py',[candidate]),
 ('integer','postgresql_integer_checks.py',[candidate]),
 ('time','postgresql_time_checks.py',[candidate]),
 ('time-binary','postgresql_time_checks.py',[candidate],'--binary'),
 ('writer','postgresql_writer_checks.py',[candidate]),
 ('copy','postgresql_copy_checks.py',[candidate]),
 ('query','postgresql_querysql_checks.py',[candidate]),
 ('snapshot','postgresql_snapshot_checks.py',[candidate]),
 ('atomic','postgresql_atomic_checks.py',[candidate]),
 ('commit','jdbc_commit_checks.py',[candidate]),
 ('commit-wire','postgresql_commit_wire_checks.py',[candidate]),
 ('file','streamwriter_checks.py',[candidate])]
state={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':[]}
(root/'floating-validation-plan.json').write_text(json.dumps({'checks':checks,'scope':'Real PG/JDK8 and existing Engine regression; historical risks intentionally reproduced; not production proof.'},indent=2))
try:
 for entry in checks:
  label,script,arguments,*extra=entry;name='floating-regression-'+label
  state['running']=name;(root/'floating-validation-state.json').write_text(json.dumps(state,indent=2))
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/'+script,*arguments,str(root/name),*extra],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600,check=True)
  state['completed'].append(name);del state['running'];(root/'floating-validation-state.json').write_text(json.dumps(state,indent=2));print(name+' passed',flush=True)
 state['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
except Exception as error:
 state['error']=repr(error);raise
finally:(root/'floating-validation-state.json').write_text(json.dumps(state,indent=2))
