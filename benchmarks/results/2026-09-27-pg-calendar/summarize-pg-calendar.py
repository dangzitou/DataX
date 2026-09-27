from pathlib import Path
import json,sys
sys.path.insert(0,'benchmarks')
from performance_gate import evaluate
root=Path('/tmp/datax-perf'); entries=[]
for scenario in ['table-single','table-parallel']:
    report=json.loads((root/('pg-calendar-perf-'+scenario)/'results.json').read_text())
    assert len(report['runs'])==12 and not report.get('pending_run')
    entry={'scenario':scenario,'medians_seconds':report['median_seconds'],
        'median_throughput_gain_percent':report['throughput_gain_percent'],
        'minimum_gain_percent':evaluate(report,threshold=0)['minimum_gain_percent'],
        'gate_25':evaluate(report,threshold=25),'gate_50':evaluate(report,threshold=50)}
    entries.append(entry); print(json.dumps(entry),flush=True)
(root/'pg-calendar-summary.json').write_text(json.dumps({'scope':'Safety-check cost on valid modern data relative to the previous fork; not original upstream or production error-rate proof','results':entries},indent=2))
