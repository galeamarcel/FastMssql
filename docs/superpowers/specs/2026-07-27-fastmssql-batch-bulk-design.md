# FastMssql Enterprise Batch and Bulk Design

**Status:** approved by the repository owner's standing authorization for
future design/specification/plan files and inline implementations, subject to
self-review and the branch/TDD gates in this document.

**Base SHA:** `0d50c480ac9d5eabd7024dc5c405cb7e5603d317`

**Repository boundary:** all branches, commits, Docker evidence and hosted
checks belong only to `galeamarcel/FastMssql`. The `Rivendael/FastMssql`
remote remains fetch-only and its push URL must stay exactly `DISABLED`.

## Goal

Replace the ambiguous historical meaning of “batch” and “bulk” with distinct,
production-safe APIs:

- `batch(sql)` remains one unparameterized TDS batch with every result set;
- `execute_many(sql, parameter_sets)` repeats one parameterized statement;
- `query_many(sql, parameter_sets, concurrency=...)` runs independent
  parameterized queries with bounded pool concurrency;
- `bulk_insert(...)` remains the compatibility `INSERT ... VALUES` path and
  stops duplicating the complete input in driver memory;
- `native_bulk_insert(...)` becomes an explicit TDS bulk-copy path;
- iterable and async-iterable inputs are consumed with bounded backpressure.

The subsystem is not complete merely because a large list inserts
successfully. Completion requires bounded input ownership, exact transaction
semantics, fail-closed cancellation, safe identifier handling, real SQL
Server evidence, installed-wheel evidence, and truthful documentation.

## Verified baseline and root causes

The isolated design worktree starts from the cumulative fork SHA above.
Before this design was written:

- FastMssql Rust passed `71/71`;
- vendored Tiberius passed `162/162` with the repository's exact
  `chrono,tds73,rustls` profile;
- `tests/sql_auth_strict/test_batch_strict.py` passed `20/20` against the
  healthy Docker SQL Server using SQL authentication.

The existing compatibility path is functionally correct for the covered
small/list cases, but its memory claim is false:

```text
Python list
  -> convert every value while holding the GIL
  -> Vec<Vec<FastParameter>> containing every chunk
  -> first await
  -> execute each INSERT ... VALUES chunk
```

Moving one chunk at a time out of the outer vector does not make peak driver
memory O(one chunk), because every converted chunk already exists before the
first await. Conversion of all rows also delays the first event-loop yield.

Additional confirmed boundaries are:

- the Rust entry point accepts only `PyList`, not a general iterable;
- bulk cells use `python_to_fast_parameter()` directly and therefore do not
  share the complete `Parameter` descriptor path used by ordinary execution;
- the current path is parameterized `INSERT ... VALUES`, not TDS bulk copy;
- Tiberius `Client::bulk_insert(table)` discovers and sends every updateable
  column, so it cannot represent FastMssql's explicit subset of columns;
- Tiberius `BulkLoadRequest` already streams encoded rows by TDS packet and
  requires `finalize()` before server completion;
- abandoning a bulk request before `finalize()` leaves protocol state
  uncertain, so FastMssql must retire the physical connection;
- `execute_batch()` accepts heterogeneous SQL commands and
  `query_batch()` buffers heterogeneous queries; neither is an
  `execute_many()` or bounded-concurrency `query_many()` contract.

## Approaches considered

### Adopted: compatibility repair followed by explicit native APIs

Keep the existing `bulk_insert()` semantics, remove its O(N) driver-side
duplication, and add a separate `native_bulk_insert()` API. Add
`execute_many()` and `query_many()` separately.

Advantages:

- no silent trigger/default/identity semantic change for existing callers;
- each behavior has a focused RED and implementation branch;
- TDS-native limitations are visible in the API and documentation;
- rollback, cancellation and pool ownership can be proved independently;
- users can choose compatibility semantics or native throughput explicitly.

### Rejected: silently replace `bulk_insert()` with TDS bulk copy

Native TDS bulk is attractive for throughput, but changing the existing name
would silently alter trigger, computed-column, identity and error-timing
behavior. Passing old tests would not prove compatibility.

### Rejected: let Rust drive arbitrary Python iterators across every await

This can produce one long native request, but it couples the TDS state
machine directly to Python iterator/GIL/error semantics and makes
cancellation ownership harder to prove. A bounded Python coordinator over
small Rust chunk primitives is easier to audit. A later optimized writer may
replace the coordinator without changing the public contract.

## Public API contract

### Existing `batch`

`Connection.batch(sql, *, buffer_size=64)` and
`Transaction.batch(sql, *, buffer_size=64)` retain their existing meaning:
one unparameterized SQL batch, all result sets, async-only bounded
`ResultStream`. They are not aliases for collection operations.

### Compatibility `bulk_insert`

```python
await connection.bulk_insert(
    table,
    columns,
    rows,
)
```

The existing positional signature and return value remain compatible:

- `rows` remains a concrete list at the raw extension boundary;
- the driver never owns converted values for more than one SQL
  `INSERT ... VALUES` chunk;
- row width and value conversion are checked chunk by chunk;
- all chunks remain one atomic transaction;
- empty input returns `0` without acquiring a connection;
- callers must not resize `rows` until the awaitable completes; a top-level
  length change observed at a conversion boundary is rejected before the
  first SQL chunk or rolls back all chunks already sent;
- identifier validation occurs before wire I/O;
- conversion or SQL failure rolls back every previously sent chunk;
- cancellation, timeout, panic or uncertain cleanup retires the socket;
- trigger/default/computed/identity behavior remains the existing SQL
  `INSERT` behavior.

This method is compatibility bulk, not native bulk copy.

### Native bulk insert

```python
await connection.native_bulk_insert(
    table,
    columns,
    rows,
    *,
    chunk_size=1000,
)
```

`rows` accepts either:

```python
Iterable[Sequence[Any]] | AsyncIterable[Sequence[Any]]
```

The connection form is atomic across the complete input. The transaction form
uses the already active transaction and never commits or rolls it back:

```python
await transaction.native_bulk_insert(
    table,
    columns,
    rows,
    *,
    chunk_size=1000,
)
```

Contract:

- `chunk_size` is an integer in `1..=10_000`; the default is `1_000`;
- at most one input chunk is converted by the driver at a time;
- the coordinator does not call `list(rows)` or retain consumed rows;
- an async producer is not advanced while a chunk is in flight;
- each chunk uses TDS `INSERT BULK`, not parameterized VALUES text;
- every chunk on the connection form uses the same physical transaction
  lease;
- the transaction form requires an active transaction, leaves settlement to
  the caller on success, and becomes rollback-only after any error once a row
  may have reached the wire;
- row counts are summed with checked arithmetic;
- the first empty input produces `0` without a bulk request;
- no retry occurs after any row may have reached the wire;
- cancellation or abandonment retires the current physical connection;
- raw table and column values are quoted as SQL identifiers, never
  interpolated as arbitrary SQL;
- the target column metadata must match the requested count and order;
- identity, computed, rowversion and unsupported metadata are rejected
  explicitly unless a later option defines their semantics;
- SQL_VARIANT, spatial, hierarchyid, CLR UDT and legacy LOB are not claimed
  by this increment;
- one individual row or LOB may still dominate memory. Byte-level row/LOB
  chunking remains a separately visible limitation.

The initial implementation may open one native bulk request per bounded
chunk. The API does not promise that internal detail. A later persistent
bulk writer can reduce request setup without changing observable semantics.

### `execute_many`

```python
await connection.execute_many(
    sql,
    parameter_sets,
    *,
    atomic=True,
    chunk_size=1000,
)
```

An already active transaction exposes the settlement-neutral form:

```python
await transaction.execute_many(
    sql,
    parameter_sets,
    *,
    chunk_size=1000,
)
```

Contract:

- one SQL statement is reused for every positional parameter set;
- sync and async inputs use the same bounded coordinator as native bulk;
- the return value is the checked total affected-row count;
- `atomic=True` is the default and pins one transaction lease;
- `atomic=False` commits each completed chunk independently and is explicitly
  partial-success capable;
- the transaction form has no `atomic` argument, never settles the caller's
  transaction, and becomes rollback-only after any error once execution has
  started;
- input order is execution order;
- no transparent retry is permitted;
- an error exposes the zero-based input index and whether earlier chunks may
  have committed when `atomic=False`;
- the statement uses the closed typed-parameter conversion path;
- one absolute public operation deadline covers acquisition, conversion,
  execution and settlement.

The heterogeneous legacy `execute_batch(commands)` remains available and is
documented separately.

### `query_many`

```python
async for result in connection.query_many(
    sql,
    parameter_sets,
    *,
    concurrency=10,
    ordered=True,
):
    ...
```

Contract:

- the result is an async iterator, not an eagerly materialized list;
- `concurrency` is a positive integer and is bounded by the pool in practice;
- no more than `concurrency` query operations and one bounded output queue
  are live;
- the input producer advances only when worker/output capacity exists;
- `ordered=True` yields input order and buffers at most the concurrency
  window;
- `ordered=False` yields completion order;
- each yielded value is the existing buffered-first-result `QueryStream`;
  callers needing all result sets use independent `stream()` calls instead;
- the first error terminates the iterator, cancels/retire-checks outstanding
  workers, and is never converted to a skip or swallowed;
- early consumer exit cancels outstanding workers and awaits their cleanup;
- each physical query remains independently observable through existing
  `query` metrics; aggregate query-many metrics require a schema-versioned
  metrics change and are not fabricated.

The transaction class does not expose concurrent `query_many()`: a single TDS
transaction session is intentionally sequential.

## Internal architecture

### Bounded compatibility conversion

The raw Rust compatibility method keeps an owned `Py<PyList>` handle, never a
GIL-bound `Bound` reference, across awaits. Each iteration:

1. acquires the GIL briefly;
2. verifies at the conversion boundary that the top-level list still has its
   captured length;
3. reads at most one SQL VALUES chunk;
4. validates row width and converts cells to owned `FastParameter` values;
5. releases the GIL;
6. sends and fully drains that chunk;
7. drops the converted chunk before converting the next one.

The transaction begins before the first non-empty chunk and the existing
absolute deadline covers every chunk. Any error after `BEGIN` follows the
existing rollback/retirement policy.

### Shared row conversion

A dedicated row converter handles raw values, typed NULLs and non-expanded
`Parameter` descriptors. It reports:

```text
row_index
column_index
parameter_index
sql_type
reason
```

without including the value, SQL text, table name, column name or credential.
Expanded descriptors are rejected for a bulk cell because one input cell must
map to exactly one target column.

### Vendored Tiberius column-subset primitive

The local Tiberius patch gains a structured bulk-column entry point. It:

1. validates and quotes the table and every requested column;
2. requests metadata with `SELECT TOP 0 <columns> FROM <table>`;
3. verifies exact count/order and that every column is writable;
4. rejects unsupported metadata through typed `BulkInput`/protocol errors;
5. emits `INSERT BULK` using checked declarations with quoted column names;
6. returns `BulkLoadRequest`;
7. requires `finalize()` to consume the terminal server response.

No `unreachable!()`, `todo!()` or raw identifier interpolation is permitted
on this new path. Existing Tiberius `bulk_insert(table)` remains compatible.

### Bounded iterable coordinator

The Python wrapper owns iterator protocol details. A single helper:

- validates `chunk_size` before consuming input;
- distinguishes async iterable from sync iterable once;
- accumulates at most one chunk;
- awaits the Rust chunk operation before requesting another row;
- preserves original exceptions and attaches cleanup failures as causes;
- shields mandatory rollback/close cleanup from repeated task cancellation;
- never uses an unbounded task list or queue.

The Rust chunk operations own SQL state and implement fail-closed disposition.
Python never decides that a TDS connection is reusable.

## Error, cancellation and settlement rules

- Local validation before wire I/O is retryable only by caller decision.
- After the first row/request reaches the wire, FastMssql does not retry.
- Constraint and conversion failures in atomic modes roll back all chunks.
- A lost COMMIT acknowledgement remains `CommitOutcomeUnknown`.
- A timeout or cancellation while a bulk request is active closes/retires the
  physical connection; it does not attempt to reuse an undrained request.
- If rollback or close also fails, the original error remains primary and
  cleanup is chained as `__cause__`.
- `BaseException`, including `CancelledError`, is never swallowed.
- Empty inputs do not create a pool, transaction or metrics success that
  claims SQL occurred.
- Compatibility and native bulk each count one public operation in the
  existing `bulk_insert` metric family, never one operation per internal
  chunk. Empty inputs are not admitted and do not increment that family.
- `execute_many` adds an exact `execute_many` operation key and upgrades the
  operation-metrics contract from schema version 1 to schema version 2 in its
  own RED/feature pair. It never increments `execute` per internal item.
- `query_many` intentionally exposes only the existing independently measured
  `query` operations; it does not fabricate an aggregate iterator metric.

## Branch decomposition

Every arrow means the implementation branch must descend from its observed
RED branch.

```text
docs/batch-bulk-design

test/bulk-bounded-buffering
  -> fix/bulk-bounded-buffering

test/bulk-row-descriptor-conversion
  -> fix/bulk-row-descriptor-conversion

test/tiberius-bulk-column-subset
  -> feat/tiberius-bulk-column-subset

test/native-bulk-insert
  -> feat/native-bulk-insert

test/bulk-iterable-backpressure
  -> feat/bulk-iterable-backpressure

test/execute-many
  -> feat/execute-many

test/query-many-bounded-concurrency
  -> feat/query-many-bounded-concurrency

verify/batch-bulk-merge
docs/batch-bulk-status
```

No branch may opportunistically implement a later pair.

## Test design

### Deterministic RED tests

- method creation does not convert every row before returning an awaitable;
- a conversion failure beyond the first chunk is raised from the awaited
  operation and rolls back earlier chunks;
- only one converted chunk is live;
- typed descriptors preserve target-compatible conversion and privacy-safe
  context;
- subset metadata retains exact requested order;
- malicious identifiers cannot alter metadata SQL;
- unsupported bulk metadata is a typed error, never a panic;
- native bulk reaches the TDS bulk encoder and finalizes;
- iterator and async-iterator pull counts prove backpressure;
- early cancellation stops input advancement and retires the session;
- `execute_many` atomic and partial modes have exact settlement evidence;
- `query_many` never exceeds configured concurrency or pool size and cleans up
  on early exit/error.

### Real SQL Server cases

The strict SQL-auth matrix gains a `BULK-*` group covering:

- compatibility multi-chunk atomicity after late conversion failure;
- bounded list memory and event-loop progress;
- native subset columns, defaults and nullable values;
- native mixed scalar types and typed NULLs;
- identity/computed/rowversion rejection;
- trigger behavior documented separately for compatibility/native modes;
- sync and async producer backpressure;
- cancellation, timeout, `KILL SPID` and recovery;
- 1,000, 10,000 and opt-in 99,999 rows;
- pool/SPID ceiling, RSS, p50/p95/p99, error count and post-load smoke;
- `execute_many` ordering/atomicity;
- `query_many` ordered/completion-order modes and bounded concurrency.

### Build and packaging gates

- `cargo fmt --check`;
- Clippy with warnings denied for FastMssql and the vendored profile;
- FastMssql and Tiberius unit suites;
- full SQL-auth strict/async/framework/resilience/load suites;
- original-local-regression suite;
- installed ABI3 wheel tests;
- Linux/macOS/Windows raw Cargo, wheel build/install and contracts;
- RustSec;
- code-review-graph rebuild, impact analysis and test-coverage review.

## Acceptance and non-claims

`feat/batch-bulk` can be marked complete in the live audit only when all seven
RED/implementation pairs are integrated with exact ancestry and all final
gates pass on one cumulative SHA.

Until then, the live audit must say which sub-slices are verified and retain
the remaining limitations. In particular, this design does not claim:

- byte-chunking inside one oversized cell or LOB;
- SQL_VARIANT, spatial, hierarchyid, CLR UDT or legacy LOB support;
- MARS or concurrent commands on one transaction session;
- aggregate multi-process metrics;
- publication of a wheel, release or Tiberius fork;
- permission to open an upstream pull request.
