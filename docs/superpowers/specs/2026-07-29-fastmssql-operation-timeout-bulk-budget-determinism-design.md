# FastMssql Operation-Timeout Bulk-Budget Determinism Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, plan and implementation self-review are
mandatory.

**Date:** 29 July 2026

**Source baseline:** `feat/execute-many` at
`8df2abbb8076365455ed3691c1349b6cfcffdac6`

**Repository boundary:** every design, reproduction, fix, evidence and status
branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original repository
remains fetch-only and its push URL stays `DISABLED`.

## Objective

Make the existing SQL-auth contract `TIME-006` deterministic without
weakening what it proves:

- one `bulk_insert()` call uses one absolute operation deadline across all
  compatibility chunks;
- the first 1,000-row request completes;
- the second request begins but cannot complete before the shared deadline;
- a third request never begins;
- the timed-out transaction is rolled back and its physical connection is
  discarded with an unknown operation outcome;
- resetting the timeout independently for every chunk would still be detected.

The correction is test-only. It does not change a production timeout,
deadline boundary, conversion path, pool operation, SQL statement, public API
or error classification.

## Measured reproduction

The complete runner on the exact execute-many candidate reached the strict
lane and reported:

```text
423 passed
1 failed
```

The only failure was:

```text
tests/sql_auth_strict/test_operation_timeouts.py::
test_batch_and_bulk_share_one_absolute_operation_budget
```

The failing assertion expected two observed bulk request identities and
received one. The captured public error remained the intended typed
`OperationTimeoutError`:

```text
phase=operation
operation=bulk_insert
timeout_seconds=0.250000
retryable=false
discarded=true
outcome_unknown=true
```

An unchanged isolated rerun failed once and passed once. A bounded diagnostic
loop then failed on iteration four after three passes. At that failure:

```text
elapsed_seconds=0.25245849997736514
observed_bulk_request_identities=1
input_rows=4001
```

No execute-many contract failed. Generated runner artifacts were copied
outside the repository before tracked reports were restored, and the
execute-many worktree remained clean.

## Root cause

`TIME-006` was written when compatibility bulk conversion happened before the
awaited operation. Its original geometry is:

```text
trigger WAITFOR per chunk = 0.160 seconds
absolute operation budget = 0.250 seconds
headroom before the first request must finish = 0.090 seconds
```

The later bounded-buffering correction intentionally moved the following work
inside the awaited operation and after creation of the absolute deadline:

1. conversion and type inference for the first 1,000-row chunk;
2. lazy pool initialization;
3. pool checkout;
4. `BEGIN TRANSACTION`;
5. construction and TDS encoding of the first `INSERT ... VALUES` request.

This ordering is production behavior required by bounded conversion and the
single absolute deadline. Under ordinary local scheduling the work usually
fits in 90 ms, so the first request completes and the sampler observes the
second. Under a slower scheduling interval the first request begins but its
160 ms trigger delay reaches the 250 ms deadline before completion. The
runtime still enforces the correct absolute deadline, but the test's exact-two
precondition is no longer guaranteed by its original margin.

The failure is therefore a deterministic-contract defect in the test
geometry, not evidence that the production deadline resets per chunk and not
an execute-many runtime defect.

## Selected design

Retain the same real SQL Server trigger, 4,001-row input, five compatibility
chunks, independent DMV sampler and exact request-count assertion. Change only
the bulk timing geometry:

```text
trigger WAITFOR per chunk = 0.500 seconds
absolute operation budget = 0.850 seconds
pre-first-request headroom = 0.350 seconds
two complete trigger waits = 1.000 seconds
```

The inequalities intentionally differ:

```text
0.500 + 0.350 = 0.850
0.850 < 2 * 0.500
```

Consequently:

- the first request may spend up to 350 ms in conversion, pool setup,
  checkout, transaction start and wire preparation and still complete;
- after the first 500 ms trigger delay, the second request begins;
- even with zero setup overhead, two complete trigger delays require at least
  one second, which exceeds the 850 ms absolute deadline;
- the third request cannot begin;
- a reset-per-chunk implementation would still complete all five requests
  because each 500 ms trigger delay is below a fresh 850 ms budget.

The elapsed-time guard becomes:

```text
0.70 <= elapsed_seconds < 1.50
```

The lower bound detects an operation that times out before the intended
second-request phase. The upper bound remains deliberately wider than the
configured deadline to tolerate cancellation and cleanup scheduling while
still detecting an unbounded or reset-per-chunk execution.

## Approaches considered

### Approach A — widen the timing margins and keep exact two, selected

This preserves every semantic assertion while accommodating the bounded
pre-wire work now covered by the deadline. It changes no production code and
continues to distinguish one absolute budget from a per-chunk reset.

### Approach B — accept one or two observed requests

Rejected because `1 <= requests <= 2` would allow the test to pass without
proving that the second request started. That would weaken the explicit
cross-chunk deadline contract.

### Approach C — pre-connect the pool without changing the timing geometry

Rejected as the sole correction because it removes only most initialization
cost. First-chunk conversion, checkout, transaction start, SQL construction,
wire encoding and scheduler delay would still compete for the same 90 ms.
It would also make the proof depend on a warmed-pool precondition that the
production operation does not require.

### Approach D — add a production test hook or external SQL coordination gate

Rejected because the existing trigger and independent DMV observer already
exercise the real wire path. A new runtime hook would expand production
surface solely to stabilize a test, while a multi-connection coordination
protocol would add more failure modes than the bounded timing relation.

## Separate reproduction and fix

Branch topology:

```text
docs/operation-timeout-bulk-budget-determinism-design
  -> test/operation-timeout-bulk-budget-determinism
     -> fix/operation-timeout-bulk-budget-determinism
        -> feat/execute-many
```

The test branch adds an opt-in bounded runner:

```text
scripts/test_operation_timeout_bulk_budget_determinism.sh
```

It repeatedly runs the unchanged `TIME-006` node against Docker SQL Server
with SQL authentication. The default is 20 iterations; accepted overrides are
integers from 1 through 100. It exits at the first failure and prints the
captured pytest output. It remains outside the normal runner.

The fix branch changes only:

- the bulk trigger delay from 160 ms to 500 ms;
- the bulk operation timeout from 250 ms to 850 ms;
- the bulk elapsed guard from `0.18..0.75` to `0.70..1.50`;
- the explanatory comment;
- `VERSION.md`.

The batch half of `TIME-006`, the request sampler, row count, chunk count,
exact-two assertion, timeout metadata assertions and rollback proof remain
unchanged.

## Acceptance gates

The correction is accepted only when all of the following pass on the exact
fix commit:

```text
shell syntax and invalid-bound tests for the opt-in runner       PASS
focused TIME-006                                                PASS
opt-in TIME-006 repetition                              20/20 PASS
complete test_operation_timeouts.py                            PASS
complete SQL-auth strict lane                                  PASS
complete scripts/sql_auth/run_all.sh                            PASS
execute-many canonical matrix                           407/407 PASS
execute-many stress sync/async at 1,000/10,000/99,999           PASS
isolated ABI3 wheel and SQL-auth smoke                           PASS
Ruff, compileall, diff and credential scans                      PASS
code-review graph exact at candidate SHA                         PASS
```

Observed counts from the final run are authoritative. No failure, skip,
swallowed exception, stale editable extension or deselection of a required
case is accepted.

## Non-goals

This correction does not:

- change timeout configuration defaults or documented public semantics;
- move the absolute deadline or exclude conversion/setup work from it;
- change compatibility bulk chunk size, atomicity or SQL generation;
- change connection retirement, retryability or outcome-unknown policy;
- change execute-many behavior or claim that feature complete;
- add sleeps, retries or request-count tolerance to production code;
- publish a wheel, release, upstream PR or original-repository branch;
- alter the displayed package version `0.7.7`.

## Self-review record

The design was checked against the current `TIME-006` source, the current
compatibility bulk implementation, the bounded-buffering history and the
captured failing state.

Corrections made during self-review:

1. The exact-two request assertion is preserved; a one-or-two tolerance was
   rejected as semantically weaker.
2. The trigger delay and deadline were increased together so the first
   request gains headroom while two complete requests remain mathematically
   outside the absolute budget.
3. The deadline continues to include first-chunk conversion and pool setup;
   no warmed-pool exception is introduced.
4. The repeated gate is bounded and opt-in, so normal suite cost increases
   only by the single corrected `TIME-006` execution.
5. The correction requires the complete runner because that runner exposed
   the defect.
6. Runtime behavior and the execute-many candidate remain unchanged until
   the test-only fix is proven on a separate branch.

The specification contains no placeholder, ambiguous runtime change,
unbounded loop or authorization for an original-repository write.
