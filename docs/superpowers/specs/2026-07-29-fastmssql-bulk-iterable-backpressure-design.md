# FastMssql Native Bulk Iterable Backpressure Design

**Status:** approved by the repository owner's standing authorization for
future design/specification/plan files and inline implementations, subject to
self-review and the branch/TDD gates below.

**Base SHA:** `cb34e4859a4ba758d2118c67a218dd4bccc1d478`

**Repository boundary:** every branch, commit, Docker artifact and hosted
check belongs only to `galeamarcel/FastMssql`. The
`Rivendael/FastMssql` remote remains fetch-only and its push URL must stay
exactly `DISABLED`.

## Goal

Close the fifth of the seven approved enterprise batch/bulk slices by
broadening the public Python `native_bulk_insert()` methods from concrete
lists to synchronous and asynchronous row producers without eager
materialization.

The implementation must preserve the already verified native-bulk
properties:

- one physical lease and one SQL transaction for the complete Connection
  operation;
- caller-owned settlement for the Transaction form;
- one absolute operation deadline;
- one public `bulk_insert` metric;
- exact global diagnostic indices;
- fail-closed cancellation and timeout handling;
- zero pool, SQL and metric activity for a successfully empty Connection
  producer;
- the existing concrete-list fast path and its behavior.

Backpressure means that FastMssql owns at most one input chunk and one
converted wire chunk at a time and does not request the next input row while
the current chunk is in flight.

## Scope

This slice changes only:

```python
Connection.native_bulk_insert(...)
Transaction.native_bulk_insert(...)
```

It adds:

- synchronous `Iterable[Sequence[Any]]` input;
- asynchronous `AsyncIterable[Sequence[Any]]` input;
- a private Python bounded producer coordinator;
- a private Rust stateful native-bulk sequence;
- deterministic producer cleanup and cancellation shielding;
- strict SQL-auth cases `BULK-013` through `BULK-021`;
- a dedicated iterable stress harness for 1,000, 10,000 and 99,999 rows.

It does not implement:

- compatibility `bulk_insert()` iterable input;
- `execute_many()` or `query_many()`;
- byte-level streaming inside one row or LOB;
- concurrent operations on one transaction session;
- MONEY/SMALLMONEY fixed-point input;
- TVP, SQL_VARIANT, spatial, hierarchyid, CLR UDT or legacy LOB;
- retry after any input may have reached the wire;
- a release, published wheel, Tiberius fork or upstream pull request.

## Confirmed current behavior

At the exact base:

- the public wrapper and both raw extension methods accept only
  `list[list[Any]]`;
- `Connection.native_bulk_insert()` opens one pooled lease and one SQL
  transaction across all concrete chunks;
- `Transaction.native_bulk_insert()` uses the active caller-owned
  transaction and becomes rollback-only after a reusable post-wire failure;
- `run_native_bulk_chunks()` converts one concrete list chunk at a time and
  reports zero-based indices relative to that complete list;
- every public native-bulk call owns its own operation observer and
  operation deadline;
- an empty Connection list returns zero without lifecycle admission, pool
  creation, SQL or metrics;
- an empty Transaction list validates that the transaction is active, then
  returns zero without metrics;
- cancellation of a `future_into_py` awaitable cancels the Rust future, so
  the existing Rust cancellation guard is the authoritative in-flight
  retirement boundary.

The focused offline baseline at this SHA passed 11/11 native-bulk contract
and compatibility bounded-buffering tests. The imported editable package
came from the equivalent technical tree
`fix/checkout-reset-ddl`; implementation verification must rebuild and load
the extension from the new feature worktree before any runtime claim.

## Approaches considered

### Adopted: bounded Python producer plus private Rust sequence

Python owns `iter()`/`aiter()`, row-sequence normalization and bounded chunk
collection. A private Rust object owns the lifecycle permit, pooled
transaction or caller transaction reservation, absolute deadline, global row
offset, cumulative affected count and one operation observer.

Python awaits each Rust `push()` before pulling another row. Rust remains the
only authority that can decide whether a physical connection is reusable.

Advantages:

- Python iterator and async-generator semantics remain outside the TDS state
  machine;
- the Rust object can keep one deadline and metric over all chunks;
- Connection atomicity and Transaction ownership are preserved;
- cancellation between chunks has an explicit database cleanup endpoint;
- a future persistent TDS bulk writer can replace per-chunk requests without
  changing the public contract.

### Rejected: loop over public Transaction calls

A Python wrapper could create `connection.transaction()` and call
`transaction.native_bulk_insert()` once per chunk. This is rejected because
it would:

- create one `bulk_insert` metric per chunk;
- restart the operation deadline per chunk;
- expose hidden `begin`/`commit` metrics for one public bulk operation;
- reset conversion indices to zero for every chunk;
- leave producer failures between successful chunks outside the Rust
  rollback-only state machine.

Passing simple insert/count tests would not make this implementation
enterprise-correct.

### Rejected: Rust drives arbitrary Python producers across every await

Rust could call Python `__next__`/`__anext__` directly from the long TDS
future. This is rejected because it couples Python GIL, event-loop,
`contextvars` and producer exception semantics to connection ownership.
It also increases the number of cancellation points inside the protocol
state machine.

### Rejected: prefetch task and queue

A producer task plus a queue can hide source latency, but it weakens the
strict contract that the producer is not advanced while a chunk is in
flight. It also adds task ownership and queue-cleanup states without a
throughput requirement that justifies them.

## Public Python contract

The wrapper signatures become logically:

```python
from collections.abc import AsyncIterable, Iterable, Sequence
from typing import Any

BulkRow = Sequence[Any]
BulkRows = (
    list[list[Any]]
    | Iterable[BulkRow]
    | AsyncIterable[BulkRow]
)

async def native_bulk_insert(
    table: str,
    columns: list[str],
    rows: BulkRows,
    *,
    chunk_size: int = 1000,
) -> int: ...
```

The public `Connection` and `Transaction` wrappers expose this widened
contract. The raw extension classes in `fastmssql.fastmssql` intentionally
remain concrete-list primitives and their raw stub remains
`list[list[Any]]`.

### Input dispatch

Dispatch is performed once:

1. validate `chunk_size` without calling `iter()`, `aiter()`, `__next__` or
   `__anext__`;
2. validate table and column identifiers without advancing the producer;
3. if `rows` is a Python list, call the existing raw method unchanged;
4. otherwise prefer the asynchronous protocol when `__aiter__` is present;
5. otherwise call `iter(rows)` exactly once;
6. reject a non-iterable or protocol-acquisition failure locally without
   pool, SQL or operation-metric activity;
7. reserve the Transaction form before the first `__next__`/`__anext__`.

An object implementing both protocols follows its async protocol. A top-level
`str`, `bytes`, `bytearray`, `memoryview` or mapping is rejected as a row
producer rather than being expanded into scalar rows.

### Row normalization

Each produced row must be a non-string `Sequence[Any]`. The coordinator
copies exactly that row into a concrete list and retains it only in the
current chunk.

- list rows remain lists;
- tuples and other finite sequences become lists;
- strings and binary-like values are rejected as rows;
- a row width mismatch remains a privacy-safe conversion failure;
- one individual row may dominate memory and remains a documented limit;
- producer and row-normalization exceptions remain primary and are not
  converted into skips.

The sequence tracks a global zero-based row offset. Rust receives that offset
with every chunk so `row_index`, `column_index` and `parameter_index` retain
the same meaning as for one concrete list.

### Backpressure

The coordinator:

- collects no more than `chunk_size` rows;
- calls the Rust sequence `push()` with that chunk;
- awaits complete TDS finalization for the chunk;
- drops the chunk;
- only then requests the next row.

It never calls `list(rows)`, creates a task per row, uses an unbounded queue,
or retains previously consumed rows.

Synchronous `next()` calls execute on the Python event-loop thread. A
blocking synchronous producer is therefore outside the true-async contract
and must be represented by an async iterable. FastMssql checks the absolute
deadline after every synchronous pull and normalization boundary, but cannot
preempt arbitrary synchronous Python code.

### Empty input

A successfully empty Connection producer returns `0` with:

- no lifecycle admission;
- no pool creation or checkout;
- no SQL/TDS request;
- no operation metric.

A successfully empty Transaction producer first verifies that the
transaction is active, then returns `0` without SQL or metrics. This matches
the existing concrete-list behavior.

### Counts and ordering

Rows are sent in producer order. Every chunk response must equal the chunk
length. The private sequence sums counts with checked `u64` arithmetic and
returns one Python integer.

## Private Python coordinator

A new focused module:

```text
python/fastmssql/_bulk_iterable.py
```

owns:

- sync/async protocol selection;
- bounded chunk creation;
- async-producer deadline wrapping;
- producer `close()`/`aclose()` on abnormal termination;
- cancellation-safe calls to Rust `abort()`;
- cleanup-cause chaining while preserving the original `BaseException`.

The module does not import credentials, construct SQL, inspect pool
internals or decide connection disposition.

### Cleanup shielding

Mandatory Rust abort and async-producer close operations are run in named
`asyncio.Task` objects retained by a strong reference. `asyncio.shield()` is
used only to allow cleanup to reach a terminal state; the caller's original
`CancelledError` is re-raised after cleanup. Repeated cancellation does not
silently abandon cleanup.

This follows the Python 3.13 cancellation contract: `CancelledError` is a
`BaseException`, cleanup belongs in `try/finally`, and cancellation should be
propagated after cleanup. It also follows the documented `shield()` rule
that callers must retain a strong task reference.

On abnormal termination:

1. stop advancing the producer;
2. abort or retire the database sequence;
3. call producer `aclose()`/`close()` when available;
4. attach cleanup failure as `__cause__`;
5. re-raise the original `BaseException`.

## Private Rust sequence

A new private PyO3 class, registered under an underscore-prefixed name, owns
the cross-chunk database state. It is an implementation detail and is not
re-exported by `fastmssql.__init__`.

Conceptual methods:

```text
_NativeBulkSequence.reserve()
_NativeBulkSequence.activate()
_NativeBulkSequence.push(rows)
_NativeBulkSequence.finish()
_NativeBulkSequence.abort(outcome)
_NativeBulkSequence.expire()
_NativeBulkSequence.remaining_timeout()
```

The exact names may vary only if the plan and contract tests are updated
together. Public API names and behavior may not vary.

### Construction

Construction is synchronous and side-effect free with respect to SQL:

- validates `chunk_size`, table and columns;
- captures one monotonic operation deadline;
- captures whether settlement is Connection-owned or caller-owned;
- does not acquire a pool lease;
- does not start a metric for an input that later proves empty.

### Reservation

Before the producer is advanced, the Transaction form awaits a private
`reserve()` transition that:

- validates the caller transaction is `Active`;
- reserves that transaction for this producer;
- rejects ordinary concurrent transaction commands deterministically;
- performs no SQL and starts no metric.

The Connection form has no pre-existing transaction to reserve, so its
`reserve()` is a side-effect-free no-op. If the producer is successfully
empty, `finish()` releases the Transaction reservation back to `Active`
without SQL or metrics.

After iterator acquisition succeeds, a producer that fails or times out in
`__next__`/`__anext__` before yielding its first row records exactly one error
or timeout metric using the captured start time, releases any Transaction
reservation, but still performs no SQL.

### Activation

Activation occurs when the first row is observed:

- starts one `bulk_insert` observer using the captured start time;
- admits one Connection lifecycle operation;
- confirms the caller Transaction reservation is still authoritative;
- does not prefetch another row.

Connection pool acquisition and `BEGIN TRANSACTION` may wait until the first
concrete chunk is ready. The absolute deadline still starts at public method
entry.

### Push

Each `push()`:

- requires a non-empty concrete list of normalized rows;
- uses the stored target and global row offset;
- uses the original absolute operation deadline;
- opens and fully finalizes native TDS bulk requests for that chunk;
- increments the cumulative count only after the response is verified;
- returns without settling the outer transaction;
- rejects concurrent or post-terminal calls deterministically.

The existing concrete-list engine gains an explicit `row_index_base`.
Conversion and affected-count errors use checked arithmetic.

### Finish

For Connection ownership:

- COMMIT uses the original operation deadline;
- loss of COMMIT acknowledgement remains `CommitOutcomeUnknown`;
- the lease is released only after complete settlement;
- the one observer records the terminal result.

For caller Transaction ownership:

- no COMMIT or ROLLBACK is sent;
- state returns from private bulk reservation to `Active`;
- the one observer records success;
- settlement remains the caller's responsibility.

Calling `finish()` for an empty Connection sequence returns zero without
admission. Calling it for an empty Transaction sequence validates `Active`
and returns zero.

### Abort

Producer error, normalization error or cancellation between chunks is a real
database-state transition:

- Connection ownership rolls back under the bounded rollback-cleanup policy;
- a cleanup timeout/panic/protocol error retires the physical connection;
- caller Transaction ownership becomes `RollbackOnly` once any row was sent;
- a caller Transaction with no sent row returns to `Active`;
- cancellation during an in-flight Rust future uses the existing
  epoch-checked cancellation guard and retires the uncertain transport;
- no automatic retry occurs.

`abort()` and `expire()` are cleanup entry points: if cancellation delivery
is still releasing the immediately preceding private awaitable, they wait for
that sequence lock handoff instead of failing with a transient concurrent-call
error. Ordinary producer calls remain fail-fast under concurrent use. This
guarantees that database state and the one operation observer are terminal
before Python re-raises the original exception.

The original producer or cancellation exception stays primary. Abort returns
or raises only cleanup evidence for Python to chain.

### Abandonment

Dropping an unfinished private sequence cannot silently return an active SQL
transaction to the pool. Its last-resort Rust drop/supervisor marks the
transport unusable and releases the lifecycle permit. Normal Python control
flow must still call `abort()` explicitly; drop behavior is a safety net, not
the primary cleanup path.

## Deadline semantics

One absolute operation deadline covers:

- async producer waits;
- sync pull checkpoints;
- row normalization;
- lifecycle admission;
- pool initialization and acquisition;
- every native bulk request;
- Connection COMMIT.

Async `__anext__()` is bounded using the sequence's remaining duration.
When it expires, Rust creates the authoritative
`OperationTimeoutError(operation="bulk_insert")` and performs cleanup.

Rollback cleanup is cancellation-shielded and bounded independently so an
expired operation deadline does not prohibit all cleanup. The primary
timeout remains visible; cleanup failure is its cause.

## Metrics

The operation-metrics schema remains version 1 in this slice.

- concrete list: existing one-operation behavior remains unchanged;
- non-empty iterable: exactly one `bulk_insert.started` and one terminal
  outcome for the complete producer;
- producer failure or producer timeout: exactly one error/timeout outcome;
- cancellation: exactly one cancelled outcome;
- successful empty producer: zero started/completed;
- internal chunks do not increment `bulk_insert`;
- internal Connection `BEGIN`/`COMMIT`/rollback cleanup do not fabricate
  separate public-operation metrics.

Pool metrics continue to reflect real checkout and connection events.

## Error and privacy contract

- `chunk_size` rejects booleans, non-integers and values outside
  `1..=10_000` before producer access.
- Raw identifiers use the existing closed vendored-Tiberius validator before
  producer access.
- Row and cell diagnostics contain only numeric indices, SQL type and closed
  reason codes.
- Values, `repr(value)`, SQL text, table names, column names and credentials
  are never added to driver diagnostics or stress artifacts.
- Producer exceptions are not stringified or wrapped by FastMssql.
- `BaseException`, including `CancelledError`, `KeyboardInterrupt` and
  `SystemExit`, is never swallowed.
- No retry occurs after producer advancement or wire activity.

## Deterministic TDD contracts

The RED branch adds these canonical cases exactly once:

- `BULK-013`: wrapper signatures accept sync/async producers while raw
  extension signatures remain list-only; concrete lists use the unchanged
  raw fast path.
- `BULK-014`: invalid `chunk_size`, invalid identifiers and invalid
  top-level producer types advance the producer zero times and perform zero
  pool/metric activity.
- `BULK-015`: successfully empty sync and async producers return zero with
  the exact Connection/Transaction behavior above.
- `BULK-016`: a synchronous generator crosses multiple chunks in order,
  returns the exact count and is not advanced while a chunk is blocked.
- `BULK-017`: an asynchronous generator crosses multiple chunks in order,
  returns the exact count and is not advanced while a chunk is blocked.
- `BULK-018`: a late conversion or producer failure reports global indices,
  rolls back all Connection rows, preserves the original exception and
  closes the producer.
- `BULK-019`: Transaction success stays uncommitted; a producer failure after
  sent rows makes the transaction rollback-only without settling it.
- `BULK-020`: cancellation during producer wait and during TDS activity stops
  pulls, performs terminal cleanup, records one cancellation and leaves the
  pool usable.
- `BULK-021`: operation timeout during async producer wait and during TDS
  activity is typed, bounded, privacy-safe and leaves no application session.

Offline tests also prove:

- protocol selection occurs once;
- an object implementing both protocols uses the async protocol;
- tuple rows normalize without eager outer materialization;
- no unbounded queue/task construction exists;
- cleanup shielding retains a strong task reference;
- raw and public stubs remain intentionally different.

## Real SQL Server and stress evidence

The SQL-auth tests use the approved Docker SQL Server with SQL
authentication. They verify:

- persisted order and exact row counts;
- one physical Connection identity across all successful chunks;
- one active transaction for Connection and caller Transaction forms;
- rollback after producer and conversion failures;
- rollback-only state for caller-owned transactions;
- lock-gated pull-count backpressure;
- cancellation/timeout DMV disappearance and replacement identity;
- zero sessions after teardown;
- operation and pool metric invariants.

The dedicated stress runner has explicit profiles:

```text
1,000 rows, sync producer,  concurrency 1, chunk 100
1,000 rows, async producer, concurrency 1, chunk 100
10,000 rows, sync producer,  chunk 1,000
10,000 rows, async producer, chunk 1,000
99,999 rows, sync producer,  chunk 1,000
99,999 rows, async producer, chunk 1,000
```

The 99,999 profiles require an explicit extended flag. Every profile records:

- exact affected and persisted rows;
- producer pulls;
- maximum buffered rows;
- RSS baseline/peak/growth;
- event-loop ticker and maximum scheduling gap;
- physical connection identities and maximum SQL sessions;
- operation/pool metric deltas;
- wall time and throughput;
- failure type and post-load smoke;
- zero application sessions after teardown.

Initial gates remain:

```text
maximum buffered rows       <= chunk_size
RSS growth                  <= 64 MiB
maximum event-loop gap      <= 0.100 s
errors/timeouts             == 0
post-load smoke             PASS
remaining app sessions      == 0
```

These characterize the driver and local host, not maximum SQL Server
capacity. A single oversized row/LOB is not bounded by the chunk-size gate.

## Build, wheel and graph gates

Before the feature can be marked `VERIFIED_FORK`:

- focused RED must fail for the missing iterable behavior, not for fixture or
  sandbox errors;
- focused GREEN and all native-bulk SQL-auth cases must pass;
- full deterministic SQL-auth, true-async, framework, resilience and
  original-local-regression suites must pass;
- FastMssql and vendored-Tiberius Rust tests must pass;
- root and vendored `cargo fmt --check` must pass;
- Clippy must deny new warnings under the existing audited profiles;
- Ruff and stub/contract checks must pass;
- an isolated ABI3 wheel must import from its own `site-packages` without
  `PYTHONPATH` and pass offline plus real SQL-auth iterable tests;
- 1,000, 10,000 and 99,999 stress artifacts must pass;
- code-review-graph must be rebuilt on the exact feature SHA and its
  impact/test gaps reviewed;
- credential-value scans must pass;
- hosted Linux/macOS/Windows and RustSec status must be reported exactly as
  PASS, FAIL or NOT RUN, never inferred from local evidence.

## Branch and commit lineage

```text
docs/bulk-iterable-backpressure-design
  -> test/bulk-iterable-backpressure
  -> feat/bulk-iterable-backpressure
  -> docs/bulk-iterable-backpressure-status
```

The feature branch must descend from the observed RED commit. History is not
rewritten. All pushes target only
`https://github.com/galeamarcel/FastMssql.git`.

Planned commit roles:

```text
docs: design bounded native bulk iterable input
docs: plan bounded native bulk iterable input
test: require bounded bulk iterable input
feat: stream bulk iterable input with backpressure
docs: record bulk iterable validation evidence
```

Corrective RED commits discovered during execution remain on separate
`test/...` branches and are merged history-only before the feature
implementation.

## Live-document update

The production-readiness audit remains unchanged on the design and RED
branches except for lineage/status notes that are explicitly labeled as
design or reproduced failure.

Only after the exact feature gates pass may a status branch:

- mark iterable backpressure `VERIFIED_FORK`;
- check only the iterable part of the batch/bulk acceptance criterion;
- retain `execute_many()` and `query_many()` as separate open slices;
- record exact commit ancestry, test counts, wheel hash, stress artifacts,
  hosted status and residual limits;
- keep the public version and release state truthful.

## Source notes

The design relies on:

- the repository-pinned PyO3 and `pyo3-async-runtimes` 0.29.0 APIs;
- the official `future_into_py` contract that cancelling the Python future
  cancels the Rust future;
- the official Python 3.13 task-cancellation and `asyncio.shield()` rules;
- the official Python asynchronous-iterator protocol.

Context7 was queried first as required but its monthly quota was exhausted.
The fallback sources were the official PyO3/docs.rs and Python
documentation:

- <https://docs.rs/pyo3-async-runtimes/0.29.0/pyo3_async_runtimes/tokio/fn.future_into_py.html>
- <https://pyo3.rs/main/async-await.html>
- <https://docs.python.org/3.13/library/asyncio-task.html>
- <https://docs.python.org/3.13/reference/datamodel.html#asynchronous-iterators>
