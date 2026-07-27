# FastMssql Checkout Reset and First-Statement DDL Design

**Status:** Approved by Marcel Galea on 2026-07-28 through the standing
approval for future enterprise designs, specifications, plans, tests and
inline implementations, subject to self-review

**Source baseline:** `docs/native-bulk-insert-status` at
`53ee12628eed3ad0e22561088ef78561f9e0948c`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and validation produced by this
candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only with push URL `DISABLED`.
No upstream branch, push, pull request, package publication or dependency fork
is authorized by this design.

## Decision summary

FastMssql will complete every required pooled-session reset before returning a
lease to application SQL, even when
`PoolConfig(test_on_check_out=False)`.

`test_on_check_out=False` will continue to mean that FastMssql does not issue
an optional liveness probe. It will not disable the mandatory security and
session-isolation reset between borrowers.

The internal bb8 checkout hook will always run so that reset remains inside
bb8's acquisition timeout and cancellation-safe lease wrapper. The connection
manager will distinguish the optional health policy from the mandatory reset:

| Connection state | Requested checkout probe | Checkout action |
| --- | --- | --- |
| `Clean` | disabled | no wire request |
| `NeedsReset` | disabled | one private reset control request, fully drained |
| `Clean` | enabled/default | one health request, fully drained |
| `NeedsReset` | enabled/default | reset piggybacked on the health request, fully drained |
| `Broken` | any | reject and retire |

The vendored Tiberius client will gain an additive immediate-reset primitive.
It will send the TDS `RESETCONNECTION` bit with the isolation-level baseline in
a private SQL batch and consume the complete response. Application SQL will
therefore remain byte-for-byte separate and may begin with `CREATE TRIGGER`,
`CREATE PROCEDURE`, `CREATE FUNCTION`, `CREATE VIEW`, or any other statement
whose SQL Server grammar requires a pristine batch.

This deliberately adds one private round trip for a reused lease when the
optional checkout probe is disabled. The default and explicit
`test_on_check_out=True` paths retain their existing single combined
reset/health round trip. Correct session isolation takes priority over the
unsafe zero-round-trip optimization.

## Problem

The current pool state machine correctly marks a fully synchronized physical
connection as `NeedsReset` after an application operation. On its next lease,
`ManagedConnection::prepare_for_checkout()` arms Tiberius so the first Batch or
RPC request carries TDS `RESETCONNECTION`.

TDS does not reset transaction isolation level. The vendored Tiberius client
therefore prepends this baseline to the first request:

```sql
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
```

With bb8's default checkout validation, `AzureConnectionManager::is_valid()`
is the first request. It safely carries and consumes the reset before the
application request.

When a caller sets `PoolConfig(test_on_check_out=False)`, FastMssql forwards
that value to `bb8::Builder::test_on_check_out(false)`. bb8 then bypasses
`ManageConnection::is_valid()` entirely. `PooledOperationGuard::new()` arms the
pending reset, and the next application request becomes:

```sql
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
CREATE TRIGGER ...
```

SQL Server requires `CREATE TRIGGER` to be the first statement in its batch,
so the otherwise valid DDL is rejected. The same architecture can affect other
module DDL with a first-statement or batch-isolation rule.

The failure is configuration-dependent:

- default `test_on_check_out=None` inherits bb8's `true` default and passes;
- explicit `test_on_check_out=True` passes;
- explicit `test_on_check_out=False` exposes the reset prefix to application
  SQL and fails;
- the physical connection and DDL are otherwise valid.

This is not a native-bulk defect. It was discovered while validating native
bulk because that suite intentionally disables the optional health probe.

## Verified external contracts

The design was checked against the authoritative sources on 2026-07-28:

- bb8 0.9.1 documents `test_on_check_out=true` as the default and calls
  `ManageConnection::is_valid()` before returning a connection when enabled;
- bb8 0.9.1 wraps the complete checkout loop, including `is_valid()`, in
  `connection_timeout`;
- MS-TDS 2.2.3.1.2 requires `RESETCONNECTION` on the first request packet and
  explicitly states that isolation levels are not reset;
- SQL Server documents that `CREATE TRIGGER` must be the first statement in
  its batch.

Primary references:

- [bb8 0.9.1 `Builder`](https://docs.rs/bb8/0.9.1/bb8/struct.Builder.html)
- [bb8 0.9.1 source `PoolInner::get`](https://docs.rs/bb8/0.9.1/src/bb8/inner.rs.html)
- [MS-TDS packet status and RESETCONNECTION](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce398f9a-7d47-4ede-8f36-9dd6fc21ca43)
- [SQL Server `CREATE TRIGGER`](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-trigger-transact-sql)

The local dependency source for exact bb8 0.9.1 behavior remains the
deterministic implementation reference for this repository.

## Goals

1. Never prepend driver-owned SQL to an application batch.
2. Preserve complete cross-lease reset of transactions, session state,
   security context and transaction isolation.
3. Preserve `test_on_check_out=False` as an opt-out from the optional health
   probe, not from mandatory session isolation.
4. Keep reset wait, failure and cancellation inside the pool-acquisition
   phase and its configured deadline.
5. Retire a physical connection if immediate reset is cancelled, times out,
   panics, returns a protocol/I/O error, or cannot be fully drained.
6. Preserve one combined reset/health request for the default and explicit
   health-enabled paths.
7. Cover every pool acquisition path: regular operations, result streams,
   batch APIs, native bulk, pooled transactions, readiness and warm-up.
8. Add deterministic unit, exact-wire, real Docker SQL-auth, timeout,
   cancellation, pool-recovery and isolated-wheel evidence.
9. Preserve public API compatibility and the displayed package version
   `0.7.7`.

## Non-goals

This candidate does not:

- make session reset optional;
- add a SQL parser or classify which DDL needs a pristine batch;
- rewrite module DDL through dynamic SQL;
- silently enable the optional health probe when the caller disabled it;
- retry application SQL;
- add TDS MARS, ATTENTION, distributed-transaction reset, or session recovery;
- alter direct non-pooled transaction ownership;
- add a public reset method to FastMssql's Python API;
- change pool sizing, queue order, min-idle, retry policy or lifecycle policy;
- change SQL-auth credentials, Docker topology or database schema;
- bump or publish the package version;
- publish a Tiberius fork or push anything to the original FastMssql
  repository.

## Options considered

### Option A — Mandatory private reset in the internal checkout hook

Run the bb8 hook for every checkout. Let the manager perform no request for a
clean connection when health probing is disabled, perform a private immediate
reset for `NeedsReset`, and retain the combined reset/health request when
health probing is enabled.

**Selected.** This keeps the acquisition timeout and cancellation behavior
coherent, does not parse or mutate application SQL, preserves optional health
semantics, and gives one fail-closed invariant for every API.

### Option B — Detect first-statement DDL and reset separately only for it

Inspect SQL text and use a private reset only when the leading tokens look
like module DDL; otherwise retain the zero-round-trip piggyback.

**Rejected.** SQL Server has multiple current and legacy module forms,
comments and dynamic execution complicate classification, and a false
negative recreates the correctness defect. A driver should not need a
partial T-SQL parser to decide whether its own hidden prefix is safe.

### Option C — Wrap affected DDL in dynamic SQL

Transform a module definition into `EXEC(...)` or `sp_executesql` so the
definition begins a nested batch.

**Rejected.** It changes parameter, scope, permission, error, result and
auditing semantics; it also requires escaping arbitrary application SQL.

### Option D — Retire every `NeedsReset` connection when probing is disabled

Close the physical connection and authenticate a replacement rather than
resetting it.

**Rejected.** It is safe but defeats persistent pooling, increases TLS/login
load and latency, and creates connection churn under concurrency.

### Option E — Force the existing health query and ignore the public setting

Always execute the current validation query.

**Rejected.** It makes `test_on_check_out=False` misleading and performs an
optional health probe the caller explicitly disabled. Mandatory reset and
optional validation are separate policies.

## Public behavior

No Python signature or exported symbol changes.

`PoolConfig.test_on_check_out` retains these values:

- `None`: use the default health-enabled behavior;
- `True`: validate each checkout;
- `False`: skip the optional health probe.

The documentation will clarify that all three values still enforce mandatory
cross-lease session reset. Disabling the optional probe cannot expose a prior
borrower's state to a later borrower.

Observable behavior changed by this bugfix:

- first-statement DDL succeeds after a reused lease with
  `test_on_check_out=False`;
- application SQL no longer includes a hidden reset prefix;
- reused leases with the optional probe disabled incur one reset round trip;
- a failed reset is handled as checkout invalidation before application SQL,
  not as an application-operation failure.

## Internal architecture

### Separate reset policy from health policy

`AzureConnectionManager` will store the effective optional health policy:

```text
pool_config.test_on_check_out.unwrap_or(true)
```

The bb8 builder will always enable its internal checkout hook. This is an
implementation mechanism, not a change to the public health policy. It is
required because bb8 0.9.1 includes `ManageConnection::is_valid()` inside the
same `connection_timeout` future and protects the checked-out connection with
its cancellation-safe `PooledConnection` before awaiting the hook.

### Checkout action matrix

The manager will choose an explicit action before any await:

```text
Broken
  -> return an error; bb8 retires the connection

Clean + health disabled
  -> return success without wire I/O

NeedsReset + health disabled
  -> mark Broken
  -> execute immediate Tiberius reset control request
  -> drain the entire response
  -> mark Clean only after success

Clean + health enabled
  -> mark Broken
  -> execute and drain the health request
  -> mark Clean only after success

NeedsReset + health enabled
  -> arm RESETCONNECTION on the health request
  -> mark Broken
  -> execute and drain the health request
  -> mark Clean only after success
```

`Broken` is the state during every wire await. Cancellation therefore cannot
return an uncertain connection to bb8. `has_broken()` remains the final
synchronous retirement gate.

### Immediate Tiberius reset

The vendored client will add an additive Rust method:

```rust
pub async fn reset_connection(&mut self) -> crate::Result<()>
```

It will:

1. flush any fully consumable prior stream state;
2. reset client-side transaction descriptor, metadata and collation state;
3. arm TDS `RESETCONNECTION`;
4. send the existing `READ COMMITTED` isolation baseline as a private Batch
   request;
5. consume all response tokens through terminal DONE/error;
6. return only after the request is complete.

The method will reuse a private helper shared with the named-RPC reset path.
It will not expose or accept SQL text and will not return a result set.

The existing `reset_connection_on_next_request()` API remains available for
Tiberius callers that intentionally want piggyback behavior. FastMssql will
use it only when the first request is its own health request, never when the
first request could contain application SQL.

### Acquisition invariant

After `Pool::get()` or `Pool::get_owned()` returns to FastMssql:

```text
ManagedConnection.disposition == Clean
Tiberius pending RESETCONNECTION == false
no reset response remains unread
no driver-owned prefix can reach application SQL
```

`PooledOperationGuard::new()` and pooled
`TransactionConnection` will no longer arm a reset after bb8 returns the
lease. They may rely on the acquisition invariant and keep their current
fail-closed operation guards.

### API path coverage

The invariant applies without per-method SQL classification because every
pooled path acquires through bb8:

- `Connection.query`, `simple_query`, `execute`, `ping` and readiness;
- `Connection.stream`, `batch` and `callproc`;
- `query_batch`, `execute_batch` and compatibility bulk;
- native TDS bulk;
- pooled transaction acquisition before `BEGIN`;
- pool warm-up and min-idle checkout.

Direct standalone transactions do not reuse a cross-borrower lease and remain
unchanged.

## Timeout, cancellation and error semantics

The operation-timeout specification defines pool acquisition as ending only
after pending reset/validation completes. The implementation will satisfy
that contract through bb8 itself:

- `connection_timeout` starts immediately before the checkout loop;
- the loop wraps queue wait, physical connection creation and the forced
  internal `is_valid()` hook;
- a stalled private reset therefore raises the existing typed acquire-phase
  timeout;
- application SQL has not started, so `outcome_unknown=false`;
- the reset-in-progress connection is `Broken` and is discarded;
- FastMssql does not retry application SQL.

If reset returns an ordinary connection error before the deadline, bb8 marks
that connection invalid and may obtain another connection under its existing
retry/acquisition policy. If no safe lease becomes available before the
deadline, the checkout times out normally.

External Python cancellation, lifecycle force-close, Rust future drop and
panic all retain the existing fail-closed rule. No path marks the connection
clean in a destructor or cancellation handler.

## Observability and privacy

The private reset:

- remains part of the logical operation's acquisition latency;
- remains inside bb8 get counters and timeout accounting;
- increments bb8 invalid/broken close counters when applicable;
- does not increment a separate public SQL-operation count;
- does not log application SQL, reset SQL, parameters, credentials or
  connection strings;
- does not expose the private control request as a result set.

The benchmark report will distinguish reset overhead from SQL Server capacity
and will compare the enabled/default and disabled optional-probe policies.

## Test strategy

### Separate branch discipline

The implementation sequence is:

1. this approved design branch;
2. a RED reproduction branch containing no runtime fix;
3. a fix branch that contains the RED branch in its ancestry;
4. a documentation/status branch after all gates pass.

Every branch is pushed only to the fork.

### RED SQL-auth contracts

With one physical SQL-auth connection and
`PoolConfig(test_on_check_out=False)`:

1. perform a completed operation so the lease becomes `NeedsReset`;
2. prove the same SPID is reused;
3. submit `CREATE TRIGGER` as the first application statement;
4. require successful creation and trigger side effects;
5. repeat with representative procedure, function and view module DDL;
6. require cleanup and zero leaked sessions.

The principal pre-fix RED is SQL Server rejecting the module definition
because the hidden isolation baseline precedes it.

### Session-isolation contracts

The fix must also prove that disabling the optional health probe does not
weaken reset:

- same physical SPID after cross-lease reset;
- local transaction rolled back;
- isolation restored to `READ COMMITTED`;
- database/language/date/ANSI/session context and local temp state restored;
- application module DDL remains unmodified and succeeds immediately after
  contamination.

### Tiberius contracts

Vendored tests will cover:

- immediate reset emits a dedicated reset request and drains it;
- the next application query is not prefixed;
- default piggyback behavior remains unchanged;
- named RPC does not perform a duplicate reset;
- reset send/receive errors remain visible;
- exact packet status retains `RESETCONNECTION | EOM` on a single packet.

A direct real SQL-auth Tiberius test will prove same-SPID reset, restored
isolation/session state, and first-statement DDL on the next request.

### Failure and deadline contracts

Using the existing fault-proxy infrastructure where deterministic:

- stall/drop the reset control response;
- require an acquire-phase timeout or connection error before application SQL;
- require retirement of the uncertain physical session;
- require a replacement connection and post-fault smoke query;
- require no duplicate DDL execution and no leaked sessions.

### Compatibility and performance gates

Required gates include:

- focused Rust and vendored-Tiberius tests;
- full root and vendored Rust suites;
- root and vendored `cargo fmt --check`;
- root and vendored Clippy with warnings denied;
- focused and complete strict SQL-auth suites;
- connection, pool, batch, result-stream, transaction, native-bulk and
  framework regressions;
- original local regression suite;
- Ruff, compile/import and isolated-wheel SQL-auth tests;
- source/diff/credential checks;
- graph rebuild, change detection, affected flows and coverage review.

Performance profiles will use real Docker SQL Server and SQL authentication:

- 1,000 operations as the mandatory baseline;
- 10,000 operations as the standard sustained profile;
- 99,999 operations only through the already approved explicit stress gate;
- bounded concurrency and pool size;
- enabled/default versus disabled optional-probe policy;
- exact success/failure counts, QPS, p95/p99, RSS growth, event-loop progress,
  SPID bounds, recovery smoke and teardown session count.

The expected additional reset round trip for reused leases with the optional
probe disabled is a documented semantic cost, not hidden as a regression.
Unexpected connection churn, unbounded memory, event-loop starvation, reset
duplication or session leakage remains a failure.

## Documentation changes

After verification:

- clarify `PoolConfig.test_on_check_out` in the stub and README;
- update the live production-readiness audit from
  `OPEN_REPRO_REQUIRED` to the exact RED/fix evidence;
- update the session-reset section to replace the universal
  “no additional round trip” statement with the policy-dependent behavior;
- add exact commits, test counts, stress measurements and remaining risks;
- update `VERSION.md` without changing displayed `0.7.7`;
- update the upstream PR roadmap only as a future candidate description,
  without creating or pushing an upstream PR.

## Success criteria

The candidate is complete only when:

1. real SQL-auth first-statement DDL passes with
   `test_on_check_out=False` after a reused and contaminated lease;
2. the same tests are demonstrably RED on the source baseline;
3. application SQL is never prefixed by the reset baseline;
4. all session-isolation invariants still pass on the same physical SPID;
5. reset failure/cancellation is fail-closed and occurs before application
   SQL;
6. acquire timeout metadata remains correct;
7. the default/true path retains one combined reset/health request;
8. the false path performs no optional health request on a clean connection;
9. all focused, compatibility, wheel, SQL-auth and quality gates pass;
10. the 1,000/10,000 profiles pass and the explicitly enabled 99,999 profile
    is recorded if run;
11. the graph is rebuilt and the final diff is self-reviewed;
12. every commit is pushed exclusively to Marcel Galea's fork and the
    original repository remains push-disabled.

## Residual risks

- A reused lease with optional health disabled now costs one private reset
  round trip. The performance report must quantify this cost.
- bb8 exposes only one hook named `is_valid`; FastMssql will use it for both
  mandatory reset and optional health policy. Internal comments and tests
  must preserve that distinction.
- Tiberius remains vendored. A future upstream FastMssql proposal must either
  include an accepted Tiberius primitive or retain a reviewed dependency
  strategy; no such publication is part of this candidate.
- Distributed transaction state is outside ordinary `RESETCONNECTION` and
  remains unsupported.
- A process or network failure can always interrupt reset. Retirement, not
  same-socket recovery, is the safe fallback.
