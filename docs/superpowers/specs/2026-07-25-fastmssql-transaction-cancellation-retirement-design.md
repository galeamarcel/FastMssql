# FastMssql Transaction Cancellation Retirement Design

**Date:** 2026-07-25

**Status:** Approved under the owner's standing authorization for enterprise
tests, reproductions and fixes on `galeamarcel/FastMssql`

**Scope:** Automatic fail-closed retirement after Python cancellation of an
in-flight `Transaction` operation

## Context

FastMssql already treats cancellation of ordinary pooled `Connection` methods
fail-closed. The operation guard is dropped with the Rust future, marks the
lease `Broken`, bb8 removes the physical connection, and SQL Server terminates
the request when the transport closes.

A live probe on the current cumulative source tree observed:

```text
Python result                    CancelledError
request cleanup                 0.001484 seconds
cancelled SQL session           closed
next pooled operation           different connection_id
```

The transaction path has a different ownership model. A `Transaction` stores a
direct socket or an owned pooled lease in
`Arc<AsyncMutex<TransactionSession>>`. Cancellation drops the operation future
and releases the mutex, but the connection remains owned by the transaction
object in an in-flight state. It is marked unsafe, yet it is not physically
dropped until the caller explicitly invokes `close()` or destroys the object.

The existing TX-026 contract therefore requires this sequence:

```text
cancel operation
  -> transaction becomes indeterminate
  -> pool waiter remains blocked
  -> caller invokes close()
  -> socket is retired
  -> waiter receives a replacement
```

That explicit-cleanup dependency is not a sufficient enterprise cancellation
boundary. A forgotten or delayed `close()` can leave the server request
running, retain a direct SQL session, and hold one pooled lease indefinitely.

## Protocol facts and design correction

MS-TDS defines `ATTENTION` as the cancellation message for a complete request.
After sending it, the client must discard response data until a `DONE` token
with `DONE_ATTN` acknowledges the cancellation:

- [MS-TDS 2.2.1.7 — Attention](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/dc28579f-49b1-4a78-9c5f-63fbda002d2e)
- [MS-TDS 4.19.2 — Out-of-Band Attention Signal](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/f8aa004e-bf76-4f94-bd5b-39597a888146)
- [MS-TDS 3.2.5.9 — Sent Attention State](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/61fb4f9d-95ec-4171-8d4b-dac4eb16149f)

The vendored Tiberius source already knows the `AttentionSignal` packet type
and `DONE_ATTN` bit, but exposes no cancellation state machine. Its token
decoders keep part of their progress in the currently-polled future. Dropping
that future in the middle of a token and then starting a fresh token parser
cannot be proven synchronization-safe.

Sending `ATTENTION` without preserving decoder progress, waiting for
`DONE_ATTN`, and enforcing a cancel deadline would be less safe than the
existing transport-close policy. It could return a desynchronized connection
to the pool.

SQL Server also documents that a broken connection terminates the query and
registers the cancellation internally as attention/error 3617:

- [MSSQLSERVER_3617](https://learn.microsoft.com/en-us/sql/relational-databases/errors-events/mssqlserver-3617-database-engine-error)

The P0 correctness contract is therefore refined:

- FastMssql must terminate the in-flight server request promptly;
- it must never reuse a connection whose token stream was abandoned;
- transaction state must remain fail-closed;
- pool capacity must recover without relying on user cleanup;
- transparent retry remains forbidden.

TDS `ATTENTION` plus safe same-session reuse is retained as a later
performance/lifecycle feature. It requires a cancellation-safe Tiberius
request state machine, `DONE_ATTN` draining and a cancel timer. It is not
implemented speculatively in this fix.

## Options considered

### Option A — Automatic socket retirement with an epoch guard

When a transaction operation enters an in-flight state, arm a Rust RAII guard
with a monotonically increasing operation epoch. If Python cancellation drops
the future before a terminal transition:

1. the guard verifies that its epoch is still current;
2. it marks the connection unusable;
3. it removes the direct socket or owned pooled lease from the session;
4. it sets the transaction state to `Failed`;
5. dropping the connection closes the transport, terminating the SQL request.

Cleanup first attempts the transaction mutex synchronously. If the mutex is
still held by the cancelled future during field destruction, cleanup is
scheduled on the initialized PyO3 Tokio runtime and runs immediately after the
lock is released.

**Advantages**

- preserves the proven fail-closed transport behavior;
- works for TLS without splitting or bypassing the Rustls state machine;
- recovers pooled capacity without an explicit `close()`;
- requires no speculative Tiberius fork or publication;
- keeps Python's standard `CancelledError`;
- epoch validation prevents delayed cleanup from closing a newer operation.

**Cost**

- the cancelled physical connection is not reused;
- reconnect/login cost occurs after cancellation;
- cancellation of `COMMIT` still requires business reconciliation because the
  server outcome may already be durable.

### Option B — TDS `ATTENTION` and same-socket reuse now

Add an attention packet writer and drain until `DONE_ATTN`, then return the
same connection to the pool.

**Rejected for this change.** The current decoder does not provide a
cancellation-safe resumable request state. Implementing only the packet write
would violate the protocol requirement to preserve and drain the response
correctly. A complete solution also needs request and cancel timers.

### Option C — Keep requiring explicit `close()`

Retain the current API and document that cancellation makes `close()`
mandatory.

**Rejected.** Delayed cleanup leaves a request and session active and can hold
the only lease in a saturated pool. Enterprise safety cannot depend on every
caller remembering a secondary cleanup call after cancellation.

## Selected architecture

Option A is selected.

### Transaction session epoch

`TransactionSession` gains:

```rust
operation_epoch: u64
```

Every transition into `Beginning`, `Executing`, `Committing` or
`RollingBack` increments the epoch under the same mutex that validates state
and owns the connection.

The epoch is not a public transaction identifier. It only prevents this race:

```text
operation A is cancelled
  -> cleanup task is scheduled
  -> caller closes and reuses the object
  -> operation B starts
  -> delayed cleanup A must not close operation B
```

Cleanup for A is allowed only when both conditions hold:

- `session.operation_epoch == A.epoch`;
- the state is still one of the in-flight states.

### Cancellation retirement guard

The guard owns:

```text
Arc<AsyncMutex<TransactionSession>>
armed_epoch: Option<u64>
```

Lifecycle:

```text
construct unarmed
  -> connect / acquire lease
  -> validate state under mutex
  -> mark connection operation unsafe
  -> increment epoch and enter in-flight state
  -> arm guard with epoch
  -> await TDS operation
  -> complete normal/error state transition
  -> disarm guard
```

If the future is cancelled at an `await`, the armed guard drops. Retirement is
idempotent and conditional on the captured epoch.

### Connection-specific behavior

For a pooled lease:

```text
mark_unusable
  -> take OwnedPooledConnection
  -> bb8 has_broken == true
  -> physical connection retired
  -> waiting checkout may create a replacement
```

For a direct transaction:

```text
take Client<Compat<TcpStream>>
  -> drop TLS/TCP transport
  -> SQL Server terminates request and session
```

In both cases, `close()` remains safe and idempotent after automatic
retirement.

## Public behavior

### Data-operation cancellation

Cancellation of `query`, `simple_query`, `execute`, `query_batch` or
`execute_batch`:

- returns `asyncio.CancelledError`;
- retires the physical connection without a separate user call;
- changes the Rust transaction state to `Failed`;
- rejects further use until `close()`;
- ends the server request and, for an open transaction, causes SQL Server to
  roll it back when the session closes.

### Transaction-command cancellation

Cancellation of `begin`, `commit` or `rollback` follows the same retirement
path.

For `commit`, cancellation is not evidence that the transaction rolled back.
The operation might already be durable. Python cancellation still wins and
surfaces `CancelledError`; the application must reconcile through an
idempotency/business key exactly as it would for `CommitOutcomeUnknown`.
FastMssql does not retry or issue a second settlement command.

### Wrapper flags

The Python wrapper only updates committed/rolled-back flags after the Rust call
returns successfully. On cancellation those flags remain unsettled. A later
`close()` resets them and is idempotent; it does not recover or reinterpret the
cancelled operation.

## Deterministic SQL-auth contracts

The strict specification grows from 274 to 277 IDs.

### TX-032 — pooled data-operation cancellation retires autonomously

1. Create `Connection(pool.max_size=1)`.
2. Begin transaction A and record SPID plus `connection_id`.
3. Start a tokenized `WAITFOR DELAY '00:00:10'`.
4. Confirm the request in `sys.dm_exec_requests`.
5. Start transaction B; verify its `begin()` is waiting.
6. Cancel A's query and assert `CancelledError`.
7. Do not call `A.close()`.
8. Assert the request and SQL session disappear within a bounded deadline.
9. Assert B begins within the same deadline on a different `connection_id`.
10. Assert A remains fail-closed until its later idempotent `close()`.

Baseline expectation: RED because B remains blocked until A is explicitly
closed.

### TX-033 — direct cancellation closes and rolls back autonomously

1. Begin a direct transaction.
2. Insert a row without committing.
3. Record SPID and `connection_id`.
4. Start and observe a tokenized `WAITFOR`.
5. Cancel it and assert `CancelledError`.
6. Do not call `close()`.
7. Assert request and session disappearance.
8. Assert `is_connected() is False`.
9. Assert an observer sees zero rows because session close rolled back.
10. Call `close()` twice to prove idempotence.

Baseline expectation: RED because the direct socket remains owned by the
transaction object.

### TX-034 — cancelled COMMIT retires without claiming rollback

1. Begin a pooled transaction through the transparent TLS proxy.
2. Insert a row and record `connection_id`.
3. Start a second transaction waiting for the only pool lease.
4. Pause downstream bytes and start `commit()`.
5. Confirm through an observer that the row is durable.
6. Confirm the proxy is holding the response.
7. Cancel the commit task and assert `CancelledError`.
8. Do not call `close()` on the cancelled transaction.
9. Assert the waiter receives a different `connection_id`.
10. Assert the row remains durable and no rollback/retry is issued.

Baseline expectation: RED because the cancelled transaction retains the lease
until explicit close.

## Verification

The fix is accepted only if all of these hold on the exact cumulative fork
tree:

```text
TX-032–TX-034                         PASS
request/session cleanup without close PASS
replacement connection_id             different
direct uncommitted row                 rolled back
cancelled durable COMMIT               remains durable
automatic retry/rollback               zero
strict SQL-auth IDs                    277/277
strict suite                           zero failures
applicable upstream suite              zero failures
Rust / fmt / Clippy / RustSec           PASS
pooled stress                          bounded at pool.max_size
origin                                 galeamarcel/FastMssql
upstream push                          DISABLED
```

## Out of scope

- public query/transaction timeout configuration;
- TDS `ATTENTION` and `DONE_ATTN` draining;
- same-socket reuse after cancellation;
- retry of reads or writes;
- conversion of `CancelledError` to `CommitOutcomeUnknown`;
- distributed transaction cancellation;
- Tiberius fork publication or upstream proposal;
- lifecycle/observability APIs beyond this cancellation boundary.

