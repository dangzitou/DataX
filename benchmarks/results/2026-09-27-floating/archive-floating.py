from pathlib import Path
import datetime,gzip,hashlib,json,shutil,subprocess,xml.etree.ElementTree as ET
root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-floating';archive.mkdir(parents=True,exist_ok=True)
for state in ['floating-perf-state.json','floating-perf-followup-state.json','floating-validation-state.json']:
 d=json.loads((root/state).read_text());assert 'finished_at' in d and 'error' not in d,state
for directory in sorted(root.iterdir()):
 if not directory.is_dir() or not (directory.name in ['float-upstream','float-upstream-v2','float-prior','float-common','floating-before'] or directory.name.startswith(('floating-regression-','floating-perf-'))):continue
 result=directory/'results.json'
 if result.exists():shutil.copyfile(result,archive/(directory.name+'.json'))
 artifacts={str(p.relative_to(directory)):p.read_text() for p in sorted(directory.rglob('*')) if p.is_file() and p.suffix in ['.json','.log','.properties','.java','.py'] and p!=result}
 (archive/(directory.name+'-artifacts.json.gz')).write_bytes(gzip.compress(json.dumps(artifacts,ensure_ascii=True).encode(),mtime=0))
for p in sorted(root.iterdir()):
 if p.is_file() and (p.name.startswith(('float-','floating-','run-floating-','summarize-floating-')) or p.name=='archive-floating.py'):
  if p.suffix=='.log':(archive/(p.name+'.gz')).write_bytes(gzip.compress(p.read_bytes(),mtime=0))
  elif p.suffix in ['.json','.py'] and p.name!='run-floating-validation-next.py':shutil.copyfile(p,archive/p.name)
for name in ['baseline-all','candidate-stream-fatal','candidate-float-common','candidate-float-driver']:
 runtime=root/name;meta=json.loads((runtime/'build-metadata.json').read_text())
 for rel,digest in meta['jars'].items():assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
 shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
 (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_float_checks.py','postgresql_checks.py','postgresql_binding_checks.py','postgresql_integer_checks.py',
 'postgresql_time_checks.py','postgresql_writer_checks.py','postgresql_copy_checks.py','postgresql_querysql_checks.py','postgresql_snapshot_checks.py',
 'postgresql_atomic_checks.py','postgresql_commit_wire_checks.py','jdbc_commit_checks.py','streamwriter_checks.py',
 'mysql_querysql.py','postgresql_scenarios.py','performance_gate.py','build.py','JdbcCommitFaultCheck.java','PostgresqlIntegerReadCheck.java']:
 shutil.copyfile(repo/'benchmarks'/name,archive/name)
(archive/'postgresql_float_checks-before-atomic.py').write_bytes(subprocess.check_output(['git','show','3fccc88:benchmarks/postgresql_float_checks.py'],cwd=repo))
shutil.copyfile(repo/'common/src/test/java/com/alibaba/datax/common/element/FloatingPointColumnTest.java',archive/'FloatingPointColumnTest.java')
summary={}
for module in ['common','core','plugin-rdbms-util','starrockswriter','doriswriter','streamwriter']:
 values={k:0 for k in ['tests','failures','errors','skipped']}
 for p in (repo/module/'target/surefire-reports').glob('TEST-*.xml'):
  n=ET.parse(p).getroot()
  for k in values:values[k]+=int(n.attrib.get(k,0))
 summary[module]=values
assert sum(x['tests'] for x in summary.values())==66
assert all(not sum(v[k] for k in ['failures','errors','skipped']) for v in summary.values())
(archive/'junit-final.json').write_text(json.dumps(summary,indent=2))
print('Archived',len(list(archive.iterdir())),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes. Runtime hashes all verified.')
