from pathlib import Path
import json, sys
sys.path.insert(0, 'benchmarks')
from postgresql_checks import sql

out = Path('/tmp/datax-perf/atomic-group-multisets.json')
assert not out.exists()
a, b = "decode('0000ff','hex')", "decode('0000fe','hex')"
null = 'record_send(ROW(NULL::text))'
empty = "record_send(ROW(''::text))"
cases = [
    ('empty', [], [], True),
    ('duplicates-reordered', [a, b, a, null], [null, a, a, b], True),
    ('missing-duplicate', [a, a, b], [a, b], False),
    ('extra-duplicate', [a, b], [a, a, b], False),
    ('same-count-different-multiplicity', [a, a, b], [a, b, b], False),
    ('same-count-changed-byte', [a], [b], False),
    ('null-vs-empty', [null], [empty], False),
    ('float-signed-zero', ["record_send(ROW('-0'::float8))"], ['record_send(ROW(0::float8))'], False),
    ('numeric-scale', ['record_send(ROW(1.0::numeric))'], ['record_send(ROW(1.00::numeric))'], False),
]

def values(items):
    return 'VALUES ' + ','.join('('+x+')' for x in items) if items else 'SELECT NULL::bytea WHERE false'

results = []
for attempt in range(1, 5):
    for name, sent, written, equal in cases:
        query = ('WITH sent(b) AS ('+values(sent)+'), written(b) AS ('+values(written)+'), '
                 'previous AS ((TABLE sent EXCEPT ALL TABLE written) UNION ALL (TABLE written EXCEPT ALL TABLE sent)), '
                 'candidate AS (SELECT b FROM (SELECT b,1 n FROM sent UNION ALL SELECT b,-1 n FROM written) '
                 'compared GROUP BY b HAVING sum(n)<>0) '
                 'SELECT json_build_object(\'previous_equal\',NOT EXISTS(TABLE previous),'
                 "'candidate_equal',NOT EXISTS(TABLE candidate))")
        actual = json.loads(sql(query))
        assert actual['previous_equal'] == actual['candidate_equal'] == equal, (name, actual)
        results.append(dict(case=name, attempt=attempt, expected_equal=equal, **actual))
out.write_text(json.dumps({'scope': 'Direct real PostgreSQL SQL checks, not Engine or performance tests',
                         'results': results}, indent=2))
print('PASS', len(results), 'direct SQL multiset checks')
