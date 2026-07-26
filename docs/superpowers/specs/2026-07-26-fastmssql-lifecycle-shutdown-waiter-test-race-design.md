# FastMssql Lifecycle Shutdown Waiter Test Race Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, plan and implementation self-review are
mandatory.

**Date:** 26 July 2026

**Source baseline:** `test/sql-auth-validation` at
`eaacc504be8582e99edab4fd05633458b8190984`

**Repository boundary:** every design, reproduction, fix, evidence and status
branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original repository
remains fetch-only and its push URL stays `DISABLED`.

## Objective

Make the Rust lifecycle unit gate deterministic without weakening the
production shutdown contract. The affected test is:

```text
lifecycle::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor
```

The intended scenario is:

1. an operation permit keeps the shared shutdown round open;
2. the first shutdown waiter starts that round and is cancelled;
3. two later waiters subscribe to the same round;
4. the permit is released;
5. both subscribed waiters observe the same graceful result.

The test must prove that cancellation of the first waiter does not cancel the
detached supervisor. It must not accidentally test a caller that begins only
after the round has already completed.

## Measured reproduction

The exact cumulative merge passed all SQL-auth, async, framework, resilience,
load and original-local-regression lanes. Its raw `cargo test --locked` gate
failed once:

```text
53 passed, 1 failed
assertion failed: waiter ... expect("shared shutdown must remain graceful")
```

The isolated test was then executed 100 times without source changes:

```text
91 PASS
9 FAIL
```

The terminology candidate changes no Rust, Cargo, lifecycle or dependency
file. The lifecycle test is unchanged from its original feature commit.

The assertion uses:

```rust
let second = tokio::spawn(lifecycle.shutdown(...));
let third = tokio::spawn(lifecycle.shutdown(...));
drop(permit);
```

`tokio::spawn` schedules a task but does not guarantee that it has been polled.
On the current-thread test runtime, the supervisor may consume the permit
drop, publish `Closed`, and finish before `third` executes `shutdown()`.
A caller entering after `Closed` correctly receives `Ok(false)` because it did
not participate in the completed round. The test incorrectly requires that
late caller to return `true`.

## Root-cause experiment

A temporary uncommitted diagnostic added a bounded wait for exactly two
`watch` receivers on the existing shutdown result sender before
`drop(permit)`.

The same isolated test then produced:

```text
200 PASS
0 FAIL
```

The diagnostic change was removed completely after the experiment. This
confirms that missing subscription synchronization, not runtime lifecycle
behavior, causes the nondeterminism.

## Approaches considered

### Approach A — wait for exact subscriber count, selected

Add a test-only helper that waits until
`shutdown_sender.receiver_count() == 2` with a one-second Tokio timeout.
Call it after spawning the second and third waiters and before releasing the
permit.

Advantages:

- observes the exact precondition the test claims;
- uses the real shutdown sender and real waiter tasks;
- remains bounded and produces a precise failure;
- changes no production code, public API or scheduler policy;
- is deterministic under current-thread and multi-thread scheduler timing.

### Approach B — one or more `yield_now()` calls

Rejected because yielding does not contractually prove that both spawned
tasks subscribed. Scheduler ordering could make the test flaky again.

### Approach C — accept `Ok(false)` from later waiters

Rejected because it would weaken the test. The test is supposed to prove that
two known subscribers coalesce into the shared round, not merely that a late
caller is harmless.

## Exact test-only implementation

Inside `#[cfg(test)] mod tests` in `src/lifecycle.rs`, add:

```rust
async fn wait_until_shutdown_waiters(
    lifecycle: &ConnectionLifecycle,
    expected_receivers: usize,
) {
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            let receivers = lifecycle
                .lock_inner()
                .shutdown_sender
                .as_ref()
                .map_or(0, |sender| sender.receiver_count());
            if receivers == expected_receivers {
                return;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("shutdown waiters must subscribe before work is released");
}
```

Use:

```rust
wait_until_shutdown_waiters(&lifecycle, 2).await;
drop(permit);
```

No production-visible method, field, synchronization point or timeout is
added. The helper may access private state because the Rust test module is a
child module of `lifecycle`.

## Separate reproduction branch

The RED/reproduction branch adds the opt-in script:

```text
scripts/test_lifecycle_shutdown_waiter_race.sh
```

It runs the exact isolated Rust test 200 times by default, stops on the first
failure, prints the captured cargo output and returns non-zero. The iteration
bound is configurable only through:

```text
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS
```

Accepted values are integers from 1 through 1,000. The default 200-iteration
baseline reproduction is expected to expose the measured race; its observed
result is recorded honestly rather than claimed as a mathematical guarantee.

The script remains outside `scripts/sql_auth/run_all.sh`; the normal full gate
runs the Rust suite once, while the opt-in stress gate exists for regression
verification and scheduler investigations.

## Acceptance gates

The fix passes only when:

```text
focused lifecycle test                         PASS
200-iteration waiter stress             200/200 PASS
all FastMssql Rust tests                   54/54 PASS
cargo fmt                                      PASS
Clippy -D warnings                              PASS
complete Docker/MSSQL runner                     PASS
strict / async / framework        340/340 + 16/16 + 33/33 PASS
resilience / load                     6/6 + 11/11 PASS
original-local-regression                  930/930 PASS
matrix                                       337/337 PASS
```

The expected strict count increases by one because the new Python runner
behavior test entered the terminology candidate before this test-race fix.
The original-local-regression lane excludes `tests/sql_auth_strict`, so its
count remains 930. The exact observed counts from the final run, not these
forecast values, are authoritative.

Hosted raw Cargo/Rust/wheel contracts must pass on Linux, macOS and Windows at
the final cumulative SHA. RustSec must remain green. Hosted CI does not run a
real MSSQL server; Docker SQL-auth evidence remains local.

## Branch topology

```text
docs/lifecycle-shutdown-waiter-test-race-design
  -> test/lifecycle-shutdown-waiter-race
     -> fix/lifecycle-shutdown-waiter-test-race
        -> test/sql-auth-validation
```

The reproduction script commit must be an ancestor of the test fix. The fix
is integrated separately before re-running the blocked terminology gate.

## Non-goals

This correction does not:

- change `ConnectionLifecycle::shutdown()` or its return values;
- add a production barrier, sleep, retry or scheduler yield;
- change graceful/force timeout defaults;
- change cancellation, pool, transaction or connection behavior;
- make late post-`Closed` callers report resources they did not close;
- modify dependencies, lockfile, version or release metadata;
- add the 200-iteration stress loop to the default test runner;
- write, push, open a PR or publish a release in the original repository.

## Self-review record

The design was checked against the complete shutdown implementation, watch
channel ownership, task scheduling, the original test and the exact failure
output.

Corrections made during self-review:

1. `yield_now()` alone was rejected because it does not prove subscription.
2. Accepting `false` was rejected because it weakens the coalescing contract.
3. The synchronization reads the existing receiver count and adds no
   production instrumentation.
4. The wait has a one-second timeout, so a future regression fails bounded
   with a specific message.
5. The repeated reproduction remains opt-in to avoid multiplying every normal
   Cargo gate.
6. Final acceptance includes the complete SQL-auth runner because the race was
   discovered there, not only an isolated Rust rerun.

The specification contains no placeholder, ambiguous runtime change,
unbounded loop or authorization for an original-repository write.
