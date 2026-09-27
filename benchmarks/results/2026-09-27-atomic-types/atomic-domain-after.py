from pathlib import Path
import json,sys,os
sys.path.insert(0,'benchmarks')
from postgresql_checks import sql,job
from mysql_querysql import run
root=Path('/tmp/datax-perf');out=root/'atomic-domain-after';out.mkdir(exist_ok=True)
assert not (out/'results.json').exists()
sql('DROP TABLE IF EXISTS pg_domain_target; DROP DOMAIN IF EXISTS datax_atomic_numeric_probe; CREATE DOMAIN datax_atomic_numeric_probe AS numeric(9,2)')
results=[]
for mode in ['jdbc','copy']:
 for attempt in range(1,5):
  sql('DROP TABLE IF EXISTS pg_domain_target; CREATE TABLE pg_domain_target(id bigint,amount datax_atomic_numeric_probe)')
  config=job(destination='pg_domain_target',query='SELECT 1::bigint AS id,1.2345::numeric AS amount')
  config['job']['content'][0]['reader']['parameter']['consistentSnapshot']=True
  config['job']['content'][0]['writer']['parameter'].update(column=['id','amount'],atomicBatchId='domain-before-'+mode+'-'+str(attempt),useCopy=mode=='copy')
  seconds=run(root/'candidate-atomic-types',config,out,mode+'-'+str(attempt),expect_success=False)
  result={'mode':mode,'attempt':attempt,'job_succeeded':False,'source':'1.2345','target':sql('SELECT amount::text FROM pg_domain_target'),
     'rows':int(sql('SELECT count(*) FROM pg_domain_target')),'changed_rows':int(sql('SELECT count(*) FROM pg_domain_target WHERE amount::numeric IS DISTINCT FROM 1.2345::numeric')),'seconds_diagnostic_only':seconds}
  results.append(result);(out/'results.json').write_text(json.dumps(results,indent=2));print(result,flush=True)
  assert result['rows']==0 and result['changed_rows']==0 and result['target']=='',result
  assert 'does not support domains or user-defined column types' in (out/(mode+'-'+str(attempt)+'.log')).read_text()
