from pathlib import Path
import json,sys,statistics
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate

root=Path('/tmp/datax-perf'); entries=[]
for scenario in ['table-single','stream-to-pg','table-parallel']:
    report=json.loads((root/('atomic-unlogged-perf-'+scenario)/'results.json').read_text())
    nonnegative=evaluate(report,threshold=0)
    entry={'scenario':scenario,'medians_seconds':report['median_seconds'],
        'median_throughput_gain_percent':report['throughput_gain_percent'],
        'meets_performance_retention':report['throughput_gain_percent']>=5 and nonnegative['passed'],
        'minimum_gain_percent':nonnegative['minimum_gain_percent'],
        'gate_25':evaluate(report,threshold=25),'gate_50':evaluate(report,threshold=50)}
    entry['median_database_wal_bytes']={v:statistics.median(r['database_wal_bytes_during_run'] for r in report['runs'] if r['variant']==v and r['round']>0) for v in ['baseline','candidate']}
    entries.append(entry)
    print(scenario,entry['medians_seconds'],entry['median_throughput_gain_percent'],
          'min',entry['minimum_gain_percent'],'+25',entry['gate_25']['passed'],'+50',entry['gate_50']['passed'])
summary={'scope':'Same atomic mode relative to c9b499f, not untouched upstream or other writers',
    'results':entries,'meets_initial_performance_retention':all(x['meets_performance_retention'] for x in entries),'retained':False,'recovery_decision':'Default logged staging retained regardless of throughput; unlogged crash destroys the staged recovery copy.'}
(root/'atomic-unlogged-summary.json').write_text(json.dumps(summary,indent=2))
print('retained',summary['retained'])
