# FastMssql SQL-auth transaction stress report

## Scope

This run tests FastMssql transaction correctness and async resource behavior
with SQL Server username/password authentication. It does not benchmark the
universal capacity of SQL Server.

The target is the dedicated `fastmssql-sql-auth-dev` SQL Server 2022 Developer
Edition container. The host is ARM64 macOS and the SQL Server `linux/amd64`
image runs under emulation, so timings are diagnostic rather than production
capacity claims.

## Required load gate

`LOAD-008` creates 1,000 `Transaction` objects with at most 50 active
simultaneously. Every transaction inserts a unique row, even identifiers
commit, odd identifiers roll back, and the final count and sum must be exact.

| Transactions | Concurrency | Commit | Rollback | Seconds | Tx/s | Distinct sampled sessions | Result |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1,000 | 50 | 500 | 500 | 1.628 | 614.08 | 35 | PASS |

The complete bounded load lane passed 8 tests in 5.06 seconds. It also covered
1,000 concurrent short queries, bounded result-memory growth, repeated result
conversion, bulk inserts through 10,000 rows, 250 connection lifecycles, 500
mixed query/update/transaction operations, and post-load pool recovery.

## Extended persistent-session profiles

The endurance harness uses a fixed number of persistent `Transaction`
connections. Each worker repeatedly begins, commits or rolls back, and begins
the next transaction on the same SQL session. This isolates driver transaction
volume from TCP/TLS/login churn.

| Transactions | Persistent sessions | Commit | Rollback | Seconds | Tx/s | Event-loop ticks | RSS growth | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10,000 | 100 | 5,000 | 5,000 | 2.838 | 3,523.38 | 281 | 17.32 MB | PASS |
| 99,999 | 100 | 50,000 | 49,999 | 27.094 | 3,690.82 | 2,679 | 12.01 MB | PASS |
| 99,999 | 200 | 50,000 | 49,999 | 30.778 | 3,249.09 | 3,034 | 17.81 MB | PASS |

Every profile verified exact committed state, absence of rolled-back rows, a
post-load query, zero remaining application sessions, bounded client memory,
and continued event-loop progress.

On this host, concurrency 100 was more efficient than 200. Doubling the
sessions reduced throughput by about 12.0% and increased client RSS growth.
This is evidence for tuning the pool instead of maximizing its size.

## Connection-churn reproduction

The alternate `per-transaction` strategy constructs and closes a physical
`Transaction` connection for every unit of work.

Two independent runs requesting 10,000 transactions at concurrency 100 failed
after sustained connection churn with:

`SqlConnectionError: Failed to connect to database: failed to fill whole buffer`

After both failures:

- the SQL Server container remained healthy and was not OOM-killed;
- no SQL Server log rejection was observed;
- no stress sessions or tables remained;
- SQL Server reported its normal auto-configured connection limit.

This is not hidden by retries. It is retained as a reproducible diagnostic via
`--connection-strategy per-transaction`.

## Production interpretation

The passing 99,999-transaction profiles demonstrate that FastMssql can sustain
large true-async transaction volume when physical SQL sessions are reused.
They do not justify opening one connection per request or tens of thousands of
simultaneous SQL sessions.

For a large ASGI application, use a bounded, persistent connection strategy and
tune concurrency from measured latency and throughput. This run indicates 100
sessions outperform 200 on the test host. Flask under WSGI remains worker-bound;
Flask through an ASGI adapter has a persistent event loop but remains adapted
WSGI. FastAPI/native ASGI is the primary true-async deployment model validated
by the framework lane.

The current public `Transaction` object supports sequential reuse of its
dedicated SQL session but is not itself a shared transaction pool. A native
transaction-leasing pool is therefore a candidate production enhancement; it
should be designed and reviewed separately rather than masking connection
churn with automatic transaction retries.

## Reproduction

The required load lane runs through:

```text
./scripts/sql_auth/run_all.sh
```

The opt-in endurance profiles run through:

```text
./scripts/sql_auth/run_transaction_stress.sh
```

The churn diagnostic uses:

```text
python scripts/sql_auth/transaction_stress.py \
  --profiles 10_000:100 \
  --connection-strategy per-transaction \
  --metrics-output .artifacts/sql-auth/transaction-churn-metrics.json
```

Machine-readable evidence is committed in
`docs/evidence/SQL_AUTH_TRANSACTION_STRESS_2026-07-24.json`.
