from pathlib import Path
import gzip,hashlib,json,shutil
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-olap-batch';archive.mkdir(parents=True,exist_ok=True)
state=json.loads((root/'olap-batch-state.json').read_text())
assert state.get('finished_at') and not state.get('error') and not state.get('running')
counts={}
for name in state['completed']+['olap-current-'+b+'-'+f for b in ['starrocks','doris'] for f in ['csv','json']]:
 directory=root/name;report=json.loads((directory/'results.json').read_text());assert not report.get('pending_run')
 shutil.copyfile(directory/'results.json',archive/(name+'.json'))
 payload=json.dumps({p.name:p.read_text() for p in sorted(directory.iterdir()) if p.is_file() and p.suffix in ['.json','.log','.xml'] and p.name!='results.json'},ensure_ascii=True).encode()
 dest=archive/(name+'-artifacts.json.gz');dest.write_bytes(gzip.compress(payload,mtime=0));assert gzip.decompress(dest.read_bytes())==payload
 counts[name]={'result_entries':len(report.get('runs',[])),'engine_logs':len(list(directory.glob('*.log')))}
 if (directory/'events.json.gz').exists():
  shutil.copyfile(directory/'events.json.gz',archive/(name+'-selected-events.json.gz'))
  assert json.loads(gzip.decompress((archive/(name+'-selected-events.json.gz')).read_bytes()))['recording']['events']
for p in sorted(root.iterdir()):
 if not p.is_file() or not p.name.startswith(('olap-batch-','olap-current-starrocks-container-','olap-current-doris-container-','run-olap-batch','summarize-olap-batch','archive-olap-batch','profile-current-olap')):continue
 if p.suffix=='.log':
  data=p.read_bytes();dest=archive/(p.name+'.gz');dest.write_bytes(gzip.compress(data,mtime=0));assert gzip.decompress(dest.read_bytes())==data
 elif p.suffix in ['.json','.py']:shutil.copyfile(p,archive/p.name)
for name in ['baseline-all','candidate-row-count']:
 runtime=root/name;meta=json.loads((runtime/'build-metadata.json').read_text())
 for rel,digest in meta['jars'].items():assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
 shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
 data=(runtime/'build.log').read_bytes();(archive/(name+'-build.log.gz')).write_bytes(gzip.compress(data,mtime=0))
for name in ['olap_scenarios.py','mysql_querysql.py','mysql_scenarios.py','postgresql_checks.py','postgresql_scenarios.py','starrocks_checks.py','performance_gate.py','build.py']:
 shutil.copyfile(repo/'benchmarks'/name,archive/name)
shutil.copyfile(root/'row-count-implementation.patch',archive/'row-count-implementation.patch')
shutil.copyfile(root/'json-direct-profile-before/profile-1ms.jfc',archive/'profile-1ms.jfc')
(archive/'counts.json').write_text(json.dumps(counts,indent=2))
assert sum(c['engine_logs'] for c in counts.values())==76
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(archive.iterdir()) if p.name!='SHA256SUMS.json'}
(archive/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2))
print('Archived',len(manifest),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes; 76 Engine logs; all JAR hashes verified')
