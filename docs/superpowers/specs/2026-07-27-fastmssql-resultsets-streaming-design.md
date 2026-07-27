# FastMssql Enterprise Result Sets, Streaming and RPC Results Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, implementation plan and every implementation
branch require self-review.

**Date:** 27 July 2026

**Source baseline:** `test/sql-auth-validation` at
`88ac9c00d3d80edbb84377fc1f812070b5cf289b`

**Repository boundary:** every design, reproduction, test, fix, feature,
evidence and status branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original Rivendael
repository is fetch-only and its push URL remains exactly `DISABLED`. The
vendored Tiberius source may be changed inside this FastMssql fork, but no
Tiberius fork, publication or external pull request is authorized by this
design.

## Objective

Replace the current first-result, fully buffered compatibility path with an
additive enterprise API that preserves the complete SQL Server response and
supports true asynchronous, bounded-buffer row-at-a-time consumption.

The completed subsystem must:

1. preserve every result set in wire order, including empty result sets;
2. make column metadata available before the first row and when a result set
   has zero rows;
3. expose a Python asynchronous iterator whose row payload is bounded by
   explicit backpressure;
4. preserve DONE row counts only when SQL Server marks the count valid;
5. preserve SQL Server informational messages separately from rows and
   errors;
6. expose stored-procedure return status and OUTPUT/INPUT_OUTPUT values;
7. keep the physical pool lease and lifecycle permit until the complete
   response reaches a terminal state;
8. return a normally exhausted connection to the pool through the existing
   reset path;
9. retire the physical connection on early response abandonment,
   cancellation, conversion failure or protocol uncertainty;
10. integrate with pooled and transactional execution without allowing a
    second operation on the same in-flight TDS response;
11. keep the existing buffered `query()` and `QueryStream` behavior
    compatible while documenting it honestly;
12. prove the contract with real Docker SQL Server, SQL authentication,
    deterministic fault cases, installed-wheel tests and bounded load;
13. keep the pool lease fail-closed until Python has acknowledged every
    conversion-bearing event already read from the wire.

This design closes audit item `feat/resultsets-streaming` through several
independently reviewable RED/feature pairs. It does not declare the entire
library production-ready.

## Measured baseline

The current implementation has four distinct limitations:

- `Connection::execute_query_async_gil_free()` and its transaction
  equivalent call `tiberius::QueryStream::into_first_result()`;
- `into_first_result()` first collects the complete response with
  `into_results()`, then discards result sets after index zero;
- `PyQueryStream` stores `Vec<Option<Row>>` and offers synchronous
  `__iter__`, indexing, `len()` and `reset()`;
- non-INPUT parameter directions are rejected locally before execution.

The focused SQL-auth suite on the exact baseline passed 15/15. Two of those
passing cases intentionally encode current limitations:

- `RESULT-014` requires the object to be synchronous and not async iterable;
- `RESULT-015` measures growth after the complete result has already been
  buffered and then converted.

A read-only reproduction against the healthy
`fastmssql-sql-auth-dev` container produced:

```text
multiple_visible_rows= [{'first_value': 11}]
empty_len= 0
empty_columns= ValueError: No column information available
has_aiter= False
has_anext= False
output_direction_reason= unsupported_direction
active_after_buffered_query= 0
```

The two-statement batch returned only the row from its first result set. An
empty typed SELECT lost its column metadata. The result object implemented no
async iteration. An OUTPUT descriptor was rejected. The active pool count was
already zero because the entire server response had been consumed before
Python received the object.

The first attempted baseline run stopped before SQL Server because the
ignored local extension binary predated `OperationMetricsConfig`. The binary
timestamp and missing exported symbol identified the environmental mismatch.
Rebuilding the extension from the exact source baseline made the same 15-test
suite pass. This setup correction is not classified as a result-streaming
runtime defect.

Two additional read-only Docker SQL-auth probes found reproducible vendored
token-safety defects on the same baseline:

```text
SELECT TOP (1) name FROM sys.objects FOR BROWSE
  -> ProtocolError: Failed to get results: invalid token type a4

SELECT CAST(1 AS SQL_VARIANT) AS value
  -> Tokio worker panic at type_info.rs ("not yet implemented for SSVariant")
  -> Python ProtocolError after the panic hook wrote to stderr
```

`0xA4` is MS-TDS TABNAME and is followed by COLINFO for browse metadata. The
second result type is explicitly unsupported in this project, but
unsupported server metadata must return a typed error without executing a
Rust `panic!`/`todo!`. These defects receive their own RED/fix branch pair
before the response-event implementation; the fix does not claim
SQL_VARIANT value support.

## Protocol and binding evidence

The design is based on current primary sources and the exact vendored code:

- the PyO3 async-iterator protocol permits `__aiter__` to return self and
  `__anext__` to return an awaitable;
- `pyo3-async-runtimes::tokio::future_into_py` converts a `'static` Rust
  future into the Python awaitable;
- Tiberius documents that its `QueryStream` must be polled empty before the
  client begins another request;
- Tiberius currently exposes only `QueryItem::Metadata` and
  `QueryItem::Row`, while its internal token stream already decodes DONE,
  INFO, RETURNSTATUS and RETURNVALUE;
- MS-TDS defines a procedure name as `US_VARCHAR` in an RPC request and the
  `fByRefValue` parameter flag for output parameters;
- MS-TDS measures `B_VARCHAR` and `US_VARCHAR` lengths in Unicode characters,
  while SQL Server regular identifiers are limited to 128 characters and do
  not admit supplementary characters;
- MS-TDS RESETCONNECTION restores default environment state for pooling but
  explicitly does not reset the transaction isolation level;
- MS-TDS defines DONE_COUNT as the validity bit for `DoneRowCount`;
- MS-TDS requires RETURNSTATUS for RPC execution and one RETURNVALUE token
  for each output parameter.

Primary references:

- [PyO3 asynchronous iterator protocol](https://pyo3.rs/main/class/protocols.html)
- [pyo3-async-runtimes](https://github.com/PyO3/pyo3-async-runtimes)
- [Tiberius QueryStream](https://github.com/prisma/tiberius/blob/main/src/tds/stream/query.rs)
- [MS-TDS RPC Request](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/619c43b6-9495-4a58-9e49-a4950db245b3)
- [MS-TDS variable-length data streams](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/47acf60e-9c36-4184-b016-a4947263e25f)
- [MS-TDS packet status and RESETCONNECTION](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce398f9a-7d47-4ede-8f36-9dd6fc21ca43)
- [MS-TDS COLINFO](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/aa8466c5-ca3d-48ca-a638-7c1becebe754)
- [MS-TDS DONE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/3c06f110-98bd-4d5b-b836-b1ba66452cb7)
- [MS-TDS RETURNSTATUS](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/c719f199-e71b-4187-90b9-94f78bd1870e)
- [MS-TDS RETURNVALUE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/7091f6f6-b83d-4ed2-afeb-ba5013dfb18f)
- [SQL Server database identifiers](https://learn.microsoft.com/en-us/sql/relational-databases/databases/database-identifiers)

## Approaches considered

### Approach A — owned producer plus bounded channel, selected

A Tokio producer task owns an `OwnedPooledConnection`, the lifecycle permit
and the Tiberius response stream. It sends owned response events through one
bounded `tokio::sync::mpsc` channel. Python-side async iterators consume the
channel and convert one raw row at a time under the GIL.

Advantages:

- no self-referential Rust object and no `unsafe`;
- the Tiberius stream and borrowed client remain in one ordinary async stack;
- channel capacity provides deterministic row backpressure;
- the producer can select between wire progress, lifecycle force and consumer
  cancellation;
- dropping the receiver unblocks a producer waiting on a full channel;
- a physical connection has one owner until EOF or fail-closed retirement.

### Approach B — store Tiberius QueryStream directly in a pyclass

Rejected. `tiberius::QueryStream<'a>` borrows the mutable client which would
also need to live in the same Python object. That is a self-referential
lifetime. Solving it through leaked allocations, transmute, pinned
self-references or an unsafe helper crate would make cancellation and pool
ownership harder to prove.

### Approach C — SQL Server cursor or application pagination

Rejected as the default result API. Server cursors and rewritten paginated
queries change transaction duration, locking, execution plans and supported
SQL shapes. They also do not preserve DONE, INFO, output and return-status
tokens as one original response. Applications may build pagination
separately, but it is not a replacement for TDS backpressure.

## Compatibility boundary and public API

The implementation is additive for the 0.8.0 candidate.

Existing calls remain source-compatible:

```python
buffered = await connection.query(sql, params)
raw_buffered = await connection.simple_query(sql)
```

They continue returning the existing buffered `QueryStream` and continue
selecting the first result set. Their documentation and stubs must stop
claiming async iteration or bounded memory. The class remains available for
indexing, `len()`, `reset()` and synchronous compatibility.

The enterprise response APIs are:

```python
response = await connection.stream(
    sql,
    params=None,
    *,
    buffer_size=64,
)

response = await connection.batch(
    sql,
    *,
    buffer_size=64,
)

response = await connection.callproc(
    procedure,
    params=None,
    *,
    buffer_size=64,
)
```

The same methods are available on a begun `Transaction`.

`stream()` uses the parameterized RPC path and accepts the same positional
parameter forms as `query()`. `batch()` sends one raw SQL batch and accepts no
parameters. `callproc()` sends one direct named RPC rather than constructing
an `EXEC` SQL string.

`query()`, `execute()`, `stream()` and the batch APIs continue accepting only
`INPUT` directions. `callproc()` is the only API in this subsystem that
consumes `OUTPUT`, `INPUT_OUTPUT` or `RETURN_VALUE` descriptors.

`buffer_size` is a plain integer from 1 through 1,024. Python booleans,
floats, negative values, zero and values above the limit are rejected locally
before pool checkout. The default of 64 is fixed and documented.

No existing method silently changes its return type in this subsystem.

## Python result model

### ResultStream

`ResultStream` is an async context manager and an async iterator of
`ResultSet` objects:

```python
response = await connection.stream(sql, params, buffer_size=32)

async with response:
    async for result_set in response:
        print(result_set.index, result_set.columns)
        async for row in result_set:
            process(row)

summary = response.summary
```

The outer iterator yields result sets strictly in wire order. A result set is
yielded as soon as its metadata arrives, including when no row follows.

Only one result set may be active at a time. Calling outer `__anext__` before
the current set reaches EOF or is explicitly discarded raises a stable
`RuntimeError`; it never races two consumers against one TDS response.

Public terminal operations:

```text
await response.aclose()       abort the complete response; return None
await response.finish()       consume/discard through normal EOF; return summary
response.closed               terminal local state
response.complete             true only after normal EOF and lease release
response.summary              available only after normal released completion
```

`aclose()` is idempotent and waits for producer acknowledgement that the
lease was released or retired. It never reports completion merely because a
cancel signal was queued.

`finish()` drains rows without materializing them, preserving result
metadata, DONE records, messages, return status and output values. It is the
explicit way to obtain a complete summary when the caller does not need every
row. It may take over from a non-busy active `ResultSet`, closes that set
locally, drains the remaining response and returns the immutable
`ResultSummary`. A simultaneous consuming operation is rejected by the same
non-waiting single-consumer gate. Repeating `finish()` after normal completion
returns the existing summary; calling it after an aborted response raises a
stable local `RuntimeError`, and calling it after a failed response re-raises
the stored typed terminal error.

`ResultStream.__aexit__()` is a no-op after normal completion and otherwise
awaits full `aclose()`. Exiting the context early therefore retires rather
than silently returning a partially consumed connection to the pool.

Accessing `summary` before normal EOF raises `RuntimeError`. An aborted
response has no partial summary masquerading as complete.

### ResultSet

Each `ResultSet` has immutable metadata:

```text
index
columns          tuple[ColumnMetadata, ...]
column_names     tuple[str, ...]
```

`ResultSet.index` and `ColumnMetadata.ordinal` are zero-based wire-order
indices. `column_names` preserves ordinal order and does not deduplicate
duplicate SQL names.

`ColumnMetadata` contains:

```text
ordinal
name
type_name
nullable
precision
scale
length
```

`nullable` is `True`, `False` or `None` when SQL Server marks nullability
unknown. `type_name` is the canonical lowercase base SQL name without
size/precision suffixes (`nvarchar`, `decimal`, `datetime2`, and so on);
nullable variable-width integer/float/money/datetime metadata resolves to its
actual declared base name from the received storage length. `length` is an
integer, `"MAX"` or `None`. For `NCHAR`/`NVARCHAR`, it reports declared
UTF-16 code-unit capacity (the received TDS byte length divided exactly by
two); for `CHAR`/`VARCHAR` and binary families it reports byte capacity,
including under UTF-8 collations. TDS's `0xffff` PLP sentinel is reported as
`"MAX"`. TIME, DATETIME2 and DATETIMEOFFSET expose their received
fractional-seconds scale through `scale`, not as a storage length;
DECIMAL/NUMERIC expose received precision and scale. Fields not represented
by the received TDS metadata are `None`; the driver does not guess. Metadata
is constructed from COLMETADATA, not from the first row.

`ResultSet` implements:

```text
__aiter__
__anext__
await aclose()     discard this set through its boundary, retain response
closed
```

`ResultSet.aclose()` means “skip remaining rows in this set”, not “abort the
whole response”. It consumes through the next result-set boundary under the
same absolute operation deadline. `ResultStream.aclose()` aborts the complete
response and retires the connection.

Finding a result-set boundary can require receiving the next COLMETADATA.
The consumer keeps exactly one such metadata envelope in a private look-ahead
slot. It does not acknowledge, discard or expose that envelope through the
closing `ResultSet`; the outer `ResultStream` consumes and acknowledges it on
its next operation. This prevents `ResultSet.aclose()` from losing an empty
following set. A terminal raw summary is different: the inner operation
converts and acknowledges it in the same cancellation-safe poll, then awaits
the out-of-band successful release. Only that release completes the public
response and makes `response.summary` available.

There is no `len`, synchronous iterator, item indexing, slice, reset or replay
on `ResultSet`. Those operations require buffering and remain only on the
legacy `QueryStream`.

### ResultSummary

After normal EOF, `ResultStream.summary` is an immutable
`ResultSummary` containing:

```text
result_set_count
done                  tuple[DoneResult, ...]
messages              tuple[SqlMessage, ...]
return_status         int | None
output_parameters     mapping[str | int, object]
```

The `output_parameters` property returns a fresh dictionary snapshot on each
access, so mutating it cannot change the internally immutable summary.
Named output slots use their canonical name without `@`. Positional output
and positional RETURN_VALUE slots use their original zero-based descriptor
index. The mapping contains only OUTPUT, INPUT_OUTPUT and explicitly declared
RETURN_VALUE slots.

`DoneResult` preserves:

```text
kind                  DONE | DONEPROC | DONEINPROC
rows_affected         int | None
more_results          bool
in_transaction        bool
attention_acknowledged bool
```

`rows_affected` is `None` unless DONE_COUNT is set. A valid zero remains
integer zero and is not confused with an absent count.

`SqlMessage` preserves number, state, severity, message, server, procedure and
line for the caller. Messages are returned as data and are never logged
automatically.

The initial implementation enforces fixed safety limits of 4,096 DONE
records, 1,024 informational messages and 1,024 result sets per response.
Exceeding a limit raises `ProtocolError` with the stable safe reason
`response_limit_exceeded` and retires the connection. These limits bound
non-row response metadata without adding a second configuration object. They
may become configurable only with measured evidence.

## Producer and backpressure architecture

The producer owns:

```text
OwnedPooledConnection or owned Transaction session guard
lifecycle OperationPermit
operation deadline
Tiberius ResponseStream
bounded Sender<ResponseEvent>
bounded Receiver<ConsumerAck>
cancellation receiver
out-of-band terminal-release sender
```

The Python `ResultStream` owns:

```text
bounded Receiver<ResponseEvent>
bounded Sender<ConsumerAck>
cancellation sender
out-of-band terminal-release receiver
single-consumer state
current ResultSet state
one pending next-set metadata envelope
terminal summary or terminal error
```

The channel holds both control events and rows. At most `buffer_size` pending
events exist, so a fast SQL Server cannot cause an unbounded `Vec<Row>`.
Tiberius decodes only the next token requested by the producer. Python
conversion occurs only after a row leaves the bounded channel.

This is a count bound, not a byte-streaming claim. Peak queued row memory is
`O(buffer_size × largest decoded row)` plus protocol/runtime overhead; one
individual row or LOB is still decoded as one owned Tiberius row. The RSS gate
therefore fixes payload size and records it with the measured ceiling.
Chunked cell/LOB streaming and a byte-budgeted channel require a separate
protocol/API design.

Every conversion-bearing event—result metadata, row or terminal raw
summary—has a monotonically increasing sequence number. The consumer sends
exactly one bounded `ConsumerAck` for that sequence:

```text
Converted
Discarded
ConversionFailed
```

`Converted` is sent in the same Rust future poll that dequeues and converts
the event, with no intervening await. Cancelling a pending `__anext__` before
the receive is ready therefore leaves the event queued; cancelling after it
is ready cannot interrupt between dequeue, state update and acknowledgement.
`ResultSet.aclose()` uses `Discarded` only for rows belonging to the set it is
closing, leaves next-set metadata pending, and converts a terminal summary.
`ResultStream.finish()` uses `Discarded` for metadata and rows it deliberately
does not convert. Any conversion failure uses `ConversionFailed`, cancels the
remaining response and requires physical retirement.

The producer uses an explicit outstanding-credit counter and does not send a
new conversion envelope while the count equals `buffer_size`; bounded channel
capacity alone is not treated as the credit invariant. It therefore tracks
at most `buffer_size` unacknowledged conversion events.
After protocol EOF it waits, under the original absolute operation deadline,
for every outstanding acknowledgement, including terminal output conversion.
Only then may it mark the connection `NeedsReset`, complete metrics and
release the lease. This prevents wire read-ahead from turning a later Python
conversion failure into reuse of a connection that the public contract says
must be retired.

Terminal release is not another bounded response event. A coalescing watch
channel carries a cloneable, value-redacted state:

```text
Pending
ReleasedSuccess
ReleasedFailure(typed safe error state)
ReleasedRetired
```

The producer changes it exactly once, only after the response and all owned
guards have been dropped. Closing the event sender on error cannot be blocked
by a full row channel; after draining any already-decoded envelopes, a
consumer that sees event-channel EOF reads the terminal state and raises the
stored typed failure. Processing a normal raw summary sends its conversion
ACK and then awaits either `ReleasedSuccess` or `ReleasedRetired` before
public EOF or `finish()` returns. Both are successful public completion;
`ReleasedRetired` additionally proves that policy retired the physical
transport. On an explicitly closed/abandoned response the consumer's own
terminal state remains `Closed`, so the same release signal cannot turn an
abort into normal completion. Cancellation during that post-ACK wait does
not change the already-recorded terminal state, so a repeated terminal
operation can finish the wait without duplicating metrics or release.

The producer body runs inside the existing Rust panic boundary. An outer
supervisor publishes a value-free typed protocol failure only after an
unwound body future has dropped its response and lease. The supervisor owns
the operation observer so the unwind records one error rather than a
cancelled Drop outcome; close, drop and lifecycle force signal cancellation
and never abort that supervisor.

The producer state machine is:

```text
Created
  -> Running
     -> Completed       complete protocol EOF; lease NeedsReset
     -> CompletedRetired
                        complete protocol EOF; public success; lease Broken
     -> Failed          typed SQL/protocol/I/O error disposition
     -> Cancelled       Python cancellation; lease Broken
     -> Abandoned       receiver/drop/early close; lease Broken
     -> ForcedClosed    lifecycle force; lease Broken
```

The consumer state machine is:

```text
OpenNoSet
  -> ActiveSet
     -> OpenNoSet       set EOF or set discard
     -> Complete        terminal summary at final set boundary
  -> Complete           normal response EOF and immutable summary
  -> Closed             explicit full-response close
  -> Failed             stable terminal exception
```

All state transitions are one-way and terminal notification is exactly once.

## Pool ownership, reset and cancellation

The pool path acquires `bb8::PooledConnection<'static, ...>` through the
existing owned checkout helper. Before sending the request it calls the same
checkout preparation and fail-closed `begin_operation()` used by current
operations.

On complete protocol EOF plus successful acknowledgement of every
conversion-bearing event for ordinary SQL:

1. the producer validates the terminal response state;
2. the connection becomes `NeedsReset`;
3. the operation metric completes successfully;
4. the lifecycle permit is released;
5. dropping the owned lease returns it to bb8;
6. the next checkout uses the existing RESETCONNECTION path.

If the existing `requires_connection_retirement(sql)` policy classifies a
parameterized or raw batch, normal completion still returns success and
records a successful metric, but the managed connection remains `Broken` and
bb8 retires it instead of applying `NeedsReset`.

On early `ResultStream.aclose()`, dropped receiver, producer-task
cancellation, row-conversion failure, SQL/protocol uncertainty or lifecycle
force:

1. the response stream is dropped;
2. the managed connection remains `Broken`;
3. bb8 retires the physical connection;
4. no partially consumed TDS stream is returned to another borrower;
5. terminal acknowledgement is sent only after owned resources are dropped.

The first implementation deliberately does not try to reuse an early-closed
socket. Tiberius exposes no public ATTENTION request and same-socket reuse
would require ATTENTION, DONE_ATTN validation and complete drain. Physical
retirement is the deterministic fail-closed behavior.

Dropping a Python object cannot await. `Drop` therefore sends best-effort
cancellation; the producer also detects receiver closure. Tests wait on pool
statistics and SQL Server DMVs to prove eventual retirement. Explicit
`async with` or `aclose()` remains the deterministic application contract.

Dropping an active `ResultSet` without exhausting it or calling its
`aclose()` cancels the complete response. A destructor cannot safely skip to
the next boundary asynchronously, so it must use the same fail-closed
retirement path as a dropped `ResultStream`.

Cancelling one pending Python `__anext__` await does not dequeue or lose an
event. The receiver operation is cancellation-safe and remains usable if the
caller keeps the response object. Dropping the object after task cancellation
triggers complete response cancellation and retirement.

## Deadlines and lifecycle

Pool acquisition remains governed by `acquire_timeout`.

The existing absolute `operation_timeout` begins before the request is sent
and covers the complete response through protocol EOF, including time spent
blocked by consumer backpressure. This preserves the established absolute
deadline policy and prevents an abandoned-but-referenced stream from holding
a session forever. Applications streaming intentionally long responses must
configure an appropriate finite operation timeout.

Every live response retains its lifecycle operation permit. Graceful
`disconnect()` waits for response completion or explicit close. At grace
expiry the registered stream participant cancels the producer, retires the
connection and reports the existing typed lifecycle timeout. A cancelled
disconnect waiter does not cancel the shared shutdown supervisor.

The stream does not start a fresh timeout per row and does not hide the
application's backpressure time.

## Transaction semantics

A transaction response holds the owned transaction-session mutex guard for
the complete response. No query, execute, commit or rollback can interleave
with that response.

On `ResultSet.aclose()` through a valid boundary, the response remains open
and continues owning the transaction-session guard. Only normal EOF of the
complete response returns the transaction to its prior begun state.

Normal EOF for SQL classified by `requires_connection_retirement` is a
successful response but not a reusable transaction state: the summary is
delivered, the transport is retired, the transaction becomes `Failed` and a
later commit is rejected.

On complete response abandonment, Python task cancellation, forced shutdown,
conversion failure or uncertain TDS state:

- the physical transaction connection is retired;
- the transaction transitions to `Failed`;
- SQL Server rolls back the open transaction when the transport closes;
- later data operations and commit are rejected;
- explicit close remains idempotent.

The direct compatibility `Transaction(...)` path follows the same stream
state machine but remains outside the shared pool as already documented.

## Vendored Tiberius response events

The vendored crate gains a public response-event path without changing the
existing `QueryStream<Item = QueryItem>` contract.

The new owned event enum preserves:

```text
Metadata(ResultMetadata)
Row(Row)
Done(ResponseDone)
Info(ResponseInfo)
ReturnStatus(i32)
ReturnValue(ResponseReturnValue)
```

`ResponseDone` exposes the DONE variant and status bits through safe getters.
It returns an optional row count based on DONE_COUNT rather than exposing the
initialized numeric field unconditionally.

`ResponseInfo` owns every INFO field. It does not implement a value-bearing
`repr` beyond safe structural metadata.

`ResponseReturnValue` owns ordinal, name, status, type metadata and
`ColumnData<'static>`. FastMssql converts it through the same Python scalar
mapping used for row values.

An ERROR token remains terminal error control rather than caller-owned
`ResponseEvent` data. The adapter lets the existing token stream consume the
server's trailing DONE state and then propagates its stored server error,
preserving the difference between a fully drained nonfatal SQL error and an
immediate protocol/I/O failure.

For a fully drained nonfatal server error that is eligible for reset, the
FastMssql producer first closes the event sender and waits for successful
acknowledgement of every already-queued conversion envelope. Only then may it
mark the lease `NeedsReset` and publish the stored server error. A conversion
failure, abandonment or deadline while those acknowledgements are pending
overrides reuse and retires the transport. An immediate protocol/I/O failure
is already non-reusable and may drop its resources before queued events are
drained.

New Tiberius entry points return `ResponseStream` for:

```text
parameterized SQL
raw SQL batch
named RPC procedure
```

Existing `Client::query()` and `Client::simple_query()` remain compatible
adapters that filter response events into `QueryItem`.

When a pooled session is armed for RESETCONNECTION, parameterized SQL and raw
batches retain the existing isolation-baseline prefix. A direct named RPC
cannot contain that prefix. Before its first named RPC on such a checkout,
Tiberius therefore sends and fully consumes one reset-bearing direct batch
containing only `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`, then sends
the named RPC. Both responses remain inside the same FastMssql absolute
operation deadline. This adds one reset round trip only for a recycled
session and prevents a previous borrower's isolation level from leaking into
the procedure.

The currently unimplemented named `RpcProcIdValue::Name` encoder is completed
using the MS-TDS `US_VARCHAR` form. It validates the UTF-16 code-unit length
before writing and never reaches an unimplemented branch or panics on
application input.

Tiberius token tracing is structural only. It may record token kind, numeric
code, severity or counts, but never SQL Server message text, row values,
RETURNVALUE contents, metadata names, environment-change payloads or
procedure names. `Client`/connection `Debug` reports only buffer length and
state; it never renders the raw TDS buffer. The `SqlReadBytes::debug_buffer`
hook is likewise structural and contains no hex or byte dump.

RPC parameters carry:

```text
name
value
explicit TypeInfo when declared
ByRefValue for OUTPUT and INPUT_OUTPUT
safe parameter index/declaration metadata for encoding errors
```

`DefaultValue` is never set by this subsystem. A procedure argument omitted
from `Parameters` is not encoded at all.

No raw SQL is generated to invoke a procedure.

## Stored procedures, OUT values and return status

`callproc()` accepts either positional parameters or named parameters in a
`Parameters` object. Mixing positional and named parameters is rejected.
Named keys may be supplied with or without one leading `@`; the canonical
output mapping omits it and the wire name adds exactly one leading `@`.
Canonical names contain 1 through 127 ASCII characters, begin with an ASCII
letter, `_` or `#`, and thereafter contain only ASCII letters/digits or
`_`, `@`, `$`, `#`. This keeps the complete wire identifier within SQL
Server's 128-character limit and deliberately rejects `@@`, supplementary
characters, whitespace, quoting and punctuation. Duplicate canonical names
are rejected.

The procedure name is sent as the RPC ProcName field, not interpolated into
SQL. It contains one through three dot-separated identifier components.
Each component contains 1 through 128 ASCII characters, begins with an ASCII
letter, `_` or `#`, and thereafter contains only ASCII letters/digits or
`_`, `@`, `$`, `#`. This is a deliberately conservative subset of SQL
Server regular identifiers. Unicode, supplementary characters, whitespace,
quoting syntax, batch punctuation, NUL and control characters are rejected
locally in the initial release. The validated name is sent byte-for-byte and
is never logged.

Direction rules are:

| Direction | Input value | Explicit SQL type | Wire behavior |
|---|---|---|---|
| `INPUT` | required, nullable allowed | optional | by value |
| `OUTPUT` | must be `None` | required | typed NULL, ByRefValue |
| `INPUT_OUTPUT` | required, nullable allowed | required | value, ByRefValue |
| `RETURN_VALUE` | must be `None` | must be `INT` or omitted | local status slot, not an RPC parameter |

Expanded parameters are rejected for `callproc()`. Table-valued parameters
remain a separate feature.

At most one `RETURN_VALUE` descriptor is accepted. SQL Server's
RETURNSTATUS token is always captured even when no descriptor was supplied;
the descriptor controls only whether an aligned entry is also present in
`output_parameters`.

RETURNVALUE tokens are matched by original parameter ordinal and validated
name, never by arrival order. MS-TDS permits large output values to be moved
after smaller values, so arrival-order matching would be incorrect.

Output conversion supports the scalar grammar already implemented for typed
input. MONEY/SMALLMONEY, TVP, SQL_VARIANT, spatial, hierarchyid, CLR UDT and
legacy LOB output remain explicitly unsupported until their own exact
conversion designs.

## Row conversion and metadata

COLMETADATA creates one shared `Arc<ColumnInfo>` before any row is consumed.
Every `FastRow` in that result set reuses it.

Raw Tiberius `Row` values cross the bounded channel, but Python objects do
not. `ResultSet.__anext__` converts exactly one row under the GIL and returns
one `FastRow`.

If conversion of a received row fails:

- the consumer receives the typed conversion/protocol error;
- the complete response is cancelled;
- the producer retires the physical connection;
- subsequent iteration returns the same terminal classification without
  exposing row contents.

Duplicate SQL column names retain ordinal access. The existing name map keeps
its current deterministic last-name behavior until a separate row-name
ambiguity contract changes it.

## Errors and privacy

New local misuse errors are deterministic and occur before network I/O:

- invalid `buffer_size`;
- mixed named and positional procedure parameters;
- invalid direction/value combinations;
- missing explicit output type;
- expanded procedure parameter;
- duplicate canonical parameter name;
- concurrent result-set consumers;
- accessing `summary` before normal EOF.

Wire, SQL Server and lifecycle failures continue using the existing typed
FastMssql exception hierarchy. Timeout, lifecycle and protocol failures that
retire a stream record `connection_discarded=True` through their existing
typed metadata. Explicit `aclose()` is successful control flow rather than a
fabricated query error. No response is retried automatically.

No driver-authored exception context, `repr`, metric, warning, report, log or
terminal acknowledgement may add:

- SQL text;
- parameter or output values;
- row payloads;
- connection strings;
- usernames or passwords;
- driver-authored metric labels or logs derived from procedure names.

The opaque message of a SQL Server-originated error and caller-owned
`SqlMessage` fields can themselves contain server-supplied object, server or
procedure names and message text. FastMssql returns those fields only to the
caller, labels them as server data and never copies them into
driver-authored context or logs them automatically.

`ResultStream`, `ResultSet`, `ResultSummary`, `ColumnMetadata`,
`DoneResult`, and `SqlMessage` reprs contain only type, counts, indices and
terminal state.

## Operation metrics

The fixed operation-metrics schema remains version 1 in this subsystem:

- `stream()` contributes to `query`;
- `batch()` contributes to `query_batch`;
- `callproc()` contributes to `query`.

Duration spans lifecycle admission, pool/session acquisition and protocol
work through EOF or terminal close, matching the current outer
`observe_operation` boundary. A response abandoned before EOF records one
`cancelled` outcome. A SQL/protocol error records one `errors` outcome.
Operation-timeout and lifecycle-force outcomes retain their existing typed
categories. No SQL- or procedure-derived metric label is added.

Adding separately named stream/RPC metric series would require a distinct
schema-version design and is not bundled here.

## Test design

### Existing compatibility cases

`RESULT-001` through `RESULT-015` remain green for legacy `QueryStream`.
Their descriptions are corrected to call it buffered and synchronous.
`RESULT-014` no longer acts as evidence about the new `ResultStream`.

### Tiberius token-safety defect cases

- `TIB-SAFE-001`: a real `FOR BROWSE` response consumes TABNAME and COLINFO
  structurally and leaves legacy `QueryStream` rows intact.
- `TIB-SAFE-002`: real SQL_VARIANT metadata returns a deterministic typed
  unsupported/protocol error without a Rust panic or panic-hook output.
- `TIB-SAFE-003`: raw TABNAME/COLINFO payload and unsupported
  UDT/SQL_VARIANT metadata unit cases, plus the dispatch source contract,
  never reach `panic!`, `todo!`, `unimplemented!` or `unreachable!`.

### Tiberius response-event cases

- `TIB-RESULT-001`: metadata and rows preserve multiple result indices.
- `TIB-RESULT-002`: empty metadata survives without a row.
- `TIB-RESULT-003`: DONE kind, DONE_COUNT and zero count are distinct.
- `TIB-RESULT-004`: INFO fields are preserved.
- `TIB-RESULT-005`: RETURNSTATUS is decoded as signed 32-bit.
- `TIB-RESULT-006`: RETURNVALUE preserves ordinal, name, type and value.
- `TIB-RESULT-007`: named RPC uses exact US_VARCHAR encoding.
- `TIB-RESULT-008`: ByRefValue is set only for output-capable directions.
- `TIB-RESULT-009`: existing QueryStream adapters retain their old contract.
- `TIB-RESULT-010`: token tracing never records message, row, output or
  procedure payloads, and connection diagnostics never dump raw TDS bytes.

`TIB-RESULT-001..006` and `TIB-RESULT-009` run as a no-skip Rust integration
test against the same Docker SQL Server and SQL-auth environment as the
Python matrix. Exact ProcName/parameter wire bytes and ByRef flags for
`TIB-RESULT-007..008` run as vendored crate unit tests. A separate database-
independent public-API compile contract and the trace-privacy source contract
run in hosted Linux/macOS/Windows CI; hosted jobs do not pretend to provide
SQL Server integration evidence.

### SQL-auth result-stream cases

- `RESULT-016`: three result sets are yielded in order.
- `RESULT-017`: an empty middle result set retains complete metadata.
- `RESULT-018`: outer and inner async iteration are genuinely awaitable and
  reject synchronous iteration.
- `RESULT-019`: a large slow-consumer response remains within the configured
  channel bound and a measured RSS ceiling.
- `RESULT-020`: ordinary normal EOF returns a resettable lease, while
  successfully drained retirement-classified SQL closes its physical SPID;
  both permit a smoke query.
- `RESULT-021`: skipping one result set reaches the next boundary, preserves
  an empty following set and terminal summary, and does not materialize
  skipped rows.
- `RESULT-022`: complete `aclose()` retires the physical SPID and recovers
  the pool.
- `RESULT-023`: dropped and garbage-collected responses eventually retire the
  physical SPID.
- `RESULT-024`: cancellation during `__anext__`, conversion failure and a
  midstream SQL error cannot poison a later checkout; fully drained nonfatal
  SQL errors reset/reuse while conversion or protocol uncertainty retires.
- `RESULT-025`: concurrent consumers and invalid buffer sizes fail locally
  and privacy-safely.
- `RESULT-026`: graceful disconnect waits; forced disconnect retires the
  stream within the lifecycle budget.
- `RESULT-027`: transaction EOF preserves the transaction, while abandonment
  fails and retires it.
- `RESULT-028`: DONE records, valid zero counts and INFO messages remain
  separate from rows.
- `RESULT-029`: a 1,000-operation bounded-stream profile at concurrency 64
  respects `pool.max_size`, completes exactly once, records
  throughput/latency/pool-wait/RSS/client-CPU/SQL-session-CPU/ticker evidence
  and leaves a post-load smoke query green.
- `RESULT-030`: the installed wheel exposes the same async protocol and real
  SQL-auth behavior.
- `RESULT-031`: queued metadata, rows and output summaries keep the lease
  active until successful consumer acknowledgement; a conversion failure
  observed after wire EOF still retires the physical connection.

The load gate starts at 1,000 streams with pool size 8 and concurrency 64.
Larger gates are increased only after the focused contract is stable; the
objective is driver backpressure and lease correctness, not SQL Server
saturation.

The harness uses exactly `concurrency` long-lived worker coroutines fed by a
bounded queue, so a 99,999-operation profile never allocates one task per
operation. The required 1,000-operation gate records and enforces a
134,217,728-byte RSS-growth ceiling; any override is explicit evidence, not
an unrecorded default change.

`RESULT-030` is deliberately not inserted into the ordinary central
one-case/one-pytest-node SQL-auth specification: a source/editable test cannot
truthfully prove an installed wheel. Its required evidence is the dedicated
isolated-wheel lane with resolved import path, wheel hash and real-MSSQL
result/lifecycle/RPC test counts, copied into the final status report.

### Stored-procedure cases

- `RPC-001`: direct named procedure with no parameters returns exact status.
- `RPC-002`: INPUT, OUTPUT and INPUT_OUTPUT integer values round-trip.
- `RPC-003`: Unicode, binary, decimal, UUID and temporal outputs preserve
  their Python types.
- `RPC-004`: multiple and empty result sets precede output summary correctly.
- `RPC-005`: output values reordered by large-object rules still match their
  original names/ordinals.
- `RPC-006`: a valid zero return status differs from absent status.
- `RPC-007`: SQL error, cancellation and early close retire or reset the
  connection according to terminal certainty.
- `RPC-008`: named and positional parameter modes, direction rules,
  procedure-name limits and named-parameter limits are deterministic and
  injection-safe.
- `RPC-009`: pooled and transactional call paths share one implementation.
- `RPC-010`: concurrent procedure calls preserve outputs without exceeding
  pool/session bounds.
- `RPC-011`: a recycled session restores READ COMMITTED before a direct named
  RPC and does not inherit the previous borrower's isolation level.

All SQL objects use per-test unique names and are dropped through the cleanup
registry. No exception handler swallows an unexpected failure.

## Branch topology

Every arrow means strict Git ancestry from the previous reviewed branch:

```text
docs/resultsets-streaming-design
  -> test/tiberius-token-safety
     -> fix/tiberius-token-safety
        -> test/tiberius-response-events
           -> feat/tiberius-response-events
              -> test/resultsets-streaming
                 -> feat/resultsets-streaming
                    -> test/resultstream-lifecycle
                       -> feat/resultstream-lifecycle
                          -> test/rpc-output-results
                             -> feat/rpc-output-results
                                -> verify/resultsets-streaming-merge
                                   -> test/sql-auth-validation
```

Responsibilities:

- `test/tiberius-token-safety` records the two real reproductions and
  panic-free decoder contracts only;
- `fix/tiberius-token-safety` consumes TABNAME/COLINFO and converts explicitly
  unsupported TYPE_INFO into typed errors without adding SQL_VARIANT/UDT
  value support;
- `test/tiberius-response-events` records raw protocol RED tests only;
- `feat/tiberius-response-events` changes only the vendored response/RPC
  event surface and its patch documentation;
- `test/resultsets-streaming` records Python and real-MSSQL RED contracts;
- `feat/resultsets-streaming` implements the additive bounded result API;
- `test/resultstream-lifecycle` records early-close, cancellation,
  transaction and shutdown RED contracts;
- `feat/resultstream-lifecycle` completes fail-closed ownership semantics;
- `test/rpc-output-results` records procedure OUT/return RED contracts;
- `feat/rpc-output-results` implements direct RPC and output conversion;
- any newly discovered defect receives its own `test/...` then `fix/...`
  branch inserted before technical verification;
- `verify/resultsets-streaming-merge` merges reviewed histories and contains
  only merge/evidence changes;
- a later dedicated status branch updates the live audit after exact merged
  verification.

No intermediate branch is presented as a production candidate. In
particular, the basic stream feature is not integrated into the cumulative
branch until the lifecycle and fail-closed retirement branch is also green.

No implementation branch is pushed to or based for publication on the
original Rivendael repository.

## TDD and acceptance gates

Every focused problem requires:

```text
new focused test on its test branch                     RED
same focused test after implementation                 PASS
all pre-existing tests for that layer                  PASS
git diff --check                                       PASS
privacy sentinel scan                                 PASS
branch ancestry and fork remote check                 PASS
```

Every Rust implementation branch also requires:

```text
cargo fmt --check                                      PASS
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked                                    PASS
vendored Tiberius fmt/clippy                           PASS
vendored Tiberius tests                                PASS
```

Every Python/MSSQL implementation branch also requires:

```text
focused real SQL-auth cases                            PASS, zero skips
legacy RESULT-001..RESULT-015                         PASS
affected transaction/pool/timeout/lifecycle cases     PASS
stub/runtime export contract                           PASS
```

The exact final technical merge requires:

```text
complete scripts/sql_auth/run_all.sh                   PASS
vendored Tiberius fmt/clippy/unit gates                PASS
strict matrix                                          PASS, zero skips
async/framework/resilience/load lanes                  PASS, zero skips
original-local-regression lane                         PASS
1,000-stream bounded load and post-load smoke          PASS
RSS/backpressure evidence                              PASS
physical SPID retirement evidence                      PASS
wheel build and isolated install                       PASS
installed-wheel real SQL-auth cases                    PASS
raw Cargo and wheel on Linux/macOS/Windows             PASS hosted
RustSec                                                PASS hosted
secret/privacy scans                                   PASS
local/fork SHA parity                                  PASS
original-repository push URL                           DISABLED
```

Observed counts and memory/session metrics from the final exact SHA are
authoritative. The design does not forecast them as completed evidence.

## Live-audit update

Only after exact merged verification, a dedicated status branch updates
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` with:

- original first-result/buffered reproduction;
- the selected producer/channel architecture;
- every RED, feature and fix branch plus exact SHA;
- supported APIs and deliberate compatibility behavior;
- result-set, metadata, DONE, INFO, OUT and return-status evidence;
- bounded channel, RSS, pool and physical-SPID metrics;
- transaction, timeout and lifecycle outcomes;
- exact local Docker, wheel and hosted gate results;
- remaining TVP, MONEY, SQL_VARIANT, native bulk and framework-matrix work.

The audit checkbox for bounded streaming is marked complete only after the
memory and early-close gates pass. The checkbox for multiple/empty
resultsets and output parameters is marked complete only after RPC cases pass.

## Non-goals

This design does not:

- change existing `query()` into a breaking async stream;
- provide synchronous iteration over the new `ResultStream` or `ResultSet`;
- buffer a live result merely to preserve indexing, length or reset;
- reuse an early-closed physical connection without ATTENTION/DONE_ATTN;
- implement MARS or concurrent commands on one physical session;
- implement TVP, MONEY/SMALLMONEY fixed-point, SQL_VARIANT, spatial,
  hierarchyid, CLR UDT or legacy LOB conversion;
- provide chunked cell/LOB reads or a channel byte budget;
- implement native bulk copy or bounded input bulk iteration;
- close the dedicated FastAPI/Flask `ResultStream` integration matrix; the
  existing framework suite remains a regression gate and framework-specific
  streaming evidence stays open for its own audit branch;
- add dynamic SQL/procedure labels to metrics;
- add an ODBC fallback;
- bump package metadata, publish a wheel/release or claim overall enterprise
  production readiness;
- publish a Tiberius fork or external Tiberius pull request;
- write, push, open a pull request or publish anything in the original
  FastMssql repository.

## Self-review record

The design was checked against the live audit, `connection.rs`,
`transaction.rs`, `pool_manager.rs`, `helpers.rs`, `types.rs`,
`parameter_conversion.rs`, `py_parameters.rs`, both Python stub layers,
strict result/parameter/timeout/lifecycle tests, the vendored Tiberius client,
query stream, token decoder, RPC request encoder and Microsoft MS-TDS token
contracts.

Corrections made during self-review:

1. Replacing `QueryStream` in place was rejected because it would silently
   break indexing, length, reset and synchronous consumers.
2. A direct pyclass-held Tiberius stream was rejected because its client
   lifetime is self-referential.
3. The pool lease and lifecycle permit last through response EOF, not merely
   through request submission.
4. Early close retires the socket instead of pretending a dropped Tiberius
   stream is reusable.
5. Empty result-set metadata is built from COLMETADATA rather than the first
   row.
6. The API separates skipping one set from abandoning the entire response.
7. DONE row count uses DONE_COUNT, so absent and valid zero are distinct.
8. INFO tokens are caller-owned data and are not written to logs.
9. Output parameters are matched by ordinal/name rather than arrival order
   because MS-TDS may reorder large outputs.
10. Stored procedures use direct RPC ProcName encoding rather than generated
    `EXEC` SQL.
11. RETURNSTATUS is captured independently of an optional RETURN_VALUE
    descriptor.
12. The operation timeout remains one absolute response deadline; it does not
    restart per row.
13. Transaction abandonment fails the transaction and closes its transport
    instead of allowing commit on uncertain protocol state.
14. Row payload is bounded by the channel, while fixed caps also bound
    result-set, DONE and INFO metadata.
15. Existing operation-metric schema is preserved by mapping the new APIs to
    stable existing operation names.
16. Real MSSQL, RSS, DMV, installed-wheel and hosted OS gates are all required;
    unit tests alone cannot prove bounded production behavior.
17. The work is decomposed into protocol, streaming, lifecycle and RPC
    RED/feature pairs rather than one opaque patch.
18. Remaining type, bulk and framework work stays explicit and does not become
    an implied production-ready claim.
19. A bounded consumer-acknowledgement channel prevents protocol read-ahead
    from releasing a lease before Python row, metadata and output conversion
    succeeds.
20. Dropping an active result set retires the complete response because a
    destructor cannot asynchronously skip a result boundary.
21. Direct named RPC execution consumes a reset-bearing isolation-baseline
    batch before the RPC when a recycled session has RESETCONNECTION pending.
22. Column nullability and length semantics are exact, including unknown
    nullability, Unicode-code-unit versus byte capacities and the PLP `MAX`
    sentinel.
23. Procedure identifiers use one closed non-quoted grammar in the first
    release; batch punctuation is rejected even though the value is never
    interpolated into SQL.
24. Vendored Tiberius tracing is structural and never emits server message,
    row, output or procedure payloads.
25. One unacknowledged metadata look-ahead envelope preserves the next empty
    result set; a terminal summary is instead converted and acknowledged
    immediately when an inner iterator reaches final EOF.
26. Closing one result set never releases a transaction-session guard; only
    complete response EOF restores the transaction's prior state.
27. Procedure components and named RPC parameters use conservative ASCII
    identifier subsets with exact SQL Server and wire-length boundaries.
28. Terminal-operation semantics are closed: `finish()` may take over a
    non-busy active set and returns the summary, while an early async-context
    exit performs full fail-closed `aclose()`.
29. Vendored protocol behavior is split honestly between no-skip real Docker
    SQL-auth tests and database-independent unit/public API hosted gates;
    generic hosted runners never masquerade as SQL Server integration.
30. Trace privacy also covers COLMETADATA, ENVCHANGE and raw connection
    buffers, closing diagnostic paths that could bypass safe response reprs.
31. Privacy language distinguishes opaque SQL Server messages returned to the
    caller from driver-authored context, which must never copy or log them.
32. Metadata units now match SQL Server declarations: NCHAR/NVARCHAR use
    UTF-16 code units, CHAR/VARCHAR/binary use bytes, and temporal scale is
    never mislabeled as length.
33. Bounded streaming is stated as a strict event-count bound with measured
    fixed-payload RSS, not as an unsupported byte bound for arbitrarily large
    rows or LOBs.
34. Real SQL-auth probes exposed missing TABNAME/COLINFO handling and an
    SQL_VARIANT metadata panic; a separate RED/fix pair now precedes response
    work and guarantees typed unsupported errors without claiming conversion.
35. Token-safety RED now combines real SQL Server behavior, raw
    length-prefixed payload tests and scoped source contracts, so
    TABNAME/COLINFO dispatch is not accepted from a textual check alone.
36. The vendored Tiberius crate is a path dependency rather than a workspace
    member, so it receives its own fmt, clippy, unit and real SQL-auth lanes;
    root Cargo gates are not treated as coverage for those targets.
37. Isolated worktrees use the repository's actual shared
    `../../.venv` path for focused build/test commands; the plan does not
    assume an unprovisioned per-worktree virtual environment.
38. Terminal success/failure travels through a separate coalescing release
    channel, so a full bounded row queue cannot hide a timeout/error and
    public normal EOF waits until the physical lease disposition is final.
39. Stream metrics retain the existing outer observation boundary and include
    lifecycle admission plus pool/session acquisition rather than starting
    only after checkout.
40. Existing framework tests remain mandatory regressions, but the design
    does not relabel them as FastAPI/Flask evidence for the new streaming API;
    that integration matrix remains an explicit later audit item.
41. Response type names come from a total `TypeInfo` mapping rather than the
    legacy metadata `Display` implementation, whose unreachable fallbacks do
    not cover every valid type accepted by the decoder.
42. Producer panic containment and terminal publication are separated: the
    supervised body drops all TDS ownership first, then a single terminal
    failure is exposed, so an unexpected unwind cannot strand Python waiters.
43. SQL ERROR tokens are drained through their trailing DONE state before the
    stored server exception is propagated; they are not surfaced early as
    successful events or mislabeled as protocol uncertainty.
44. Successful protocol EOF does not override the existing
    `requires_connection_retirement` security decision: results succeed, but
    the classified physical session is still closed instead of reset/reused.
45. The same successful-retirement rule is explicit for transaction streams:
    the summary survives, but transport retirement transitions the
    transaction to `Failed` and prevents commit.
46. Stream stress evidence now separates admitted driver latency from
    scheduled task latency and records throughput, exact pool-wait deltas,
    client CPU and SQL-session CPU instead of reporting concurrency/RSS alone.
47. The required stress lane writes a fresh atomic RESULT-029 matrix record
    and SHA-bound metrics, while extended profiles use separate paths; a green
    lane can no longer leave the central case as `NOT RUN` or reuse stale data.
48. RESULT-029 also owns one non-load pytest evidence validator, run after the
    stress lane, satisfying the central one-case/one-node collector without
    executing the 1,000-operation profile twice.
49. Installed-wheel RESULT-030 remains a distinct external gate with import
    path, hash and real SQL-auth counts; an editable/source pytest node is not
    allowed to impersonate wheel evidence in the central matrix.
50. Every planned worktree is created from the primary checkout root, keeping
    branch isolation, ignored paths and the shared `../../.venv` reference
    deterministic instead of accidentally nesting worktrees.
51. A normal response whose physical session is deliberately retired remains
    successful to the caller; terminal waiting accepts `ReleasedRetired`
    without confusing it with an explicitly closed consumer state.
52. Reset after a fully drained nonfatal SQL error is now conversion-ACK
    gated, so a queued row that later fails Python conversion cannot race
    connection reuse.
53. The load harness uses fixed workers plus a bounded queue and an exact
    required RSS-growth limit, preventing the 99,999 profile from measuring
    unbounded Python task allocation instead of driver behavior.
54. Runtime/stub parity resolves package-adjacent stub files from the imported
    module, so hosted and isolated-wheel lanes inspect packaged artifacts
    instead of silently rereading only the repository copies.

The specification contains no placeholder, swallowed exception, unbounded
row buffer, arbitrary SQL procedure invocation or authorization for an
original-repository write.
