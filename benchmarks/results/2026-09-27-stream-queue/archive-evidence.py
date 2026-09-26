"""Archive this local validation run, preserving development failures and compiled probes."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import shutil

repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
root = Path('/tmp/datax-perf')

for folder in sorted(root.glob('stream-queue-*')):
    if not folder.is_dir():
        continue
    if (folder / 'results.json').exists():
        shutil.copy2(folder / 'results.json', out / (folder.name + '.json'))
    files = sorted(p for p in folder.rglob('*') if p.is_file())
    artifacts = {
        'text': {str(p.relative_to(folder)): p.read_text() for p in files
                 if p.suffix in ('.log', '.json', '.properties', '.java') and p.name != 'results.json'},
        'compiled_java_base64': {str(p.relative_to(folder)): base64.b64encode(p.read_bytes()).decode()
                                for p in files if p.suffix == '.class'}}
    (out / (folder.name + '-artifacts.json.gz')).write_bytes(
        gzip.compress(json.dumps(artifacts, ensure_ascii=False, sort_keys=True).encode(), mtime=0))
for file in sorted(root.glob('stream-queue-*')):
    if file.is_file() and file.suffix in ('.json', '.log'):
        if file.suffix == '.json':
            shutil.copy2(file, out / file.name)
        else:
            (out / (file.name + '.gz')).write_bytes(gzip.compress(file.read_bytes(), mtime=0))
for runtime in ['baseline-all', 'candidate-native-read', 'candidate-stream-queue']:
    source = root / runtime / 'build-metadata.json'
    if source.exists():
        shutil.copy2(source, out / (runtime + '-build.json'))
(out / 'build.log.gz').write_bytes(gzip.compress((root / 'candidate-stream-queue/build.log').read_bytes(), mtime=0))
for name in ['StreamLoadQueueFailureCheck.java', 'stream_load_queue_checks.py', 'stream_load_status_checks.py',
             'stream_load_row_checks.py', 'starrocks_checks.py', 'postgresql_checks.py', 'mysql_querysql.py', 'build.py']:
    shutil.copy2(repo / 'benchmarks' / name, out / name)

expected = {'stream-queue-final': 60, 'stream-queue-upstream-final': 8, 'stream-queue-previous-final': 12,
            'stream-queue-sr-real-before': 4, 'stream-queue-sr-real-after': 4,
            'stream-queue-doris-real-before-v2': 4, 'stream-queue-doris-real-after': 4,
            'stream-queue-status': 56, 'stream-queue-sr-rows': 22, 'stream-queue-doris-rows': 22,
            'stream-queue-sr-fidelity': 16, 'stream-queue-doris-fidelity': 16}
for name, count in expected.items():
    result = json.loads((out / (name + '.json')).read_text())
    assert len(result['results']) == count, (name, count)
    assert result.get('exit_code', 0) == 0, name
summary = {'scope': 'Small-data correctness and lifecycle tests; no throughput or production error-rate estimate.',
           'source_runtime_commit': '305cd330d9b70f8b8c46d6adc778bf249c15a0b1',
           'final_checks': expected, 'junit': 55, 'simulated_manager_checks': 80,
           'real_database_manager_checks_with_replayed_failure': 16,
           'simulated_http_engine_runs': 56, 'real_database_engine_runs': 76,
           'performance_goal_met': False, 'hundred_million_row_recovery_validated': False}
(out / 'validation-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))

(out / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
    for p in sorted(out.iterdir()) if p.is_file() and p.name != "SHA256SUMS"))
