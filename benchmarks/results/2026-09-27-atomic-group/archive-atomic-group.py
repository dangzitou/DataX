from pathlib import Path
import gzip,hashlib,json,shutil,subprocess

root=Path('/tmp/datax-perf');repo=Path('/Users/dengzitao/Developer/projects/DataX')
archive=repo/'benchmarks/results/2026-09-27-atomic-group';archive.mkdir(parents=True,exist_ok=True)
directories=['atomic-batch-pilot-table-single','atomic-batch-pilot-v2-table-single','atomic-batch-pilot-v2-stream-to-pg',
             'atomic-group-regression','atomic-group-float','atomic-group-upgrade',
             'atomic-group-perf-table-single','atomic-group-perf-stream-to-pg','atomic-publication-plans']
counts={}
for name in directories:
    directory=root/name;result=directory/'results.json'
    shutil.copyfile(result,archive/(name+'.json'))
    artifacts={p.name:p.read_text() for p in sorted(directory.iterdir())
               if p.is_file() and p.suffix in ['.json','.log','.sql','.properties'] and p.name!='results.json'}
    payload=json.dumps(artifacts,ensure_ascii=True).encode();dest=archive/(name+'-artifacts.json.gz')
    dest.write_bytes(gzip.compress(payload,mtime=0));assert gzip.decompress(dest.read_bytes())==payload
    report=json.loads(result.read_text())
    entries=report if isinstance(report,list) else report.get('runs',report.get('results',[]))
    counts[name]={'result_entries':len(entries),'engine_logs':len(list(directory.glob('*.log'))),
                  'pending_run':report.get('pending_run') if isinstance(report,dict) else None}
for p in sorted(root.iterdir()):
    if p.is_file() and (p.name.startswith(('atomic-batch-','atomic-group-','run-atomic-','check-atomic-',
                                         'explain-atomic-','atomic-publication-')) or p.name in ['PgAtomicOptionsCheck.java','archive-atomic-group.py']):
        if p.suffix=='.log':
            data=p.read_bytes();dest=archive/(p.name+'.gz');dest.write_bytes(gzip.compress(data,mtime=0));assert gzip.decompress(dest.read_bytes())==data
        elif p.suffix in ['.json','.py','.java','.patch']:
            shutil.copyfile(p,archive/p.name)
for name in ['candidate-atomic-types','candidate-atomic-group','candidate-atomic-group-final']:
    runtime=root/name;meta=json.loads((runtime/'build-metadata.json').read_text())
    for rel,digest in meta['jars'].items():assert hashlib.sha256((runtime/rel).read_bytes()).hexdigest()==digest,(name,rel)
    shutil.copyfile(runtime/'build-metadata.json',archive/(name+'-build.json'))
    (archive/(name+'-build.log.gz')).write_bytes(gzip.compress((runtime/'build.log').read_bytes(),mtime=0))
for name in ['postgresql_scenarios.py','postgresql_atomic_checks.py','postgresql_float_checks.py',
             'postgresql_checks.py','postgresql_commit_wire_checks.py','mysql_scenarios.py',
             'mysql_querysql.py','performance_gate.py','build.py']:
    shutil.copyfile(repo/'benchmarks'/name,archive/name)
(archive/'counts.json').write_text(json.dumps(counts,indent=2))
print('Archived',len(list(archive.iterdir())),'files;',sum(p.stat().st_size for p in archive.iterdir()),'bytes. Runtime JAR hashes verified.')
