from pathlib import Path
import gzip, hashlib, json, shutil

root = Path('/tmp/datax-perf')
repo = Path('/Users/dengzitao/Developer/projects/DataX')
archive = repo/'benchmarks/results/2026-09-27-atomic-types'
archive.mkdir(parents=True, exist_ok=True)

def compressed(source, destination):
    data = source.read_bytes()
    destination.write_bytes(gzip.compress(data, mtime=0))
    assert gzip.decompress(destination.read_bytes()) == data

for name in ['atomic-domain-before', 'atomic-domain-after', 'atomic-types-regression',
             'atomic-types-regression-v2', 'atomic-types-float',
             'native-batch-pilot-table-single', 'native-batch-pilot-stream-to-pg']:
    directory = root/name
    if not (directory/'results.json').exists():
        continue
    shutil.copyfile(directory/'results.json', archive/(name+'.json'))
    artifacts = {p.name: p.read_text() for p in sorted(directory.iterdir())
                 if p.is_file() and p.suffix in ['.json', '.log'] and p.name != 'results.json'}
    payload = json.dumps(artifacts, ensure_ascii=True).encode()
    dest = archive/(name+'-artifacts.json.gz')
    dest.write_bytes(gzip.compress(payload, mtime=0))
    assert gzip.decompress(dest.read_bytes()) == payload

for name in ['jdbc-profile-table-single', 'jdbc-profile-stream-to-pg']:
    directory = root/name
    selected = json.loads((directory/'selected-events.json').read_text())
    assert {e['type'] for e in selected['recording']['events']} == {'jdk.ExecutionSample', 'jdk.NativeMethodSample'}
    # Only selected stack samples are published; the full recording has environment events.
    for file in ['results.json', 'summary.json', 'profile.json']:
        shutil.copyfile(directory/file, archive/(name+'-'+file))
    for file in ['profile.log', 'selected-events.json']:
        compressed(directory/file, archive/(name+'-'+file+'.gz'))

for file in ['atomic-domain-before.py', 'atomic-domain-after.py', 'profile-current-jdbc.py',
             'run-native-batch-pilot.py', 'native-batch-pilot-plan.json', 'native-batch-pilot-state.json',
             'archive-atomic-types.py']:
    shutil.copyfile(root/file, archive/file)
for file in ['atomic-domain-before.log', 'atomic-domain-after.log', 'atomic-types-regression.log',
             'atomic-types-regression-v2.log', 'atomic-types-float.log', 'atomic-types-unit.log']:
    if (root/file).exists():
        compressed(root/file, archive/(file+'.gz'))
for name in ['candidate-float-driver', 'candidate-atomic-types']:
    runtime = root/name
    metadata = json.loads((runtime/'build-metadata.json').read_text())
    for file, digest in metadata['jars'].items():
        assert hashlib.sha256((runtime/file).read_bytes()).hexdigest() == digest, (name, file)
    shutil.copyfile(runtime/'build-metadata.json', archive/(name+'-build.json'))
    compressed(runtime/'build.log', archive/(name+'-build.log.gz'))
for file in ['postgresql_atomic_checks.py', 'postgresql_float_checks.py', 'postgresql_checks.py',
             'postgresql_commit_wire_checks.py', 'postgresql_scenarios.py', 'mysql_querysql.py',
             'mysql_scenarios.py', 'performance_gate.py', 'build.py']:
    shutil.copyfile(repo/'benchmarks'/file, archive/file)
shutil.copyfile(root/'json-direct-profile-before/profile-1ms.jfc', archive/'profile-1ms.jfc')
print('archived', len(list(archive.iterdir())), 'files;', sum(p.stat().st_size for p in archive.iterdir()), 'bytes')
