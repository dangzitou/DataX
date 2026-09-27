from pathlib import Path
import hashlib,json,statistics,sys
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
sys.path.insert(0,str(repo/'benchmarks'))
from performance_gate import evaluate
state=json.loads((root/'olap-batch-state.json').read_text())
assert state.get('finished_at') and not state.get('error') and not state.get('running'),state
assert len(state['completed'])==6
summary={'scope':'Four same-runtime batch-size tuning groups; two untouched-upstream code controls at equal 20 MiB; no billion-row or recovery acceptance','groups':{}}
script_hash=hashlib.sha256((repo/'benchmarks/olap_scenarios.py').read_bytes()).hexdigest()
for name in state['completed']:
 out=root/name;d=json.loads((out/'results.json').read_text())
 assert len(d['runs'])==12 and not d.get('pending_run')
 assert d['script_sha256']==script_hash
 g={'backend':d['backend'],'scenario':d['scenario'],'comparison':'upstream versus fork, same 20 MiB' if name.endswith('same-config') else 'same fork runtime, 5 MiB versus 20 MiB','batch_mib':d['batch_mib'],'median_seconds':d['median_seconds'],'median_throughput_gain_percent':d['throughput_gain_percent'],'gate25':evaluate(d,threshold=25),'gate50':evaluate(d,threshold=50),'variants':{}}
 for v in ['baseline','candidate']:
  runs=[x for x in d['runs'] if x['variant']==v and x['round']>0]
  g['variants'][v]={'peak_rss_mib_median':statistics.median(x['peak_rss_mib'] for x in runs),'peak_rss_mib_max':max(x['peak_rss_mib'] for x in runs),'stream_load_attempts':sorted({x['stream_load_attempts'] for x in runs}),'max_request_body_bytes':max(x['max_request_body_bytes'] for x in runs),'attempted_body_bytes':sorted({x['attempted_body_bytes'] for x in runs})}
 for run in d['runs']:
  assert run['expected']==run['actual']==run['distinct_keys']==1000000 and run['mismatched_rows']==0
  run_name=('warmup' if run['round']==0 else str(run['round']))+'-'+run['variant']
  config=json.loads((out/(run_name+'.json')).read_text())
  assert config==d['configs_by_variant'][run['variant']]
  p=config['job']['content'][0]['writer']['parameter']
  assert p['maxBatchSize' if d['backend']=='starrocks' else 'batchSize']==d['batch_mib'][run['variant']]*1024**2
  assert p['loadProps']['strict_mode'] is True and p['loadProps']['max_filter_ratio']==0
 summary['groups'][name]=g
assert set(summary['groups'])==set(state['completed'])
summary['engine_runs']=sum(len(json.loads((root/n/'results.json').read_text())['runs']) for n in state['completed'])
summary['maximum_database_storage_before_bytes']=max(x['database_storage_before_bytes'] for n in state['completed'] for x in json.loads((root/n/'results.json').read_text())['runs'])
(root/'olap-batch-summary.json').write_text(json.dumps(summary,indent=2))
for n,g in summary['groups'].items():
 print(n,g['median_seconds'],round(g['median_throughput_gain_percent'],3),'worst',round(g['gate25']['minimum_gain_percent'],3),'pass25',g['gate25']['passed'],'pass50',g['gate50']['passed'])
print('Validated configs and full-field results:',summary['engine_runs'],'jobs')
