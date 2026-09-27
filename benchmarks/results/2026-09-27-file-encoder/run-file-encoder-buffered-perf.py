import os,subprocess,sys,json,time
from pathlib import Path
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
state={'completed':[]}
try:
 for label,reference in [('prior','candidate-stream-fatal'),('upstream','baseline-all')]:
  for scenario in ['pg-to-file','pg-integer-file']:
   name='file-encoder-buffered-perf-'+label+'-'+scenario
   state['pending']=name;(root/'file-encoder-buffered-driver-status.json').write_text(json.dumps(state,indent=2))
   with (root/(name+'.log')).open('w') as log:
    subprocess.run([sys.executable,'benchmarks/postgresql_scenarios.py',str(root/reference),str(root/'candidate-file-encoder-buffered'),str(root/name),'--scenario',scenario,'--rounds','5'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600,check=True)
   d=json.loads((root/name/'results.json').read_text());state['completed'].append({'name':name,'median_seconds':d['median_seconds'],'throughput_gain_percent':d['throughput_gain_percent']});del state['pending']
   (root/'file-encoder-buffered-driver-status.json').write_text(json.dumps(state,indent=2));print(state['completed'][-1],flush=True)
except Exception as error:
 state['error']=repr(error);(root/'file-encoder-buffered-driver-status.json').write_text(json.dumps(state,indent=2));raise
