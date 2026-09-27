"""Archive payload experiments, including rejected variants and the initial fixture failure."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import shutil

repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
root = Path('/tmp/datax-perf')
for folder in sorted(root.glob('payload-*')):
    if not folder.is_dir(): continue
    if (folder / 'results.json').exists():
        shutil.copy2(folder / 'results.json', out / (folder.name + '.json'))
    artifacts = {'text': {}, 'compiled_java_base64': {}}
    for file in sorted(folder.rglob('*')):
        if not file.is_file(): continue
        name = str(file.relative_to(folder))
        if file.suffix in ('.json', '.log', '.java', '.xml') and file.name != 'results.json':
            artifacts['text'][name] = file.read_text()
        elif file.suffix == '.class':
            artifacts['compiled_java_base64'][name] = base64.b64encode(file.read_bytes()).decode()
    (out / (folder.name + '-artifacts.json.gz')).write_bytes(
        gzip.compress(json.dumps(artifacts, ensure_ascii=False, sort_keys=True).encode(), mtime=0))
for file in sorted(root.glob('payload-*')):
    if file.is_file() and file.suffix == '.json': shutil.copy2(file, out / file.name)
    elif file.is_file() and file.suffix == '.log':
        (out / (file.name + '.gz')).write_bytes(gzip.compress(file.read_bytes(), mtime=0))
for runtime in ['baseline-all', 'candidate-stream-recovery', 'candidate-stream-payload',
                'candidate-stream-payload-buffered', 'candidate-stream-payload-nio', 'candidate-stream-payload-final']:
    source = root / runtime / 'build-metadata.json'
    if source.exists(): shutil.copy2(source, out / (runtime + '-build.json'))
    else: assert (out / (runtime + '-build.json')).exists(), runtime
    log = root / runtime / 'build.log'
    if log.exists(): (out / (runtime + '-build.log.gz')).write_bytes(gzip.compress(log.read_bytes(), mtime=0))
for name in ['StreamLoadPayloadCheck.java', 'stream_load_payload_checks.py', 'StreamLoadQueueFailureCheck.java',
             'stream_load_queue_checks.py', 'stream_load_status_checks.py', 'olap_scenarios.py',
             'postgresql_checks.py', 'postgresql_scenarios.py', 'starrocks_checks.py', 'mysql_querysql.py',
             'mysql_scenarios.py', 'performance_gate.py', 'build.py']:
    shutil.copy2(repo / 'benchmarks' / name, out / name)
for file in root.glob('run-payload*.py'): shutil.copy2(file, out / file.name)
(out / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
    for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
print('Archived', len(list(out.iterdir())), 'files')
