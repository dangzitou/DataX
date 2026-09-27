from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate
root=Path('/tmp/datax-perf');results=[]
for scenario in ['table-single','table-parallel','stream-to-pg']:
 d=json.loads((root/('row-count-perf-'+scenario)/'results.json').read_text())
 assert not d.get('pending_run') and len(d['runs'])==12
 result={'scenario':scenario,'medians_seconds':d['median_seconds'],'median_throughput_gain_percent':d['throughput_gain_percent'],
 'gate_25':evaluate(d,threshold=25),'gate_50':evaluate(d,threshold=50)}
 results.append(result);print(json.dumps(result),flush=True)
(root/'row-count-summary.json').write_text(json.dumps({'scope':'Current safety fix vs previous fork, ordinary JDBC mode; not upstream/all-writer/billion-row performance proof','results':results},indent=2))
