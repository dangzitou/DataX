from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate
root=Path('/tmp/datax-perf'); entries=[]
for scenario in ['table-single','stream-to-pg','table-parallel']:
    report=json.loads((root/('atomic-singlepass-perf-'+scenario)/'results.json').read_text())
    assert len(report['runs'])==12 and not report.get('pending_run')
    nonnegative=evaluate(report,threshold=0)
    entry={'scenario':scenario,'medians_seconds':report['median_seconds'],
        'median_throughput_gain_percent':report['throughput_gain_percent'],
        'minimum_gain_percent':nonnegative['minimum_gain_percent'],
        'performance_retention_passed':report['throughput_gain_percent']>=5 and nonnegative['passed'],
        'gate_25':evaluate(report,threshold=25),'gate_50':evaluate(report,threshold=50)}
    entries.append(entry); print(json.dumps(entry),flush=True)
(root/'atomic-singlepass-summary.json').write_text(json.dumps({'scope':'Same atomic mode and same calendar safety checks relative to previous fork; not upstream, all writers or billion-row guarantee',
    'results':entries,'all_performance_retention_passed':all(x['performance_retention_passed'] for x in entries)},indent=2))
