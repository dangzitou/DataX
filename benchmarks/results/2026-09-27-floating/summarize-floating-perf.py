from pathlib import Path
import json,statistics,sys
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate
root=Path('/tmp/datax-perf');summary=[]
for file in sorted(root.glob('floating-perf-*/results.json')):
 report=json.loads(file.read_text());runs=report['runs'];assert len(runs)==12
 assert all(x['actual']==1000000 and x['mismatched_rows']==0 for x in runs)
 if report['scenario'].endswith('-file'):assert all(x['exact_byte_equality'] for x in runs)
 item={'name':file.parent.name,'scenario':report['scenario'],'median_seconds':report['median_seconds'],
  'throughput_gain_percent':report['throughput_gain_percent'],
  'gate25':evaluate(report,metric='throughput',threshold=25,minimum_rounds=5),
  'gate50':evaluate(report,metric='throughput',threshold=50,minimum_rounds=5),
  'cpu_medians':{v:{key:statistics.median(x[key] for x in runs if x['variant']==v and x['round']) for key in ['user_cpu_seconds','system_cpu_seconds','peak_rss_mib']} for v in ['baseline','candidate']}}
 assert item['gate50']==json.loads((file.parent/'gate.json').read_text()),file
 summary.append(item)
 print(item['name'],round(item['throughput_gain_percent'],3),'min throughput pair',round(item['gate25']['minimum_gain_percent'],3),'gates',item['gate25']['passed'],item['gate50']['passed'])
assert len(summary)==12 and all(not g['gate25']['passed'] and not g['gate50']['passed'] for g in summary)
(root/'floating-perf-summary.json').write_text(json.dumps({'groups':summary,'million_row_jobs':144,'goal_met':False},indent=2))
