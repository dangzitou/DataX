from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate

root=Path('/tmp/datax-perf'); entries=[]
for scenario in ['table-single','stream-to-pg','table-parallel']:
    report=json.loads((root/('atomic-merge-perf-'+scenario)/'results.json').read_text())
    nonnegative=evaluate(report,threshold=0)
    entry={'scenario':scenario,'medians_seconds':report['median_seconds'],
        'median_throughput_gain_percent':report['throughput_gain_percent'],
        'retained':report['throughput_gain_percent']>=5 and nonnegative['passed'],
        'minimum_gain_percent':nonnegative['minimum_gain_percent'],
        'gate_25':evaluate(report,threshold=25),'gate_50':evaluate(report,threshold=50)}
    entries.append(entry)
    print(scenario,entry['medians_seconds'],entry['median_throughput_gain_percent'],
          'min',entry['minimum_gain_percent'],'+25',entry['gate_25']['passed'],'+50',entry['gate_50']['passed'])
summary={'scope':'Same atomic mode relative to c9b499f, not untouched upstream or other writers',
    'results':entries,'retained':all(x['retained'] for x in entries)}
(root/'atomic-merge-summary.json').write_text(json.dumps(summary,indent=2))
print('retained',summary['retained'])
