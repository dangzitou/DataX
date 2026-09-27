#!/usr/bin/env python3
"""Real PG JDBC rewrite controls: types, rollback, parameter limits and triggers."""
import argparse
import json
from pathlib import Path
from postgresql_checks import sql, job, validate, COLUMNS
from mysql_querysql import run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    results = []
    def record(entry):
        results.append(entry)
        (output/'results.json').write_text(json.dumps({'build':json.loads((args.runtime/'build-metadata.json').read_text()),
                                                      'results':results},indent=2))
        print(json.dumps(entry),flush=True)

    # pg_source is the precision/NULL/binary fixture created by postgresql_checks.py.
    assert int(sql('SELECT count(*) FROM pg_source')) == 4
    for rewrite in [False, True]:
        mode = 'rewrite' if rewrite else 'original-batch'
        def config(query='SELECT * FROM pg_source ORDER BY id'):
            c = job(query=query)
            c['job']['content'][0]['writer']['parameter']['connection'][0]['jdbcUrl'] += \
                '?reWriteBatchedInserts=' + str(rewrite).lower()
            return c
        for attempt in range(4):
            name = '%s-types-%d' % (mode,attempt+1)
            sql('DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source INCLUDING ALL)')
            seconds = run(args.runtime.resolve(),config(),output,name,jvm_options=['-Duser.timezone=UTC'])
            check = validate(); assert check['exact_match'],check
            record({'case':name,'seconds':seconds,**check})

        for attempt in range(4):
            name = '%s-batch-fallback-%d' % (mode,attempt+1)
            sql('DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source INCLUDING ALL)')
            c=config('SELECT * FROM pg_source UNION ALL SELECT * FROM pg_source WHERE id=2 ORDER BY id')
            c['job']['setting']['errorLimit']['record']=1
            seconds=run(args.runtime.resolve(),c,output,name,jvm_options=['-Duser.timezone=UTC'])
            check=validate(); assert check['exact_match'],check
            log=(output/(name+'.log')).read_text()
            assert '采用每次写入一行' in log
            record({'case':name,'seconds':seconds,**check})

        # The driver must split statements exceeding its bind-parameter limit.
        name=mode+'-wide-parameters'
        cols=['id']+['c%d'%i for i in range(1,100)]
        sql('DROP TABLE IF EXISTS pg_wide_target; CREATE TABLE pg_wide_target ('+
            ','.join(c+' bigint' for c in cols)+',PRIMARY KEY(id))')
        c=config('SELECT id,'+','.join('id+%d AS c%d'%(i,i) for i in range(1,100))+
                 ' FROM generate_series(1,1024) id')
        writer=c['job']['content'][0]['writer']['parameter']
        writer['column']=cols; writer['batchSize']=1024; writer['connection'][0]['table']=['pg_wide_target']
        seconds=run(args.runtime.resolve(),c,output,name,jvm_options=['-Duser.timezone=UTC'])
        assert sql('SELECT count(*) FROM pg_wide_target')=='1024'
        assert sql('SELECT count(*) FROM pg_wide_target WHERE '+
                   ' OR '.join('c%d IS DISTINCT FROM id+%d'%(i,i) for i in range(1,100)))=='0'
        record({'case':name,'seconds':seconds,'expected':1024,'actual':1024,'mismatched_rows':0})

        # Expose a semantic difference: statement-level triggers fire once per
        # rewritten INSERT, not once per input row. Never silently enable this.
        for attempt in range(4):
            name='%s-statement-trigger-%d'%(mode,attempt+1)
            sql('''DROP TABLE IF EXISTS pg_target; CREATE TABLE pg_target (LIKE pg_source INCLUDING ALL);
                DROP TABLE IF EXISTS pg_statement_audit; CREATE TABLE pg_statement_audit(n integer);
                CREATE OR REPLACE FUNCTION pg_count_statement() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN INSERT INTO pg_statement_audit VALUES(1); RETURN NULL; END $$;
                CREATE TRIGGER count_insert_statement AFTER INSERT ON pg_target
                    FOR EACH STATEMENT EXECUTE FUNCTION pg_count_statement();''')
            seconds=run(args.runtime.resolve(),config(),output,name,jvm_options=['-Duser.timezone=UTC'])
            check=validate(); assert check['exact_match'],check
            statements=int(sql('SELECT count(*) FROM pg_statement_audit'))
            assert statements==(2 if rewrite else 4),statements
            record({'case':name,'seconds':seconds,'statement_trigger_calls':statements,
                    'semantic_difference_expected':rewrite,**check})
    print('PASS',len(results),'real PG driver checks',flush=True)


if __name__=='__main__':
    main()
