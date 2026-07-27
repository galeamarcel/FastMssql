# FastMssql Checkout Reset and First-Statement DDL Implementation Plan

> **Execution workflow:** follow the approved design, systematic debugging,
> test-driven development and verification-before-completion. Do not skip the
> RED evidence, do not combine the test and runtime-fix branches, and do not
> publish outside Marcel Galea's fork.

**Goal:** Complete mandatory TDS session reset inside pool acquisition so
application SQL remains a pristine batch when
`PoolConfig(test_on_check_out=False)`, while preserving optional health-probe
semantics, fail-closed cancellation, timeout taxonomy and high-concurrency
pool behavior.

**Architecture:** bb8's internal checkout hook always runs because bb8 0.9.1
places that hook inside its acquisition timeout and cancellation-safe
`PooledConnection`. `AzureConnectionManager` separately stores whether the
optional health query was requested. A clean connection with probing disabled
performs no wire request; a reusable `NeedsReset` connection performs a private
immediate Tiberius reset; an enabled/default probe combines reset and health in
one request. The vendored client exposes an additive immediate reset that
sends and drains the existing isolation baseline separately from all
application SQL.

**Source design:** `docs/checkout-reset-ddl-design` at
`a1c222ba60dcf6ab75b95bccb37c6733631e8ce1`

**Execution base:** resolve the exact
`origin/docs/checkout-reset-ddl-design` HEAD after all plan corrections and
record it before creating the RED worktree. It must contain
`ffff9d21c4c5fe74aeb193e20d69f63bc5ca4de3` in ancestry.

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** push only named branches to `origin`, which must
resolve to Marcel Galea's fork. Keep `upstream` fetch-only with push URL
`DISABLED`. Do not create a PR, release, package publication or dependency
fork.

**Displayed package version:** retain `0.7.7`; update `VERSION.md` in every
commit that modifies repository content.

---

## Task 1: Reconfirm the baseline and publication guardrails

**Branch:** remain on `docs/checkout-reset-ddl-design`

**Read:**

- `src/pool_manager.rs`
- `src/transaction.rs`
- `src/connection.rs`
- `src/batch.rs`
- `vendor/tiberius/src/client.rs`
- `vendor/tiberius/src/client/connection.rs`
- `tests/sql_auth_strict/test_pool.py`
- `tests/sql_auth_strict/test_operation_timeouts.py`
- `tests/sql_auth_strict/tcp_fault_proxy.py`

### Step 1: Verify exact source and remotes

Run:

```bash
git rev-parse HEAD
git status --short --branch
git remote get-url origin
git remote get-url --push origin
git remote get-url upstream
git remote get-url --push upstream
```

Expected:

- local HEAD equals `origin/docs/checkout-reset-ddl-design`;
- `ffff9d21c4c5fe74aeb193e20d69f63bc5ca4de3` is an ancestor;
- worktree is clean;
- both origin URLs are `https://github.com/galeamarcel/FastMssql.git`;
- upstream fetch is Rivendael and upstream push is `DISABLED`.

### Step 2: Reconfirm the root cause from source

Record these exact facts in the RED evidence:

1. `establish_pool()` forwards explicit `False` to bb8;
2. bb8 skips `ManageConnection::is_valid()` when that flag is false;
3. `PooledOperationGuard::new()` then calls
   `prepare_for_checkout()`;
4. Tiberius prepends `RESET_ISOLATION_BASELINE` to the next Batch/RPC;
5. SQL Server rejects first-statement module DDL after that prefix.

Do not change runtime code in this task.

---

## Task 2: Add the principal public RED contracts

**Create branch/worktree:**

```bash
git worktree add \
  -b test/checkout-reset-ddl \
  .worktrees/test-checkout-reset-ddl \
  <docs/checkout-reset-ddl-design-head-sha>
```

**Modify:**

- `tests/sql_auth_strict/test_pool.py`
- `tests/sql_auth_strict/test_operation_timeouts.py`
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- `VERSION.md`

### Step 1: Add `POOL-024` for trigger DDL

Add a real SQL-auth test named:

```python
@case("POOL-024")
@pytest.mark.asyncio
async def test_disabled_checkout_probe_preserves_first_statement_trigger_batch(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    ...
```

The test must:

1. use `max_size=1`, `min_idle=1`,
   `test_on_check_out=False`, `retry_connection=False`;
2. create source and audit tables;
3. capture `@@SPID`;
4. contaminate transaction isolation with `SERIALIZABLE`;
5. submit `CREATE TRIGGER` as the next application batch;
6. require the same SPID, restored `READ COMMITTED`, successful insert and
   exact trigger side effect;
7. clean every object through `CleanupRegistry`;
8. disconnect even on failure.

Why it matters: this proves driver-owned reset SQL never precedes module DDL
and proves the fix did not replace reset with unsafe session reuse.

### Step 2: Add `POOL-025` for representative module DDL

Add one parametrized test whose base node owns one matrix ID:

```python
@case("POOL-025")
@pytest.mark.parametrize("module_kind", ("procedure", "function", "view"))
@pytest.mark.asyncio
async def test_disabled_checkout_probe_preserves_module_definition_batches(
    ...
) -> None:
    ...
```

For each module:

- first create any supporting object;
- perform one completed request so the physical session is `NeedsReset`;
- submit the module definition through the public connection API;
- execute/query the module and assert its exact result;
- verify the SPID remains stable;
- use type-correct `DROP PROCEDURE`, `DROP FUNCTION` or `DROP VIEW` cleanup.

Do not work around the bug with dynamic SQL.

### Step 3: Add `TIME-011` for reset acquisition timeout

Use the existing `DownstreamGateProxy`:

```python
@case("TIME-011")
@pytest.mark.asyncio
async def test_checkout_reset_uses_acquire_deadline_before_application_sql(
    ...
) -> None:
    ...
```

Required sequence:

1. create an attempt table through the independent SA observer;
2. connect through the proxy with a one-connection pool,
   `test_on_check_out=False`, a short acquire timeout and a longer operation
   timeout;
3. execute one query and capture SPID plus `connection_id`;
4. pause downstream bytes only after that response is fully consumed;
5. start a unique `INSERT` application command;
6. wait until the proxy holds the reset response;
7. require `OperationTimeoutError` with:
   - `phase == "acquire"`;
   - `operation == "execute"`;
   - `retryable is True`;
   - `connection_discarded is False`;
   - `outcome_unknown is False`;
8. require zero inserted rows because application SQL never started;
9. resume the proxy, require the old physical identity to disappear, then
   require replacement-session smoke;
10. close the proxy and leave zero application sessions.

The metadata intentionally follows the existing timeout contract:
`connection_discarded=False` means no lease was returned to the caller even
though bb8 internally retires the reset-in-progress physical connection.

### Step 4: Register matrix IDs

Extend the SQL-auth validation design with:

- `POOL-024`: first-statement trigger DDL after a reused lease with checkout
  probe disabled;
- `POOL-025`: procedure/function/view definitions remain pristine;
- `TIME-011`: stalled mandatory reset expires in acquire before application
  SQL.

Keep every ID present exactly once in collected source.

### Step 5: Record the test-only version entry

Add a `VERSION.md` section that states:

- the three RED contracts;
- no runtime fix;
- expected baseline failures;
- displayed version remains `0.7.7`.

### Step 6: Run collection and deterministic RED

Load the repository-local SQL-auth environment without printing it. Run the
matrix contract/collection, then:

```bash
../../.venv/bin/pytest \
  tests/sql_auth_strict/test_pool.py::test_disabled_checkout_probe_preserves_first_statement_trigger_batch \
  tests/sql_auth_strict/test_pool.py::test_disabled_checkout_probe_preserves_module_definition_batches \
  tests/sql_auth_strict/test_operation_timeouts.py::test_checkout_reset_uses_acquire_deadline_before_application_sql \
  -vv
```

Expected source-baseline RED:

- `POOL-024`/`POOL-025`: SQL Server error 111 or the equivalent typed
  first-statement rejection caused by the hidden isolation prefix;
- `TIME-011`: application SQL reaches SQL Server and/or the timeout is
  operation-phase rather than acquire-phase.

Capture exact exception type, SQL code/state/severity, timing, inserted-row
count, SPID and pool counters. Do not weaken assertions to match the baseline.

### Step 7: Commit and push the RED branch

Run:

```bash
git diff --check
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/sql_auth_strict/test_pool.py \
  tests/sql_auth_strict/test_operation_timeouts.py
git commit -m "test: reproduce checkout reset DDL corruption"
git push -u origin test/checkout-reset-ddl
```

Verify the remote branch SHA exactly. Never push upstream.

---

## Task 3: Capture pre-fix throughput evidence

**Branch:** `test/checkout-reset-ddl`

**Modify:** no tracked source.

Use the extension built from the same runtime source as the RED branch. Record
the exact git SHA, dirty state, Python/platform metadata and SQL Server
container identity.

### Step 1: Build and verify the exact RED-branch source

Build/install the release extension from the clean RED worktree with isolated
temporary Cargo and uv caches. Confirm the imported extension belongs to that
build before collecting baseline metrics.

### Step 2: Run the mandatory profiles

Use `scripts/sql_auth/operation_metrics_stress.py` with the existing
`test_on_check_out=False` policy:

- 1,000 operations, 100 workers, pool 20, three enabled/disabled pairs;
- 10,000 operations, 100 workers, pool 20, three pairs.

Write JSON outside tracked source under `/private/tmp` with a name containing
the exact RED SHA.

Run the corresponding 1,000/10,000 profiles through the existing bounded
result-stream stress harness as the resource companion. It records RSS growth,
p99 latency, event-loop stall, pool/session bounds, exact result identities
and teardown.

### Step 3: Run the approved extended profile

Run 99,999 operations, 200 workers, pool 100, three pairs. This executes
599,994 logical operations across the six paired trials and is within the
explicitly approved 99,999-per-trial ceiling.

Also run the opt-in 99,999 bounded result-stream profile so the extended
evidence includes RSS growth and event-loop stall rather than inferring them
from the throughput gate.

Required evidence:

- exact completion and sum;
- zero failure/timeout;
- throughput per trial and median;
- p95/p99 operation histograms;
- maximum pool connections and SQL sessions;
- event-loop ticks;
- zero sessions after teardown;
- post-profile smoke.

This is driver/load evidence, not a SQL Server capacity benchmark.

---

## Task 4: Add the vendored-Tiberius immediate-reset RED

**Create branch/worktree from the public RED commit:**

```bash
git worktree add \
  -b test/tiberius-immediate-reset \
  .worktrees/test-tiberius-immediate-reset \
  <test/checkout-reset-ddl-sha>
```

**Modify:**

- `vendor/tiberius/tests/response_events_sql_auth.rs`
- `VERSION.md`

### Step 1: Add `TIB-RESET-001`

Add one no-skip real SQL-auth integration test that calls the proposed method:

```rust
client.reset_connection().await?;
```

Name the test
`tib_reset_001_immediate_reset_preserves_next_ddl_batch`.

The test must:

1. connect once and capture SPID;
2. set `SERIALIZABLE`, `SESSION_CONTEXT`, database/session options and a local
   temp object;
3. call immediate reset;
4. send `CREATE TRIGGER` as the next application batch;
5. require the same SPID, `READ COMMITTED`, cleared state and absent temp
   object;
6. fire the trigger and verify one exact side effect;
7. use an independent cleanup client and aggregate primary/cleanup errors.

### Step 2: Run the compile RED

Run:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_events_sql_auth \
  tib_reset_001_immediate_reset_preserves_next_ddl_batch \
  -- --exact --nocapture --test-threads=1
```

Expected RED: the additive async `Client::reset_connection()` method does not
exist. Preserve that compiler evidence.

### Step 3: Commit and push the test-only branch

Commit message:

```text
test: require immediate Tiberius session reset
```

Verify the remote branch on the fork.

---

## Task 5: Implement immediate reset and checkout policy

**Create branch/worktree from the Tiberius RED commit:**

```bash
git worktree add \
  -b fix/checkout-reset-ddl \
  .worktrees/fix-checkout-reset-ddl \
  <test/tiberius-immediate-reset-sha>
```

**Modify:**

- `vendor/tiberius/src/client.rs`
- `src/pool_manager.rs`
- `src/transaction.rs`
- `python/fastmssql/fastmssql.pyi`
- `README.md`
- `VERSION.md`

### Step 1: Add the Tiberius immediate-reset primitive

In `vendor/tiberius/src/client.rs`:

1. factor the existing named-RPC reset batch/drain into a private helper whose
   only job is to complete a pending reset;
2. retain `reset_connection_on_next_request()` unchanged;
3. add:

```rust
pub async fn reset_connection(&mut self) -> crate::Result<()> {
    self.connection.flush_stream().await?;
    self.connection.reset_connection_on_next_request();
    self.complete_pending_connection_reset().await
}
```

4. make named RPC reuse the same private completion helper;
5. send the existing `RESET_ISOLATION_BASELINE` exactly once;
6. drain every token to terminal completion;
7. do not accept, concatenate or return application SQL.

Run the focused Tiberius unit and real SQL-auth tests. `TIB-RESET-001` must
turn GREEN while existing reset-prefix and named-RPC tests remain GREEN.

### Step 2: Model checkout actions explicitly

In `src/pool_manager.rs`, add a private, pure action enum such as:

```rust
enum CheckoutAction {
    Ready,
    Reset,
    Validate { reset: bool },
    Reject,
}
```

Derive the action from `ConnectionDisposition` and the effective optional
health policy. Add unit tests for all state/policy combinations, especially:

- `Clean + false -> Ready`;
- `NeedsReset + false -> Reset`;
- `Clean + true -> Validate { reset: false }`;
- `NeedsReset + true -> Validate { reset: true }`;
- `Broken -> Reject`.

The tests encode why health is optional but session isolation is not.

### Step 3: Separate manager policy from bb8 hook activation

Extend `AzureConnectionManager` with the effective health-policy boolean:

```rust
pool_config.test_on_check_out.unwrap_or(true)
```

In `establish_pool()`:

- always call `builder.test_on_check_out(true)` internally;
- stop forwarding explicit `False` to bb8;
- pass the requested/effective health policy to the manager.

Do not change the Python `PoolConfig` value or representation.

### Step 4: Implement fail-closed `is_valid()`

Before every wire await:

- choose the action;
- arm reset only when required;
- mark the disposition `Broken`.

Then:

- `Ready`: return without wire I/O;
- `Reset`: call Tiberius immediate reset;
- `Validate { reset: false }`: execute/drain the health query;
- `Validate { reset: true }`: arm reset and execute/drain the health query as
  one request;
- `Reject`: return a connection-manager error.

Mark `Clean` only after full success. Any error, panic, timeout or cancellation
must leave `Broken`.

### Step 5: Remove post-checkout reset arming

- make `PooledOperationGuard::new()` accept the already-clean lease without
  calling `prepare_for_checkout()`;
- remove pooled `TransactionConnection::prepare_for_checkout()` and its
  acquisition call;
- remove or rename the old method so application paths cannot accidentally
  reintroduce piggyback reset.

Audit every `pool.get()` and `pool.get_owned()` caller. The forced bb8 hook
must cover connection, stream, batch, native bulk, pooled transaction,
readiness and warm-up paths.

### Step 6: Clarify public documentation

Update the stub and README:

- `False` disables only the optional health probe;
- mandatory cross-lease reset always runs;
- a reused lease may incur a private reset round trip;
- default/true combines reset with health when possible.

Do not add a Python reset API or change any signature.

### Step 7: Update `VERSION.md`

Record:

- immediate Tiberius reset;
- checkout action matrix;
- pristine application batches;
- fail-closed acquire semantics;
- expected false-policy round trip;
- displayed `0.7.7` unchanged.

---

## Task 6: Turn every focused RED GREEN

**Branch:** `fix/checkout-reset-ddl`

### Step 1: Build the exact worktree source

Use separate temporary Cargo/uv caches and install the release extension from
the fix worktree. Confirm:

```python
import fastmssql
print(fastmssql.__file__)
```

The loaded module must be the just-built candidate, not a stale root or wheel.

### Step 2: Run root and vendor unit gates

Run:

```bash
cargo test --locked
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
```

### Step 3: Run direct Tiberius SQL-auth

Run `TIB-RESET-001`, then the complete
`response_events_sql_auth` integration test with one test thread.

### Step 4: Run focused FastMssql SQL-auth

Run:

- `POOL-018`, `POOL-019`, `POOL-020`;
- new `POOL-024`, `POOL-025`;
- new `TIME-011`;
- existing `TIME-003`, `TIME-004`;
- `SQL-019`, `SQL-024`;
- representative result-stream, batch, callproc, transaction and native-bulk
  cases that use `test_on_check_out=False`.

All must pass without retries, sleeps added to hide races or swallowed
exceptions.

### Step 5: Inspect server-side invariants

For the focused reset tests record:

- SPID and `connection_id` before/after;
- isolation-level code;
- session context/temp object state;
- exact object/row counts;
- pool close-reason counters;
- zero application sessions after teardown.

---

## Task 7: Run compatibility, framework and packaging gates

**Branch:** `fix/checkout-reset-ddl`

### Step 1: Rust quality

Run:

- root `cargo fmt --check`;
- root Clippy, all targets, warnings denied;
- vendored Tiberius fmt;
- vendored Tiberius Clippy with only the ten already audited legacy
  allowances and every other warning denied.

### Step 2: Python quality and matrix

Run:

- Ruff check and format check for changed Python;
- matrix contract/collection;
- complete strict functional SQL-auth lane, deterministic `-n1`;
- async and framework lanes;
- non-disruptive resilience lane;
- original local regression lane;
- compile/import/stub contracts.

Do not call a skipped or deselected result a pass.

### Step 3: Isolated wheel

Build an ABI3 wheel from the exact clean fix SHA, install it into a fresh
isolated environment, remove repository `PYTHONPATH`, and verify:

- import path is isolated `site-packages`;
- offline API/config contracts;
- focused `POOL-024`, `POOL-025`, `TIME-011`;
- representative connection/batch/stream/transaction/native-bulk SQL-auth;
- no undeclared runtime dependency.

Record the exact wheel filename and SHA-256. Do not publish it.

### Step 4: Hosted fork checks

Read public GitHub Actions for the fork only. Record hosted Linux/macOS/Windows
and RustSec status if a run exists for the exact candidate ancestry. Do not
infer hosted success from local gates and do not trigger or modify an upstream
workflow.

---

## Task 8: Run post-fix performance and stress

**Branch:** clean exact `fix/checkout-reset-ddl` SHA

Repeat the exact Task 3 commands and configuration:

- 1,000 operations;
- 10,000 operations;
- explicitly approved 99,999 operations per trial.

Compare pre/post on:

- median and per-trial throughput;
- p95/p99 and maximum latency;
- pool/session high-water marks;
- physical connection creation/retirement;
- RSS and event-loop progress;
- exact results and teardown.

Also run one pooled transaction profile because a pooled transaction now
performs its reset before `BEGIN` rather than piggybacking reset on `BEGIN`.

Document the expected additional false-policy reset round trip. Investigate
any result beyond that causal cost, including connection churn, duplicate
reset, unbounded queueing, timeout drift, memory growth or starvation.

---

## Task 9: Self-review and graph impact analysis

**Branch:** `fix/checkout-reset-ddl`

### Step 1: Rebuild the graph

Run:

```bash
uvx code-review-graph build
```

Require graph metadata to match the exact final fix SHA.

### Step 2: Review changes and flows

Use:

- `detect_changes`;
- `get_impact_radius`;
- `get_affected_flows`;
- `query_graph` callers/callees/tests;
- direct source verification for vendored Tiberius, which the graph may not
  index structurally.

Review specifically:

- every pool acquisition path;
- cancellation between state transition and first packet;
- bb8 timeout drop behavior;
- duplicate reset risk;
- named RPC behavior;
- pooled transaction lease behavior;
- result-stream ownership;
- pool close counters and metrics;
- hidden application-SQL mutation;
- credentials and generated artifacts.

### Step 3: Final repository checks

Run:

```bash
git diff --check <red-base>..HEAD
git status --short --branch
```

Scan the committed diff for credentials, connection strings, build targets,
`Cargo.lock` generated under vendored Tiberius, caches and stress artifacts.
Move untracked build artifacts recoverably outside the worktree; do not delete
user data.

### Step 4: Commit and push the fix

Commit message:

```text
fix: complete session reset before application DDL
```

Push only `fix/checkout-reset-ddl` to the fork and verify exact remote SHA.

---

## Task 10: Update the live audit on a status branch

**Create branch/worktree from the exact fix commit:**

```bash
git worktree add \
  -b docs/checkout-reset-ddl-status \
  .worktrees/docs-checkout-reset-ddl-status \
  <fix/checkout-reset-ddl-sha>
```

**Modify:**

- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- `docs/SQL_AUTH_TEST_MATRIX.md` and report artifacts only when regenerated
  from exact successful evidence;
- add a focused checkout-reset stress report if the evidence is too detailed
  for the live audit;
- `VERSION.md`

### Step 1: Close the live observation precisely

Replace `OPEN_REPRO_REQUIRED` only after all required gates pass. Record:

- design, RED, Tiberius-test, fix and status branches;
- exact commit SHAs;
- exact pre-fix error and timeout behavior;
- exact post-fix test counts;
- same-SPID/reset evidence;
- timeout and recovery evidence;
- wheel hash;
- graph metadata;
- 1,000/10,000/99,999 performance measurements;
- hosted status separately from local status;
- residual round-trip cost and unsupported distributed transactions.

Correct the older universal “no additional round trip” wording:

- default/true: reset piggybacks on health;
- false + clean: no control request;
- false + reused `NeedsReset`: one private reset request before application
  SQL.

### Step 2: Update future upstream candidate text only

Describe the minimal future Tiberius/FastMssql PR split, but do not create a PR
or push to upstream. Publication remains subject to a later explicit approval.

### Step 3: Verify, commit and push status

Run Markdown link/fence checks, `git diff --check`, credential scan, graph
rebuild and self-review.

Commit message:

```text
docs: record safe checkout reset evidence
```

Push only `docs/checkout-reset-ddl-status` to the fork and verify exact remote
SHA.

---

## Completion gate

Do not mark the candidate complete unless all of the following are true:

- public RED is preserved in fork history;
- Tiberius compile RED is preserved in fork history;
- first-statement trigger/procedure/function/view DDL passes on real Docker
  SQL Server with SQL authentication and `test_on_check_out=False`;
- same-SPID session reset remains complete;
- stalled reset times out in acquire before application SQL;
- the uncertain reset connection is retired and recovery succeeds;
- no API signature or displayed-version change occurred;
- root/vendor/unit/SQL-auth/framework/wheel/quality gates pass;
- mandatory and extended stress evidence is exact and privacy-safe;
- graph and final diff were self-reviewed;
- every pushed SHA exists only on Marcel Galea's fork;
- upstream push remains `DISABLED`;
- the live audit reflects evidence rather than intention.
