from pathlib import Path
import sys,subprocess,json,hashlib
repo=Path('/Users/dengzitao/Developer/projects/DataX');root=Path('/tmp/datax-perf');out=root/'common-profile-current';out.mkdir(exist_ok=True)
sys.path.insert(0,str(repo/'benchmarks'))
from postgresql_scenarios import configuration,disk_guard
from mysql_querysql import run,process_metrics
runtime=root/'candidate-stream-fatal'
record={'scope':'Diagnostic JFR, not a timed performance comparison','build':json.loads((runtime/'build-metadata.json').read_text()),'storage_before':disk_guard(out),'pending_run':'profile'}
(out/'results.json').write_text(json.dumps(record,indent=2))
config,_=configuration('pg-to-file',1000000,out,False)
query="COPY (SELECT id,coalesce(tenant::text,'null'),coalesce(amount::text,'null'),coalesce(created::text,'null'),coalesce(message,'null'),payload FROM pg_perf_source ORDER BY id) TO STDOUT"
reference=out/'reference.tsv'
with reference.open('wb') as stream:
 subprocess.run(['docker','exec','-i','datax-perf-postgres','psql','-X','-q','-U','postgres','-d','datax_bench','-v','ON_ERROR_STOP=1','-c',query],stdout=stream,check=True)
settings=root/'json-direct-profile-before/profile-1ms.jfc'
seconds=run(runtime,config,out,'profile',jvm_options=['-Duser.timezone=UTC','-XX:StartFlightRecording=settings='+str(settings)+',filename='+str(out/'profile.jfr')+',dumponexit=true,maxsize=64m'])
actual=out/'actual.tsv';rows=total=0;digest=hashlib.sha256()
with reference.open('rb') as a, actual.open('rb') as b:
 while True:
  x=a.read(65536);y=b.read(65536);assert x==y,'Output byte mismatch'
  if not x:break
  rows+=x.count(b'\n');total+=len(x);digest.update(x)
assert rows==1000000
record.update(seconds=seconds,rows=rows,bytes=total,sha256=digest.hexdigest(),exact_byte_equality=True,profile_bytes=(out/'profile.jfr').stat().st_size,**process_metrics(out/'profile.log'))
del record['pending_run'];reference.unlink();actual.unlink();record['storage_after']=disk_guard(out)
(out/'results.json').write_text(json.dumps(record,indent=2));print({k:v for k,v in record.items() if k!='build'})
