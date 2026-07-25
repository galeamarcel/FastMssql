# FastMssql `CommitOutcomeUnknown` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. This thread does not authorize
> subagent delegation.

**Goal:** Expose a typed, non-retryable outcome when FastMssql cannot prove
whether SQL Server applied COMMIT, retire the affected connection, and prove
the behavior with deterministic TLS-transparent fault injection.

**Architecture:** Classification occurs at the Rust transaction state boundary
after a valid transaction enters `Committing`. Non-fatal structured SQL Server
errors remain `SqlError`; every other in-flight COMMIT failure becomes
`CommitOutcomeUnknown` with the original error as its cause. An in-process
asyncio TCP proxy withholds only server-to-client bytes so SQL Server can be
proven to commit while the client loses the acknowledgement.

**Tech Stack:** Rust, PyO3 0.29, Tokio, Tiberius/TDS, bb8, Python 3.11+,
asyncio, pytest, Ruff, Maturin, SQL Server Developer with SQL authentication in
Docker, Git worktrees.

## Global Constraints

- All development, commits and pushes use
  `https://github.com/galeamarcel/FastMssql.git`.
- `https://github.com/Rivendael/FastMssql.git` remains fetch-only with push URL
  `DISABLED`.
- Do not create any upstream PR without separate explicit approval for the
  final diff.
- Do not create or publish a Tiberius fork.
- Use separate `docs/*`, `test/*`, `fix/*` worktrees.
- Demonstrate TX-027–TX-030 RED before production changes; TX-031 is the
  already-correct control.
- Do not add automatic retry, implicit rollback after an unknown COMMIT,
  TDS `ATTENTION`, distributed transactions, savepoints or timeout APIs.
- SQL-auth secrets remain in `.env.sql-auth.local`, ignored by Git, and never
  appear in test output or exceptions.
- Keep TLS enabled through the fault proxy.
- Identify physical sockets by
  `sys.dm_exec_connections.connection_id`, not SPID.
- A timeout or proxy failure is a failed test, never a skip, xfail or swallowed
  exception.
- Preserve author `Marcel Galea <galea.marcel@gmail.com>`.

## File Map

- Create `tests/sql_auth_strict/tcp_fault_proxy.py`: one-purpose transparent
  fault proxy used only by SQL-auth tests.
- Modify `tests/sql_auth_strict/test_transactions_strict.py`: TX-027–TX-031.
- Modify `tests/sql_auth_strict/test_matrix_contract.py`: count and contract
  for 274 specification IDs.
- Modify
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`:
  declare TX-027–TX-031.
- Modify `src/types.rs`: exception type and factory.
- Modify `src/lib.rs`: native extension export.
- Modify `src/transaction.rs`: classification and fail-closed state handling.
- Modify `python/fastmssql/__init__.py`: package export and context-manager
  behavior.
- Modify `python/fastmssql/fastmssql.pyi`: native exception stub.
- Modify `python/fastmssql/__init__.pyi`: package exception import/export.
- Modify `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: live status only after
  all gates pass.
- Modify
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`.
- Modify on the separate `docs/upstream-pr-plan` branch:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`.
- Promote the separate upstream candidate only after verification.

---

### Task 1: Publish and integrate the approved design

**Files:**

- Create:
  `docs/superpowers/specs/2026-07-25-fastmssql-commit-outcome-unknown-design.md`
- Create:
  `docs/superpowers/plans/2026-07-25-fastmssql-commit-outcome-unknown.md`

**Interfaces:**

- Consumes: cumulative fork branch `test/sql-auth-validation`.
- Produces: an auditable design/plan base for both RED and fix branches.

- [ ] **Step 1: Verify the document branch is isolated and clean**

```bash
git branch --show-current
git status --short
git diff --check
```

Expected branch: `docs/commit-outcome-unknown-design`. Expected status after
adding this plan: only this plan and the reviewed spec are present.

- [ ] **Step 2: Scan the design and plan for placeholders**

```bash
rg -n 'T[B]D|T[O]DO|F[I]XME|implement l[a]ter|fill i[n]' \
  docs/superpowers/specs/2026-07-25-fastmssql-commit-outcome-unknown-design.md \
  docs/superpowers/plans/2026-07-25-fastmssql-commit-outcome-unknown.md
```

Expected: no matches.

- [ ] **Step 3: Commit and push the plan**

```bash
git add \
  docs/superpowers/specs/2026-07-25-fastmssql-commit-outcome-unknown-design.md \
  docs/superpowers/plans/2026-07-25-fastmssql-commit-outcome-unknown.md
git commit -m "docs: plan unknown commit outcome handling"
git push -u origin docs/commit-outcome-unknown-design
```

- [ ] **Step 4: Merge only into the cumulative fork branch**

```bash
git -C /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql \
  merge --no-ff docs/commit-outcome-unknown-design \
  -m "merge: plan unknown commit outcome handling"
git -C /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql \
  push origin test/sql-auth-validation
```

Expected: `origin` is the personal fork and `upstream` remains untouched.

---

### Task 2: Create the RED branch and specification contracts

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Consumes: current 269-ID SQL-auth specification and `case()` result
  recording.
- Produces: TX-027–TX-031 and an exact total of 274 IDs.

- [ ] **Step 1: Create the isolated test worktree**

```bash
git worktree add \
  .worktrees/test-commit-outcome-unknown \
  -b test/commit-outcome-unknown \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-commit-outcome-unknown/.env.sql-auth.local
```

Verify `.worktrees` is ignored before this command.

- [ ] **Step 2: Add the five specification IDs**

Append the following exact contract:

```markdown
- `TX-027`: `CommitOutcomeUnknown` is public and independent from
  `SqlConnectionError` and `SqlError`.
- `TX-028`: a pooled COMMIT applied by SQL Server with its response withheld
  raises `CommitOutcomeUnknown`, is non-retryable, retires the socket and
  releases the pool waiter.
- `TX-029`: a direct compatibility transaction has the same unknown-outcome
  type and closes its physical socket.
- `TX-030`: automatic context-manager COMMIT propagates
  `CommitOutcomeUnknown` without calling rollback or retrying.
- `TX-031`: SQL Server error 3902 at severity 16 remains `SqlError`.
```

- [ ] **Step 3: Update every exact matrix count**

In `tests/sql_auth_strict/test_matrix_contract.py`, replace the five
contractual occurrences of `269` with `274`, including the test function name.
Do not mechanically replace unrelated historical prose.

- [ ] **Step 4: Add the public exception RED contract**

Add `import fastmssql` and:

```python
@case("TX-027")
def test_commit_outcome_unknown_is_a_distinct_public_exception() -> None:
    unknown_type = getattr(fastmssql, "CommitOutcomeUnknown", None)
    assert unknown_type is not None
    assert issubclass(unknown_type, Exception)
    assert not issubclass(unknown_type, fastmssql.SqlConnectionError)
    assert not issubclass(unknown_type, fastmssql.SqlError)
    assert "CommitOutcomeUnknown" in fastmssql.__all__
```

- [ ] **Step 5: Run the static contract RED**

```bash
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_transactions_strict.py::test_commit_outcome_unknown_is_a_distinct_public_exception \
  -q --tb=short
```

Expected: matrix tests pass with 274 IDs; TX-027 fails because the public
exception does not exist.

- [ ] **Step 6: Commit the specification contract**

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: require typed unknown commit outcomes"
```

---

### Task 3: Add the deterministic TLS-transparent proxy

**Files:**

- Create: `tests/sql_auth_strict/tcp_fault_proxy.py`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Produces:
  `DownstreamGateProxy(target_host: str, target_port: int)`.
- Methods:
  `start()`, `pause_downstream()`, `wait_until_downstream_held()`,
  `abort_connections()`, `resume_downstream()`, `close()`.
- Properties: `host`, `port`, `accepted_connections`.

- [ ] **Step 1: Implement the proxy test utility**

Create this complete behavior:

```python
from __future__ import annotations

import asyncio
from contextlib import suppress


class DownstreamGateProxy:
    def __init__(self, target_host: str, target_port: int) -> None:
        self._target_host = target_host
        self._target_port = target_port
        self._server: asyncio.AbstractServer | None = None
        self._downstream_gate = asyncio.Event()
        self._downstream_gate.set()
        self._downstream_held = asyncio.Event()
        self._handlers: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._unexpected: list[BaseException] = []
        self.accepted_connections = 0

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def __aenter__(self) -> "DownstreamGateProxy":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("proxy is already started")
        self._server = await asyncio.start_server(
            self._accept,
            host=self.host,
            port=0,
        )

    async def _accept(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        handler = asyncio.current_task()
        if handler is None:
            raise RuntimeError("proxy handler has no asyncio task")
        self._handlers.add(handler)
        self.accepted_connections += 1
        server_writer: asyncio.StreamWriter | None = None
        try:
            server_reader, server_writer = await asyncio.open_connection(
                self._target_host,
                self._target_port,
            )
            self._writers.update((client_writer, server_writer))
            upstream = asyncio.create_task(
                self._relay(client_reader, server_writer, downstream=False)
            )
            downstream = asyncio.create_task(
                self._relay(server_reader, client_writer, downstream=True)
            )
            try:
                await asyncio.gather(upstream, downstream)
            finally:
                upstream.cancel()
                downstream.cancel()
                await asyncio.gather(
                    upstream,
                    downstream,
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as error:
            if self._downstream_gate.is_set():
                self._unexpected.append(error)
        finally:
            for writer in (client_writer, server_writer):
                if writer is None:
                    continue
                self._writers.discard(writer)
                writer.close()
                with suppress(ConnectionError, OSError):
                    await writer.wait_closed()
            self._handlers.discard(handler)

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        downstream: bool,
    ) -> None:
        while data := await reader.read(64 * 1024):
            if downstream and not self._downstream_gate.is_set():
                self._downstream_held.set()
                await self._downstream_gate.wait()
            writer.write(data)
            await writer.drain()

    def pause_downstream(self) -> None:
        self._downstream_held.clear()
        self._downstream_gate.clear()

    def resume_downstream(self) -> None:
        self._downstream_gate.set()

    async def wait_until_downstream_held(
        self,
        timeout: float = 2.0,
    ) -> None:
        await asyncio.wait_for(self._downstream_held.wait(), timeout)

    async def abort_connections(self) -> None:
        handlers = tuple(self._handlers)
        for handler in handlers:
            handler.cancel()
        for writer in tuple(self._writers):
            writer.transport.abort()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.abort_connections()
        self.resume_downstream()
        if self._unexpected:
            rendered = " | ".join(
                f"{type(error).__name__}: {error}"
                for error in self._unexpected
            )
            raise AssertionError(f"unexpected proxy failure: {rendered}")
```

- [ ] **Step 2: Add a proxy smoke test**

The smoke test creates a real FastMssql `Connection` through the proxy with
`SslConfig.development()`, executes `SELECT 1`, calls `disconnect()`, then
asserts `proxy.accepted_connections == 1`. This exercises a complete TLS/TDS
session and lets `proxy.close()` distinguish a clean shutdown from expected
fault injection.

- [ ] **Step 3: Run Ruff and the proxy smoke test**

```bash
../../.venv/bin/ruff check \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k tcp_fault_proxy_smoke -q --tb=short
```

Expected: PASS. This is infrastructure validation, not production behavior.

- [ ] **Step 4: Commit the proxy**

```bash
git add \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: add deterministic commit response fault proxy"
```

---

### Task 4: Reproduce pooled and direct unknown outcomes

**Files:**

- Modify: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Consumes: `DownstreamGateProxy`, SQL-auth fixtures, `CleanupRegistry`.
- Produces: live RED cases TX-028 and TX-029.

- [ ] **Step 1: Allow the pooled helper to use a proxy endpoint**

Extend `_pooled_transaction_connection` with:

```python
server: str | None = None,
port: int | None = None,
```

and pass:

```python
server=server or config.host,
port=port or config.port,
```

- [ ] **Step 2: Add a bounded row-visibility helper**

```python
async def _wait_for_row_count(
    connection: Connection,
    table: str,
    expected: int,
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await scalar(
            connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        ) == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"row count in {table} did not reach {expected}"
    )
```

The table value is always produced by `quote_identifier`.

- [ ] **Step 3: Add TX-028 pooled fault injection**

Create
`test_pooled_commit_ack_loss_is_typed_and_retires_connection`. The test must:

1. create a unique table through `sa_connection`;
2. start `DownstreamGateProxy`;
3. create one shared `Connection` through the proxy with `max_size=1`;
4. begin the first transaction and record `connection_id`;
5. assert `encrypt_option == "TRUE"`;
6. insert `id=1`;
7. start a second transaction's `begin()` and prove it waits;
8. pause downstream and start `commit()`;
9. poll through `sa_connection` until the row is visible;
10. wait until proxy response bytes are held;
11. abort the old transports and resume downstream;
12. capture the commit exception without accepting a generic type;
13. assert its type name is `CommitOutcomeUnknown`;
14. assert `operation == "commit"`, `retryable is False`,
    `connection_discarded is True`, and `__cause__ is not None`;
15. assert exactly one row exists;
16. await the waiting transaction and assert a different `connection_id`;
17. rollback/close and assert zero active pool leases.

Use `asyncio.wait_for` for every commit/waiter boundary and unconditional
`finally` cleanup.

- [ ] **Step 4: Add TX-029 direct fault injection**

Create `test_direct_commit_ack_loss_is_typed_and_closes_socket`. Repeat the
database proof through:

```python
transaction = Transaction(
    server=proxy.host,
    port=proxy.port,
    database=sql_auth_config.database,
    username=sql_auth_config.owner_user,
    password=sql_auth_config.owner_password,
    ssl_config=SslConfig.development(),
)
```

After the fault, assert the exception type name, one committed row,
`transaction.is_connected() is False`, and successful idempotent `close()`.

- [ ] **Step 5: Run TX-028/TX-029 RED**

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'pooled_commit_ack_loss or direct_commit_ack_loss' \
  -q --tb=short
```

Expected: both tests reach a proven committed row but fail because FastMssql
raises `TlsError`, `SqlConnectionError`, or another generic exception instead
of `CommitOutcomeUnknown`.

- [ ] **Step 6: Commit the live reproductions**

```bash
git add tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: reproduce unknown commit outcomes"
```

---

### Task 5: Add context-manager RED and deterministic SQL control

**Files:**

- Modify: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Produces: TX-030 RED and TX-031 control.

- [ ] **Step 1: Add the recording transaction core**

```python
class _RecordingUnknownCommitCore:
    def __init__(self, unknown_type: type[Exception]) -> None:
        self._unknown_type = unknown_type
        self.begin_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    async def begin(self) -> None:
        self.begin_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1
        raise self._unknown_type(
            "COMMIT completion was not confirmed; "
            "the transaction outcome is unknown"
        )

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
```

- [ ] **Step 2: Add TX-030**

```python
@case("TX-030")
@pytest.mark.asyncio
async def test_context_manager_does_not_rollback_unknown_commit() -> None:
    unknown_type = getattr(fastmssql, "CommitOutcomeUnknown", None)
    assert unknown_type is not None
    core = _RecordingUnknownCommitCore(unknown_type)
    transaction = Transaction._from_rust(core)

    with pytest.raises(unknown_type):
        async with transaction:
            pass

    assert core.begin_calls == 1
    assert core.commit_calls == 1
    assert core.rollback_calls == 0
    assert core.close_calls == 1
```

- [ ] **Step 3: Add TX-031 server-error control**

Create `test_server_commit_rejection_remains_sql_error`. Create a unique
primary-key table, begin a pooled transaction, execute
`SET XACT_ABORT ON`, insert `id=1`, then execute the same insert again and
assert error 2627 or 2601. Call `commit()` and assert:

```python
with pytest.raises(SqlError) as captured:
    await transaction.commit()
assert captured.value.code == 3902
assert captured.value.severity == 16
assert type(captured.value).__name__ == "SqlError"
```

- [ ] **Step 4: Run the five-case RED/control set**

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'commit_outcome_unknown or commit_ack_loss or unknown_commit' \
  -q --tb=short
```

Expected baseline: TX-027–TX-030 fail for the missing type/behavior and TX-031
passes as the deterministic control.

- [ ] **Step 5: Commit and push only the test branch**

```bash
git add tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: require safe commit outcome propagation"
git push -u origin test/commit-outcome-unknown
```

Record exact RED counts and exception types before starting production code.

---

### Task 6: Create the fix branch and public exception

**Files:**

- Modify: `src/types.rs`
- Modify: `src/lib.rs`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`

**Interfaces:**

- Produces native class `CommitOutcomeUnknown` and:

```rust
pub(crate) fn create_commit_outcome_unknown(cause: PyErr) -> PyResult<PyErr>
```

- [ ] **Step 1: Create the fix worktree from the RED branch**

```bash
git worktree add \
  .worktrees/fix-commit-outcome-unknown \
  -b fix/commit-outcome-unknown \
  test/commit-outcome-unknown
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-commit-outcome-unknown/.env.sql-auth.local
```

- [ ] **Step 2: Declare the exception and factory**

In `src/types.rs`:

```rust
create_exception!(crate::fastmssql, CommitOutcomeUnknown, PyException);

const UNKNOWN_COMMIT_MESSAGE: &str =
    "COMMIT completion was not confirmed; the transaction outcome is unknown";

pub(crate) fn create_commit_outcome_unknown(cause: PyErr) -> PyResult<PyErr> {
    Python::attach(|py| {
        let error = CommitOutcomeUnknown::new_err(UNKNOWN_COMMIT_MESSAGE);
        {
            let value = error.value(py);
            value.setattr("message", UNKNOWN_COMMIT_MESSAGE)?;
            value.setattr("operation", "commit")?;
            value.setattr("retryable", false)?;
            value.setattr("connection_discarded", true)?;
        }
        error.set_cause(py, Some(cause));
        Ok(error)
    })
}
```

- [ ] **Step 3: Export native and package classes**

Add `CommitOutcomeUnknown` to `pub use types::{...}`, the PyO3 module, both
stub files, the package-root import and `__all__`.

The stub is:

```python
class CommitOutcomeUnknown(Exception):
    """COMMIT may have been applied but its completion was not confirmed."""

    message: str
    operation: str
    retryable: bool
    connection_discarded: bool
```

- [ ] **Step 4: Build and run TX-027 GREEN**

```bash
../../.venv/bin/maturin develop --release
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py::test_commit_outcome_unknown_is_a_distinct_public_exception \
  -q --tb=short
```

Expected: TX-027 passes. TX-028–TX-030 are still RED.

- [ ] **Step 5: Commit the public API**

```bash
git add \
  src/types.rs \
  src/lib.rs \
  python/fastmssql/fastmssql.pyi \
  python/fastmssql/__init__.py \
  python/fastmssql/__init__.pyi
git commit -m "feat: expose unknown commit outcome error"
```

---

### Task 7: Classify in-flight COMMIT failures

**Files:**

- Modify: `src/transaction.rs`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Consumes: `create_commit_outcome_unknown`.
- Produces:

```rust
fn is_deterministic_commit_rejection(error: &PyErr) -> bool
```

- [ ] **Step 1: Add deterministic server-error classification**

Import `SqlError` and:

```rust
fn is_deterministic_commit_rejection(error: &PyErr) -> bool {
    Python::attach(|py| {
        if !error.is_instance_of::<SqlError>(py) {
            return false;
        }
        error
            .value(py)
            .getattr("severity")
            .and_then(|value| value.extract::<u8>())
            .is_ok_and(|severity| severity <= 19)
    })
}
```

- [ ] **Step 2: Validate physical connection before `Committing`**

In `execute_transaction_command`, after state validation and before the
in-flight transition:

```rust
if session.conn.is_none() {
    return Err(PyRuntimeError::new_err("Connection is not established"));
}
session.state = command.in_flight_state();
```

This preserves local invariant errors instead of misclassifying them as an
unknown COMMIT.

- [ ] **Step 3: Wrap only uncertain COMMIT errors**

After marking/taking the connection and setting `Failed`:

```rust
let public_error = if matches!(command, TransactionCommand::Commit)
    && !is_deterministic_commit_rejection(&error)
{
    create_commit_outcome_unknown(error)?
} else {
    error
};
Err(public_error)
```

The connection must be marked by `finish_operation` or `mark_unusable` and
removed before constructing the public error.

- [ ] **Step 4: Rebuild and run TX-028/TX-029/TX-031**

```bash
../../.venv/bin/maturin develop --release
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'pooled_commit_ack_loss or direct_commit_ack_loss or server_commit_rejection' \
  -q --tb=short
```

Expected: all three pass. The faulted pooled connection ID differs from the
replacement connection ID, and error 3902 remains `SqlError`.

- [ ] **Step 5: Commit the classification**

```bash
git add src/transaction.rs
git commit -m "fix: classify unconfirmed commit outcomes"
```

---

### Task 8: Make automatic commit preserve the unknown outcome

**Files:**

- Modify: `python/fastmssql/__init__.py`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Consumes: package-root `CommitOutcomeUnknown`.
- Produces: no rollback call after automatic unknown COMMIT.

- [ ] **Step 1: Add the explicit context-manager branch**

Change only the automatic commit error handling:

```python
try:
    await self.commit()
except CommitOutcomeUnknown:
    raise
except Exception as commit_err:
    try:
        await self.rollback()
    except Exception:
        pass
    raise commit_err
```

The remaining best-effort rollback suppression is pre-existing and outside
this candidate. Do not add a broader refactor in this task.

- [ ] **Step 2: Run TX-030 GREEN**

```bash
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py::test_context_manager_does_not_rollback_unknown_commit \
  -q --tb=short
```

Expected call counts: begin 1, commit 1, rollback 0, close 1.

- [ ] **Step 3: Run all five cases**

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'commit_outcome_unknown or commit_ack_loss or unknown_commit or server_commit_rejection' \
  -q --tb=short
```

Expected: TX-027–TX-031 all pass.

- [ ] **Step 4: Commit and push the fix branch**

```bash
git add python/fastmssql/__init__.py
git commit -m "fix: preserve unknown outcome in transaction context"
git push -u origin fix/commit-outcome-unknown
```

---

### Task 9: Run complete verification on the fix source tree

**Files:**

- No production edits unless a failing gate exposes a separately reproduced
  defect.

**Interfaces:**

- Produces exact evidence for integration and the live audit.

- [ ] **Step 1: Run static and Rust gates**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
/private/tmp/fastmssql-cargo-audit-0.22.2/bin/cargo-audit \
  audit --deny warnings
../../.venv/bin/ruff check \
  python \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/tcp_fault_proxy.py
../../.venv/bin/python -m compileall -q python tests/sql_auth_strict
git diff --check
```

Expected: all exit zero; RustSec scans 219 dependencies with zero findings.

- [ ] **Step 2: Run transaction regressions**

Use SQL-auth plus the upstream transaction files:

```bash
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/test_transaction.py \
  tests/test_transaction_flags.py \
  tests/test_transaction_forwarded_methods.py \
  -q --tb=short
```

Expected: zero failures.

- [ ] **Step 3: Run the complete strict suite**

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=/private/tmp/commit-outcome-strict.json \
FASTMSSQL_FRAMEWORK_METRICS_PATH=/private/tmp/commit-outcome-framework.json \
FASTMSSQL_LOAD_METRICS_PATH=/private/tmp/commit-outcome-load.json \
../../.venv/bin/python -m pytest tests/sql_auth_strict -q --tb=no
```

Expected: all 274 specification IDs reported and zero failures.

- [ ] **Step 4: Run the complete applicable upstream suite**

Export all six upstream fixture variables:

```text
FASTMSSQL_TEST_CONNECTION_STRING
FAST_MSSQL_TEST_DB_USER
FAST_MSSQL_TEST_DB_PASSWORD
FAST_MSSQL_TEST_SERVER
FAST_MSSQL_TEST_PORT
FAST_MSSQL_TEST_DATABASE
```

Run `pytest -n1 tests` with the five Azure files and
`tests/sql_auth_strict` ignored. Expected: 896/896 PASS.

- [ ] **Step 5: Run pooled stress**

```bash
FASTMSSQL_TRANSACTION_STRESS_STRATEGY=pooled \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE=100 \
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
./scripts/sql_auth/run_transaction_stress.sh
```

Expected: zero failed operations, at most 100 physical/SQL sessions, exact
commit/rollback totals, smoke PASS and zero remaining application sessions.

- [ ] **Step 6: Inspect repository safety**

```bash
git status --short
git diff --check
git remote -v
git log --format=fuller -8
```

Expected: no uncommitted files, author is Marcel Galea, `origin` is the fork,
and upstream push remains `DISABLED`.

---

### Task 10: Integrate the verified fix only into the fork

**Files:**

- Merge commit only.

**Interfaces:**

- Consumes: verified `fix/commit-outcome-unknown`.
- Produces: cumulative fork source tree with the P0 fix.

- [ ] **Step 1: Merge locally**

```bash
git -C /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql \
  merge --no-ff fix/commit-outcome-unknown \
  -m "merge: classify unknown commit outcomes"
```

- [ ] **Step 2: Verify identical production tree and focused behavior**

Compare the merge tree with the feature tree, run the five focused cases and
`cargo test --locked` on the merged result.

- [ ] **Step 3: Push only the cumulative fork branch**

```bash
git -C /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql \
  push origin test/sql-auth-validation
```

Do not push or create anything against `upstream`.

---

### Task 11: Update the live audit and upstream candidate registry

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Modify separately on `docs/upstream-pr-plan`:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`

**Interfaces:**

- Consumes: exact RED/GREEN commits and final gate output.
- Produces: a new independently reviewable upstream candidate with status
  `VERIFIED_FORK`, never `APPROVED_TO_PUBLISH`.

- [ ] **Step 1: Create `docs/commit-outcome-unknown-status`**

Base it on the cumulative merged source. Do not edit the fix branch's audit.

- [ ] **Step 2: Record exact evidence**

Document:

- RED counts and original exception types;
- test/fix/merge commit hashes;
- proxy proof that the row committed while the response was withheld;
- direct and pooled socket retirement;
- strict/upstream/Rust/RustSec/stress totals;
- conservative classification trade-off;
- cancellation still surfaces `CancelledError` while retiring the socket;
- no TDS `ATTENTION`, retry, distributed transaction recovery or automatic
  reconciliation.

- [ ] **Step 3: Promote the candidate in the cumulative documents**

Add the candidate after PR-17 in the audit and detailed roadmap. Its state is
`VERIFIED_FORK`; porting requires a clean branch from the latest
`upstream/master` and comparison with draft #121.

- [ ] **Step 4: Commit, push and merge cumulative documentation on the fork**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record unknown commit outcome candidate"
git push -u origin docs/commit-outcome-unknown-status
```

Merge into `test/sql-auth-validation`, rerun `git diff --check`, and push only
to `origin`.

- [ ] **Step 5: Update the separate executive upstream registry**

In the existing `.worktrees/docs-upstream-pr-plan` worktree, update only
`docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`.
Move PR-18 from reserved to the main registry as `VERIFIED_FORK`, add its exact
RED/GREEN evidence, commit with:

```bash
git add \
  docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md
git commit -m "docs: promote unknown commit outcome candidate"
git push origin docs/upstream-pr-plan
```

Do not merge this intentionally separate registry branch into the cumulative
source branch unless that documentation strategy is changed explicitly.

---

## Completion Evidence

This plan is complete only when all of the following are true on the exact
cumulative fork tree:

```text
TX-027–TX-031                         PASS
strict SQL-auth IDs                   274/274 reported
strict suite                          zero failures
applicable upstream suite             896/896 PASS
Rust unit tests                       zero failures
fmt / Clippy -D warnings              PASS
RustSec                               219 scanned, zero findings
pooled stress                         bounded at pool.max_size
faulted pooled/direct sockets         retired
unknown COMMIT rollback/retry         zero
origin                                galeamarcel/FastMssql
upstream push                         DISABLED
upstream PR                           not created
```
