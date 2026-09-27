from pathlib import Path
import gzip,hashlib,json,shutil,zipfile
root=Path('/tmp/datax-perf'); repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-jdbc-row-count'; archive.mkdir(parents=True,exist_ok=True)
dirs=['row-count-upstream','row-count-before','row-count-after','row-count-fidelity','row-count-writer',
 'row-count-wire','row-count-atomic','row-count-perf-table-single','row-count-perf-table-parallel','row-count-perf-stream-to-pg',
 'current-jdbc-profile-table-single','current-jdbc-profile-stream-to-pg']
counts={}
for name in dirs:
 directory=root/name; report=json.loads((directory/'results.json').read_text())
 assert not report.get('pending_run'),name
 shutil.copyfile(directory/'results.json',archive/(name+'.json'))
 payload=json.dumps({p.name:p.read_text() for p in sorted(directory.iterdir()) if p.is_file()
  and p.suffix in ['.json','.log','.sql'] and p.name!='results.json'},ensure_ascii=True).encode()
 dest=archive/(name+'-artifacts.json.gz');dest.write_bytes(gzip.compress(payload,mtime=0))
 assert gzip.decompress(dest.read_bytes())==payload
 counts[name]={'result_entries':len(report.get('results',report.get('runs',[]))),'engine_logs':len(list(directory.glob('*.log')))}
for p in sorted(root.iterdir()):
 if not p.is_file() or not p.name.startswith(('row-count-','run-row-count','archive-row-count','summarize-row-count','profile-current-jdbc-refresh','RowCountProbe.java')):continue
 if p.suffix=='.log':
  data=p.read_bytes();dest=archive/(p.name+'.gz');dest.write_bytes(gzip.compress(data,mtime=0));assert gzip.decompress(dest.read_bytes())==data
 elif p.suffix in ['.json','.py','.patch','.java']:shutil.copyfile(p,archive/p.name)
for name in ['baseline-all','candidate-calendar-final','candidate-row-count']:
 runtime=root/name;meta=json.loads((runtime/'build-metadata.json').read_text())
 for rel,digest in meta['jars'].items():assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
 shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
 (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_row_count_checks.py','postgresql_checks.py','postgresql_writer_checks.py','postgresql_commit_wire_checks.py',
 'postgresql_atomic_checks.py','postgresql_scenarios.py','mysql_querysql.py','mysql_scenarios.py','performance_gate.py','build.py']:
 shutil.copyfile(repo/'benchmarks'/name,archive/name)
meta=json.loads((root/'candidate-row-count/build-metadata.json').read_text())
assert meta['tracked_diff_sha256']==hashlib.sha256((root/'row-count-implementation.patch').read_bytes()).hexdigest()
changes=[]
for p in (root/'candidate-row-count').rglob('*.jar'):
 if not p.name.startswith(('datax-','plugin-rdbms-util')):continue
 rel=p.relative_to(root/'candidate-row-count');old=root/'candidate-calendar-final'/rel
 if not old.exists():continue
 with zipfile.ZipFile(p) as a,zipfile.ZipFile(old) as b:
  aa={n:hashlib.sha256(a.read(n)).hexdigest() for n in a.namelist() if n.endswith('.class')}
  bb={n:hashlib.sha256(b.read(n)).hexdigest() for n in b.namelist() if n.endswith('.class')}
  for n in sorted(aa.keys()|bb.keys()):
   if aa.get(n)!=bb.get(n):changes.append({'jar':str(rel),'class':n,'before':bb.get(n),'after':aa.get(n)})
assert {x['class'] for x in changes}=={'com/alibaba/datax/plugin/rdbms/writer/CommonRdbmsWriter$Task.class','com/alibaba/datax/plugin/rdbms/util/DBUtilErrorCode.class','com/alibaba/datax/plugin/rdbms/writer/CommonRdbmsWriter$Job.class','com/alibaba/datax/plugin/rdbms/writer/CommonRdbmsWriter.class'},changes
(archive/'class-diff.json').write_text(json.dumps(changes,indent=2))
(archive/'counts.json').write_text(json.dumps(counts,indent=2));print('Archived',len(list(archive.iterdir())),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes; JAR hashes and class changes verified')

shutil.copyfile(root/'json-direct-profile-before/profile-1ms.jfc',archive/'profile-1ms.jfc')
