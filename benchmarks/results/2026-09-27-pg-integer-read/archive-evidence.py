import gzip, hashlib, json, shutil, statistics, sys
from pathlib import Path

repo = Path('/Users/dengzitao/Developer/projects/DataX')
root = Path('/tmp/datax-perf')
out = repo / 'benchmarks/results/2026-09-27-pg-integer-read'
out.mkdir(exist_ok=True)
sys.path.insert(0, str(repo / 'benchmarks'))
from performance_gate import evaluate

def archive(name):
    folder = root / name
    shutil.copy2(folder / 'results.json', out / (name + '.json'))
    artifacts = {str(p.relative_to(folder)): p.read_text() for p in sorted(folder.rglob('*'))
                 if p.is_file() and p.suffix in ('.json', '.log', '.properties')
                 and p.name not in ('results.json', 'gate.json')}
    (out / (name + '-artifacts.json.gz')).write_bytes(
        gzip.compress(json.dumps(artifacts, ensure_ascii=False, sort_keys=True).encode(), mtime=0))

plan = json.loads((root / 'native-read-perf-plan.json').read_text())
summary = []
rows = []
historical = json.loads((repo / 'benchmarks/results/2026-09-27-integer/gates-25-all.json').read_text())
assert historical['summary']['comparisons'] == len(historical['results']) == 42
for entry in historical['results']:
    assert hashlib.sha256((repo / entry['report']).read_bytes()).hexdigest() == entry['report_sha256']
labels = {'pg-integer-file': '八列整数→文件', 'pg-to-file': '六列混合→文件',
          'table-single': '普通表单路→PG', 'table-parallel': '普通表四路→PG'}
for entry in plan['comparisons']:
    name = Path(entry['output']).name
    report = json.loads((root / name / 'results.json').read_text())
    assert len(report['runs']) == 12
    assert all(r['actual'] == r['expected'] == 1000000 and r['mismatched_rows'] == 0 for r in report['runs'])
    archive(name)
    gates = {n: evaluate(report, threshold=n) for n in (25, 50)}
    for n, gate in gates.items():
        (out / (name + '-gate-' + str(n) + '.json')).write_text(json.dumps(gate, indent=2))
    m = report['median_seconds']
    gate = gates[25]
    cpu = {v: statistics.median(r['user_cpu_seconds'] for r in report['runs']
                               if r['variant'] == v and r['round']) for v in ('baseline', 'candidate')}
    summary.append({**entry, 'median_seconds': m, 'gain_percent': report['throughput_gain_percent'],
                    'minimum_gain_percent': gate['minimum_gain_percent'], 'cpu_user_seconds': cpu,
                    'gate_25_passed': gate['passed'], 'gate_50_passed': gates[50]['passed']})
    rows.append('| %s | %s | %.3f | %.3f | %+.2f%% | %+.2f%% | %s |' % (
        labels[entry['scenario']], '前版 3a0d252' if entry['reference'] == 'before' else '原版 80ec23d',
        m['baseline'], m['candidate'], report['throughput_gain_percent'],
        gate['minimum_gain_percent'], '通过' if gate['passed'] else '未通过'))
    result_path = out / (name + '.json')
    historical['results'].append({'report': str(result_path.relative_to(repo)),
        'report_sha256': hashlib.sha256(result_path.read_bytes()).hexdigest(), 'rows': report['rows'],
        'throughput_gain_percent': report['throughput_gain_percent'], 'gate': gate})

for name in ['native-read-integers-before', 'native-read-integers-after',
             'native-read-integers-before-final', 'native-read-integers-after-final',
             'native-read-fidelity', 'native-read-snapshot', 'native-read-atomic', 'native-read-binding']:
    archive(name)
for runtime in ['baseline-all', 'candidate-binding', 'candidate-native-read']:
    shutil.copy2(root / runtime / 'build-metadata.json', out / (runtime + '-build.json'))
for name in ['native-read-runtime-diff.json', 'native-read-perf-plan.json', 'native-read-storage-before.json']:
    shutil.copy2(root / name, out / name)
for name in ['native-read-unit.log', 'native-read-unit-final.log']:
    (out / (name + '.gz')).write_bytes(gzip.compress((root / name).read_bytes(), mtime=0))
(out / 'build.log.gz').write_bytes(gzip.compress((root / 'candidate-native-read/build.log').read_bytes(), mtime=0))
for name in ['postgresql_integer_checks.py', 'PostgresqlIntegerReadCheck.java', 'postgresql_scenarios.py',
             'postgresql_checks.py', 'postgresql_snapshot_checks.py', 'postgresql_atomic_checks.py',
             'postgresql_binding_checks.py', 'mysql_querysql.py', 'mysql_scenarios.py', 'performance_gate.py', 'build.py']:
    shutil.copy2(repo / 'benchmarks' / name, out / name)
shutil.copy2(Path(__file__), out / 'archive-evidence.py')
(out / 'summary.json').write_text(json.dumps(summary, indent=2))
passed = sum(bool(e['gate']['passed']) for e in historical['results'])
historical['scope'] = '50 historical comparisons, including intermediate candidates and previous-fork controls; not distinct scenarios or a final-HEAD all-scenario retest.'
historical['summary'] = {'comparisons': len(historical['results']), 'passed': passed,
                         'unpassed_or_incomplete': len(historical['results']) - passed}
(out / 'gates-25-all.json').write_text(json.dumps(historical, indent=2))
markdown = '| 场景 | 参照 | 参照中位秒 | 候选中位秒 | 吞吐变化 | 最差配对 | 每轮 +25% |\n|---|---|---:|---:|---:|---:|---|\n' + '\n'.join(rows)
markdown += '\n\n共 96 次百万行作业，包括双方预热；全部完成对应数据检查。八组中每轮 +25%% 通过 %d 组，+50%% 通过 %d 组。中位数不能覆盖变慢的配对；相对前版的结果不能当作相对原版的收益。' % (
    sum(r['gate_25_passed'] for r in summary), sum(r['gate_50_passed'] for r in summary))
report_file = repo / 'benchmarks/REPORT-pg-integer-read.zh-CN.md'
text = report_file.read_text()
assert 'PERFORMANCE_RESULTS_PENDING' in text
report_file.write_text(text.replace('PERFORMANCE_RESULTS_PENDING', markdown))
print(json.dumps({'comparisons': len(summary), 'historical': historical['summary'],
                  'evidence_files_so_far': len(list(out.iterdir()))}))
