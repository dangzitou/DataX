from pathlib import Path
import gzip,hashlib,json,shutil

root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-atomic-unlogged';archive.mkdir(parents=True,exist_ok=True)
directories=['atomic-unlogged-crash-baseline','atomic-unlogged-crash-candidate','atomic-unlogged-regression',
             'atomic-unlogged-float','atomic-unlogged-upgrade','atomic-unlogged-perf-table-single',
             'atomic-unlogged-perf-stream-to-pg','atomic-unlogged-perf-table-parallel',
             'atomic-unlogged-crash-v2-baseline','atomic-unlogged-crash-v2-candidate']
counts={}
for name in directories:
 directory=root/name;report=json.loads((directory/'results.json').read_text())
 shutil.copyfile(directory/'results.json',archive/(name+'.json'))
 payload=json.dumps({p.name:p.read_text() for p in sorted(directory.iterdir()) if p.is_file()
    and p.suffix in ['.json','.log','.sql','.properties'] and p.name!='results.json'},ensure_ascii=True).encode()
 dest=archive/(name+'-artifacts.json.gz');dest.write_bytes(gzip.compress(payload,mtime=0))
 assert gzip.decompress(dest.read_bytes())==payload
 counts[name]={'result_entries':len(report.get('results',report.get('runs',[]))),
               'engine_logs':len(list(directory.glob('*.log'))),'pending_run':report.get('pending_run')}
for p in sorted(root.iterdir()):
 if not p.is_file() or not p.name.startswith(('atomic-unlogged-','run-atomic-unlogged','check-atomic-unlogged',
                                            'archive-atomic-unlogged','summarize-atomic-unlogged')):continue
 if p.suffix=='.log':
  data=p.read_bytes();dest=archive/(p.name+'.gz');dest.write_bytes(gzip.compress(data,mtime=0));assert gzip.decompress(dest.read_bytes())==data
 elif p.suffix in ['.json','.py','.patch']:shutil.copyfile(p,archive/p.name)
for name in ['candidate-atomic-group-final','candidate-atomic-unlogged']:
 runtime=root/name;meta=json.loads((runtime/'build-metadata.json').read_text())
 for rel,digest in meta['jars'].items():assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
 shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
 (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_atomic_crash_checks.py','postgresql_atomic_checks.py','postgresql_scenarios.py',
             'postgresql_float_checks.py','postgresql_checks.py','postgresql_commit_wire_checks.py',
             'mysql_scenarios.py','mysql_querysql.py','performance_gate.py','build.py']:
 shutil.copyfile(repo/'benchmarks'/name,archive/name)
shutil.copyfile(repo/'.github/workflows/data-fidelity.yml',archive/'data-fidelity.yml')
shutil.copyfile(root/'postgresql_atomic_crash_checks-initial.py',archive/'postgresql_atomic_crash_checks-initial.py')
(archive/'counts.json').write_text(json.dumps(counts,indent=2))
print('Archived',len(list(archive.iterdir())),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes. Runtime hashes verified.')
