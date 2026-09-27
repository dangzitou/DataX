from pathlib import Path
import gzip,hashlib,json,shutil
root=Path('/tmp/datax-perf'); repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-atomic-singlepass'; archive.mkdir(parents=True,exist_ok=True)
directories=['atomic-singlepass-probe','atomic-singlepass-regression','atomic-singlepass-crash',
    'atomic-singlepass-calendar','atomic-singlepass-float','atomic-singlepass-upgrade',
    'atomic-singlepass-perf-table-single','atomic-singlepass-perf-stream-to-pg','atomic-singlepass-perf-table-parallel']
counts={}
for name in directories:
    directory=root/name; report=json.loads((directory/'results.json').read_text())
    shutil.copyfile(directory/'results.json',archive/(name+'.json'))
    payload=json.dumps({p.name:p.read_text() for p in sorted(directory.iterdir()) if p.is_file()
        and p.suffix in ['.json','.log','.sql','.properties'] and p.name!='results.json'},ensure_ascii=True).encode()
    dest=archive/(name+'-artifacts.json.gz'); dest.write_bytes(gzip.compress(payload,mtime=0))
    assert gzip.decompress(dest.read_bytes())==payload
    counts[name]={'result_entries':len(report.get('results',report.get('runs',report.get('checks',[])))),
        'engine_logs':len(list(directory.glob('*.log'))),'pending_run':report.get('pending_run')}
for p in sorted(root.iterdir()):
    if not p.is_file() or not p.name.startswith(('atomic-singlepass-','run-atomic-singlepass','check-atomic-singlepass',
        'probe-atomic-singlepass','archive-atomic-singlepass','summarize-atomic-singlepass')): continue
    if p.suffix=='.log':
        data=p.read_bytes(); dest=archive/(p.name+'.gz'); dest.write_bytes(gzip.compress(data,mtime=0))
        assert gzip.decompress(dest.read_bytes())==data
    elif p.suffix in ['.json','.py','.patch']: shutil.copyfile(p,archive/p.name)
for name in ['candidate-calendar-final','candidate-atomic-singlepass']:
    runtime=root/name; meta=json.loads((runtime/'build-metadata.json').read_text())
    for rel,digest in meta['jars'].items(): assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
    shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
    (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_atomic_crash_checks.py','postgresql_atomic_checks.py','postgresql_calendar_checks.py',
    'postgresql_float_checks.py','postgresql_checks.py','postgresql_scenarios.py','postgresql_commit_wire_checks.py',
    'mysql_scenarios.py','mysql_querysql.py','performance_gate.py','build.py']:
    shutil.copyfile(repo/'benchmarks'/name,archive/name)
(archive/'counts.json').write_text(json.dumps(counts,indent=2))
print('Archived',len(list(archive.iterdir())),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes; runtime hashes verified')
