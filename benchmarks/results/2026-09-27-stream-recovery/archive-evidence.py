"""Archive this small-fixture run and its exact runtime/source metadata."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
root = Path('/tmp/datax-perf')
expected = {'unverified-before': 48, 'unverified-upstream': 48, 'unverified-after': 98,
            'recovery-starrocks': 32, 'recovery-doris': 32,
            'recovery-sr-rows': 22, 'recovery-doris-rows': 22,
            'recovery-sr-fidelity': 16, 'recovery-doris-fidelity': 16}
for name, count in expected.items():
    folder = root / name
    result = json.loads((folder / 'results.json').read_text())
    assert len(result['results']) == count and not result.get('pending_run'), name
    shutil.copy2(folder / 'results.json', out / (name + '.json'))
    artifacts = {str(p.relative_to(folder)): p.read_text() for p in sorted(folder.rglob('*'))
                 if p.is_file() and p.suffix in ('.json', '.log') and p.name != 'results.json'}
    (out / (name + '-artifacts.json.gz')).write_bytes(
        gzip.compress(json.dumps(artifacts, ensure_ascii=False, sort_keys=True).encode(), mtime=0))
    (out / (name + '.log.gz')).write_bytes(gzip.compress((root / (name + '.log')).read_bytes(), mtime=0))
for name in ['unverified-unit.log', 'unverified-junit.json', 'recovery-runtime-class-diff.json',
             'recovery-starrocks-container.json', 'recovery-doris-container.json', 'recovery-storage.json']:
    source = root / name
    if source.suffix == '.log':
        (out / (name + '.gz')).write_bytes(gzip.compress(source.read_bytes(), mtime=0))
    else:
        shutil.copy2(source, out / name)
for runtime in ['baseline-all', 'candidate-stream-queue', 'candidate-stream-recovery']:
    shutil.copy2(root / runtime / 'build-metadata.json', out / (runtime + '-build.json'))
(out / 'build.log.gz').write_bytes(gzip.compress((root / 'candidate-stream-recovery/build.log').read_bytes(), mtime=0))
for name in ['stream_load_status_checks.py', 'stream_load_recovery_checks.py', 'stream_load_row_checks.py',
             'starrocks_checks.py', 'postgresql_checks.py', 'mysql_querysql.py', 'build.py']:
    shutil.copy2(repo / 'benchmarks' / name, out / name)
# Simulator runs and StarRocks started before pending_run-only logging was added.
for name in ['stream_load_status_checks.py', 'stream_load_recovery_checks.py']:
    (out / ('executed-8b95214-' + name)).write_bytes(subprocess.check_output(
        ['git', 'show', '8b95214:benchmarks/' + name], cwd=repo))
junit = json.loads((root / 'unverified-junit.json').read_text())
assert sum(x['tests'] for x in junit.values()) == 58
assert all(x['errors'] == x['failures'] == x['skipped'] == 0 for x in junit.values())
summary = {'scope': 'Small-fixture correctness; timeout status is injected, commit reply loss is real.',
           'source_runtime_commit': json.loads((root / 'candidate-stream-recovery/build-metadata.json').read_text())['revision'],
           'final_checks': expected, 'junit': 58, 'simulated_http_engine_runs': 194,
           'real_database_engine_runs_with_response_proxy': 64,
           'real_database_engine_regressions': 76, 'production_error_rate_measured': False,
           'performance_goal_met': False, 'hundred_million_row_recovery_validated': False}
(out / 'validation-summary.json').write_text(json.dumps(summary, indent=2))
(out / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
    for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
print(json.dumps(summary))
