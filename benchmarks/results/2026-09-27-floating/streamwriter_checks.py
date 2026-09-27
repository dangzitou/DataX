#!/usr/bin/env python3
"""Real Engine file-output checks on bounded fixtures; no production filesystem claim."""
import argparse
import hashlib
import json
from pathlib import Path
from mysql_querysql import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expect-legacy', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'scope': __doc__, 'build': json.loads((args.runtime/'build-metadata.json').read_text()),
              'expect_legacy': args.expect_legacy, 'results': []}
    for scenario, delimiter, channels, count, payload in [
            ('tab', '\t', 1, 3, '中文😀'), ('double', '||', 1, 3, '中文😀'),
            ('unicode', '中文', 1, 3, '中文😀'), ('empty', '', 1, 3, '中文😀'),
            ('parallel', '\t', 4, 256, '中文😀abcd' * 1024),
            ('parallel-large', '\t', 4, 32, '中文😀abcd' * 8192)]:
        fields = ['1', payload, 'null', 'tail-end']
        expected = ((delimiter.join(fields) + '\n') * (channels * count)).encode()
        expected_hash = hashlib.sha256(expected).hexdigest()
        for attempt in range(1, 5):
            name = '%s-%d' % (scenario, attempt)
            config = {'core': {'container': {'job': {'sleepInterval': 200},
                                             'taskGroup': {'channel': channels}}},
                      'job': {'setting': {'speed': {'channel': channels}, 'errorLimit': {'record': 0}},
                              'content': [{'reader': {'name': 'streamreader', 'parameter': {
                                  'sliceRecordCount': count, 'column': [
                                      {'type': 'long', 'value': 1}, {'type': 'string', 'value': payload},
                                      {'type': 'string', 'value': 'null'}, {'type': 'string', 'value': 'tail-end'}]}},
                                  'writer': {'name': 'streamwriter', 'parameter': {
                                      'path': str(output), 'fileName': 'actual.txt', 'print': False,
                                      'fieldDelimiter': delimiter}}}]}}
            seconds = run(args.runtime.resolve(), config, output, name)
            actual = (output/'actual.txt').read_bytes()
            digest = hashlib.sha256(actual).hexdigest()
            exact = actual == expected
            entry = {'case': name, 'seconds': seconds, 'channels': channels,
                     'expected_rows': channels*count, 'actual_newlines': actual.count(b'\n'),
                     'expected_bytes': len(expected), 'actual_bytes': len(actual),
                     'expected_sha256': expected_hash, 'actual_sha256': digest, 'exact_match': exact}
            if not scenario.startswith('parallel'):
                entry['actual_text'] = actual.decode('UTF8')
            report['results'].append(entry)
            (output/'results.json').write_text(json.dumps(report, indent=2))
            (output/'actual.txt').unlink()
            print(json.dumps(entry), flush=True)
            if args.expect_legacy and scenario.startswith('parallel'):
                # Scheduling can hide the historical race on some hosts; record the observation.
                pass
            elif args.expect_legacy and scenario != 'tab':
                assert not exact, 'Historical problem was not reproduced: ' + name
            else:
                assert exact, name


if __name__ == '__main__':
    main()
