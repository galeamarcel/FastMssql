# FastMssql Compatibility-Bulk Timeout Phase-Boundary Design

**Status:** corrected after second-audit self-review; approved for inline
execution by Marcel Galea's standing authorization. Specification, plan and
implementation self-review remain mandatory.

**Date:** 29 July 2026

**Source baseline:** `feat/execute-many` at
`8df2abbb8076365455ed3691c1349b6cfcffdac6`

**Repository boundary:** every design, reproduction, fix, evidence and status
branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original repository
remains fetch-only and its push URL stays `DISABLED`.

## Objective

Restore the approved phase boundary for the legacy compatibility
`Connection.bulk_insert()` path and make `TIME-006` prove it
deterministically:

- `acquire_timeout_secs` exclusively bounds lazy pool initialization,
  checkout and checkout validation/reset;
- `operation_timeout_secs` begins only after a lease is acquired and
  immediately before the first SQL request await;
- one operation deadline then covers `BEGIN`, every generated
  `INSERT ... VALUES` chunk and `COMMIT`;
- the deadline never resets per chunk;
- expiry after wire activity retires the connection, reports an unknown
  outcome and leaves zero committed rows.

This is a compatibility-bulk runtime correction plus a deterministic
real-MSSQL regression test. It is not merely a test-timing adjustment.

## Evidence progression

The complete execute-many gate first reported:

```text
SQL-auth strict: 423 passed, 1 failed
```

The only failure was canonical `TIME-006`. It observed one compatibility bulk
request where the test required exactly two. The captured public error
remained:

```text
OperationTimeoutError
phase=operation
operation=bulk_insert
timeout_seconds=0.250000
retryable=false
connection_discarded=true
outcome_unknown=true
```

An unchanged focused rerun failed once and passed once. A diagnostic loop
failed on iteration four after three passes. At the captured failure:

```text
elapsed_seconds=0.25245849997736514
observed_bulk_request_identities=1
input_rows=4001
```

The first hypothesis classified this only as stale 160/250 ms test geometry
after bounded conversion. That diagnosis was incomplete.

The second audit correlated the implementation with the approved timeout
specification and implementation plan. Both require:

```text
pool acquisition first
operation deadline created after checkout
one deadline around BEGIN, all bulk chunks and COMMIT
```

The current source instead creates the operation deadline before:

1. conversion of the first bounded chunk;
2. lazy pool initialization;
3. pool checkout;
4. creation of the pooled-operation guard.

The initial test-only correction is therefore superseded by this corrected
design. The published Git history remains intact; corrective documentation is
added through ordinary descendant commits.

## Contract conflict found by self-review

The foundational operation-timeout design states that no phase borrows unused
time from another and that the operation deadline starts after acquisition.
Its bulk implementation step likewise says to create the deadline after pool
checkout.

The later bounded-buffering plan moved first-chunk conversion into the awaited
operation to keep memory proportional to one chunk and first-chunk failures
zero-I/O. In the same edit it moved `deadline_from(Operation, ...)` before
conversion and acquisition, saying preflight should reduce the operation
budget. That instruction conflicts with the already approved public phase
boundary and with the separate acquire timeout.

The conflict is resolved as follows:

- preserve bounded one-chunk conversion and first-chunk zero-I/O validation;
- preserve lazy pool initialization and normal acquire-timeout handling;
- restore the compatibility API's foundational post-checkout operation
  deadline;
- keep later-chunk conversion inside that one deadline because it occurs
  between SQL chunks;
- add a deterministic test that prevents acquire wait from consuming the
  operation phase again.

This resolution is deliberately scoped to compatibility
`Connection.bulk_insert()`.

Native bulk iterable and `execute_many()` are not silently changed here.
Their later approved API designs explicitly define one public-method or
sequence deadline covering producer work and acquisition. Those distinct
contracts require their own design change if they are ever reconsidered.

## Root cause

Commit `adb66370456856a88e320d83c247704f6951b11e` correctly replaced eager
whole-input conversion with one bounded chunk. Before that commit,
compatibility bulk performed:

```text
pre-convert input
initialize/acquire pool
create operation deadline
BEGIN -> chunks -> COMMIT
```

After that commit it performs:

```text
create operation deadline
convert first chunk
initialize/acquire pool
BEGIN -> chunks -> COMMIT
```

The first-chunk conversion move is required. The deadline move is not.

With a 250 ms operation budget and 160 ms trigger delay, only 90 ms remains
for all work preceding completion of the first request. A cold or delayed
pool path can consume enough of that operation budget that the first request,
not the second, reaches the deadline. More importantly, a saturated pool can
consume the entire operation deadline while still remaining within its
separate acquire budget. The method then acquires a lease and immediately
raises a phase-`operation` timeout before application SQL begins.

That behavior violates phase separation and can discard a healthy lease even
though the operation phase should not have started.

## Deterministic RED contract

Extend the bulk half of existing canonical `TIME-006`; do not allocate a new
matrix ID.

Use:

```text
pool max_size                 1
acquire timeout               2.0 seconds
operation timeout             0.85 seconds
trigger delay per chunk       0.50 seconds
input rows                    4,001
compatibility chunk size      1,000
total potential requests      5
```

The test:

1. begins a holder Transaction on the same size-one pool;
2. starts the real `bulk_insert()` task and the independent DMV sampler;
3. waits until `pool_stats()["pending_gets"] == 1`, proving conversion has
   completed and the bulk call is blocked in checkout;
4. keeps the lease occupied for 0.95 seconds, longer than the 0.85 second
   operation budget but shorter than the 2.0 second acquire budget;
5. proves the bulk task is still pending, then rolls back/closes the holder;
6. awaits the bulk timeout;
7. requires exactly two observed bulk requests, typed fail-closed timeout
   metadata and zero rows after rollback.

On the unchanged implementation, the operation deadline is already expired
when checkout finishes. The call therefore submits zero bulk requests and the
exact-two assertion fails deterministically.

After the fix, the operation deadline begins only after checkout:

- the first 500 ms request completes;
- the second begins;
- two complete trigger waits need at least one second and cannot fit into the
  shared 850 ms operation budget;
- the third never begins;
- a reset-per-chunk implementation would allow all five requests because each
  500 ms delay fits within a fresh 850 ms timer.

Measure the post-release operation phase separately and require:

```text
0.70 <= operation_phase_elapsed_seconds < 1.50
```

This avoids treating the intentional 0.95 second acquire wait as operation
time while retaining bounded cleanup tolerance.

## Runtime correction

In `src/batch.rs::bulk_insert`, keep:

```text
first-chunk conversion
pool initialization
pool checkout
PooledOperationGuard construction
```

in their current order. Move only:

```rust
let deadline =
    deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
```

from before first-chunk conversion to immediately after
`PooledOperationGuard::new(pooled)` and before `run_until(...)`.

Do not move conversion after checkout. First-chunk shape/type errors must
remain local, zero-I/O and zero-lease. Do not create another deadline for
later chunks. The existing loop continues to convert each later chunk inside
the original `run_until` future.

## Approaches considered

### Approach A — restore post-checkout deadline and add saturated-pool RED,
selected

This matches the approved phase contract, proves it deterministically and
keeps bounded conversion, atomicity, retirement and exact-two semantics.

### Approach B — widen only the trigger/deadline timing

Rejected after the second audit. It would make the intermittent test pass
while leaving acquire wait charged to the operation phase.

### Approach C — accept one or two requests

Rejected because it would stop proving that the second request begins and
would hide both phase-boundary regression and per-chunk deadline reset.

### Approach D — pre-connect without saturating the pool

Rejected because it avoids the violated boundary instead of proving it.
It would make the test dependent on a warmed pool and could not distinguish
whether acquire wait consumes operation time.

### Approach E — move first-chunk conversion after checkout

Rejected because first-chunk conversion errors would acquire a lease
needlessly and break the established zero-I/O preflight contract.

## Branch topology

```text
docs/operation-timeout-bulk-budget-determinism-design
  -> test/compatibility-bulk-operation-deadline-boundary
     -> fix/compatibility-bulk-operation-deadline-boundary
        -> feat/execute-many
```

The test branch adds the deterministic saturated-pool extension and the
bounded opt-in repeated runner. The fix branch changes the single runtime
deadline placement and `VERSION.md`. Both branches are published only on the
fork.

## Acceptance gates

The correction is accepted only when all of the following pass on one exact
fix commit:

```text
unchanged runtime + saturated-pool TIME-006                     RED
focused corrected TIME-006                                     PASS
opt-in TIME-006 repetition                             20/20 PASS
complete test_operation_timeouts.py                            PASS
compatibility bulk bounded/descriptor suites                    PASS
complete SQL-auth strict lane                                  PASS
complete scripts/sql_auth/run_all.sh                            PASS
execute-many canonical matrix                           407/407 PASS
execute-many stress sync/async at 1,000/10,000/99,999           PASS
isolated ABI3 wheel and SQL-auth smoke                           PASS
root/vendored Rust, Ruff, compileall and diff checks             PASS
exact code-review graph and direct source review                 PASS
credential/generated-artifact/fork-boundary scans                PASS
```

Observed final counts are authoritative. No required failure, skip,
deselection, swallowed exception, stale editable extension or ancestor-only
result is accepted.

## Non-goals

This correction does not:

- change timeout defaults or public exception fields;
- change the separate acquire timeout implementation;
- change compatibility chunk size, SQL generation or transaction atomicity;
- remove later-chunk conversion from the one absolute operation budget;
- change native bulk iterable or execute-many deadline semantics;
- add a retry, sleep or tolerance to production code;
- publish a wheel, release, upstream PR or original-repository branch;
- alter displayed package version `0.7.7`.

## Self-review correction record

The corrected design was checked against:

- the foundational timeout specification and implementation plan;
- the bounded compatibility-bulk plan and commit `adb6637`;
- current `src/batch.rs`;
- current pool observability counters;
- the exact full-runner and diagnostic failure states;
- explicit later native-bulk iterable and execute-many deadline contracts.

Corrections made:

1. The first test-only diagnosis was withdrawn before any test or runtime fix
   commit.
2. The conflicting approved documents are surfaced explicitly rather than
   blended silently.
3. The foundational acquire/operation phase boundary remains authoritative
   for compatibility bulk.
4. The RED test uses `pending_gets == 1`, not scheduler sleeps, to prove the
   call reached checkout before the controlled hold begins.
5. The acquire hold exceeds the operation timeout but remains below the
   acquire timeout, making the old boundary fail deterministically.
6. Exact-two requests, five-chunk reset detection, fail-closed metadata and
   rollback remain mandatory.
7. Native bulk iterable and execute-many are excluded because their later
   approved designs explicitly choose broader sequence deadlines.

The specification contains no placeholder, ambiguous phase ownership,
unbounded loop or authorization for an original-repository write.
