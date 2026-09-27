from pathlib import Path
import sys,subprocess,json,os
repo=Path('/Users/dengzitao/Developer/projects/DataX');root=Path('/tmp/datax-perf')
sys.path.insert(0,str(repo/'benchmarks'))
from postgresql_scenarios import configuration,disk_guard,validate
from postgresql_checks import sql
from mysql_querysql import run,process_metrics
runtime=root/'candidate-float-driver';settings=root/'json-direct-profile-before/profile-1ms.jfc'
for scenario in ['table-single','stream-to-pg']:
 out=root/('jdbc-profile-'+scenario);out.mkdir(exist_ok=True)
 assert not (out/'results.json').exists()
 config,_=configuration(scenario,1000000,out,False)
 suffix=' INCLUDING ALL' if scenario=='table-single' else ''
 sql('DROP TABLE IF EXISTS pg_perf_target; CREATE TABLE pg_perf_target (LIKE pg_perf_source'+suffix+')')
 record={'scope':'One diagnostic JFR, not a performance comparison','scenario':scenario,
  'build':json.loads((runtime/'build-metadata.json').read_text()),'storage_before':disk_guard(out),'pending_run':'profile'}
 (out/'results.json').write_text(json.dumps(record,indent=2))
 seconds=run(runtime,config,out,'profile',jvm_options=['-Duser.timezone=UTC','-XX:StartFlightRecording=settings='+str(settings)+',filename='+str(out/'profile.jfr')+',dumponexit=true,maxsize=64m'])
 record.update(seconds=seconds,**validate(1000000,scenario=='stream-to-pg'),profile_bytes=(out/'profile.jfr').stat().st_size,**process_metrics(out/'profile.log'))
 del record['pending_run'];record['storage_after']=disk_guard(out)
 (out/'results.json').write_text(json.dumps(record,indent=2));print({k:v for k,v in record.items() if k!='build'},flush=True)
