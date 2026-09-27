from pathlib import Path
import gzip, hashlib, json, shutil, subprocess

root = Path('/tmp/datax-perf'); repo = Path('/Users/dengzitao/Developer/projects/DataX')
archive = repo/'benchmarks/results/2026-09-27-atomic-merge'; archive.mkdir(parents=True,exist_ok=True)
directories = ['atomic-hash-probe','atomic-hash-regression','atomic-hash-float','atomic-hash-upgrade',
    'atomic-hash-perf-table-single','atomic-hash-perf-stream-to-pg','atomic-merge-probe',
    'atomic-merge-regression','atomic-merge-float','atomic-merge-upgrade',
    'atomic-merge-perf-table-single','atomic-merge-perf-stream-to-pg','atomic-merge-perf-table-parallel','atomic-merge-retained']
counts = {}
for name in directories:
    directory = root/name; result = directory/'results.json'
    shutil.copyfile(result,archive/(name+'.json'))
    artifacts = {p.name:p.read_text() for p in sorted(directory.iterdir())
                 if p.is_file() and p.suffix in ['.json','.log','.sql','.properties'] and p.name!='results.json'}
    payload = json.dumps(artifacts,ensure_ascii=True).encode(); dest=archive/(name+'-artifacts.json.gz')
    dest.write_bytes(gzip.compress(payload,mtime=0)); assert gzip.decompress(dest.read_bytes())==payload
    report=json.loads(result.read_text())
    entries=report if isinstance(report,list) else report.get('runs',report.get('results',report.get('checks',[])))
    counts[name]={'result_entries':len(entries),'engine_logs':len(list(directory.glob('*.log'))),
                 'pending_run':report.get('pending_run') if isinstance(report,dict) else None}
prefixes=('atomic-hash-','atomic-merge-','run-atomic-hash','run-atomic-merge',
          'check-atomic-hash','check-atomic-merge','probe-atomic-hash','probe-atomic-merge',
          'check-hash-evaluations','archive-atomic-merge','summarize-atomic-merge')
for p in sorted(root.iterdir()):
    if not p.is_file() or not p.name.startswith(prefixes): continue
    if p.suffix=='.log':
        data=p.read_bytes(); dest=archive/(p.name+'.gz'); dest.write_bytes(gzip.compress(data,mtime=0))
        assert gzip.decompress(dest.read_bytes())==data
    elif p.suffix in ['.json','.py','.patch']: shutil.copyfile(p,archive/p.name)
for name in ['candidate-atomic-group-final','candidate-atomic-hash','candidate-atomic-merge','candidate-atomic-merge-final']:
    runtime=root/name
    if not runtime.exists(): continue
    meta=json.loads((runtime/'build-metadata.json').read_text())
    for rel,digest in meta['jars'].items(): assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
    shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
    (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_scenarios.py','postgresql_atomic_checks.py','postgresql_float_checks.py','postgresql_checks.py',
             'postgresql_commit_wire_checks.py','mysql_scenarios.py','mysql_querysql.py','performance_gate.py','build.py']:
    shutil.copyfile(repo/'benchmarks'/name,archive/name)
(archive/'postgresql_atomic_checks-hash.py').write_bytes(subprocess.check_output(
    ['git','show','2c94c0f:benchmarks/postgresql_atomic_checks.py'],cwd=repo))
(archive/'counts.json').write_text(json.dumps(counts,indent=2))
print('Archived',len(list(archive.iterdir())),'files,',sum(p.stat().st_size for p in archive.iterdir()),'bytes; runtime hashes verified.')
