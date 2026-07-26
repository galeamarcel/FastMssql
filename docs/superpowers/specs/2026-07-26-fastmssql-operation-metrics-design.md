# FastMssql Operation Duration and Outcome Metrics Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, plan and implementation self-review are
mandatory.

**Date:** 26 July 2026

**Source baseline:** `test/sql-auth-validation` at
`c3311b66a85f7f4fbbda7a9087506375f761ba8f`

**Repository boundary:** every design, RED, GREEN, evidence and status branch
is published only to `https://github.com/galeamarcel/FastMssql.git`.
`Rivendael/FastMssql` remains fetch-only and receives no branch, push or pull
request without a later explicit approval.

## Objective

Add opt-in, bounded, low-cardinality operation metrics that let a production
application answer these questions per `Connection` object:

- how many operations started, completed or remain in flight;
- whether completed operations succeeded, failed, timed out, were cancelled or
  ended with an explicitly unknown commit outcome;
- how much end-to-end time each public operation consumed;
- how those durations are distributed across fixed cumulative buckets;
- whether any internal counter or duration accumulator saturated.

The metrics cover true asynchronous work from the first poll of the Rust
future through its returned result or cancellation. They include lazy
connection creation, pool acquisition, SQL Server I/O, response consumption
and result construction performed inside FastMssql's Rust async body. They do
not claim to measure Python task-queue latency before the first poll or the
async bridge's final scalar conversion after the Rust future has returned.

This candidate remains an in-process pull API. It does not add a telemetry
provider, exporter, callback, background task, SQL label or dependency. The
existing pool metrics continue to describe pool mechanics; this candidate
describes logical FastMssql operations.

## Measured baseline gap

The verified fork exposes complete bb8 pool statistics through
`await connection.pool_stats()`, including checkout waits and physical
connection retirement. Those statistics cannot answer:

- which logical operation caused the checkout;
- whether the logical operation succeeded, timed out or was cancelled;
- whether COMMIT completion was confirmed;
- how long the entire logical operation took;
- how many logical operations remain in flight when the pool itself is not
  saturated.

The driver already has one fixed `OperationName` enum used for timeout and
lifecycle metadata:

```text
connect       ping          query          simple_query
execute       query_batch   execute_batch  bulk_insert
begin         commit        rollback       close
disconnect
```

`OperationName::Transaction` is reserved for a future asynchronous transaction
factory. `Connection.transaction()` is currently synchronous and performs no
I/O, so it is not a metric series in this candidate.

There is currently no operation counter, clock, histogram or event interface.
Applications must wrap every call themselves, cannot observe Rust-future
cancellation precisely, and cannot aggregate pooled transaction work back to
the owning `Connection`.

## Scope decomposition

The observability roadmap stays split into independently reviewable layers:

1. **Verified pool metrics:** current bb8 gauges, checkout counters and
   retirement events.
2. **This candidate — operation metrics:** opt-in bounded duration histograms
   and typed outcomes.
3. **Future telemetry bridge:** optional OpenTelemetry/Prometheus adapters that
   consume stable snapshots without instrumenting SQL paths again.
4. **Future tracing:** trace-context propagation and spans, subject to a
   separate privacy, sampling and provider-ownership design.

This candidate stabilizes a dependency-free source contract without selecting
or owning an application's monitoring stack.

## Approaches considered

### Approach A — opt-in fixed in-process registry, recommended

Store one bounded atomic registry in each metrics-enabled `Connection`.
Instrument accepted Rust async futures with a small RAII guard and expose fresh
snapshots through `await connection.operation_stats()`.

Advantages:

- the default path performs no clock read and no atomic update;
- enabled memory and cardinality are fixed at construction;
- success does not attach the Python GIL;
- cancellation is recorded by Rust `Drop`, including forced task cancellation;
- observers cannot block SQL work or change its result;
- pooled transaction operations share the exact owner registry;
- no dependency, exporter thread, global singleton or process callback is
  introduced.

Trade-off: applications must explicitly scrape the connection-local snapshot
and bridge it to their monitoring system.

### Approach B — always-on registry

Allocate and update metrics for every connection, whether or not they are ever
read.

Rejected because it imposes a monotonic-clock read, atomics and cache traffic
on every SQL operation by default. A production diagnostic feature must not
silently tax applications that did not enable it.

### Approach C — callbacks or direct OpenTelemetry integration

Invoke Python listeners or create SDK instruments/spans inside the driver.

Rejected because callbacks add GIL attachment, exception, reentrancy,
backpressure and lifetime semantics to the SQL hot path. Direct SDK integration
would couple the ABI3 wheel to global provider ownership, exporter shutdown,
sampling and optional-package version churn. Both choices are larger than the
stable metrics source needed here.

## Public configuration API

The extension exports one new PyO3 class:

```python
OperationMetricsConfig(enabled: bool = False)
```

Its exact behavior is:

- `enabled` accepts and returns an exact Python `bool`; integers, strings,
  `None` and other non-bools raise `TypeError`;
- `inspect.signature(OperationMetricsConfig)` exposes `(enabled=False)`;
- `repr(config)` is
  `OperationMetricsConfig(enabled=False)` or
  `OperationMetricsConfig(enabled=True)`;
- `enabled` has a validated getter and setter on the configuration object;
- the object is cloneable and contains no callback, label or bucket setting;
- `Connection.operation_metrics_config` returns an isolated copy;
- mutating the source configuration after `Connection` construction, or
  mutating the returned property copy, does not reconfigure that connection.

`Connection` gains one final optional constructor argument:

```python
Connection(..., operation_metrics_config: OperationMetricsConfig | None = None)
```

It is appended after `lifecycle_config`, preserving every existing positional
argument. `None` is identical to `OperationMetricsConfig(enabled=False)`.

Metrics are initialization policy, not a runtime switch. Enabling or disabling
an existing registry concurrently would complicate counter epochs and create
partial histories, so this candidate deliberately provides no connection
setter.

The direct compatibility constructor `Transaction(...)` does not accept
`OperationMetricsConfig`. A transaction returned by
`Connection.transaction()` automatically inherits the owning connection's
registry.

## Public snapshot API

`Connection` and the Python wrapper add:

```python
await connection.operation_stats()
```

The method performs no SQL, does not create or acquire a pool connection, does
not enter lifecycle admission and does not update its own metrics.

Every call returns a newly allocated dictionary with this exact schema:

```python
{
    "schema_version": 1,
    "enabled": bool,
    "bucket_bounds_seconds": [
        0.0001,
        0.00025,
        0.0005,
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
    ],
    "operations": {
        "connect": operation_snapshot,
        "ping": operation_snapshot,
        "query": operation_snapshot,
        "simple_query": operation_snapshot,
        "execute": operation_snapshot,
        "query_batch": operation_snapshot,
        "execute_batch": operation_snapshot,
        "bulk_insert": operation_snapshot,
        "begin": operation_snapshot,
        "commit": operation_snapshot,
        "rollback": operation_snapshot,
        "close": operation_snapshot,
        "disconnect": operation_snapshot,
    },
}
```

Each `operation_snapshot` is a new dictionary with these exact keys:

```python
{
    "started": int,
    "completed": int,
    "in_flight": int,
    "succeeded": int,
    "errors": int,
    "timed_out": int,
    "cancelled": int,
    "outcome_unknown": int,
    "duration_seconds_sum": float,
    "duration_seconds_min": float | None,
    "duration_seconds_max": float | None,
    "duration_seconds_buckets": [
        int,  # one cumulative count for each top-level finite bound
        ...
    ],
    "saturated": bool,
}
```

The operation names and insertion order are fixed. Consumers must address
series by name rather than relying on dictionary order.

Mutating any returned dictionary or list changes only that caller's snapshot
and cannot modify the registry or a later snapshot.

`bucket_bounds_seconds` and `duration_seconds_buckets` are lists of equal
length. Element `i` is the cumulative number of completed operations whose
duration was less than or equal to bound `i`. `completed` is the implicit
positive-infinity bucket, so a duration above 30 seconds is counted in
`completed` but in no finite bucket beyond the count already accumulated
below 30 seconds.

The snapshot deliberately has no duplicated all-operation total. A consumer
that needs a total can sum the fixed operation series. Avoiding a second
registry prevents two public aggregates from diverging during concurrent
scrapes.

The public stubs add precise `TypedDict` definitions for the nested operation
snapshot and top-level snapshot. They use these exact private, stub-only names:

```python
class _OperationStatsEntry(TypedDict): ...
class _OperationStatsByName(TypedDict): ...  # the exact 13 operation fields
class _OperationStatsSnapshot(TypedDict): ...
```

`schema_version` is typed as `Literal[1]`. The method return is
`Coroutine[Any, Any, _OperationStatsSnapshot]`. No importable runtime type is
added for these private typing helpers; the runtime return value remains
ordinary fresh Python dictionaries and lists.

## Disabled behavior

Operation metrics are disabled by default.

For a disabled connection:

- no registry is allocated;
- the operation wrapper branches directly to the original future;
- no `Instant::now()` call and no atomic read or write occurs in normal
  connect/query/transaction/batch/disconnect paths;
- no background task or observer exists;
- `operation_stats()` still returns the exact fixed schema with
  `enabled=False`, all integer values zero, all durations `0.0`/`None`, every
  bucket zero and every `saturated` value false.

The disabled snapshot makes configuration introspection deterministic without
requiring callers to special-case a missing method, `None` value or changing
key set.

## Operation boundary

An operation starts inside the Rust async body immediately before lifecycle
admission or any connection work. This point is reached when that Rust future
is first polled by the async bridge.

An operation ends when one of these events occurs:

1. the Rust future produces its final `PyResult`;
2. the future is dropped before producing a result, which records
   cancellation.

The measured interval therefore includes, where applicable:

- lifecycle admission and forced-shutdown observation;
- lazy pool and physical connection initialization;
- pool acquisition wait;
- SQL Server request and response I/O;
- response consumption;
- Rust row and result construction;
- Python result/list/dictionary construction performed inside the async body.

It excludes synchronous validation performed before the async body exists,
including constructor validation, SQL parameter conversion and batch-input
conversion. Such a rejected call never started asynchronous driver work and
must not create a misleading started/completed pair.

FastMssql currently consumes query results and constructs its `QueryStream`
before the operation future returns. Later user iteration over that already
constructed stream is outside the metric. For scalar Rust values such as an
affected-row count, the pyo3 async bridge's final conversion after the future
returns is also outside the metric.

Constructing `Connection.transaction()` is synchronous and excluded. Every
subsequent async operation on that pooled `Transaction` is measured separately.

`operation_stats()`, `pool_stats()`, configuration properties,
`lifecycle_state`, `is_connected()` and direct
`Transaction.is_connected()` are diagnostic/synchronous state reads and are
excluded.

## Exact operation mapping

The public call-to-series mapping is:

| Public call | Series | Notes |
|---|---|---|
| `Connection.connect()` | `connect` | Includes optional readiness query; `validate=False` still records successful lazy allocation. |
| `Connection.__aenter__()` | `connect` | Exactly one connect operation, including readiness. |
| `Connection.ping()` | `ping` | Includes checkout and complete `SELECT 1` response consumption. |
| `Connection.query()` / pooled `Transaction.query()` | `query` | Includes returned stream construction. |
| `Connection.simple_query()` / pooled `Transaction.simple_query()` | `simple_query` | Includes returned stream construction. |
| `Connection.execute()` / pooled `Transaction.execute()` | `execute` | Includes affected-row result. |
| `Connection.query_batch()` / pooled `Transaction.query_batch()` | `query_batch` | One metric for the whole public batch, not one per element. |
| `Connection.execute_batch()` / pooled `Transaction.execute_batch()` | `execute_batch` | One metric for the whole public batch, including the connection-owned dedicated-socket implementation. |
| `Connection.bulk_insert()` | `bulk_insert` | One metric for the complete public bulk operation. |
| pooled `Transaction.begin()` / `__aenter__()` | `begin` | Includes pool initialization and acquisition on first use. |
| pooled `Transaction.commit()` / successful `__aexit__()` | `commit` | Confirmed and unconfirmed completion are distinct outcomes. |
| pooled `Transaction.rollback()` / exceptional `__aexit__()` | `rollback` | Includes explicit or context-manager rollback. |
| pooled `Transaction.close()` | `close` | Includes automatic rollback cleanup when active. |
| `Connection.disconnect()` | `disconnect` | Each public caller is one operation, including a deduplicated concurrent shutdown waiter. |
| `Connection.__aexit__()` | `disconnect` | Exactly one disconnect operation. |

A batch's internal statements, a readiness query inside connect/ping and
cleanup SQL inside close are not double-counted as other public operations.
Metrics describe public logical calls, not internal protocol commands.

The direct compatibility `Transaction(...)` path has no owner registry and
records nothing. Its behavior is otherwise unchanged. This is an explicit
scope boundary, not a claim that direct transactions are unimportant; adding a
standalone registry would require a separate public ownership and snapshot
contract.

## Outcome classification

Every completed operation increments exactly one mutually exclusive outcome:

1. `succeeded` when the future returns `Ok`;
2. `outcome_unknown` when the top-level returned exception is
   `CommitOutcomeUnknown`;
3. `timed_out` when the top-level returned exception is
   `OperationTimeoutError` or `ShutdownTimeoutError`;
4. `errors` for every other returned `PyErr`, including SQL, connection, TLS,
   protocol, conversion, lifecycle, validation and panic-conversion failures;
5. `cancelled` only when the started Rust future is dropped without returning a
   `PyResult`.

The priority is intentional. A `CommitOutcomeUnknown` may wrap a timeout as its
cause, but it increments only `outcome_unknown`. An `OperationTimeoutError`
whose metadata says a write outcome may be unknown increments only
`timed_out`; callers still receive and may inspect the full exception metadata.
The metrics classify the top-level operational result and never double-count
an exception cause.

Cancellation before the Rust async body receives its first poll starts no
operation. After the guard is created, cancellation records duration and
`cancelled` through `Drop`, even when transaction cleanup continues
asynchronously to retire an unsafe lease.

Error classification attaches Python only on the already failing completion
path to identify public exception classes. Successful operation recording and
cancellation never acquire the GIL.

## Registry ownership and lifecycle

An enabled `Connection` creates exactly one
`Arc<OperationMetricsRegistry>`. The registry lives for the entire Python
connection object:

- counters start at zero at construction;
- lazy/explicit pool creation does not reset them;
- disconnect does not reset them;
- explicit or lazy reconnect in a new lifecycle generation continues the same
  operation history;
- a pooled transaction holds a clone of the same registry;
- dropping a transaction does not drop or reset the owner's registry;
- dropping the owning connection releases the registry after outstanding
  instrumented futures release their clones.

This differs deliberately from `pool_stats()`, whose bb8 counters describe
only the current concrete pool epoch. Operation metrics describe the logical
`Connection` lifetime across pool/lifecycle generations.

There is no reset API. Concurrent reset would require an epoch identifier and
well-defined treatment of operations spanning that epoch. A future reset
candidate must add those semantics explicitly rather than silently zeroing
live counters.

## Internal representation

Implementation adds:

```text
src/operation_metrics_config.rs
src/operation_metrics.rs
```

The registry is a fixed array indexed by the 13 active `OperationName` values.
Each entry contains:

- one saturating `AtomicU64` started counter;
- five saturating `AtomicU64` outcome counters;
- one saturating `AtomicU64` duration sum in whole microseconds;
- one atomic minimum duration, initialized to `u64::MAX`;
- one atomic maximum duration, initialized to zero;
- 17 raw, non-cumulative finite-bucket atomics;
- one atomic saturation flag.

There is no completed counter. The snapshot derives `completed` with
saturating addition of the five outcomes. There is no in-flight counter. The
snapshot derives it from reconciled started and completed values, avoiding a
second start/end update that could drift after cancellation or panic.

Duration storage uses saturated whole microseconds:

```text
reported_seconds = stored_microseconds / 1_000_000.0
```

Microseconds provide sub-millisecond operational diagnostics and approximately
584,000 years of aggregate headroom in `u64`. The monotonic elapsed `Duration`
is converted without wrapping; sub-microsecond durations may report `0.0`.
Finite-bucket selection compares the unrounded elapsed `Duration` against
fixed bounds, so microsecond sum rounding cannot place an operation in the
wrong bucket.

One completion updates at most one raw finite-bucket atomic. Snapshot creation
performs the bounded 17-element cumulative sum. Durations above 30 seconds
update no raw finite bucket and are represented by the implicit
positive-infinity `completed` count.

All increments and additions saturate at `u64::MAX`; none may wrap. If a
counter, duration conversion, sum or bucket increment saturates, the
operation's `saturated` flag becomes permanently true.

## RAII recording

One generic helper wraps accepted operation futures:

```rust
observe_operation(metrics, operation, future).await
```

Its disabled branch is structurally first:

```rust
match metrics {
    None => future.await,
    Some(registry) => {
        // Only this branch reads the clock or atomics.
    }
}
```

The enabled branch:

1. captures a monotonic start instant;
2. increments `started`;
3. owns an armed guard;
4. awaits the original future;
5. records duration data;
6. publishes exactly one returned-result outcome;
7. disarms the guard before returning the unchanged result.

If the wrapper is dropped while armed, the guard performs steps 5 and 6 with
the `cancelled` outcome. Recording must not allocate, panic, attach Python or
block.

The observer returns the original success value or the exact original `PyErr`.
It does not wrap, log, suppress, retry or replace operation results.

## Atomic publication and snapshot consistency

The registry is lock-free for operation writers and readers. A completed
writer stores duration sum/min/max and its raw histogram bucket before a
Release update of the final outcome counter. Snapshot reads use Acquire loads
for outcome publication and the bounded data needed to render the dictionary.

Independent atomic counters cannot provide a single multi-word instant.
FastMssql therefore publishes a coherent arithmetic snapshot with these
reconciliations:

```text
completed = saturating_sum(
    succeeded,
    errors,
    timed_out,
    cancelled,
    outcome_unknown,
)

exported_started = max(raw_started, completed)
in_flight = exported_started - completed
```

Finite bucket values are cumulatively summed with saturation, made
non-decreasing and clamped to `completed`. If `completed == 0`, public
duration sum is `0.0`, min/max are `None` and every bucket is zero, even if a
concurrent writer has prepared duration fields but has not yet published its
outcome.

When `completed > 0`, rendering reconciles:

```text
duration_min <= duration_max
duration_sum >= duration_max
```

without changing registry state. The snapshot may transiently represent an
operation that just completed as still in flight, or may include a prepared
duration in the aggregate while excluding that writer's not-yet-published
outcome. It never violates the public arithmetic/type invariants, and a
snapshot taken after all writers quiesce is exact.

No SQL operation waits for a scraper, and a scraper never holds a driver,
pool, lifecycle or transaction lock.

## Required snapshot invariants

For every operation in every returned snapshot:

```text
started == completed + in_flight
completed == succeeded + errors + timed_out + cancelled + outcome_unknown

all integer values >= 0
len(duration_seconds_buckets) == len(bucket_bounds_seconds) == 17
duration_seconds_buckets is non-decreasing
every bucket <= completed
```

When `completed == 0`:

```text
duration_seconds_sum == 0.0
duration_seconds_min is None
duration_seconds_max is None
every bucket == 0
```

When `completed > 0`:

```text
duration_seconds_sum is finite and >= 0.0
duration_seconds_min is finite and >= 0.0
duration_seconds_max is finite and >= duration_seconds_min
duration_seconds_sum >= duration_seconds_max
```

The bucket bounds are finite, strictly increasing and identical in every
snapshot. Once true, `saturated` never returns to false for that operation.

## Security and privacy

The schema has fixed operation names and fixed numeric bounds. It must never
contain or derive:

- SQL text, normalized SQL or query fingerprints;
- parameter values, counts, names or types;
- table, schema, procedure, database, server, port or instance names;
- application name, username or authentication method;
- connection strings, passwords, access tokens or certificate paths;
- exception messages, SQL error codes or arbitrary labels;
- request, tenant, route, trace, transaction or user identifiers.

Configuration and registry `repr` values contain only the fixed class name and
enabled boolean. Metrics are never logged, persisted or exported
automatically. Reading them is an explicit application action.

## Performance and resource model

Disabled operation paths add one predictable `Option` branch and carry no
registry allocation. The branch must occur before any clock or atomic work.

An enabled operation adds:

- one registry `Arc` reference-count clone/drop while the operation owns its
  handles;
- one monotonic clock read at start and one at completion/drop;
- one saturating started update;
- duration sum, min and max updates;
- zero or one raw finite-bucket update;
- one final outcome update;
- no allocation beyond existing future ownership;
- no lock, callback, exporter, background task or GIL on success.

Each enabled connection owns exactly 13 fixed entries and 221 finite-bucket
counters. Memory cannot grow with operation count, concurrency, SQL variety or
scrape frequency.

The deterministic implementation review must confirm the disabled branch
precedes clock/atomic access. The real SQL-auth performance profile uses a
dedicated Docker SQL Server, a parameterized one-row `SELECT`, 99,999
operations per trial, 200 worker tasks and `PoolConfig(max_size=100)`.

It runs three fresh-connection enabled/disabled pairs in this exact order:

```text
disabled -> enabled
enabled  -> disabled
disabled -> enabled
```

Each trial receives one untimed warm-up query. Candidate SQL sessions are
verified absent between trials. For each pair:

```text
degradation =
    (disabled_transactions_per_second - enabled_transactions_per_second)
    / disabled_transactions_per_second
```

Trial order alternates to reduce warm-up bias. The candidate passes when:

- both modes complete every operation with exact result counts;
- enabled metric deltas after the warm-up are exactly 99,999 and no scrape
  occurs inside the timed interval;
- the median of the three paired degradation values is no more than `0.15`;
- pool size and SQL sessions remain bounded by the configured maximum;
- event-loop progress remains non-zero;
- post-load health succeeds and teardown leaves zero candidate sessions.

This is a driver-overhead regression gate, not a SQL Server capacity claim.
Exact host/container resources, concurrency, pool size, trial order,
throughput and ratio are recorded in evidence. No single timing sample is used
as a universal benchmark.

## Deterministic SQL-auth and framework cases

The strict specification gains sixteen IDs:

- `OPMET-001`: exported configuration API, exact bool/default/repr behavior,
  constructor compatibility and isolated-copy semantics.
- `OPMET-002`: disabled exact schema, zero values and unchanged values after
  successful/error/cancelled SQL prove that default instrumentation is off;
  mutation of one returned snapshot cannot alter the next.
- `OPMET-003`: connect/query/simple-query/execute successes produce exact
  counters, end-to-end non-zero durations and correct cumulative buckets after
  quiescence.
- `OPMET-004`: deterministic SQL/lifecycle errors increment only `errors`;
  synchronous parameter/batch validation rejected before future creation is
  explicitly not counted.
- `OPMET-005`: server-gated pool saturation produces one acquire
  `OperationTimeoutError`, increments only `timed_out` for the public operation
  and preserves pool recovery.
- `OPMET-006`: server-gated operation and graceful-shutdown deadlines classify
  `OperationTimeoutError` and `ShutdownTimeoutError` as `timed_out`, preserve
  their exact public errors and recover in a new generation.
- `OPMET-007`: cancellation after server-confirmed execution start increments
  only `cancelled`, retires unsafe transport where required and leaves the
  connection healthy with no leaked session.
- `OPMET-008`: a pooled transaction aggregates begin/query/simple-query/
  execute/query-batch/execute-batch/commit/rollback/close into its owner;
  context-manager paths are counted exactly once and a standalone direct
  transaction does not alter the owner snapshot.
- `OPMET-009`: explicit connect, ping, connection context entry/exit,
  disconnect and reconnect retain one connection-lifetime registry across
  lifecycle generations without double-counting internal readiness SQL.
- `OPMET-010`: connection-owned query batch, dedicated-socket execute batch and
  bulk insert each produce one whole-public-call metric with exact database
  effects and cleanup.
- `OPMET-011`: at least 10,000 bounded concurrent operations plus continuous
  scraping, using 100 workers and a pool maximum of 20, preserve every
  invariant, event-loop progress, exact results, at most 20 physical sessions
  and zero candidate sessions after teardown.
- `OPMET-012`: SQL, parameter, credential and identifier sentinels are absent
  from keys, values, `repr`, reports and load evidence.
- `OPMET-013`: the deterministic commit fault proxy produces exactly one
  `CommitOutcomeUnknown`, increments only `outcome_unknown`, preserves the
  original exception/cause contract and retires the uncertain session.
- `OPMET-014`: FastAPI/native ASGI request concurrency reports exact completed
  operation deltas without blocking event-loop progress.
- `OPMET-015`: Flask `async def` under WSGI remains functionally compatible
  and reports exact operation deltas while the report preserves the
  per-request-event-loop limitation.
- `OPMET-016`: Flask through the approved ASGI adapter uses a persistent event
  loop and reports exact concurrent operation deltas.

The strict matrix grows from 321 to 337 unique case IDs. Existing IDs,
assertions and timing bounds are not weakened.

The histogram boundary, saturation, publication/reconciliation and RAII-drop
algorithms also receive Rust unit tests that use injected durations and
near-`u64::MAX` state. Those implementation tests are additional to the 16
real-environment IDs.

Tests use server-visible gates, DMV session observation and the existing
deterministic TCP fault proxy. Scheduler sleeps are not accepted as proof that
an operation reached SQL Server, pool wait or commit response handling.

## RED/GREEN discipline

The RED branch adds public/stub/static contracts and all deterministic
environment tests before implementation.

On baseline `c3311b66a85f7f4fbbda7a9087506375f761ba8f`:

- importing `OperationMetricsConfig` fails;
- passing `operation_metrics_config` fails;
- calling `operation_stats()` fails;
- existing SQL and pool behavior remains green;
- every new failure points to the absent metrics contract, not an unrelated
  setup or database failure.

GREEN adds the smallest implementation satisfying the approved contract.
Tests never use `xfail`, skip, retry, swallowed cleanup exception or broad
error acceptance to manufacture a pass.

If RED exposes a conflict with current timeout, cancellation, lifecycle,
transaction or batch semantics, this design is corrected explicitly and
self-reviewed before implementation changes that contract.

## Required regression gates

Before the candidate can be marked `VERIFIED_FORK`:

- `OPMET-001`–`OPMET-016` pass against the approved Docker SQL Server with SQL
  authentication and the deterministic fault proxy where specified;
- all 337 strict specification IDs have complete PASS evidence;
- complete strict, true-async, framework, resilience, load and applicable
  upstream lanes pass;
- the existing 10,000 and both 99,999 transaction profiles remain green;
- the new interleaved 99,999 enabled/disabled overhead profile meets its
  declared 15% median gate;
- `cargo fmt --check`, Clippy with `-D warnings` and all Rust tests pass;
- Ruff and `compileall` pass;
- dependency audit has zero vulnerabilities and zero warnings;
- a clean ABI3 wheel installs and the configuration/snapshot contract passes
  from the installed artifact;
- raw Cargo, Rust, wheel and installed static contracts pass on Linux, macOS
  and Windows;
- concurrent scrape/load evidence has zero leaked candidate SQL sessions;
- generated reports contain no credential or sentinel value;
- all changed public Python/Rust interfaces and documentation receive a
  self-review;
- no branch, push or PR targets the original repository.

Real SQL-auth remains local unless a hosted SQL Server runner is added.
Hosted cross-platform evidence must not be represented as hosted MSSQL
evidence.

## Branch discipline

The candidate uses these independent branches/worktrees:

```text
docs/operation-metrics-design
test/operation-metrics
feat/operation-metrics
docs/operation-metrics-status
```

Required history:

1. approved and self-reviewed design specification;
2. detailed self-reviewed implementation plan;
3. deterministic RED public/static/SQL-auth/framework/load tests;
4. minimal GREEN configuration, registry and propagation changes;
5. implementation self-review and focused verification;
6. exact technical merge into `test/sql-auth-validation`;
7. full SQL-auth, stress, wheel, hosted and security evidence from that exact
   merge;
8. live audit, matrix, report and upstream-roadmap status;
9. documentation-only status merge into the cumulative fork branch.

Every push targets `origin`. The `upstream` push URL remains exactly
`DISABLED`. No release or upstream pull request is created by this candidate.

## Compatibility and migration

This is additive and opt-in:

- default `Connection` behavior and return values are unchanged;
- existing positional constructor calls remain valid because the new argument
  is last;
- existing timeout, lifecycle and pool configuration copies are unchanged;
- the Python wrapper continues to forward unknown constructor arguments to the
  Rust connection;
- no existing method changes awaitability or result/error type;
- direct `Transaction(...)` behavior is unchanged;
- pool statistics retain their per-current-pool epoch semantics;
- enabling metrics allocates a connection-local registry but does not alter
  pool size, retry, timeout, transaction or lifecycle policy;
- no SQL schema, application configuration or data migration is required.

Applications opt in explicitly:

```python
from fastmssql import Connection, OperationMetricsConfig

connection = Connection(
    connection_string,
    operation_metrics_config=OperationMetricsConfig(enabled=True),
)
```

The README documents fixed buckets, connection-lifetime counter scope,
weakly-consistent concurrent snapshots, exact-after-quiescence behavior,
privacy and the fact that this API is not an exporter.

## Rollback

Reverting the GREEN commit removes the new config class, registry,
instrumentation propagation, snapshot method, stubs/docs and candidate tests.
Because metrics are opt-in and hold no database state, rollback requires no
SQL migration, counter drain, exporter shutdown or protocol change.

## Non-goals

This candidate does not add:

- query text, fingerprints, labels or per-table/procedure metrics;
- percentiles computed in the driver;
- configurable bucket boundaries;
- per-statement metrics inside batch calls;
- metrics for the standalone direct `Transaction(...)` constructor;
- a reset API or epoch identifier;
- process-global or cross-process aggregation;
- logs, callbacks, listeners or metric push;
- OpenTelemetry, Prometheus, StatsD or other exporter dependencies;
- trace/span context, sampling or request correlation;
- retry or timeout behavior changes;
- SQL Server server-capacity benchmarking;
- Tiberius, bb8 or upstream-repository changes;
- package version bump, release, fork publication beyond branches or upstream
  pull request.

## Design self-review record

The design was reviewed against the verified pool-observability contract,
`OperationName`, every `Connection` async method, pooled/direct transaction
ownership, batch/dedicated-socket paths, timeout exception construction,
commit-outcome handling, cancellation retirement and lifecycle generations.

Corrections made during self-review:

1. Metrics were changed from always-on to opt-in/default-off so applications
   that never scrape them incur no clock or atomic update.
2. Runtime enable/disable and reset were removed because operations spanning a
   toggle would need an explicit epoch contract.
3. A global registry was replaced with one registry per `Connection` to avoid
   cross-tenant/process labels and provider ownership.
4. Pool-generation reset was rejected. Operation history now follows the
   logical connection object, while `pool_stats()` keeps its intentionally
   different current-pool epoch.
5. The synchronous `Connection.transaction()` factory and pre-future argument
   conversion were excluded so only accepted asynchronous work is counted.
6. Pooled transaction metrics were explicitly propagated to the owner; the
   direct constructor remains outside scope instead of silently creating an
   inaccessible registry.
7. `execute_batch()` is included even though its implementation uses a
   dedicated socket, because the public call is still owned by `Connection`.
8. Internal readiness, batch statements and cleanup SQL were made
   non-recursive metrics to prevent double-counting logical calls.
9. Mutually exclusive outcome priority was fixed. `CommitOutcomeUnknown`
   outranks its timeout/error cause; ordinary timeout types remain
   `timed_out`.
10. A completed counter and an in-flight decrement were removed. Both values
    are derived from one final outcome publication, preventing drift when a
    future is cancelled or panics.
11. Raw histogram buckets are non-cumulative and bounded. Snapshot-time
    accumulation replaces 17 atomic increments per completion with at most
    one.
12. The finite histogram was given an explicit implicit-positive-infinity
    contract: `completed` includes durations above the final 30-second bound.
13. Nanosecond accumulation was changed to saturated microseconds to expand
    aggregate headroom from roughly 584 years to roughly 584,000 years.
    Bucket selection still uses the unrounded elapsed duration.
14. Snapshot semantics were corrected from “atomic” to
    weakly-consistent-but-reconciled. Public arithmetic remains coherent
    during writers and becomes exact after quiescence.
15. Duration/histogram writes precede a Release outcome publication. A
    concurrent reader zeros/clamps unpublished data, avoiding impossible
    completed-zero duration snapshots without adding locks.
16. The snapshot contains no duplicated total series; consumers may sum the
    fixed operations without risking divergence between two registries.
17. `Connection.__aenter__`/`__aexit__`, transaction context methods,
    concurrent disconnect waiters and reconnect generations received explicit
    mappings to prevent accidental omission or double-counting.
18. Framework coverage was split across native ASGI, Flask WSGI and Flask via
    ASGI adapter so the report preserves the already approved event-loop
    distinction.
19. The performance gate was changed from one noisy timing comparison to three
    interleaved 99,999-operation pairs with a median ratio and full
    environment evidence.
20. The public schema contains only fixed operation names and numeric values;
    SQL, errors, credentials and arbitrary labels are forbidden.
21. The timing claim was narrowed to the Rust async body. It includes current
    row/list construction performed there, but not Python scheduling before
    first poll, later user iteration or the bridge's final scalar conversion.
22. Stub typing was fixed to three exact private `TypedDict` names instead of
    leaving the added type surface implementation-defined.
23. The load gate now fixes SQL shape, operation count, concurrency, pool size,
    trial order, warm-up, session isolation and the exact degradation formula.

No placeholder, unresolved API choice, unbounded collection, hidden dependency
or upstream write remains.
