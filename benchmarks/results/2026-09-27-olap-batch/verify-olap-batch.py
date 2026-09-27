from pathlib import Path
import ast,gzip,hashlib,json,subprocess,sys,tempfile
repo=Path('/Users/dengzitao/Developer/projects/DataX');root=Path('/tmp/datax-perf')
archive=repo/'benchmarks/results/2026-09-27-olap-batch'
manifest=json.loads((archive/'SHA256SUMS.json').read_text())
for name,digest in manifest.items():assert hashlib.sha256((archive/name).read_bytes()).hexdigest()==digest,name
for name in ['mysql_querysql.py','olap_scenarios.py']:ast.parse((repo/'benchmarks'/name).read_text())
checks=[]
with tempfile.TemporaryDirectory(prefix='datax-batch-cli-') as temporary:
 for scenario,flags in [('writer-json',['--baseline-batch-mib','0']),('writer-csv',['--candidate-batch-mib','33']),('reader-pg',['--baseline-batch-mib','20'])]:
  output=Path(temporary)/str(len(checks))
  result=subprocess.run([sys.executable,str(repo/'benchmarks/olap_scenarios.py'),'unused-baseline','unused-candidate',str(output),'--backend','starrocks','--scenario',scenario,*flags],capture_output=True,text=True)
  assert result.returncode!=0 and 'AssertionError' in result.stderr and not output.exists(),result.stderr
  checks.append({'scenario':scenario,'flags':flags,'rejected_before_output_or_database_access':True})
patch=(root/'row-count-implementation.patch').read_bytes()
changed=['plugin-rdbms-util/src/main/java/com/alibaba/datax/plugin/rdbms/util/DBUtilErrorCode.java','plugin-rdbms-util/src/main/java/com/alibaba/datax/plugin/rdbms/writer/CommonRdbmsWriter.java','plugin-rdbms-util/src/test/java/com/alibaba/datax/plugin/rdbms/writer/RdbmsWriterRegressionTest.java']
actual=subprocess.check_output(['git','diff','baf6c1f','255d256','--',*changed],cwd=repo)
assert actual==patch,'Runtime implementation patch does not match committed change'
assert not subprocess.check_output(['git','diff','baf6c1f','255d256','--','*.java',':(exclude)benchmarks/**',*[':(exclude)'+p for p in changed]],cwd=repo),'Other Java changes not included in runtime'
result={'manifest_files_verified':len(manifest),'cli_rejection_checks':checks,'production_java_matches_255d256':True,'status':'passed'}
(root/'olap-batch-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
