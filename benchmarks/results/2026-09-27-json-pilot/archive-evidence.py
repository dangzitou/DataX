"""Archive the rejected prototype and exact measured controls without publishing raw JFR."""
import gzip,hashlib,json,shutil,statistics,subprocess,sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent;root=Path('/tmp/datax-perf')
sys.path.insert(0,str(repo/'benchmarks'))
from performance_gate import evaluate
names=['json-direct-sr-before','json-direct-sr-upstream','json-direct-sr-upstream-final',
       'json-direct-doris-before','json-direct-doris-before-be24','json-direct-doris-upstream-be24']
summary=[]
for name in names:
 folder=root/name;report=json.loads((folder/'results.json').read_text())
 shutil.copy2(folder/'results.json',out/(name+'.json'))
 artifacts={p.name:p.read_text() for p in sorted(folder.iterdir()) if p.is_file() and p.suffix in ('.json','.log') and p.name!='results.json'}
 (out/(name+'-artifacts.json.gz')).write_bytes(gzip.compress(json.dumps(artifacts,sort_keys=True,ensure_ascii=False).encode(),mtime=0))
 gates={}
 for threshold in [25,50]:
  try:gates[threshold]=evaluate(report,threshold=threshold)
  except ValueError as error:gates[threshold]={'passed':False,'error':str(error)}
  (out/(name+'-gate-'+str(threshold)+'.json')).write_text(json.dumps(gates[threshold],indent=2))
 complete='median_seconds' in report
 if complete:
  assert len(report['runs'])==12 and not report.get('pending_run')
  assert all(r['expected']==r['actual']==r['distinct_keys']==1000000 and r['mismatched_rows']==0 for r in report['runs'])
 summary.append({'name':name,'completed_validated_runs':len(report['runs']),'complete':complete,
                 'median_seconds':report.get('median_seconds'),'gain_percent':report.get('throughput_gain_percent'),
                 'gate_25':gates[25],'gate_50':gates[50],
                 'cpu_medians':{v:statistics.median(r['user_cpu_seconds'] for r in report['runs'] if r['round'] and r['variant']==v) for v in ['baseline','candidate']} if complete else None})
for source in sorted(root.glob('json-direct-*')):
 if source.is_file() and source.suffix in ('.json','.patch','.log'):
  if source.suffix=='.log':(out/(source.name+'.gz')).write_bytes(gzip.compress(source.read_bytes(),mtime=0))
  else:shutil.copy2(source,out/source.name)
for runtime in ['baseline-all','candidate-stream-queue','candidate-json-direct']:
 source=root/runtime/'build-metadata.json'
 if source.exists():shutil.copy2(source,out/(runtime+'-build.json'))
(out/'prototype-build.log.gz').write_bytes(gzip.compress((root/'candidate-json-direct/build.log').read_bytes(),mtime=0))
for name in ['olap_scenarios.py','performance_gate.py','test_performance_gate.py','mysql_querysql.py',
             'mysql_scenarios.py','postgresql_checks.py','postgresql_scenarios.py','starrocks_checks.py','build.py']:
 shutil.copy2(repo/'benchmarks'/name,out/name)
old=subprocess.check_output(['git','show','487dd82:benchmarks/olap_scenarios.py'],text=True)
old=old.replace("'maxBatchRows': 500000, 'maxBatchSize': 5*1024*1024, 'flushQueueLength': 1,",
                "'maxBatchRows': 500000, ('maxBatchSize' if backend == 'starrocks' else 'batchSize'): 5*1024*1024,\n            'flushQueueLength': 1,")
(out/'olap_scenarios-batch-key-only.py').write_text(old)
script_hashes={hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ['olap_scenarios.py','olap_scenarios-batch-key-only.py']}
for name in names:assert json.loads((out/(name+'.json')).read_text())['script_sha256'] in script_hashes,name
for name in ['run-json-direct.py','run-json-direct-be24.py']:shutil.copy2(root/name,out/name)
profile=root/'json-direct-profile-before'
for name in ['profile.json','result.json','summary.json','profile-1ms.jfc']:shutil.copy2(profile/name,out/('profile-'+name))
(out/'profile.log.gz').write_bytes(gzip.compress((profile/'profile.log').read_bytes(),mtime=0))
events=json.loads(gzip.decompress((profile/'events.json.gz').read_bytes()))['recording']['events'];compact=[]
for event in events:
 v=event['values'];frames=(v.get('stackTrace') or {}).get('frames',[])
 compact.append({'type':event['type'],'time':v.get('startTime'),'thread':(v.get('sampledThread') or v.get('eventThread') or {}).get('javaName'),
  'frames':[f['method']['type']['name']+'.'+f['method']['name'] for f in frames],
  'allocation_class':(v.get('objectClass') or {}).get('name'),
  **{k:v[k] for k in ['allocationSize','tlabSize','duration','name','cause','sumOfPauses'] if k in v}})
(out/'profile-selected-events.json.gz').write_bytes(gzip.compress(json.dumps(compact,sort_keys=True).encode(),mtime=0))
(out/'summary.json').write_text(json.dumps(summary,indent=2))
(out/'validation-summary.json').write_text(json.dumps({'scope':'Rejected prototype; no released-runtime speed claim.',
 'complete_comparisons':4,'completed_measured_and_warmup_jobs':48,'completed_groups_gate_25_passed':0,
 'completed_groups_gate_50_passed':0,'additional_incomplete_groups_validated_jobs':13,
 'additional_engine_success_without_completed_validation':1,'profile_jobs_with_data_validation':1,
 'prototype_and_retained_source_unit_tests_each':57,'prototype_retained':False},indent=2))
for name in ['candidate-json-direct','candidate-stream-queue','baseline-all']:
 m=json.loads((out/(name+'-build.json')).read_text())
 if (root/name).exists():
  for relative,digest in m['jars'].items():assert hashlib.sha256((root/name/relative).read_bytes()).hexdigest()==digest,(name,relative)
assert hashlib.sha256((out/'json-direct-prototype.patch').read_bytes()).hexdigest()==json.loads((out/'candidate-json-direct-build.json').read_text())['tracked_diff_sha256']
(out/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(out.iterdir()) if p.is_file() and p.name!='SHA256SUMS'))
print(json.dumps({'groups':len(summary),'files':len(list(out.iterdir())),'bytes':sum(p.stat().st_size for p in out.iterdir() if p.is_file())}))
