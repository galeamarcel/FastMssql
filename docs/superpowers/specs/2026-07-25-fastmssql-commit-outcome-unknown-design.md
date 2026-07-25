# FastMssql `CommitOutcomeUnknown` Design

**Date:** 2026-07-25  
**Status:** approved by the standing enterprise-hardening mandate  
**Scope:** transaction COMMIT outcome classification, fail-closed connection
handling, public Python exception contract, and deterministic fault injection

## Problem

After a client sends `COMMIT`, losing the response does not prove either that
SQL Server committed or that it rolled the transaction back. Retrying COMMIT,
replaying the transaction, or reporting a normal connection failure can cause
duplicate or contradictory business effects.

FastMssql currently maps every COMMIT failure through the generic error
taxonomy and sets the transaction state to `Failed`. It retires an unsafe
pooled connection, but callers cannot distinguish:

- a deterministic SQL Server rejection;
- a local transaction-state error before any COMMIT operation;
- a transport, TLS, protocol, panic, or cancellation boundary where the final
  server outcome is unknowable.

The public context manager also treats every commit exception as a definite
failure and attempts rollback. That is unsafe after an unconfirmed COMMIT:
rollback cannot undo a commit that may already have completed.

## Goals

1. Expose an independent public `CommitOutcomeUnknown` exception.
2. Never report rollback or retry when the client lacks proof of the COMMIT
   outcome.
3. Retire the physical connection for every unknown outcome.
4. Preserve deterministic server errors and local precondition failures as
   their existing exception types.
5. Preserve the original transport/protocol exception as `__cause__`.
6. Apply the same semantics to pooled transactions and the direct compatibility
   constructor.
7. Reproduce a lost COMMIT acknowledgement deterministically against the real
   Docker SQL Server while TLS remains enabled.
8. Keep the feature independent of a public Tiberius fork.

## Non-goals

- Automatic retry of COMMIT or replay of the transaction.
- Application idempotency keys or an outbox/inbox schema.
- Distributed transaction recovery.
- TDS `ATTENTION`.
- Savepoints or isolation-level API changes.
- General timeout configuration.
- Publishing a FastMssql or Tiberius upstream PR.
- Treating `CommitOutcomeUnknown` as evidence that COMMIT succeeded.

## Alternatives

### A. Conservative classification at the FastMssql transaction boundary

Once a valid active transaction enters `Committing`, any non-deterministic
failure is classified as unknown. A non-fatal `SqlError` returned explicitly by
SQL Server remains deterministic. Transport, TLS, protocol, conversion,
internal, panic, missing-severity, and fatal server failures become
`CommitOutcomeUnknown`.

Advantages:

- fail-closed and safe for financial/business writes;
- small FastMssql-only API surface;
- no driver retry and no dependency publication;
- upstreamable independently from a Tiberius protocol API.

Trade-off: a failure during the write itself may be classified as unknown even
if no complete COMMIT request reached SQL Server. This conservatism is
intentional: a partial write or lost response cannot prove that the server did
not act.

### B. Extend Tiberius with request-phase telemetry

Tiberius could expose whether the COMMIT packet was encoded, partially written,
flushed, and fully acknowledged.

Advantages: richer diagnostics and fewer conservative unknown outcomes.

Disadvantages: larger protocol change, dependency coordination, and no complete
certainty after a partial socket write. It would block the FastMssql fix on an
unpublished dependency API.

### C. Application-level commit witness

Applications could write an idempotency key or outbox record in the
transaction, then query that witness after reconnecting.

Advantages: the strongest business-level reconciliation.

Disadvantages: requires application schema and domain-specific recovery. A
generic driver cannot impose it.

## Decision

Implement alternative A. Document alternative C as the recommended
application-level reconciliation pattern after catching the exception.
Alternative B can be proposed later as optional diagnostic enrichment, not as a
prerequisite.

## Public API

The native extension and package root export:

```python
class CommitOutcomeUnknown(Exception):
    message: str
    operation: str
    retryable: bool
    connection_discarded: bool
```

Every instance raised by the driver has:

```text
operation = "commit"
retryable = False
connection_discarded = True
```

The stable primary message is:

```text
COMMIT completion was not confirmed; the transaction outcome is unknown
```

The original exception is attached as `__cause__`. The primary message and
attributes do not include the SQL text, parameter values, username, password,
or connection string.

`CommitOutcomeUnknown` is an independent `Exception`, not a subclass of
`SqlConnectionError` or `SqlError`. This prevents generic connection-retry code
from accidentally treating the transaction as safe to repeat.

## Classification Rules

Classification happens only after all of these conditions hold:

1. the Rust transaction state is `Active`;
2. a physical connection is present;
3. `commit()` atomically changes the state to `Committing`;
4. the COMMIT operation begins.

Failures before those conditions retain their current types:

- no prior `begin()` -> `RuntimeError`;
- already committed/rolled back -> `RuntimeError`;
- concurrent or indeterminate state -> `RuntimeError`;
- missing physical connection before the in-flight transition ->
  `RuntimeError`.

After the transition:

| Result | Public outcome |
|---|---|
| response consumed completely | success; state `Committed` |
| `SqlError` with severity 0–19 | original `SqlError`; known server rejection |
| `SqlError` severity 20–25 | `CommitOutcomeUnknown` |
| `SqlError` without usable severity | `CommitOutcomeUnknown` |
| I/O, EOF, reset, TLS, protocol, conversion, routing or internal error | `CommitOutcomeUnknown` |
| driver panic | `CommitOutcomeUnknown` |
| Python cancels the future | `CancelledError` remains the task outcome; Rust state stays `Committing`, and `close()` retires the connection |

Cancellation cannot be transformed into another Python exception after the
future has been dropped. Its enterprise guarantee is therefore fail-closed
state and socket retirement. A later operation is rejected locally until
`close()`.

## State and Connection Handling

For an unknown COMMIT outcome:

```text
Active
  -> Committing
  -> response unavailable/unsafe
  -> connection marked Broken
  -> socket or pool lease removed
  -> state Failed
  -> raise CommitOutcomeUnknown
```

The connection is removed before control returns to Python. It can never be
reset and reused because reset cannot resolve an uncertain transaction
outcome. `close()` is idempotent and returns the object to `Idle` without
attempting rollback on the retired socket.

For a non-fatal deterministic `SqlError`, the socket is still removed from the
transaction object. The implementation does not assume the transaction remains
usable after a failed COMMIT.

## Context Manager Semantics

When `__aexit__` receives `CommitOutcomeUnknown` from automatic commit:

1. it does not call `rollback()`;
2. it does not retry COMMIT or replay statements;
3. it closes/retires the transaction object;
4. it propagates the same `CommitOutcomeUnknown` instance.

For other commit errors, the existing deterministic cleanup path remains
separate. Removing all historical best-effort cleanup exception suppression is
a distinct error-aggregation change and must not be bundled into this P0
candidate.

## Deterministic Fault Injection

The test suite uses an in-process transparent asyncio TCP proxy. It does not
decrypt or inspect TDS/TLS and adds no production dependency.

Test sequence:

1. Start the proxy on an ephemeral localhost port and forward to the Docker SQL
   Server port.
2. Connect FastMssql through the proxy with `SslConfig.development()`; verify
   the SQL Server session reports `encrypt_option=TRUE`.
3. Begin a transaction and insert a unique row.
4. Pause only the proxy's server-to-client direction.
5. Call `commit()`; client-to-server traffic continues.
6. Through a separate direct observer connection, poll until the inserted row
   becomes visible. Visibility proves SQL Server applied COMMIT.
7. Confirm the proxy holds downstream response bytes, then abort both proxy
   transports before those bytes reach FastMssql.
8. Assert `commit()` raises `CommitOutcomeUnknown`.
9. Assert the row exists exactly once, proving no transaction replay.
10. Assert the prior physical `connection_id` is retired and a pooled waiter or
    later query recovers on a different connection.

The test uses `sys.dm_exec_connections.connection_id`, not SPID, because SQL
Server may reuse a numeric SPID immediately.

The proxy has bounded waits and unconditional cleanup. A timeout is a test
failure, never a skip or swallowed exception.

## Test Matrix

Add strict cases:

- `TX-027`: the public exception exists and is independent from connection/SQL
  errors.
- `TX-028`: a pooled COMMIT applied by SQL Server with a withheld response
  raises `CommitOutcomeUnknown`, writes exactly once, retires the physical
  connection, releases pool backpressure, and exposes the stable
  non-retryable attributes.
- `TX-029`: the direct compatibility transaction produces the same typed
  unknown outcome and closes its socket.
- `TX-030`: automatic context-manager commit propagates the typed exception
  without rollback or retry. A recording core passed through the real
  `Transaction._from_rust` wrapper makes rollback/commit invocation counts
  observable without changing production code; the live proxy test separately
  proves the database outcome.
- `TX-031`: a non-fatal SQL Server rejection during COMMIT remains `SqlError`,
  not `CommitOutcomeUnknown`. The deterministic setup uses `XACT_ABORT ON` and
  a constraint violation to make SQL Server end the transaction, followed by
  `commit()` returning error 3902 at severity 16.

The matrix contract and SQL-auth specification must contain all five IDs.

## Branch and Commit Discipline

1. `docs/commit-outcome-unknown-design`
   - this design and the implementation plan;
2. `test/commit-outcome-unknown`
   - proxy fixture, TX-027–TX-031, matrix/spec updates;
   - demonstrate RED before production changes;
3. `fix/commit-outcome-unknown`
   - exception type, classification, state handling, wrapper and stubs;
4. `docs/commit-outcome-unknown-status`
   - live audit and upstream candidate evidence after all gates pass.

Every branch is pushed only to `galeamarcel/FastMssql`. The original repository
remains fetch-only and receives no PR without explicit approval for the final
candidate.

## Verification Gates

Minimum gates on the exact source tree:

- TX-027–TX-030 RED before the fix, TX-031 PASS as a deterministic control,
  and TX-027–TX-031 GREEN after the fix;
- all strict transaction tests;
- complete strict SQL-auth suite with complete matrix reporting;
- complete applicable upstream suite;
- pooled transaction stress at 10,000 and 99,999 operations;
- `cargo fmt --check`;
- `cargo test --locked`;
- `cargo clippy --locked --all-targets -- -D warnings`;
- `cargo audit --deny warnings`;
- Python compile and Ruff checks;
- `git diff --check`;
- no remaining application sessions from the fault proxy tests;
- no secret material in exceptions, logs, or tracked files.

## Acceptance Criteria

The feature is accepted only when:

- a real COMMIT can be proven applied while FastMssql raises
  `CommitOutcomeUnknown` because the acknowledgement was withheld;
- the exception is stable, public, non-retryable and causally chained;
- the affected socket is demonstrably retired;
- no automatic rollback or retry occurs after the unknown outcome;
- deterministic SQL Server COMMIT errors keep their original type;
- direct and pooled paths share the contract;
- existing transaction, strict and upstream regressions remain green;
- the live production-readiness audit records exact branches, commits, tests,
  limitations and remaining work.
