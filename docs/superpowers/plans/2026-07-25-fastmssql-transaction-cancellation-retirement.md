# FastMssql Transaction Cancellation Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL:
> `superpowers:executing-plans`. Implement task-by-task with TDD checkpoints.

**Goal:** Make cancellation of every in-flight `Transaction` operation retire
its direct socket or pooled lease automatically, terminate the SQL Server
request, and recover pool capacity without an explicit user `close()`.

**Architecture:** Add a monotonically increasing operation epoch to the
Rust-owned `TransactionSession`. Arm an RAII cancellation guard only after a
validated operation enters an in-flight state. If Python cancellation drops
the Rust future before its terminal transition, the guard conditionally marks
the current connection unusable, removes it from the session and changes the
state to `Failed`. It first tries the transaction mutex synchronously and
otherwise schedules cleanup on the initialized PyO3 Tokio runtime.

**Tech Stack:** Rust, PyO3 0.29, Tokio, bb8, vendored Tiberius 0.12.3,
Python 3.13, pytest-asyncio, SQL Server Developer in Docker with SQL
authentication.

---

## Non-negotiable constraints

- All branches, commits and pushes target
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` remains fetch-only with push URL `DISABLED`.
- Do not create an upstream PR without separate explicit approval.
- Do not publish or create a Tiberius fork.
- Keep test, fix, design and status branches separate.
- Preserve `asyncio.CancelledError`; do not replace it with a driver exception.
- Never retry `COMMIT`, rollback, writes or any cancelled operation.
- A cancelled `COMMIT` remains a business outcome that the caller must
  reconcile; the driver must not claim rollback.
- Never return a connection with an abandoned TDS response to the pool.
- Secrets remain only in `.env.sql-auth.local`.
- Use `sys.dm_exec_connections.connection_id`, not only SPID, to prove physical
  replacement.
- Every strict exception must be asserted; no `except: pass`, `skip`, `xfail`
  or accepted alternative outcomes.

## Source design

Read first:

- `docs/superpowers/specs/2026-07-25-fastmssql-transaction-cancellation-retirement-design.md`
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- `src/transaction.rs`
- `src/pool_manager.rs`
- `tests/sql_auth_strict/test_transactions_strict.py`
- `tests/sql_auth_strict/tcp_fault_proxy.py`

The design intentionally does not implement TDS `ATTENTION`. The current
Tiberius decoder cannot prove safe reuse after a decoder future is dropped in
the middle of a token. Transport close is the conservative P0 behavior.

---

## Branch graph

```text
test/sql-auth-validation at 052bb43
  |
  +-- docs/transaction-cancellation-retirement-design
  |     0de5706 design
  |     3ece52e plan
  |
  +-- test/transaction-cancellation-retirement
  |     1757094 RED TX-032–TX-034
  |
  +-- fix/transaction-cancellation-retirement
  |     c5dcd2d epoch + RAII retirement
  |     ec7ba56 verified branch head
  |
  +-- test/sql-auth-validation at c30c02a
  |     exact verified cumulative merge
  |
  +-- docs/transaction-cancellation-retirement-status
        audit and candidate evidence
```

The design branch is merged into the cumulative fork before the test branch is
created, matching the existing audit discipline. Production work starts only
from the committed RED branch.

---

### Task 1: Commit and integrate the approved design

**Files:**

- Add:
  `docs/superpowers/specs/2026-07-25-fastmssql-transaction-cancellation-retirement-design.md`
- Add:
  `docs/superpowers/plans/2026-07-25-fastmssql-transaction-cancellation-retirement.md`

- [x] **Step 1: Create the isolated design worktree**

```bash
git worktree add \
  .worktrees/docs-transaction-cancellation-retirement-design \
  -b docs/transaction-cancellation-retirement-design \
  test/sql-auth-validation
```

- [x] **Step 2: Record the protocol decision**

The design must contain:

- the ordinary pooled cancellation probe;
- the transaction ownership gap;
- official `ATTENTION`/`DONE_ATTN` requirements;
- the reason partial attention support is unsafe;
- the operation epoch and RAII cleanup;
- pooled, direct and cancelled-COMMIT contracts;
- explicit exclusions.

- [x] **Step 3: Commit and push design artifacts**

```bash
git add \
  docs/superpowers/plans/2026-07-25-fastmssql-transaction-cancellation-retirement.md
git commit -m "docs: plan automatic transaction cancellation retirement"
git push -u origin docs/transaction-cancellation-retirement-design
```

- [x] **Step 4: Merge only into the cumulative fork**

```bash
git merge --no-ff docs/transaction-cancellation-retirement-design \
  -m "merge: plan automatic transaction cancellation retirement"
git push origin test/sql-auth-validation
```

Do not merge the separate executive upstream registry branch.

---

### Task 2: Add deterministic RED SQL-auth contracts

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_transactions_strict.py`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`

**Produces:** TX-032, TX-033 and TX-034; exact specification total 277.

- [x] **Step 1: Create the test branch**

```bash
git worktree add \
  .worktrees/test-transaction-cancellation-retirement \
  -b test/transaction-cancellation-retirement \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-transaction-cancellation-retirement/.env.sql-auth.local
```

- [x] **Step 2: Add DMV identity helpers**

In `test_transactions_strict.py`, add helpers that:

1. locate a tokenized active request;
2. return its `session_id` and `connection_id`;
3. wait for the request to disappear;
4. wait for the SQL session to disappear.

Use observer queries equivalent to:

```sql
SELECT
    request.session_id,
    CONVERT(NVARCHAR(36), connection.connection_id)
FROM sys.dm_exec_requests AS request
JOIN sys.dm_exec_connections AS connection
  ON connection.session_id = request.session_id
CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
WHERE request.session_id <> @@SPID
  AND sql_text.text LIKE @P1
```

Deadlines must be bounded and failures must report the token/session.

- [x] **Step 3: Add TX-032 pooled autonomous retirement**

Test name:

```python
test_cancelled_pooled_transaction_retires_without_explicit_close
```

Required assertions:

- one pooled connection and two transaction objects;
- transaction A owns a known `connection_id`;
- a tokenized ten-second `WAITFOR` is active server-side;
- transaction B is blocked on `begin`;
- cancelling A returns `CancelledError`;
- no call to `A.close()` occurs before cleanup assertions;
- the request and session disappear within two seconds;
- B begins within two seconds on a different `connection_id`;
- A rejects `commit()` with the indeterminate-state message;
- later `close()` remains idempotent.

- [x] **Step 4: Add TX-033 direct autonomous rollback**

Test name:

```python
test_cancelled_direct_transaction_closes_and_rolls_back_without_close
```

Required assertions:

- insert one uncommitted row;
- record direct SPID and `connection_id`;
- start/observe tokenized `WAITFOR`;
- cancel and receive `CancelledError`;
- do not call `close()` before cleanup assertions;
- request and session disappear;
- `transaction.is_connected() is False`;
- observer sees zero rows;
- two later `close()` calls succeed.

- [x] **Step 5: Add TX-034 cancelled durable COMMIT**

Test name:

```python
test_cancelled_commit_retires_lease_without_claiming_rollback
```

Use `DownstreamGateProxy`:

- pool size one through the proxy;
- transaction A inserts and records `connection_id`;
- transaction B waits for the only lease;
- pause downstream and start A's `commit`;
- observer confirms the row is durable;
- proxy confirms response bytes are held;
- cancel commit and assert `CancelledError`;
- do not close A;
- resume downstream so the transparent proxy can observe the client EOF;
- B begins on a different `connection_id`;
- the row remains durable;
- A is disconnected and fail-closed.

The test must never accept rollback as an alternative outcome.

- [x] **Step 6: Extend the specification and exact matrix count**

Add:

```text
TX-032 pooled cancelled transaction auto-retires
TX-033 direct cancelled transaction auto-closes and rolls back
TX-034 cancelled durable COMMIT retires without claiming rollback
```

Change every matrix contract occurrence from 274 to 277, including test names,
error messages and report totals.

- [x] **Step 7: Run the focused baseline**

Build the unchanged production source, then run:

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'without_explicit_close or without_close or cancelled_commit_retires' \
  -q --tb=short
```

Expected RED:

```text
TX-032 waiter remains blocked/request remains until explicit close
TX-033 direct request/session remain until explicit close
TX-034 commit transaction retains its lease until explicit close
```

All test `finally` blocks must explicitly close sockets and cancel waiter tasks
so the RED run leaves SQL Server clean.

- [x] **Step 8: Run contract checks**

```bash
../../.venv/bin/ruff check \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_matrix_contract.py
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_matrix_contract.py -q --tb=short
git diff --check
```

Expected: contract checks PASS while the three behavior tests remain RED.

- [x] **Step 9: Commit and push the RED branch**

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: reproduce retained transactions after cancellation"
git push -u origin test/transaction-cancellation-retirement
```

---

### Task 3: Add the epoch-guarded retirement implementation

**Files:**

- Modify: `src/transaction.rs`

**Consumes:** RED TX-032–TX-034.

**Produces:** automatic physical connection retirement after cancellation.

- [x] **Step 1: Create the fix branch from RED**

```bash
git worktree add \
  .worktrees/fix-transaction-cancellation-retirement \
  -b fix/transaction-cancellation-retirement \
  test/transaction-cancellation-retirement
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-transaction-cancellation-retirement/.env.sql-auth.local
```

- [x] **Step 2: Add in-flight state classification**

Add:

```rust
impl TransactionState {
    fn is_in_flight(self) -> bool {
        matches!(
            self,
            Self::Beginning
                | Self::Executing
                | Self::Committing
                | Self::RollingBack
        )
    }
}
```

- [x] **Step 3: Add the session operation epoch**

Extend:

```rust
struct TransactionSession {
    conn: Option<TransactionConnection>,
    state: TransactionState,
    operation_epoch: u64,
}
```

Default epoch is zero.

Add methods:

```rust
fn enter_in_flight(&mut self, state: TransactionState) -> u64 {
    debug_assert!(state.is_in_flight());
    self.operation_epoch = self.operation_epoch.wrapping_add(1);
    self.state = state;
    self.operation_epoch
}

fn retire_cancelled_operation(&mut self, epoch: u64) {
    if self.operation_epoch != epoch || !self.state.is_in_flight() {
        return;
    }
    if let Some(connection) = self.conn.as_mut() {
        connection.mark_unusable();
    }
    self.conn.take();
    self.state = TransactionState::Failed;
}
```

The epoch check is mandatory. A delayed cleanup task must not retire a new
operation after `close()` and reuse.

- [x] **Step 4: Add the RAII guard**

Use an unarmed guard:

```rust
struct TransactionCancellationGuard {
    session: Arc<AsyncMutex<TransactionSession>>,
    armed_epoch: Option<u64>,
}
```

Methods:

```rust
fn new(session: Arc<AsyncMutex<TransactionSession>>) -> Self
fn arm(&mut self, epoch: u64)
fn disarm(&mut self)
```

`Drop`:

1. return if unarmed;
2. try `session.try_lock()` and retire synchronously if possible;
3. otherwise clone the `Arc` and use
   `pyo3_async_runtimes::tokio::get_runtime().spawn(...)`;
4. the async task locks and invokes the same epoch-checked retirement method.

Do not block in `Drop`. Do not ignore an error path that could return the
connection to the pool.

- [x] **Step 5: Arm every data operation**

Change `begin_data_operation` to return:

```rust
PyResult<(TransactionState, u64)>
```

After `connection.begin_operation()`, call
`session.enter_in_flight(TransactionState::Executing)`.

For `query`, `simple_query`, `execute`, `execute_batch` and `query_batch`:

- construct the guard before connection acquisition;
- arm it immediately after `begin_data_operation`;
- keep the existing mutex/I/O serialization;
- store `finish_data_operation` as a `PyResult` rather than using `?` before
  cleanup;
- disarm after the state transition on every normal/error return;
- then propagate the result.

- [x] **Step 6: Arm every transaction command**

In `execute_transaction_command`:

- construct the guard from the session `Arc`;
- after validation and connection existence checks, enter the command's
  in-flight state through the epoch method;
- arm before the first wire await;
- preserve `CommitOutcomeUnknown` classification;
- disarm after every completed/error state transition.

No second `COMMIT`/`ROLLBACK` is allowed in cleanup.

- [x] **Step 7: Add Rust unit tests for stale cleanup**

Unit-test at least:

- matching epoch plus `Executing` becomes `Failed`;
- stale epoch does not change a newer in-flight state;
- matching epoch in `Committed` is a no-op;
- repeated retirement is idempotent.

Tests may construct `TransactionSession` without a connection to isolate the
state contract.

- [x] **Step 8: Build and run GREEN focused tests**

```bash
cargo fmt --check
cargo test --locked
../../.venv/bin/maturin develop --release
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'without_explicit_close or without_close or cancelled_commit_retires' \
  -q --tb=short
```

Expected: TX-032–TX-034 PASS. The existing TX-026 contract may need an
intentional expectation update because the waiter should now recover before
the explicit compatibility `close()`.

- [x] **Step 9: Update TX-026 without weakening it**

If TX-026 fails because it still expects the waiter to remain blocked, update
it to assert the stronger contract:

- cancellation leaves state fail-closed;
- autonomous retirement occurs;
- waiter receives a different `connection_id`;
- later `close()` remains idempotent.

Do not remove any server-side or pool-bound assertion.

- [x] **Step 10: Commit and push the fix**

```bash
git add src/transaction.rs tests/sql_auth_strict/test_transactions_strict.py
git commit -m "fix: retire cancelled transaction connections automatically"
git push -u origin fix/transaction-cancellation-retirement
```

If TX-026 needs a test-only adjustment, keep it in a separate commit before
the production fix when practical.

---

### Task 4: Complete verification

**Files:** no production edits unless a newly reproduced defect requires its
own branch.

- [x] **Step 1: Static and Rust gates**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
/private/tmp/fastmssql-cargo-audit-0.22.2/bin/cargo-audit \
  audit --deny warnings
../../.venv/bin/ruff check python tests/sql_auth_strict
../../.venv/bin/python -m compileall -q python tests/sql_auth_strict
git diff --check
```

- [x] **Step 2: Transaction and cancellation regressions**

Run:

```bash
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_async_strict.py \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/test_transaction.py \
  tests/test_transaction_flags.py \
  tests/test_transaction_forwarded_methods.py \
  -q --tb=short
```

Export the six upstream fixture variables to the SQL-auth upstream database.

- [x] **Step 3: Complete strict suite**

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=/private/tmp/tx-cancel-strict.json \
FASTMSSQL_FRAMEWORK_METRICS_PATH=/private/tmp/tx-cancel-framework.json \
FASTMSSQL_LOAD_METRICS_PATH=/private/tmp/tx-cancel-load.json \
../../.venv/bin/python -m pytest tests/sql_auth_strict -q --tb=no
```

Expected: exact 277/277 case IDs and zero failures.

- [x] **Step 4: Complete applicable upstream suite**

Run `pytest -n 1 tests`, ignoring:

```text
tests/test_azure_auth_advanced.py
tests/test_azure_authentication.py
tests/test_azure_cli_path_validation.py
tests/test_transaction_azure_auth.py
tests/test_transaction_azure_auth_advanced.py
tests/sql_auth_strict
```

Expected: zero failures.

- [x] **Step 5: Pooled transaction stress**

```bash
FASTMSSQL_TRANSACTION_STRESS_STRATEGY=pooled \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE=100 \
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
./scripts/sql_auth/run_transaction_stress.sh
```

Expected:

- zero failed operations;
- no more than 100 physical/SQL sessions;
- exact commit/rollback totals;
- smoke PASS;
- zero remaining application sessions.

- [x] **Step 6: Cancellation storm with DMV cleanup**

Add or reuse a bounded test that cancels more transaction operations than
`pool.max_size`, then proves:

- all Python tasks return `CancelledError`;
- all tokenized requests disappear;
- cancelled SQL sessions disappear;
- pool capacity returns;
- replacements do not reuse the old `connection_id` values;
- no application sessions remain after disconnect.

- [x] **Step 7: Repository safety**

```bash
git status --short
git diff --check
git remote -v
git log --format=fuller -8
```

Expected:

- clean worktree;
- author Marcel Galea;
- `origin` is the fork;
- upstream push is `DISABLED`;
- no secrets or local artifacts tracked.

---

### Task 5: Integrate only into the fork

- [x] **Step 1: Merge the verified fix**

```bash
git -C /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql \
  merge --no-ff fix/transaction-cancellation-retirement \
  -m "merge: retire cancelled transaction connections"
```

- [x] **Step 2: Rebuild and verify the exact merged tree**

Run Rust tests and TX-026/TX-032–TX-034 on the merged source, not an editable
install pointing at the feature worktree.

- [x] **Step 3: Push only the cumulative fork branch**

```bash
git push origin test/sql-auth-validation
```

Do not push or create anything against `upstream`.

---

### Task 6: Update the live audit and candidate registry

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Modify separately:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`

- [x] **Step 1: Create `docs/transaction-cancellation-retirement-status`**

Base the branch on the exact verified cumulative source.

- [x] **Step 2: Correct the P0 audit based on evidence**

Record separately:

- ordinary pooled cancellation already terminated request/session by socket
  close;
- transaction cancellation previously retained the owned connection until
  explicit `close()`;
- the epoch guard removes that dependency;
- server request/session disappearance and transaction rollback;
- cancelled durable `COMMIT` remains durable and is not retried;
- TDS `ATTENTION` is deferred as same-socket reuse/lifecycle work, not claimed
  as implemented.

- [x] **Step 3: Add the upstream candidate**

Use the next registry ID only after final evidence. State:

```text
VERIFIED_FORK
```

Never use `APPROVED_TO_PUBLISH`.

- [ ] **Step 4: Commit, push and merge cumulative docs**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record automatic cancellation retirement candidate"
git push -u origin docs/transaction-cancellation-retirement-status
```

Merge only into `test/sql-auth-validation` and push only to `origin`.

- [ ] **Step 5: Update the separate executive registry**

In `.worktrees/docs-upstream-pr-plan`, update only
`2026-07-25-fastmssql-upstream-contribution-plan.md`, commit and push
`docs/upstream-pr-plan`. Keep this branch intentionally separate.

---

## Completion evidence

This plan is complete only when all are true:

```text
TX-032–TX-034                        PASS
TX-026 strengthened                  PASS
strict SQL-auth IDs                  277/277
strict suite                         zero failures
applicable upstream suite            zero failures
Rust / fmt / Clippy / RustSec         PASS
pooled stress                         bounded at pool.max_size
cancelled request/session cleanup     bounded without close()
cancelled pooled connection IDs       replaced
cancelled direct transaction          rolled back by session close
cancelled durable COMMIT               not rolled back or retried
origin                                galeamarcel/FastMssql
upstream push                         DISABLED
upstream PR                           not created
```
