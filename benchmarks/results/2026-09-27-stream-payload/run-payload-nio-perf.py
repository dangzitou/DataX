import json,os,subprocess,sys,time
from pathlib import Path
repo=Path('/Users/dengzitao/Developer/projects/DataX');root=Path('/tmp/datax-perf')
env=dict(os.environ,JAVA_HOME=str(root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'))
images={'starrocks':'starrocks/allin1-ubuntu@sha256:faf7ce9c24d9c29c9431b4e8cbd4bb7a74cd169907c63f0c5ebaacc7f9df276b','doris':'apache/doris@sha256:82a5cabc7900ebcd3d080412d636c0cebd6602799fffe2605c652b2ea7d42609'}
for backend,scenario in [('starrocks','writer-csv'),('starrocks','writer-json'),('doris','writer-csv'),('doris','writer-json')]:
 reference='candidate-stream-recovery'
 name='payload-nio-perf-'+backend+'-'+scenario
 container='datax-perf-'+backend
 ports=[29030,28030,28040] if backend=='starrocks' else [29031,28031,28041]
 command=['docker','run','-d','--name',container,'--cpus','4','--memory','4g']
 for local,remote in zip(ports,[9030,8030,8040]):command+=['-p','127.0.0.1:%s:%s'%(local,remote)]
 if backend=='doris':command+=['-e','FE_HEAP=1024m','-e','BE_HEAP=512m','-e','BE_CONFIG_EXTRA=mem_limit = 60%']
 ident=subprocess.check_output(command+[images[backend]],text=True).strip()
 probe='CREATE DATABASE IF NOT EXISTS datax_bench; CREATE TABLE IF NOT EXISTS datax_bench.ci_readiness(id BIGINT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1");'
 try:
  for attempt in range(90):
   ready=subprocess.run(['docker','exec',container,'mysql','-uroot','-h127.0.0.1','-P9030','-e',probe],capture_output=True,text=True)
   if ready.returncode==0:break
   time.sleep(2)
  else:raise RuntimeError(ready.stderr)
  with (root/(name+'.log')).open('w') as log:
   subprocess.run([sys.executable,'benchmarks/olap_scenarios.py',str(root/reference),str(root/'candidate-stream-payload-nio'),str(root/name),'--backend',backend,'--scenario',scenario,'--rounds','5'],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
  print(name,'completed',flush=True)
 finally:
  actual=subprocess.check_output(['docker','inspect','--size','--format','{{json .}}',container],text=True)
  (root/(name+'-container.json')).write_text(actual)
  if json.loads(actual)['Id']==ident:subprocess.run(['docker','rm','-f',container],check=True)
