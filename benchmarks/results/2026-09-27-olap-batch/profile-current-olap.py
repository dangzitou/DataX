from pathlib import Path
import collections,gzip,hashlib,json,os,re,subprocess,sys
repo=Path('/Users/dengzitao/Developer/projects/DataX');root=Path('/tmp/datax-perf');sys.path.insert(0,str(repo/'benchmarks'))
from mysql_querysql import run,process_metrics
from postgresql_checks import sql
from starrocks_checks import sr
backend=sys.argv[1];assert backend in ['starrocks','doris']
container='datax-perf-'+backend;runtime=root/'candidate-row-count';java=root/'zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home'
os.environ['JAVA_HOME']=str(java)
ports=(29030,28040) if backend=='starrocks' else (29031,28041)
visitor='com.starrocks.connector.datax.plugin.writer.starrockswriter.manager.StarRocksStreamLoadVisitor' if backend=='starrocks' else 'com.alibaba.datax.plugin.writer.doriswriter.DorisStreamLoadObserver'
for format_name in ['csv','json']:
 out=root/('olap-current-'+backend+'-'+format_name);out.mkdir(exist_ok=True);assert not (out/'results.json').exists()
 config=json.loads((root/'json-direct-profile-before/profile.json').read_text())
 writer=config['job']['content'][0]['writer'];writer['name']=backend+'writer';params=writer['parameter']
 params['loadUrl']=['127.0.0.1:'+str(ports[1])];params['connection'][0]['jdbcUrl']='jdbc:mysql://127.0.0.1:'+str(ports[0])+'/datax_bench'
 params.pop('maxBatchSize');params['maxBatchSize' if backend=='starrocks' else 'batchSize']=5*1024*1024
 params['loadProps']={'format':format_name,'strict_mode':True,'max_filter_ratio':0,**({'strip_outer_array':True} if format_name=='json' else {})}
 log_config=out/'logback.xml';log_config.write_text('<configuration><appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender"><encoder><pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger{0} - %msg%n</pattern></encoder></appender><logger name="'+visitor+'" level="DEBUG"/><root level="INFO"><appender-ref ref="STDOUT"/></root></configuration>')
 execute=lambda q:sr(q,container)
 execute('CREATE DATABASE IF NOT EXISTS datax_bench;DROP TABLE IF EXISTS datax_bench.olap_perf_target; CREATE TABLE datax_bench.olap_perf_target(id BIGINT NOT NULL,tenant INT,amount DECIMAL(20,4),created DATETIME,message STRING,payload STRING) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 4 PROPERTIES("replication_num"="1");')
 assert sql('SELECT count(*) FROM pg_perf_source')=='1000000'
 storage=json.loads(subprocess.check_output(['docker','inspect','--size',container],text=True))[0]['SizeRw']
 pg_bytes=int(subprocess.check_output(['docker','exec','datax-perf-postgres','du','-sk','/var/lib/postgresql/data'],text=True).split()[0])*1024
 assert storage+pg_bytes+1024**3<=6*1024**3
 result={'scope':'One diagnostic JFR per format; not a performance comparison','backend':backend,'format':format_name,'build':json.loads((runtime/'build-metadata.json').read_text()),'database_storage_before':storage+pg_bytes,'pending_run':'profile'}
 (out/'results.json').write_text(json.dumps(result,indent=2))
 seconds=run(runtime,config,out,'profile',jvm_options=['-Duser.timezone=UTC','-XX:StartFlightRecording=settings='+str(root/'json-direct-profile-before/profile-1ms.jfc')+',filename='+str(out/'profile.jfr')+',dumponexit=true,maxsize=32m'],logback_config=log_config)
 expected=['IF(id%17=0,NULL,id%100)','IF(id%23=0,NULL,CAST(id*1.2345 AS DECIMAL(20,4)))',"IF(id%29=0,NULL,TIMESTAMPADD(SECOND,id%86400,CAST('2024-01-01' AS DATETIME)))","IF(id%31=0,NULL,CONCAT('中文😀-',CAST(id AS STRING)))",'REPEAT(MD5(CAST(id AS STRING)),8)']
 cols=['tenant','amount','created','message','payload'];equal=' AND '.join('(`'+c+'` <=> '+e+')' for c,e in zip(cols,expected))
 actual=list(map(int,execute('SELECT COUNT(*),COUNT(DISTINCT id),SUM(IF(id BETWEEN 1 AND 1000000 AND '+equal+',0,1)) FROM datax_bench.olap_perf_target').split('\t')))
 assert actual==[1000000,1000000,0],actual
 result.update(seconds=seconds,actual_rows=actual[0],distinct_keys=actual[1],mismatched_rows=actual[2],**process_metrics(out/'profile.log'))
 result.pop('pending_run');(out/'results.json').write_text(json.dumps(result,indent=2))
 data=subprocess.check_output([str(java/'bin/jfr'),'print','--json','--events','jdk.ExecutionSample,jdk.NativeMethodSample','--stack-depth','24',str(out/'profile.jfr')]);(out/'events.json.gz').write_bytes(gzip.compress(data,mtime=0))
 events=json.loads(data)['recording']['events'];threads=collections.Counter();leaves=collections.Counter();presence=collections.Counter()
 for e in events:
  v=e['values'];thread=(v.get('sampledThread') or {}).get('javaName');threads[(e['type'],thread)]+=1
  names=[f['method']['type']['name']+'.'+f['method']['name'] for f in (v.get('stackTrace') or {}).get('frames',[])]
  if names:leaves[(e['type'],names[0])]+=1
  presence.update(set(names))
 summaries={'scope':'Samples are not additive wall-time proportions; export stack depth 24','threads':[{'event':k[0],'thread':k[1],'count':v}for k,v in threads.most_common()], 'leaves':[{'event':k[0],'method':k[1],'count':v}for k,v in leaves.most_common(25)],'stack_presence':presence.most_common(35)}
 (out/'summary.json').write_text(json.dumps(summaries,indent=2));print(backend,format_name,{k:v for k,v in result.items() if k!='build'},flush=True)
