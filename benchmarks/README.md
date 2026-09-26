# MySQL / querySql performance and reliability validation

[Final measured results and complete controls (中文)](REPORT-final.zh-CN.md):
seven paired million-row transfers each exceeded +50% throughput, with a
minimum of +62.86% and a ratio-of-medians gain of +72.66%. Same-task-count
controls gained +4.80% and +3.72%; the result is workload-specific.

[Non-querySql follow-up and charset correction (中文)](REPORT-scenarios.zh-CN.md)
adds ordinary-table, actual-file and isolated-writer results. Historical seed
SQL used a latin1 MySQL CLI connection, so its stored non-ASCII text was not the
intended Chinese. Both compared variants used that same fixture. The helper now
explicitly uses utf8mb4, with a Unicode precondition and 42 real MySQL checks.
These measurements do not validate PostgreSQL production backfills.

[PostgreSQL / StarRocks data-fidelity audit (中文)](REPORT-fidelity.zh-CN.md)
adds real database checks, timestamp fraction preservation, and fail-fast CSV
handling for StarRocks/Doris. Direct reruns still duplicate rows in targets
without a unique key, failed jobs still leave committed batches, and parallel
readers do not share a snapshot. This fork is not an exactly-once backfill system.

[PostgreSQL million-row results and native COPY (中文)](REPORT-pg.zh-CN.md)
adds six measured PG scenarios, an opt-in strict COPY writer, and native-driver
controls. All six +50% gates failed; successful correctness checks do not imply
a production backfill or universal speed guarantee.

[Doris real-server fidelity follow-up (中文)](REPORT-doris.zh-CN.md) adds 16
checks using both doriswriter and dorisreader, including the upstream CSV
corruption and candidate rejection. These small fixtures are not performance
or production-recovery validation.

This fork adds opt-in parallel querySql reads, a single-lock bounded memory
channel, cached JDBC column metadata, a faster integer conversion path, and
fixes for NULL preservation, partial dirty records, resource cleanup, and JDBC
error classification. The JDBC writer avoids redundant autocommit settings,
binds signed integers directly, preserves nullable boolean values, and clears
stale driver batches before use. Batch commit boundaries and per-row error
fallback are retained. MySQL writing uses the driver's server prepare cache.
Existing jobs keep their original querySql task count.

## Local disk budget

`olap_scenarios.py` caps local fixtures at one million rows. Before creating
fixtures or starting each measured JVM it checks the active OLAP container's
writable layer plus the PostgreSQL test data directory against a 6 GiB budget,
reserving 1 GiB for the next run, and requires 8 GiB free on the host. This is a
between-run check, not a filesystem quota. A lower limit can be supplied with
`--max-generated-gib`; exceeding it stops the suite and preserves its evidence.

Run one database performance suite at a time. Recreate only the disposable
OLAP container between scenarios: repeated TRUNCATE operations can retain old
tablets. Remove generated test containers and their dedicated data volumes
when finished, including old MySQL test volumes that may retain binary logs.
Keep only the current baseline/candidate runtimes, build fingerprints and
compressed reports; exported comparison files are deleted after validation.
Do not use a global Docker prune or remove unrelated containers/volumes.

The measurements use **a real MySQL server, real DataX Engine processes and
real MySQL target tables**. Data is generated, not a production workload.
Every successful run checks row count and every output field with MySQL's
NULL-safe equality; the target primary key also rejects duplicate IDs.
Reported times include JVM startup, job initialization, range discovery and
the entire read/channel/write pipeline. Dataset creation, table reset and
verification are excluded. Both variants use identical JDKs, drivers, JVM
heap, channel limits, writer batch size and database durability settings.

## Build

Requires Git, Maven, Python 3, Docker, and **JDK 8**. Set `JAVA_HOME` to your JDK.
The upstream source uses wildcard imports that conflict with `java.lang.Record`
on modern JDKs, and its legacy assembly descriptors have empty IDs. The builder
skips assembly and copies the same Maven runtime dependencies into runnable
directories, without patching either source tree or changing library versions.

```sh
git worktree add --detach ../DataX-baseline 80ec23d5c5328eb90ca364d2749e92dfaf44541e
python3 benchmarks/build.py ../DataX-baseline /tmp/datax-benchmark/baseline
python3 benchmarks/build.py . /tmp/datax-benchmark/candidate
```

This focused distribution includes core, mysqlreader/mysqlwriter, rdbmsreader,
and streamreader/streamwriter. It is not a build of every optional DataX plugin.
`build-metadata.json` records the commit, tracked diff fingerprint and all JAR
hashes. Rebuilding the baseline after installing candidate artifacts is safe:
the builder reinstalls the selected source's reactor before copying dependencies.

## Run

Use a **new disposable container**. These commands create and replace test
tables only in the `datax_bench` schema. The credentials below are public test
values, and the port is bound only to localhost.

```sh
docker run -d --name datax-perf-mysql --cpus=6 --memory=4g \
  -p 127.0.0.1:23306:3306 -e MYSQL_ALLOW_EMPTY_PASSWORD=yes \
  -e MYSQL_DATABASE=datax_bench \
  mysql:8.0@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b \
  --default-authentication-plugin=mysql_native_password --innodb-buffer-pool-size=1G
# Wait for `docker exec datax-perf-mysql mysqladmin -uroot ping` to succeed.
docker exec datax-perf-mysql mysql -uroot -e "CREATE USER 'datax'@'%' IDENTIFIED WITH mysql_native_password BY 'datax-local-benchmark'; GRANT ALL ON datax_bench.* TO 'datax'@'%';"

python3 benchmarks/mysql_querysql.py /tmp/datax-benchmark/baseline \
  /tmp/datax-benchmark/candidate /tmp/datax-benchmark/parallel \
  --seed --rows 1000000 --rounds 7 --channels 4
python3 benchmarks/mysql_querysql.py /tmp/datax-benchmark/baseline \
  /tmp/datax-benchmark/candidate /tmp/datax-benchmark/same-config \
  --rows 1000000 --rounds 5 --channels 4 --candidate-no-split
python3 benchmarks/mysql_querysql.py /tmp/datax-benchmark/baseline \
  /tmp/datax-benchmark/candidate /tmp/datax-benchmark/manual-control \
  --rows 1000000 --rounds 5 --channels 4 --baseline-manual-split
python3 benchmarks/mysql_checks.py /tmp/datax-benchmark/candidate /tmp/datax-benchmark/checks
python3 benchmarks/regressions.py /tmp/datax-benchmark/baseline \
  /tmp/datax-benchmark/baseline-regressions --repeat 4 --expect-failure
python3 benchmarks/regressions.py /tmp/datax-benchmark/candidate \
  /tmp/datax-benchmark/candidate-regressions --repeat 4
mvn -B -pl core,plugin-rdbms-util,rdbmsreader,mysqlreader,mysqlwriter,streamreader,streamwriter -am test
python3 benchmarks/channel_benchmark.py /tmp/datax-benchmark/baseline \
  /tmp/datax-benchmark/candidate /tmp/datax-benchmark/channel
```

The separate channel microbenchmark warms 1,048,576 rows per JVM, then moves
67,108,864 reused records through the real MemoryChannel with FIFO/count checks.
It includes synchronization and termination, but excludes record creation,
JDBC, serialization and disk. Its result must not be presented as database or
whole-job throughput.

Run variants sequentially on an otherwise idle machine. The benchmark warms
each variant once, excludes warmups, then alternates AB/BA. It saves each job
configuration, full process log, wall time, rows/s and (on macOS) peak process
RSS and user/system CPU time. Throughput gain is `baseline_time / new_time - 1`;
elapsed-time reduction is `1 - new_time / baseline_time`. These are different
percentages. A 50% throughput gain is not a 50% reduction in elapsed time.

### Strict acceptance of measured runs

```sh
python3 benchmarks/test_performance_gate.py
python3 benchmarks/performance_gate.py /tmp/datax-benchmark/parallel/results.json \
  --metric throughput --threshold 50 --minimum-rounds 7
```

The gate exits with status 1 if **any measured pair** gains less than 50%, if a
row-count/field comparison fails, or if evidence is incomplete. It does not use
the median to hide a failing round. Pass multiple report paths to require every
workload to pass. Use `--metric elapsed --threshold 50` if the requirement is
instead to halve elapsed time (equivalent to doubling throughput).

A passing result accepts only those measured rounds. It is not a guarantee for
future runs, arbitrary SQL, small jobs, constrained targets, other hardware, or
an already parallel upstream job. Production acceptance requires the actual
job, dataset and resource limits; this script does not infer them. The gate's
synthetic self-check is included in CI, separate from real database tests.

After retaining desired evidence, stop the disposable database with
`docker stop datax-perf-mysql`. Remove it with `docker rm datax-perf-mysql` only
when its generated data is no longer needed.

## Additional non-querySql scenarios

The following cases use `table`/`column` configuration or `streamreader`, with
identical configuration for both variants. They do not use `querySqlSplitPk`:

- `table-single`: ordinary MySQL table to MySQL, one channel, no split key.
- `table-parallel`: ordinary table with the existing `splitPk=id`, four channels
  and the unchanged upstream default split factor on both sides.
- `mysql-to-file`: one reader/channel, with the existing streamwriter writing
  a real UTF-8 TSV file. This is not a test of txtfilewriter, HDFS, or OSS. Its
  complete bytes, SHA-256 and row count are compared to a direct MySQL export.
- `stream-to-mysql`: four generated constant-record streams into a real MySQL
  table without a primary key. Every target field and row count is checked.
  This isolates writer costs; it does not represent a varied production source.

Reuse the same isolated container and million-row fixture created above. Use
the recorded timezone for date-to-text conversion, and run cases sequentially:

```sh
for scenario in table-single table-parallel mysql-to-file stream-to-mysql; do
  TZ=Asia/Shanghai python3 benchmarks/mysql_scenarios.py \
    /tmp/datax-benchmark/baseline /tmp/datax-benchmark/candidate \
    /tmp/datax-benchmark/non-querysql/$scenario \
    --scenario "$scenario" --rows 1000000 --rounds 5
done
```

Each case warms both variants once, then measures five alternating pairs.
Outputs preserve every configuration and process log. Temporary TSV contents
are removed after successful verification; their fingerprints are retained.
`gate.json` records whether every pair reached +50%; a completed experiment
does not by itself mean that performance threshold passed.

## MySQL writer defaults

The writer adds `useServerPrepStmts=true`, `cachePrepStmts=true`, and
`prepStmtCacheSqlLimit=65535` when those properties are absent. Explicit URL
values are preserved, and reader URL defaults are unchanged. The cache SQL
limit counts SQL text characters; it is separate from MySQL's parameter limit.
The existing driver can fall back to client prepares when a statement cannot
be prepared on the server. Tests cover a 1024-row, 100-column batch exceeding
the server's 65535-parameter limit.

To retain client prepares, set `useServerPrepStmts=false` in the writer JDBC
URL; `cachePrepStmts=false` also disables the driver's prepare cache. These
settings affect performance and must be kept fixed when accepting a workload.
Connection options are documented in the [Connector/J guide](https://dev.mysql.com/doc/connector-j/en/connector-j-connp-props-performance-extensions.html).

After obtaining a prepared statement, the writer clears any pending batch
before adding rows.
This matters after a type conversion fails before `executeBatch`: older driver
caches can otherwise replay the partial batch later. The real-database checks
include a target without a primary key so duplicate rows cannot be hidden by
a duplicate-key error. Both client and server modes are checked with nullable
BIT, arbitrary binary data, Unicode/control characters and millisecond timestamps.

## Use parallel querySql

Add `querySqlSplitPk` to `mysqlreader.parameter`; the named field must be a
simple output column/alias of integer type. All querySql entries in that reader
must expose the field. Set `job.setting.speed.channel` to the desired total
concurrency. Existing querySql arrays still work without the option.

```json
{
  "name": "mysqlreader",
  "parameter": {
    "username": "reader",
    "password": "replace-me",
    "querySqlSplitPk": "id",
    "queryTimeout": 120,
    "connection": [{
      "jdbcUrl": ["jdbc:mysql://localhost:3306/example?socketTimeout=180000"],
      "querySql": ["SELECT id, amount, customer_name FROM orders WHERE status='paid'"]
    }]
  }
}
```

DataX obtains MIN/MAX from the query result and wraps the original SELECT in
disjoint range predicates. The first range includes NULL keys, the edge ranges
are open-ended, and BigInteger arithmetic handles signed/unsigned BIGINT limits.
Multiple queries share the requested parallelism. Small ranges, empty results
and all-NULL keys can produce fewer tasks. A channel limit of one keeps one task.
There is no OFFSET pagination. Filters, joins, output aliases and expressions
remain inside the original query.

Use a **stable source and deterministic SELECT**: independently connected tasks
do not share a database snapshot. Concurrent updates, nondeterministic functions,
session variables, and unordered LIMIT queries can produce inconsistent results.
This is not a CDC or snapshot-isolation implementation. Ordered output is not
preserved across tasks. Check EXPLAIN: an indexed range key usually helps;
materialized derived queries, skewed keys and a saturated writer may provide
little benefit or regress. The feature currently supports MySQL; unsupported
databases and noninteger keys fail explicitly. The opt-in parser is the existing
Druid version, so newer SQL dialect features may require a manually partitioned
querySql array instead.

`queryTimeout` is a nonnegative JDBC **statement timeout in seconds** (0 disables
it); omission preserves upstream's 172800-second default. It applies to reader
queries and parallel query range discovery, and to query prechecks. It is not a
whole-job deadline; configure JDBC `socketTimeout` for stalled socket reads.
Dry-run prechecks validate every query with `setMaxRows(1)` and close their
statements. Database expressions can still be expensive even with one result row.

JDBC SQLStates/vendor codes and cause/next-exception chains are used ahead of
legacy message matching. Connection failures, timeouts, missing tables/columns,
syntax errors and permission errors are distinguishable. Failures do not trigger
automatic replay of partially written jobs. Existing target-side transaction and
idempotency requirements still apply.
