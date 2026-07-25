# FastMssql TCP Fault Proxy Client Reset Design

**Status:** Approved by Marcel Galea on 2026-07-25

**Source baseline:** `test/sql-auth-validation` at
`5a1369518456eabe90ac078653d42b6b2f38bdc1`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and validation produced by
this candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only; no upstream push, branch,
pull request or publication is authorized.

## Decision summary

The SQL-auth TCP fault proxy will distinguish an explicitly declared
client disconnect from an unexpected transport failure.

`DownstreamGateProxy.expect_client_disconnect()` will grant exactly one
pending client-to-proxy disconnect allowance. The allowance is consumed by
either:

1. EOF returned by the client-side `StreamReader.read()`; or
2. `ConnectionResetError` raised by that same client-side read.

Only the client-to-server relay may consume the allowance. It cannot suppress:

- an undeclared client reset;
- a server-to-client reset;
- a failure opening the upstream SQL Server connection;
- an error writing or draining either relay;
- any non-reset exception.

`TX-034` will declare the expected client disconnect immediately before it
cancels the in-flight `COMMIT`. This models the existing FastMssql
cancellation contract without globally swallowing failures or retrying a
flaky test.

This candidate changes test infrastructure only. It does not change the
FastMssql Rust core, Python runtime API, transaction state machine or SQL
Server behavior.

## Discovery context

The full local enterprise gate for the approved PoolConfig consistency
candidate ran at:

```text
b5239c715934ab40e0aefc0b2a8a8fac10c7f869
```

Every PoolConfig-focused gate passed, including:

```text
Rust unit tests             14/14
PoolConfig pure             92/92
PoolConfig integration      16/16
PyO3 build contract           7/7
POOL-001 real MSSQL           1/1
clean ABI3 wheel             PASS
```

The complete SQL-auth orchestrator then produced:

```text
strict functional          294/295
async                        16/16
framework                    28/28
resilience                    6/6
load                           9/9
upstream regression         906/906
```

The only failure was `TX-034`,
`test_cancelled_commit_retires_lease_without_claiming_rollback`. Every
transaction assertion passed. Cleanup failed when
`DownstreamGateProxy.close()` surfaced:

```text
ConnectionResetError: [Errno 54] Connection reset by peer
```

The generated failing matrix and report remain in the detached PoolConfig
verification worktree for auditability. No failed report was committed.

## Reproduction and root cause

### Reproducibility

The failing test was rerun without source changes:

```text
first isolated campaign      1 failure / 10 executions
diagnostic campaign          1 failure / 7 executions
```

Nine consecutive passes followed by the same failure proved that accepting a
single rerun would conceal a real timing race.

### Boundary instrumentation

Temporary, uncommitted instrumentation around `DownstreamGateProxy._relay()`
recorded the exact failing boundary:

```text
downstream=False
aborting=False
gate_set=True
error=ConnectionResetError:[Errno 54] Connection reset by peer
```

The instrumentation was removed immediately after reproduction. The detached
verification worktree has no retained source or test-helper delta.

### Data flow

`TX-034` intentionally:

1. starts a transaction through the transparent TCP proxy;
2. sends `COMMIT`;
3. pauses server-to-client bytes after SQL Server has committed;
4. cancels the Python commit task before the acknowledgement reaches it;
5. requires FastMssql to retire the indeterminate physical connection;
6. releases the proxy gate;
7. proves the old SQL Server physical connection disappears;
8. proves a waiting transaction receives a different physical connection;
9. proves the committed row exists;
10. proves the cancelled transaction remains typed as indeterminate.

FastMssql retires the client socket intentionally at step 5. On the client
side of the proxy, macOS may report that terminal event in either of two valid
ways:

- `read()` returns an empty byte string (EOF); or
- `read()` raises `ConnectionResetError` with `Errno 54`.

The proxy already treats EOF as normal relay completion. It currently catches
the reset in `_accept()` and records it as unexpected whenever
`abort_connections()` is not executing. Because `TX-034` relies on the client
retiring its own socket, `_aborting` is correctly false and cannot classify
the event.

The root cause is therefore a missing explicit expected-client-disconnect
contract in the test harness. It is not a PoolConfig regression:
`_pooled_transaction_connection()` supplies every relevant pool field
explicitly:

```text
max_size
min_idle
max_lifetime_secs
idle_timeout_secs
connection_timeout_secs
test_on_check_out
retry_connection
```

## Goals

1. Make `TX-034` deterministic without retries.
2. Represent intentional client retirement explicitly in the proxy API.
3. Treat EOF and client-side TCP reset as equivalent only after declaration.
4. Keep the proxy strict for every undeclared or differently located failure.
5. Preserve the existing transaction cancellation and indeterminate-commit
   semantics.
6. Preserve all SQL-auth specification IDs.
7. Verify the race repeatedly and through the complete enterprise runner.
8. Keep the harness fix on branches separate from the PoolConfig candidate.
9. Rebase the PoolConfig verification baseline on the stabilized cumulative
   branch before claiming PoolConfig readiness.

## Non-goals

This candidate does not:

- change FastMssql production Rust or Python code;
- make TCP resets globally successful;
- suppress `ConnectionError` or `OSError` broadly;
- retry `TX-034`;
- weaken any transaction assertion;
- convert cleanup failures into warnings;
- change `CommitOutcomeUnknown`, transaction states or connection retirement;
- change pool defaults, pool sizing or operation timeouts;
- add a SQL-auth specification case ID;
- change SQL Server configuration;
- bump the package version or publish a package;
- push to or open a pull request against the original repository.

## Options considered

### Option A — Explicit one-shot client disconnect declaration

Add `expect_client_disconnect()` and consume exactly one declaration on
client-side EOF or `ConnectionResetError`.

**Selected.** The test states its transport expectation at the exact point
where it cancels the client. Unexpected resets remain observable everywhere
else, and the proxy does not need to infer intent from timing.

### Option B — Treat every client-side reset as normal EOF

Catch all client-side `ConnectionResetError` instances and return normally.

**Rejected.** A reset caused by a driver regression, malformed relay behavior
or accidental socket abort would be hidden even when no test expected it.

### Option C — Suppress the error in `TX-034` cleanup

Wrap `proxy.close()` in `suppress(ConnectionResetError)` or catch the final
assertion.

**Rejected.** `close()` deliberately aggregates background relay failures as
an `AssertionError`, so suppressing at the caller would also hide unrelated
proxy failures.

### Option D — Retry `TX-034`

Accept the first failure and rerun the test automatically.

**Rejected.** The race occurs in the harness contract itself. A retry would
make the suite look green while retaining nondeterministic failure handling.

### Option E — Make FastMssql close the cancelled socket gracefully

Alter production cancellation retirement to force EOF rather than TCP reset.

**Rejected.** Immediate socket retirement is the safety mechanism for an
indeterminate in-flight `COMMIT`. Production semantics must not be weakened
to accommodate a test proxy's platform-specific observation.

## Approved harness API

### Declaration

`DownstreamGateProxy` gains:

```python
def expect_client_disconnect(self) -> None:
    """Allow one client-side EOF or reset to complete the relay normally."""
```

Each call increments a private pending allowance counter by one.

The method is test-infrastructure API. It is not exported from the FastMssql
package and is not part of the public driver API.

### Consumption

The client-to-server relay is the `_relay(..., downstream=False)` direction.
Only the `reader.read()` boundary in that direction may consume a pending
allowance.

The algorithm is:

```text
read client bytes
  |
  +-- bytes received --------------------> forward and drain normally
  |
  +-- EOF
  |     |
  |     +-- pending allowance ----------> consume one; return normally
  |     +-- no allowance ---------------> return normally as today
  |
  +-- ConnectionResetError
        |
        +-- pending allowance ----------> consume one; return normally
        +-- no allowance ---------------> re-raise
```

EOF remains normal when undeclared, preserving current transparent-proxy
behavior. Declared EOF consumes the allowance so it cannot later hide a reset
from a different connection.

### Strict boundaries

The pending allowance must not be inspected or consumed around:

- `asyncio.open_connection()` to SQL Server;
- `writer.write()` or `writer.drain()`;
- the server-to-client `reader.read()` direction;
- `writer.wait_closed()`;
- proxy shutdown performed by `abort_connections()`;
- exceptions other than `ConnectionResetError`.

The allowance changes none of those paths' existing handling. Open, read,
write and drain failures still reach the existing `_accept()` classification;
shutdown remains governed only by `_aborting`; and cleanup
`writer.wait_closed()` retains its existing narrowly scoped suppression. The
allowance neither consumes nor broadens any of those classifications.

### One-shot behavior

After one matching client EOF or reset, the allowance counter returns to its
previous value. A second client reset without a second declaration must be
reported as unexpected.

The counter supports multiple explicit declarations so the helper remains
well-defined if a future fault scenario intentionally retires multiple
client connections. No current test declares more than one.

## `TX-034` integration

The test will call:

```python
proxy.expect_client_disconnect()
commit_task.cancel()
```

The declaration occurs immediately before cancellation, after the proxy has
confirmed that downstream acknowledgement bytes are held.

No other `TX-034` statement or assertion changes. In particular, the test
continues to prove:

- the commit task raises `asyncio.CancelledError`;
- the original SQL Server session and physical connection disappear;
- the waiting transaction obtains a different connection ID;
- the committed row exists;
- the cancelled transaction is disconnected;
- another commit attempt reports indeterminate state;
- the waiting lease rolls back and the pool returns to zero active leases;
- final cleanup completes without background proxy failures.

## Deterministic RED contract

One focused asynchronous harness test will be added beside the existing proxy
smoke tests in `tests/sql_auth_strict/test_transactions_strict.py`.

It uses controlled fake readers and writers rather than waiting for an
operating-system race. The test proves one policy:

1. an undeclared client-side reset is re-raised;
2. a declaration allows one client-side reset;
3. a second reset is re-raised because the declaration was consumed;
4. a declaration is not consumed by a server-side reset;
5. the same declaration is then consumed by a client-side EOF;
6. an error raised by client-to-server `writer.drain()` is still re-raised.

The test-helper change that makes the RED test pass is precisely the explicit
one-shot read-boundary classification. The expected exceptions are literal
and independent of the implementation.

The test does not require SQL Server but remains in the strict transaction
module because it is a contract for the transaction fault-injection harness.
It receives no `@case` marker and therefore adds no specification ID.

## TDD and branch discipline

The candidate uses four isolated branches/worktrees:

1. `docs/tcp-fault-proxy-client-reset-design`
   - approved specification and implementation plan only;
2. `test/tcp-fault-proxy-client-reset`
   - deterministic RED harness contract only;
3. `fix/tcp-fault-proxy-client-reset`
   - minimum helper change and the single `TX-034` declaration;
4. `docs/tcp-fault-proxy-client-reset-status`
   - generated reports, live-audit evidence and final status only.

Production code is forbidden on the RED branch. The GREEN branch starts from
the committed RED test. The status branch starts only after local full-suite
verification is green.

Every commit uses:

```text
Marcel Galea <galea.marcel@gmail.com>
```

Every push target is checked before publication:

```text
origin push      https://github.com/galeamarcel/FastMssql.git
upstream push    DISABLED
```

## Verification gates

### Deterministic helper contract

The new injected contract must:

- fail on the RED branch because the explicit API/behavior is absent;
- pass on the GREEN branch;
- continue to fail if the allowance becomes global, reusable or able to hide
  downstream/write errors.

### Real SQL-auth reproduction

After GREEN:

```text
TX-034 repeated sequentially        50/50 PASS
unexpected proxy background errors   zero
transaction business assertions      unchanged
```

Repetition is evidence against the measured 10–14% race rate; it is not a
retry mechanism inside the test.

### Harness candidate full gate

At the stabilized cumulative baseline before PoolConfig is reintroduced:

```text
Rust unit tests                     13/13
strict functional                  296/296
async                                16/16
framework                            28/28
resilience                            6/6
load                                   9/9
SQL-auth specification IDs         285/285
applicable upstream regression     902/902
Ruff / compileall / Clippy             PASS
RustSec findings                         0
```

The strict function count increases from 295 to 296 because of the new
deterministic harness test. The specification registry remains exactly 285.

### PoolConfig resumption gate

After the harness fix is integrated into `test/sql-auth-validation`, the
updated cumulative baseline is merged into:

```text
test/pool-config-default-consistency
fix/pool-config-default-consistency
```

The existing PoolConfig RED and GREEN commits remain individually auditable.
The refreshed PoolConfig full gate must then produce:

```text
Rust unit tests                     14/14
PoolConfig pure                     92/92
PoolConfig integration              16/16
PyO3 build contract                   7/7
POOL-001                               PASS
strict functional                  296/296
async                                16/16
framework                            28/28
resilience                            6/6
load                                   9/9
SQL-auth specification IDs         285/285
applicable upstream regression     906/906
clean ABI3 wheel                        PASS
RustSec findings                         0
```

No PoolConfig test, fix or assertion is changed to accommodate the harness
candidate.

## Cumulative merge sequencing

To preserve subject isolation:

1. merge the approved TCP-proxy design into `test/sql-auth-validation`;
2. create and commit the deterministic RED branch;
3. create and verify the GREEN harness branch;
4. merge the GREEN harness branch into `test/sql-auth-validation`;
5. generate and merge the TCP-proxy status documentation;
6. merge the refreshed cumulative baseline into the PoolConfig RED branch;
7. merge the refreshed PoolConfig RED branch into the PoolConfig fix branch;
8. recreate the detached PoolConfig verification worktree at the resulting
   exact fix SHA;
9. rerun every PoolConfig and enterprise gate.

When diffed from the refreshed cumulative baseline, the PoolConfig fix branch
must still contain only the approved PoolConfig source, stub, README, tests
and hosted workflow changes.

No force-push, rebase of published commits or destructive history rewrite is
required.

## Live-document updates

After all harness gates pass, the status branch updates:

- `docs/SQL_AUTH_TEST_MATRIX.md`;
- `docs/SQL_AUTH_TEST_REPORT.md`;
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`.

The audit section records:

1. the original `294/295` failure at the PoolConfig verification SHA;
2. both measured reproduction campaigns;
3. the instrumented `downstream=False` boundary;
4. the distinction between EOF and TCP RST;
5. the explicit one-shot policy and strict non-swallow boundaries;
6. design, RED, GREEN, technical merge and status commit IDs;
7. `TX-034` repeated `50/50`;
8. strict `296/296` and specification `285/285`;
9. all remaining lane counts and RustSec evidence;
10. the statement that no production FastMssql code changed;
11. the statement that nothing was pushed or proposed upstream;
12. the exact refreshed baseline consumed by the resumed PoolConfig
    candidate.

The failed generated report in the detached PoolConfig verification worktree
is evidence, not a publishable status. It is replaced by newly generated
complete reports only after every required lane passes.

## Security and failure visibility

The new contract does not log connection strings, credentials or SQL payload
contents. Existing password redaction and artifact scans remain mandatory.

The proxy continues to aggregate and raise unexpected background failures in
`close()`. The selected design narrows one known terminal event at one known
read boundary only after an explicit declaration. It therefore preserves the
project rule that exceptions are not silently swallowed.

## Compatibility

There is no application-facing compatibility change:

- package imports and public symbols are unchanged;
- FastMssql connection and transaction behavior is unchanged;
- SQL Server wire behavior is unchanged;
- existing fault tests need no change except `TX-034`;
- undeclared proxy failures remain failures.

The helper method is local test infrastructure and can be reverted
independently.

## Acceptance criteria

The candidate is accepted only when:

1. the deterministic RED contract fails for the missing explicit policy;
2. GREEN passes the same test without weakening it;
3. undeclared, downstream and writer failures remain observable;
4. one declaration can hide at most one client EOF/reset;
5. `TX-034` passes 50 consecutive fresh executions;
6. strict SQL-auth passes exactly 296 tests with zero failures/skips;
7. all 285 specification IDs pass exactly once as required;
8. every other local enterprise lane passes at the same technical SHA;
9. generated documents contain no credentials or failed case;
10. the live audit records measured evidence and remaining scope;
11. all branches and commits exist only on Marcel Galea's fork;
12. the PoolConfig candidate is rerun from the stabilized cumulative baseline
    rather than accepted using the earlier failed gate.

## Rollback

The helper fix is independently revertible. Reverting it restores strict
classification of every TCP reset but also restores the measured `TX-034`
race. No application rollback, schema change or package downgrade is
involved.

## Follow-on work

After this harness candidate and the resumed PoolConfig candidate are green,
the enterprise roadmap continues with its next separately designed audit
item. Approval of this document does not authorize upstream publication or
combine unrelated timeout, shutdown, metrics, tracing or protocol features.
