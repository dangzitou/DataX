# MySQL / querySql performance and reliability validation

This fork adds opt-in parallel querySql reads, a single-lock bounded memory
channel, cached JDBC column metadata, a faster integer conversion path, and
fixes for NULL preservation, partial dirty records, resource cleanup, and JDBC
error classification. Existing jobs keep their original querySql task count.

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
  --seed --rows 1000000 --rounds 5 --channels 4
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

After retaining desired evidence, stop the disposable database with
`docker stop datax-perf-mysql`. Remove it with `docker rm datax-perf-mysql` only
when its generated data is no longer needed.

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
