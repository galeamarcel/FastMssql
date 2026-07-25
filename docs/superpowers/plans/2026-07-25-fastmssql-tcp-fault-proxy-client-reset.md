# FastMssql TCP Fault Proxy Client Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SQL-auth TCP fault proxy classify one explicitly declared
client EOF or reset as an expected disconnect so `TX-034` is deterministic
without retrying the test, hiding unrelated failures or changing production
FastMssql code.

**Architecture:** Add a synchronous one-shot declaration to
`DownstreamGateProxy` and consume it only around the client-to-server
`StreamReader.read()` boundary. Prove the complete scope with one injected
asynchronous contract, declare the expected retirement immediately before
`TX-034` cancels `COMMIT`, and leave connection, write, downstream and
non-reset failures on their current strict paths. Integrate and verify this
test-harness candidate before refreshing the already published PoolConfig
RED/GREEN branches.

**Tech Stack:** Python 3.13, asyncio streams, pytest 9.1.1, pytest-asyncio
1.4.0, Ruff, uv, Rust 1.94.0, Cargo, PyO3 0.29.0, maturin 1.14.1, GitHub
Actions and SQL Server 2022 Developer Edition in the dedicated
`fastmssql-sql-auth-dev` Docker container.

## Global Constraints

- All branches, commits and pushes target
  `https://github.com/galeamarcel/FastMssql.git`.
- The implementation baseline is `test/sql-auth-validation` at
  `5a1369518456eabe90ac078653d42b6b2f38bdc1`.
- The approved design commit is
  `fbe2c23d4b977e2993e82e3060be1610647fa229`.
- The approved behavioral source is
  `docs/superpowers/specs/2026-07-25-fastmssql-tcp-fault-proxy-client-reset-design.md`.
- The `upstream` remote remains fetch-only with push URL `DISABLED`.
- No upstream branch, push, pull request, fork, version bump, package
  publication or release is authorized.
- All commits use `Marcel Galea <galea.marcel@gmail.com>`.
- Keep design/plan, RED test, GREEN helper fix and status documentation on
  separate branches.
- The user selected inline execution and pre-approved future design, spec,
  plan and implementation work; do not dispatch subagents.
- `expect_client_disconnect()` grants exactly one pending allowance per call.
- Only client-to-server `reader.read()` EOF or `ConnectionResetError` may
  consume an allowance.
- Undeclared EOF remains normal, matching the existing proxy contract.
- Undeclared client reset, server-to-client reset, connect failures,
  `writer.write()`/`writer.drain()` failures and non-reset exceptions remain
  observable.
- Existing `_aborting` shutdown classification and the narrowly scoped
  `writer.wait_closed()` cleanup suppression do not change.
- `TX-034` declares the expected disconnect immediately before
  `commit_task.cancel()`, after downstream acknowledgement bytes are held.
- Do not add retry, rerun, sleep-based acceptance, `skip`, `xfail`, warning
  conversion, broad suppression or accepted alternative outcomes.
- Do not change FastMssql Rust source, Python package runtime, public API,
  transaction state, pool behavior or SQL Server configuration.
- Rollback is an independent revert of the test-helper GREEN merge; it
  restores the measured TX-034 race but requires no application rollback,
  schema change, dependency downgrade or package release.
- Add exactly one strict Python test function and no SQL-auth case ID.
- The stabilized harness target is exactly `296` strict functions,
  `285` specification IDs, `13` Rust tests and `902` applicable upstream
  tests.
- The refreshed PoolConfig target is exactly `296` strict functions,
  `285` specification IDs, `14` Rust tests, `7` PyO3 contract tests and
  `906` applicable upstream tests.
- `TX-034` must pass 50 consecutive fresh pytest processes; this is evidence,
  never an in-test retry mechanism.
- Docker operations target only `fastmssql-sql-auth-dev`.
- SQL-authentication secrets remain only in ignored `.env.sql-auth.local` and
  must not appear in logs or committed artifacts.
- Generated matrix/report changes are committed only from status branches
  after all required lanes pass.

---

## File Map

- `tests/sql_auth_strict/tcp_fault_proxy.py`: owns the explicit pending
  client-disconnect counter and the narrow read-boundary classification.
- `tests/sql_auth_strict/test_transactions_strict.py`: owns the deterministic
  injected helper contract and the one-line `TX-034` declaration.
- `scripts/sql_auth/run_all.sh`: executes the complete locked local enterprise
  gate and generates evidence; it is executed but not modified.
- `docs/SQL_AUTH_TEST_MATRIX.md`: receives regenerated `285/285` case evidence
  only on the TCP-proxy and PoolConfig status branches.
- `docs/SQL_AUTH_TEST_REPORT.md`: receives exact lane counts, commands and SHA
  evidence only on status branches.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: remains the authoritative
  live enterprise status document.
- `docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md`:
  remains the PoolConfig execution source and is corrected on its status
  branch for the new strict count.
- `.github/workflows/rust-unit-tests.yml`: remains unchanged for the harness
  candidate; the PoolConfig branch retains its approved extension contract on
  Linux, macOS and Windows.
- `.github/workflows/dependency-security.yml`: remains unchanged and proves
  RustSec at every cumulative SHA.
- `docs/superpowers/specs/2026-07-25-fastmssql-tcp-fault-proxy-client-reset-design.md`:
  remains the behavioral and repository-boundary source of truth.

## Branch Graph

```text
test/sql-auth-validation at 5a136951
  |
  +-- docs/tcp-fault-proxy-client-reset-design
  |     fbe2c23 approved design
  |     this implementation-plan commit
  |
  +-- test/tcp-fault-proxy-client-reset
  |     one deterministic RED harness contract
  |
  +-- fix/tcp-fault-proxy-client-reset
  |     one-shot helper behavior plus the TX-034 declaration
  |
  +-- test/sql-auth-validation
  |     exact locally and hosted-green harness merge
  |
  +-- docs/tcp-fault-proxy-client-reset-status
  |     generated matrix/report and live-audit evidence
  |
  +-- refreshed test/pool-config-default-consistency
  |     existing two RED commits plus the stabilized cumulative baseline
  |
  +-- refreshed fix/pool-config-default-consistency
  |     existing GREEN commit plus the refreshed RED branch
  |
  +-- test/sql-auth-validation
  |     exact locally and hosted-green PoolConfig merge
  |
  +-- docs/pool-config-default-consistency-status
        generated evidence, live audit and future upstream candidate record
```

The design branch is merged into the cumulative fork before the RED branch is
created. The GREEN branch starts from the committed RED branch. The harness
status branch starts only after the technical cumulative SHA passes the full
local gate and hosted RustSec/Cargo workflows. Published PoolConfig commits
are never rebased or force-pushed; the refreshed cumulative branch is merged
forward through RED and then GREEN.

## Authoritative Baselines

The last full harness-independent cumulative baseline establishes:

```text
Rust unit tests                     13
strict functional                  295
async strict                        16
framework                           28
resilience                           6
load                                 9
SQL-auth specification IDs         285
applicable upstream regression     902
```

The earlier PoolConfig verification at
`b5239c715934ab40e0aefc0b2a8a8fac10c7f869` established:

```text
PoolConfig Rust tests              14/14 PASS
PoolConfig pure                    92/92 PASS
PoolConfig integration             16/16 PASS
PyO3 build contract                 7/7 PASS
POOL-001 real SQL Server              PASS
strict functional                294/295, TX-034 cleanup only
async/framework/resilience/load       PASS
applicable upstream              906/906 PASS
```

The harness candidate adds one unmarked strict test function:

```text
Rust unit tests                     13
strict functional                  296
async strict                        16
framework                           28
resilience                           6
load                                 9
SQL-auth specification IDs         285
applicable upstream regression     902
```

After PoolConfig is refreshed, only its existing four upstream-visible tests
and one Rust test change those totals:

```text
Rust unit tests                     14
PoolConfig pure                     92
PoolConfig integration              16
PyO3 build contract                  7
strict functional                  296
async strict                        16
framework                           28
resilience                           6
load                                 9
SQL-auth specification IDs         285
applicable upstream regression     906
```

Any different collection count is investigated. It is never normalized to a
percentage or accepted as an equivalent result.

---

### Task 1: Integrate the approved design and create the RED worktree

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-25-fastmssql-tcp-fault-proxy-client-reset-design.md`
- Existing:
  `docs/superpowers/plans/2026-07-25-fastmssql-tcp-fault-proxy-client-reset.md`

**Interfaces:**

- Consumes: approved design commit `fbe2c23` and this committed plan.
- Produces: a documentation-only cumulative baseline plus isolated
  `test/tcp-fault-proxy-client-reset`.

- [ ] **Step 1: Verify the design branch, author and fork boundary**

Run in `.worktrees/docs-tcp-fault-proxy-client-reset-design`:

```bash
set -euo pipefail
git status --short --branch
git log -2 --format='%h %an <%ae> %s'
git remote -v
git rev-list --left-right --count \
  HEAD...origin/docs/tcp-fault-proxy-client-reset-design
git diff --check
```

Expected:

```text
branch           docs/tcp-fault-proxy-client-reset-design
tracked changes  none
origin parity    0 0
origin push      galeamarcel/FastMssql
upstream push    DISABLED
author           Marcel Galea <galea.marcel@gmail.com>
```

- [ ] **Step 2: Merge only design artifacts into the cumulative fork**

Run in the main `FastMssql` worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff docs/tcp-fault-proxy-client-reset-design \
  -m "merge: plan explicit TCP client reset handling"
git diff HEAD^ --name-status
git remote get-url --push origin
git remote get-url --push upstream
git push origin test/sql-auth-validation
```

Expected: only the approved specification and implementation plan enter the
cumulative branch. No source, helper, test, workflow or generated report
changes.

- [ ] **Step 3: Create the isolated RED branch/worktree**

Run in the main worktree:

```bash
git worktree add \
  .worktrees/test-tcp-fault-proxy-client-reset \
  -b test/tcp-fault-proxy-client-reset \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-tcp-fault-proxy-client-reset/.env.sql-auth.local
```

Expected: the branch is clean, the ignored environment symlink resolves to
the existing local SQL-auth configuration, and neither RED nor GREEN branch
existed beforehand.

- [ ] **Step 4: Build and verify the unchanged helper baseline**

Run in `.worktrees/test-tcp-fault-proxy-client-reset`:

```bash
set -euo pipefail
uv sync --locked --all-extras --dev
uv run maturin develop --release
uv run ruff check \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
uv run python -m compileall -q \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
```

Expected: build, Ruff and byte-compilation pass before the RED test is added.

---

### Task 2: Commit the deterministic RED helper contract

**Files:**

- Modify: `tests/sql_auth_strict/test_transactions_strict.py:135-208`
- Read: `tests/sql_auth_strict/tcp_fault_proxy.py:7-163`

**Interfaces:**

- Consumes:
  `DownstreamGateProxy._relay(reader, writer, *, downstream) -> None`.
- Produces:
  `test_tcp_fault_proxy_expected_client_disconnect_is_one_shot_and_scoped()`;
  one strict test function with no `@case` marker.

- [ ] **Step 1: Add controlled relay reader and writer doubles**

Add immediately before the proxy smoke tests in
`tests/sql_auth_strict/test_transactions_strict.py`:

```python
class _ScriptedRelayReader:
    def __init__(self, *events: bytes | BaseException) -> None:
        self._events = list(events)

    async def read(self, limit: int) -> bytes:
        assert limit == 64 * 1024
        if not self._events:
            raise AssertionError("scripted relay reader exhausted")
        event = self._events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


class _ScriptedRelayWriter:
    def __init__(
        self,
        *,
        write_error: BaseException | None = None,
        drain_error: BaseException | None = None,
    ) -> None:
        self._write_error = write_error
        self._drain_error = drain_error
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        if self._write_error is not None:
            raise self._write_error
        self.writes.append(data)

    async def drain(self) -> None:
        if self._drain_error is not None:
            raise self._drain_error
```

The doubles perform no network or SQL Server operation. Exhaustion is a hard
assertion so an implementation cannot pass by adding extra reads.

- [ ] **Step 2: Add the complete one-function policy contract**

Add after the doubles and before `test_tcp_fault_proxy_smoke`:

```python
@pytest.mark.asyncio
async def test_tcp_fault_proxy_expected_client_disconnect_is_one_shot_and_scoped(
) -> None:
    proxy = DownstreamGateProxy("127.0.0.1", 1433)
    writer = _ScriptedRelayWriter()

    await proxy._relay(
        _ScriptedRelayReader(b""),
        writer,
        downstream=False,
    )
    with pytest.raises(ConnectionResetError, match="undeclared client"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "undeclared client reset")
            ),
            writer,
            downstream=False,
        )

    proxy.expect_client_disconnect()
    await proxy._relay(
        _ScriptedRelayReader(
            ConnectionResetError(54, "declared client reset")
        ),
        writer,
        downstream=False,
    )
    with pytest.raises(ConnectionResetError, match="second client"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "second client reset")
            ),
            writer,
            downstream=False,
        )

    proxy.expect_client_disconnect()
    with pytest.raises(ConnectionResetError, match="server reset"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "server reset")
            ),
            writer,
            downstream=True,
        )
    await proxy._relay(
        _ScriptedRelayReader(b""),
        writer,
        downstream=False,
    )
    with pytest.raises(ConnectionResetError, match="after client EOF"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "reset after client EOF")
            ),
            writer,
            downstream=False,
        )

    proxy.expect_client_disconnect()
    with pytest.raises(ConnectionResetError, match="writer write"):
        await proxy._relay(
            _ScriptedRelayReader(b"request"),
            _ScriptedRelayWriter(
                write_error=ConnectionResetError(54, "writer write reset")
            ),
            downstream=False,
        )
    await proxy._relay(
        _ScriptedRelayReader(b""),
        writer,
        downstream=False,
    )
    with pytest.raises(ConnectionResetError, match="after write"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "reset after write failure")
            ),
            writer,
            downstream=False,
        )

    proxy.expect_client_disconnect()
    drain_writer = _ScriptedRelayWriter(
        drain_error=ConnectionResetError(54, "writer drain reset")
    )
    with pytest.raises(ConnectionResetError, match="writer drain"):
        await proxy._relay(
            _ScriptedRelayReader(b"request"),
            drain_writer,
            downstream=False,
        )
    assert drain_writer.writes == [b"request"]
    await proxy._relay(
        _ScriptedRelayReader(b""),
        writer,
        downstream=False,
    )
    with pytest.raises(ConnectionResetError, match="after drain"):
        await proxy._relay(
            _ScriptedRelayReader(
                ConnectionResetError(54, "reset after drain failure")
            ),
            writer,
            downstream=False,
        )
```

This one function proves undeclared EOF compatibility, undeclared reset
visibility, one-shot consumption, downstream isolation and preservation of
write/drain failures. It does not inspect the private counter.

- [ ] **Step 3: Run the focused contract and prove the intended RED**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py::test_tcp_fault_proxy_expected_client_disconnect_is_one_shot_and_scoped \
  -vv --tb=short
```

Expected:

```text
1 failed
AttributeError: 'DownstreamGateProxy' object has no attribute
'expect_client_disconnect'
```

An import error, database error, timeout or different assertion failure is not
the approved RED result.

- [ ] **Step 4: Prove registry and style invariants remain green**

Run:

```bash
uv run ruff check tests/sql_auth_strict/test_transactions_strict.py
uv run python -m compileall -q \
  tests/sql_auth_strict/test_transactions_strict.py
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
git diff --check
git diff --name-status
```

Expected: Ruff, compileall and the `285`-ID matrix contract pass. The only
tracked file changed on the RED branch is
`tests/sql_auth_strict/test_transactions_strict.py`.

- [ ] **Step 5: Commit and publish the RED branch only to the fork**

Run:

```bash
git add tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: specify expected TCP client disconnects"
git push -u origin test/tcp-fault-proxy-client-reset
git rev-list --left-right --count \
  HEAD...origin/test/tcp-fault-proxy-client-reset
```

Expected: parity `0 0`, author Marcel Galea and no production/helper change in
the RED commit.

---

### Task 3: Implement the one-shot helper behavior and wire `TX-034`

**Files:**

- Modify: `tests/sql_auth_strict/tcp_fault_proxy.py:10-120`
- Modify: `tests/sql_auth_strict/test_transactions_strict.py:1094-1100`

**Interfaces:**

- Consumes: the RED injected contract and existing
  `_relay(..., downstream: bool) -> None`.
- Produces:
  `DownstreamGateProxy.expect_client_disconnect() -> None` and one pending
  allowance consumed only at the client read boundary.

- [ ] **Step 1: Create the GREEN branch from the committed RED branch**

Run in the main worktree:

```bash
git worktree add \
  .worktrees/fix-tcp-fault-proxy-client-reset \
  -b fix/tcp-fault-proxy-client-reset \
  test/tcp-fault-proxy-client-reset
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-tcp-fault-proxy-client-reset/.env.sql-auth.local
```

Expected: the fix branch contains the exact published RED test and has no
additional changes.

- [ ] **Step 2: Add the private pending counter and public test-helper method**

In `DownstreamGateProxy.__init__`, immediately before `_aborting`, add:

```python
self._pending_client_disconnects = 0
```

Immediately before `_relay`, add:

```python
def expect_client_disconnect(self) -> None:
    """Allow one client-side EOF or reset to complete the relay normally."""
    self._pending_client_disconnects += 1
```

The method is synchronous so declaration cannot yield between incrementing the
counter and cancelling `COMMIT`.

- [ ] **Step 3: Replace only the relay read loop with narrow classification**

Replace the body of `_relay` with:

```python
while True:
    try:
        data = await reader.read(64 * 1024)
    except ConnectionResetError:
        if downstream or self._pending_client_disconnects == 0:
            raise
        self._pending_client_disconnects -= 1
        return

    if not data:
        if not downstream and self._pending_client_disconnects > 0:
            self._pending_client_disconnects -= 1
        return

    if downstream and not self._downstream_gate.is_set():
        self._downstream_held.set()
        await self._downstream_gate.wait()
    writer.write(data)
    await writer.drain()
```

The `try` block contains only `reader.read()`. Do not move gate waits,
`writer.write()` or `writer.drain()` into it. `CancelledError` and every
exception other than `ConnectionResetError` continue to propagate unchanged.

- [ ] **Step 4: Declare the intentional retirement at the exact TX boundary**

In `TX-034`, change:

```python
await proxy.wait_until_downstream_held()
commit_task.cancel()
```

to:

```python
await proxy.wait_until_downstream_held()
proxy.expect_client_disconnect()
commit_task.cancel()
```

Do not change any transaction assertion, timeout, cleanup statement or
business outcome.

- [ ] **Step 5: Run the deterministic GREEN contract**

Run in `.worktrees/fix-tcp-fault-proxy-client-reset`:

```bash
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py::test_tcp_fault_proxy_expected_client_disconnect_is_one_shot_and_scoped \
  -vv --tb=short
```

Expected: `1 passed`. Every expected exception is observed literally.

- [ ] **Step 6: Run focused static and diff checks**

Run:

```bash
uv run ruff check \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
uv run python -m compileall -q \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
git diff --check
git diff --name-status
git diff -- \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
```

Expected: the GREEN delta contains only the helper behavior and the single
`TX-034` declaration. No production, workflow, spec or generated document
changes.

---

### Task 4: Prove the real race is eliminated and publish GREEN

**Files:**

- Read/execute:
  `tests/sql_auth_strict/test_transactions_strict.py`
- Generated only:
  fresh result JSON files under a temporary directory.

**Interfaces:**

- Consumes: the complete uncommitted GREEN tree from Task 3 and the dedicated
  SQL-auth container.
- Produces: focused real-server evidence, 50/50 fresh `TX-034` passes and the
  published GREEN commit.

- [ ] **Step 1: Start and provision only the approved SQL Server container**

Run:

```bash
set -euo pipefail
set -a
source .env.sql-auth.local
set +a
test "${FASTMSSQL_SQL_AUTH_CONTAINER}" = "fastmssql-sql-auth-dev"
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
```

Expected: the existing dedicated container is healthy and provisioning is
idempotent.

- [ ] **Step 2: Run every proxy-focused transaction test once**

Run:

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/tcp-proxy-focused.json \
  uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k 'tcp_fault_proxy or cancelled_commit_retires_lease_without_claiming_rollback' \
  -vv --tb=short
```

Expected exactly:

```text
deterministic policy contract      PASS
real proxy smoke                   PASS
server-leg cleanup                 PASS
TX-034 durable cancelled COMMIT    PASS
background proxy errors            zero
```

- [ ] **Step 3: Execute 50 fresh TX-034 pytest processes**

Run:

```bash
set -euo pipefail
repeat_root="$(mktemp -d /private/tmp/fastmssql-tx034-repeat.XXXXXX)"
for iteration in $(seq 1 50); do
  FASTMSSQL_SQL_AUTH_RESULTS_PATH="${repeat_root}/result-${iteration}.json" \
    uv run pytest \
    tests/sql_auth_strict/test_transactions_strict.py::test_cancelled_commit_retires_lease_without_claiming_rollback \
    -q --tb=short
done
uv run python -c \
  'import json, pathlib, sys; paths=sorted(pathlib.Path(sys.argv[1]).glob("result-*.json")); assert len(paths)==50, len(paths); assert all(json.loads(path.read_text())["cases"]["TX-034"]["outcome"]=="passed" for path in paths); print("TX-034 fresh processes: 50/50 PASS")' \
  "${repeat_root}"
```

The loop exits on the first failure. It does not rerun a failed iteration and
does not modify the test.

- [ ] **Step 4: Run the complete strict transaction module**

Run:

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/transactions-green.json \
  uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -vv --tb=short
```

Expected: every transaction and proxy function passes with no skip or
background cleanup error.

- [ ] **Step 5: Recheck branch isolation and commit GREEN**

Run:

```bash
set -euo pipefail
git diff --check
git status --short --branch
git diff --name-status
git add \
  tests/sql_auth_strict/tcp_fault_proxy.py \
  tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: scope expected TCP client disconnects"
git push -u origin fix/tcp-fault-proxy-client-reset
git rev-list --left-right --count \
  HEAD...origin/fix/tcp-fault-proxy-client-reset
```

Expected: parity `0 0`; the fix commit changes only the two approved test
infrastructure files and is authored by Marcel Galea.

---

### Task 5: Integrate the harness fix and run the complete cumulative gate

**Files:**

- Merge only:
  `tests/sql_auth_strict/tcp_fault_proxy.py`,
  `tests/sql_auth_strict/test_transactions_strict.py`
- Execute: all repository sources/tests through `scripts/sql_auth/run_all.sh`
- Generated only in detached verification:
  `.artifacts/sql-auth/**`,
  `docs/SQL_AUTH_TEST_MATRIX.md`,
  `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**

- Consumes: focused-green `fix/tcp-fault-proxy-client-reset`.
- Produces: one exact local full-green technical cumulative SHA and hosted
  RustSec/Cargo evidence before status documentation.

- [ ] **Step 1: Verify ancestry, exact diff and fork boundary**

Run in the main worktree:

```bash
set -euo pipefail
git status --short --branch
git merge-base --is-ancestor \
  test/tcp-fault-proxy-client-reset \
  fix/tcp-fault-proxy-client-reset
git diff --check \
  test/sql-auth-validation...fix/tcp-fault-proxy-client-reset
git diff --name-status \
  test/sql-auth-validation...fix/tcp-fault-proxy-client-reset
git remote get-url --push origin
git remote get-url --push upstream
```

Expected technical diff:

```text
tests/sql_auth_strict/tcp_fault_proxy.py
tests/sql_auth_strict/test_transactions_strict.py
```

Expected remotes: fork origin and `DISABLED` upstream push.

- [ ] **Step 2: Create the local technical merge without pushing it**

Run:

```bash
git switch test/sql-auth-validation
git merge --no-ff fix/tcp-fault-proxy-client-reset \
  -m "merge: stabilize expected TCP client disconnects"
technical_sha="$(git rev-parse HEAD)"
printf 'technical_sha=%s\n' "${technical_sha}"
```

Do not push until the exact merge SHA passes the complete local gate.

- [ ] **Step 3: Create a detached verification worktree at that exact SHA**

Run:

```bash
technical_sha="$(git rev-parse test/sql-auth-validation)"
git worktree add --detach \
  .worktrees/verify-tcp-fault-proxy-client-reset \
  "${technical_sha}"
ln -s ../../.env.sql-auth.local \
  .worktrees/verify-tcp-fault-proxy-client-reset/.env.sql-auth.local
test "$(
  git -C .worktrees/verify-tcp-fault-proxy-client-reset rev-parse HEAD
)" = "${technical_sha}"
```

Expected: the detached worktree is exact and clean before verification.

- [ ] **Step 4: Run static, RustSec and complete SQL-auth gates**

Run in `.worktrees/verify-tcp-fault-proxy-client-reset`:

```bash
set -euo pipefail
uv sync --locked --all-extras --dev
uv run ruff check .
uv run python -m compileall -q python tests scripts
PATH=/private/tmp/fastmssql-cargo-audit-0.22.2/bin:${PATH} \
  scripts/security/audit_dependencies.sh
scripts/sql_auth/run_all.sh
```

Expected:

```text
Cargo build/fmt/Clippy             PASS
Rust unit tests                   13/13
RustSec dependencies scanned         219
RustSec findings                       0
strict functional               296/296
async strict                      16/16
framework                         28/28
resilience                         6/6
load                               9/9
SQL-auth specification IDs       285/285
applicable upstream             902/902
required runner lanes         all exit 0
```

- [ ] **Step 5: Assert exact JUnit totals and case completeness**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

artifact_dir = Path(".artifacts/sql-auth")
expected = {
    "strict.xml": 296,
    "async.xml": 16,
    "framework.xml": 28,
    "resilience.xml": 6,
    "load.xml": 9,
    "upstream.xml": 902,
}
for filename, total in expected.items():
    root = ET.parse(artifact_dir / filename).getroot()
    suites = [root] if root.tag == "testsuite" else list(
        root.findall(".//testsuite")
    )
    observed = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(
        int(suite.attrib.get("failures", "0")) for suite in suites
    )
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    assert (observed, failures, errors, skipped) == (total, 0, 0, 0), (
        filename,
        observed,
        failures,
        errors,
        skipped,
    )
print("exact JUnit totals: PASS")
PY
.venv/bin/python - <<'PY'
from pathlib import Path
import json

merged: dict[str, str] = {}
for path in sorted(Path(".artifacts/sql-auth").glob("*-results.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1, path
    for case_id, result in payload["cases"].items():
        assert case_id not in merged, (case_id, merged[case_id], path.name)
        assert result["outcome"] == "passed", (case_id, result["outcome"])
        merged[case_id] = path.name
assert len(merged) == 285, len(merged)
print("exact SQL-auth case union: 285/285 PASS")
PY
```

Expected: every total is exact and all 285 case IDs have `passed` outcome.

- [ ] **Step 6: Scan generated evidence for failures and credentials**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
if rg -n '\| (FAIL|ERROR|NOT RUN|SKIPPED) \|' \
  docs/SQL_AUTH_TEST_MATRIX.md; then
  exit 1
fi
if rg -F -n \
  -e "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  .artifacts/sql-auth; then
  exit 1
fi
git diff --check
git status --short --branch
```

Expected: no failed/skipped case, no credential match and only generated
matrix/report tracked changes in the detached worktree.

- [ ] **Step 7: Publish the locally green cumulative SHA only to the fork**

Run in the main worktree:

```bash
set -euo pipefail
technical_sha="$(git rev-parse test/sql-auth-validation)"
test "$(git rev-parse HEAD)" = "${technical_sha}"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Expected: parity `0 0`.

- [ ] **Step 8: Require hosted RustSec and three-OS Cargo success**

Resolve runs using `technical_sha`:

```bash
technical_sha="$(git rev-parse test/sql-auth-validation)"
dependency_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch test/sql-auth-validation \
    --commit "${technical_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch test/sql-auth-validation \
    --commit "${technical_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${dependency_run_id}"
test -n "${rust_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${dependency_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${rust_run_id}"
gh run view \
  --repo galeamarcel/FastMssql \
  "${dependency_run_id}" \
  --json headSha,status,conclusion,jobs,url
gh run view \
  --repo galeamarcel/FastMssql \
  "${rust_run_id}" \
  --json headSha,status,conclusion,jobs,url
```

Expected: RustSec succeeds and Cargo succeeds separately on Ubuntu, macOS and
Windows at exactly `technical_sha`.

- [ ] **Step 9: Verify no original-repository publication**

Run:

```bash
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:test/tcp-fault-proxy-client-reset \
  --json number,state,url
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:fix/tcp-fault-proxy-client-reset \
  --json number,state,url
```

Expected: both results are `[]`.

---

### Task 6: Record and integrate the harness status

**Files:**

- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`

**Interfaces:**

- Consumes: exact local artifacts, `technical_sha`, hosted workflow URLs,
  measured reproduction campaigns and all branch commit IDs.
- Produces: two auditable status commits and a final cumulative documentation
  merge.

- [ ] **Step 1: Create the status branch from the hosted-green technical SHA**

Run in the main worktree:

```bash
technical_sha="$(git rev-parse test/sql-auth-validation)"
git worktree add \
  .worktrees/docs-tcp-fault-proxy-client-reset-status \
  -b docs/tcp-fault-proxy-client-reset-status \
  "${technical_sha}"
ln -s ../../.env.sql-auth.local \
  .worktrees/docs-tcp-fault-proxy-client-reset-status/.env.sql-auth.local
```

Verify that its HEAD equals `technical_sha`.

- [ ] **Step 2: Regenerate matrix/report from the exact verification artifacts**

Run in `.worktrees/docs-tcp-fault-proxy-client-reset-status`:

```bash
set -euo pipefail
set -a
source .env.sql-auth.local
set +a
verification_worktree="../verify-tcp-fault-proxy-client-reset"
verification_artifacts="${verification_worktree}/.artifacts/sql-auth"
"${verification_worktree}/.venv/bin/python" \
  "${verification_worktree}/scripts/sql_auth/generate_report.py" \
  --spec "${verification_worktree}/docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md" \
  --strict-results "${verification_artifacts}/strict-results.json" \
  --artifact-dir "${verification_artifacts}" \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete
```

Expected: matrix `285/285` PASS and report lanes
`296/16/28/6/9/902`, all exit zero. Running the generator resolved from the
detached verification worktree also makes the report's `Git commit` field
equal `technical_sha`, rather than the later documentation-only status SHA.

- [ ] **Step 3: Commit the generated evidence first**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md
git commit -m "docs: refresh SQL-auth evidence after TCP proxy fix"
evidence_commit="$(git rev-parse HEAD)"
printf 'evidence_commit=%s\n' "${evidence_commit}"
```

This first status commit can be referenced by the live audit without
self-referential commit metadata.

- [ ] **Step 4: Add the measured live-audit section**

Immediately after the existing transaction cancellation section in
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`, add:

```markdown
### Stabilizarea resetului client în TX-034 — remediată și verificată
```

The section must record:

1. PoolConfig verification SHA `b5239c715934ab40e0aefc0b2a8a8fac10c7f869`
   and its sole cleanup failure `294/295`;
2. isolated campaigns `1/10` and `1/7`;
3. temporary boundary evidence
   `downstream=False`, `_aborting=False`, gate held and
   `ConnectionResetError: [Errno 54]`;
4. root cause: valid client retirement can surface as EOF or RST on macOS;
5. the explicit one-shot client-read policy and every preserved strict
   boundary;
6. design, plan, RED, GREEN, technical merge and `evidence_commit` IDs;
7. `TX-034` fresh processes `50/50`;
8. strict `296/296`, specification `285/285`, async `16/16`, framework
   `28/28`, resilience `6/6`, load `9/9`, upstream `902/902`, Rust `13/13`
   and zero RustSec findings;
9. hosted dependency-security and Linux/macOS/Windows Cargo URLs at
   `technical_sha`;
10. no FastMssql production code, public API, retry or swallowed unexpected
    failure changed;
11. no push or PR to the original repository;
12. the exact cumulative baseline that the PoolConfig branches will consume.

Resolve `evidence_commit` with `git rev-parse HEAD` in the status worktree
before editing the audit.

Use measured values and URLs only. Do not forecast a pass.

- [ ] **Step 5: Self-review and commit the audit narrative**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
git diff --check
if rg -n 'TO''DO|T''BD|FIX''ME|PLACE''HOLDER' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md; then
  exit 1
fi
if rg -F -n \
  -e "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  docs; then
  exit 1
fi
git add docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
git commit -m "docs: record explicit TCP client reset handling"
git push -u origin docs/tcp-fault-proxy-client-reset-status
git rev-list --left-right --count \
  HEAD...origin/docs/tcp-fault-proxy-client-reset-status
```

Expected: parity `0 0`, no placeholders or credentials and only three
approved status documents across the two commits.

- [ ] **Step 6: Merge status into the cumulative fork**

Run in the main worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git merge --no-ff docs/tcp-fault-proxy-client-reset-status \
  -m "merge: record explicit TCP client reset handling"
harness_final_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Expected: parity `0 0`; this merge is documentation-only relative to
`technical_sha`.

- [ ] **Step 7: Require final hosted gates at the documentation merge SHA**

Run:

```bash
harness_final_sha="$(git rev-parse test/sql-auth-validation)"
dependency_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch test/sql-auth-validation \
    --commit "${harness_final_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch test/sql-auth-validation \
    --commit "${harness_final_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${dependency_run_id}"
test -n "${rust_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${dependency_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${rust_run_id}"
gh run view \
  --repo galeamarcel/FastMssql \
  "${dependency_run_id}" \
  --json headSha,status,conclusion,jobs,url
gh run view \
  --repo galeamarcel/FastMssql \
  "${rust_run_id}" \
  --json headSha,status,conclusion,jobs,url
```

Expected: RustSec and all three Cargo operating systems succeed at the exact
final harness SHA.

---

### Task 7: Refresh the published PoolConfig RED/GREEN branches

**Files:**

- Merge-only refresh:
  `test/pool-config-default-consistency`,
  `fix/pool-config-default-consistency`
- Existing PoolConfig technical files remain unchanged.
- Generated only in detached verification:
  `.artifacts/**`, `docs/SQL_AUTH_TEST_MATRIX.md`,
  `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**

- Consumes: `harness_final_sha`, published PoolConfig RED commits
  `659a1872`/`06e3129c` and GREEN commit `b5239c71`.
- Produces: forward-merged, non-rewritten PoolConfig branches and exact local
  `14/296/285/906` evidence.

- [ ] **Step 1: Verify both PoolConfig worktrees are clean and published**

Run:

```bash
set -euo pipefail
git -C .worktrees/test-pool-config-default-consistency status --short --branch
git -C .worktrees/fix-pool-config-default-consistency status --short --branch
git rev-list --left-right --count \
  test/pool-config-default-consistency...origin/test/pool-config-default-consistency
git rev-list --left-right --count \
  fix/pool-config-default-consistency...origin/fix/pool-config-default-consistency
```

Expected: both branch worktrees are clean and both parities are `0 0`.

- [ ] **Step 2: Merge the stabilized cumulative baseline into PoolConfig RED**

Run in `.worktrees/test-pool-config-default-consistency`:

```bash
git merge --no-ff test/sql-auth-validation \
  -m "merge: refresh PoolConfig RED baseline"
git push origin test/pool-config-default-consistency
git rev-list --left-right --count \
  HEAD...origin/test/pool-config-default-consistency
```

Expected: existing RED commits are unchanged and reachable; the harness
contract is added by a merge commit.

- [ ] **Step 3: Merge refreshed RED into PoolConfig GREEN**

Run in `.worktrees/fix-pool-config-default-consistency`:

```bash
git merge --no-ff test/pool-config-default-consistency \
  -m "merge: refresh PoolConfig GREEN baseline"
refreshed_pool_fix_sha="$(git rev-parse HEAD)"
git push origin fix/pool-config-default-consistency
git rev-list --left-right --count \
  HEAD...origin/fix/pool-config-default-consistency
```

Expected: original GREEN commit `b5239c71` remains unchanged and reachable;
parity is `0 0`.

- [ ] **Step 4: Repoint the detached PoolConfig verification worktree safely**

First verify that the old detached worktree contains only the two known
generated failure documents:

```bash
git -C .worktrees/verify-pool-config-default-consistency status --short
```

Expected exactly:

```text
 M docs/SQL_AUTH_TEST_MATRIX.md
 M docs/SQL_AUTH_TEST_REPORT.md
```

The failure is now recorded in the committed TCP-proxy audit section. Restore
only those generated files, then repoint:

```bash
refreshed_pool_fix_sha="$(
  git rev-parse fix/pool-config-default-consistency
)"
git -C .worktrees/verify-pool-config-default-consistency restore \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md
git -C .worktrees/verify-pool-config-default-consistency switch \
  --detach "${refreshed_pool_fix_sha}"
test "$(
  git -C .worktrees/verify-pool-config-default-consistency rev-parse HEAD
)" = "${refreshed_pool_fix_sha}"
```

Do not reset, force-remove or rewrite either published PoolConfig branch.

- [ ] **Step 5: Run focused PoolConfig static, Rust and real-server gates**

Run in `.worktrees/verify-pool-config-default-consistency`:

```bash
set -euo pipefail
uv sync --locked --all-extras --dev
uv run maturin develop --release
cargo build --locked
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
PATH=/private/tmp/fastmssql-cargo-audit-0.22.2/bin:${PATH} \
  scripts/security/audit_dependencies.sh
uv run ruff check .
uv run python -m compileall -q python tests scripts
uv run pytest tests/test_pyo3_build_contract.py -q
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  -m "not integration" -q
```

Expected:

```text
Rust unit tests             14/14
PyO3 build contract          7/7
PoolConfig pure             92/92, 16 integration deselected
Cargo/Ruff/compileall          PASS
RustSec findings                 0
```

Load `.env.sql-auth.local`, start/provision the approved container and run:

```bash
set -a
source .env.sql-auth.local
set +a
test "${FASTMSSQL_SQL_AUTH_CONTAINER}" = "fastmssql-sql-auth-dev"
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  -m integration -q
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/pool-default-refreshed.json \
  uv run pytest \
  tests/sql_auth_strict/test_pool.py::test_default_pool_config_matches_runtime \
  -vv
```

Expected: `16` integration tests, `POOL-001` with exact
`15/15/15` peak active/connections/sessions for both explicit and implicit
paths, parameter values `0..29`, and zero sessions after both disconnects.

- [ ] **Step 6: Build and import a clean ABI3 wheel**

Run:

```bash
wheel_root="$(mktemp -d /private/tmp/fastmssql-pool-wheel.XXXXXX)"
uv run maturin build --release --locked --out "${wheel_root}"
uv venv "${wheel_root}/venv" --python 3.13
uv pip install \
  --python "${wheel_root}/venv/bin/python" \
  "${wheel_root}"/*.whl
env -C /private/tmp \
  "${wheel_root}/venv/bin/python" \
  -c "import inspect; from fastmssql import PoolConfig; c=PoolConfig(); assert (c.max_size,c.min_idle,c.max_lifetime_secs,c.idle_timeout_secs,c.connection_timeout_secs,c.test_on_check_out,c.retry_connection)==(15,3,1800,300,30,None,None); assert Ellipsis not in [p.default for p in inspect.signature(PoolConfig).parameters.values()]"
```

Expected: a `cp311-abi3` wheel installs and imports outside the source tree
with the exact seven-field canonical profile.

- [ ] **Step 7: Run the complete refreshed PoolConfig enterprise gate**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected exact totals:

```text
Rust unit tests                   14/14
strict functional               296/296
async strict                      16/16
framework                         28/28
resilience                         6/6
load                               9/9
SQL-auth specification IDs       285/285
applicable upstream             906/906
required runner lanes         all exit 0
```

Verify exact totals:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

artifact_dir = Path(".artifacts/sql-auth")
expected = {
    "strict.xml": 296,
    "async.xml": 16,
    "framework.xml": 28,
    "resilience.xml": 6,
    "load.xml": 9,
    "upstream.xml": 906,
}
for filename, total in expected.items():
    root = ET.parse(artifact_dir / filename).getroot()
    suites = [root] if root.tag == "testsuite" else list(
        root.findall(".//testsuite")
    )
    observed = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(
        int(suite.attrib.get("failures", "0")) for suite in suites
    )
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    assert (observed, failures, errors, skipped) == (total, 0, 0, 0), (
        filename,
        observed,
        failures,
        errors,
        skipped,
    )
print("refreshed PoolConfig JUnit totals: PASS")
PY
.venv/bin/python - <<'PY'
from pathlib import Path
import json

merged: dict[str, str] = {}
for path in sorted(Path(".artifacts/sql-auth").glob("*-results.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1, path
    for case_id, result in payload["cases"].items():
        assert case_id not in merged, (case_id, merged[case_id], path.name)
        assert result["outcome"] == "passed", (case_id, result["outcome"])
        merged[case_id] = path.name
assert len(merged) == 285, len(merged)
print("exact SQL-auth case union: 285/285 PASS")
PY
set -a
source .env.sql-auth.local
set +a
if rg -n '\| (FAIL|ERROR|NOT RUN|SKIPPED) \|' \
  docs/SQL_AUTH_TEST_MATRIX.md; then
  exit 1
fi
if rg -F -n \
  -e "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  .artifacts/sql-auth; then
  exit 1
fi
```

Expected: all six JUnit totals are exact, all 285 IDs pass and no generated
evidence contains a failed case or credential.

- [ ] **Step 8: Prove PoolConfig subject isolation after refresh**

Run:

```bash
git diff --check \
  test/sql-auth-validation...fix/pool-config-default-consistency
git diff --name-status \
  test/sql-auth-validation...fix/pool-config-default-consistency
git log --format='%h %an <%ae> %s' \
  test/sql-auth-validation..fix/pool-config-default-consistency
```

Expected subject files only:

```text
.github/workflows/rust-unit-tests.yml
README.md
python/fastmssql/fastmssql.pyi
src/pool_config.rs
tests/sql_auth_strict/test_pool.py
tests/test_pool_config.py
tests/test_pool_config_default_contract.py
tests/test_pool_config_validation.py
tests/test_pyo3_build_contract.py
```

The TCP proxy helper/test are part of the refreshed baseline, not the
PoolConfig subject delta.

- [ ] **Step 9: Merge the locally green PoolConfig tree into cumulative**

Run in the main worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git merge --no-ff fix/pool-config-default-consistency \
  -m "merge: align PoolConfig default construction"
pool_technical_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Expected: parity `0 0`, fork-only push.

- [ ] **Step 10: Require exact hosted PoolConfig and RustSec success**

Run:

```bash
pool_technical_sha="$(git rev-parse test/sql-auth-validation)"
dependency_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch test/sql-auth-validation \
    --commit "${pool_technical_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch test/sql-auth-validation \
    --commit "${pool_technical_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${dependency_run_id}"
test -n "${rust_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${dependency_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${rust_run_id}"
gh run view \
  --repo galeamarcel/FastMssql \
  "${dependency_run_id}" \
  --json headSha,status,conclusion,jobs,url
gh run view \
  --repo galeamarcel/FastMssql \
  "${rust_run_id}" \
  --json headSha,status,conclusion,jobs,url
```

Require:

```text
dependency-security RustSec            PASS
Ubuntu raw Cargo + wheel contract       PASS
macOS raw Cargo + wheel contract        PASS
Windows raw Cargo + wheel contract      PASS
Rust tests on every OS                 14/14
PoolConfig Python contract on every OS   3/3
```

Do not accept a successful run for an earlier SHA.

---

### Task 8: Finalize PoolConfig status and return to the enterprise roadmap

**Files:**

- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Modify:
  `docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md`

**Interfaces:**

- Consumes: `pool_technical_sha`, refreshed local artifacts and exact hosted
  URLs.
- Produces: a fully current PoolConfig status branch, final cumulative fork
  SHA and the next enterprise-candidate handoff.

- [ ] **Step 1: Create the PoolConfig status branch at the technical SHA**

Run:

```bash
pool_technical_sha="$(git rev-parse test/sql-auth-validation)"
git worktree add \
  .worktrees/docs-pool-config-default-consistency-status \
  -b docs/pool-config-default-consistency-status \
  "${pool_technical_sha}"
ln -s ../../.env.sql-auth.local \
  .worktrees/docs-pool-config-default-consistency-status/.env.sql-auth.local
```

Expected: exact hosted-green technical SHA.

- [ ] **Step 2: Regenerate final matrix/report from refreshed artifacts**

Run in `.worktrees/docs-pool-config-default-consistency-status`:

```bash
set -euo pipefail
set -a
source .env.sql-auth.local
set +a
verification_worktree="../verify-pool-config-default-consistency"
verification_artifacts="${verification_worktree}/.artifacts/sql-auth"
"${verification_worktree}/.venv/bin/python" \
  "${verification_worktree}/scripts/sql_auth/generate_report.py" \
  --spec "${verification_worktree}/docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md" \
  --strict-results "${verification_artifacts}/strict-results.json" \
  --artifact-dir "${verification_artifacts}" \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete
```

Expected: `285/285` cases and lanes
`296/16/28/6/9/906`, all green. The report's `Git commit` field must equal
the detached `pool_technical_sha`, not the documentation-only status SHA.

- [ ] **Step 3: Correct only post-harness PoolConfig plan counts**

In
`docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md`:

- preserve historical baseline `strict functional 295` at `f35176c`;
- change every forecast/final PoolConfig strict result from `295` to `296`;
- change future PR evidence from `295 strict` to `296 strict`;
- add one sentence that the unmarked deterministic TCP-proxy contract entered
  the cumulative baseline before PoolConfig resumption and did not change the
  `285` case IDs.

Use this exact explanatory sentence after the final-count table:

```markdown
The cumulative baseline gained one unmarked deterministic TCP-proxy harness
test before PoolConfig resumed, so final strict collection is 296 while the
SQL-auth specification registry remains exactly 285 IDs.
```

Do not change PoolConfig source, tests, expected profile or hosted contract.

- [ ] **Step 4: Add the measured PoolConfig audit section and roadmap record**

Add this heading to
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`:

```markdown
## Consistența defaulturilor PoolConfig — remediată și verificată
```

Record all of the following with measured commit IDs, SHA values and hosted
URLs:

1. RED divergence `20/2/None/None/30` versus
   `15/3/1800/300/30`;
2. root cause: PyO3 constructor literals diverged from
   `PyPoolConfig::default()`;
3. upstream provenance commit `2c5620a`;
4. canonical seven-field profile `15/3/1800/300/30/None/None`;
5. explicit-`None` and preset/adaptive compatibility boundaries;
6. exact design, RED, GREEN and status branch/commit IDs;
7. focused pure, integration, PyO3 and real `POOL-001` results;
8. peak `15/15/15` active/connections/sessions for both explicit and implicit
   construction paths, parameter values `0..29`, and zero sessions after
   disconnect;
9. exact local enterprise totals:

```text
canonical profile              15/3/1800/300/30/None/None
PoolConfig pure                92/92
PoolConfig integration         16/16
PyO3 build contract             7/7
Rust                           14/14
strict                        296/296
async                           16/16
framework                       28/28
resilience                       6/6
load                             9/9
specification IDs              285/285
upstream                       906/906
RustSec findings                     0
```

10. clean ABI3 wheel, Ruff, compileall, Cargo, Clippy and RustSec evidence;
11. hosted dependency-security and Linux/macOS/Windows extension URLs at
    `pool_technical_sha`;
12. the separate remaining `None`/operation-timeout semantics candidate;
13. explicit statements that the original `294/295` result was a harness
    cleanup failure, final strict is `296/296`, no PoolConfig assertion or
    production fix was weakened, and nothing was pushed or proposed upstream.

In `## Ordinea recomandată a branch-urilor`, insert the completed PoolConfig
item immediately after the PyO3 build item:

```markdown
12. `fix/pool-config-default-consistency` — **defaulturile explicite și
    implicite canonice, introspecția, stubul și limitele reale de pool
    finalizate și verificate hosted**
```

Renumber later unresolved items. Add this checked acceptance criterion:

```markdown
- [x] `PoolConfig()` și `Connection(..., pool_config=None)` folosesc același
  profil `15/3/1800/300/30`, iar pool-ul real rămâne limitat la 15 sesiuni;
```

Update `## Starea verificată curentă` with `pool_technical_sha`, every exact
local total above and both hosted URLs. Preserve the distinction between the
technical SHA and the later documentation-only merge.

In
`docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`, add
`PR-21 PoolConfig default consistency` to the proposed order, Candidate
intake table and focused candidate section with:

```text
fork implementation branch   fix/pool-config-default-consistency
future clean branch          fix/upstream-pool-config-default-consistency
future title                 fix: align PoolConfig default construction
classification               VERIFIED_FORK / requires fresh upstream rebase
public API change            omitted PoolConfig arguments become canonical
compatibility note           historical direct profile remains explicit
required evidence            RED, 906 upstream, 296 strict, 285 IDs,
                             14 Rust, 7 PyO3, three hosted OS jobs
publication                  forbidden until new explicit user approval
```

Do not create the future clean branch, an upstream branch or a pull request.

- [ ] **Step 5: Self-review, commit and publish PoolConfig status**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
git diff --check
if rg -n 'TO''DO|T''BD|FIX''ME|PLACE''HOLDER' \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md \
  docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md; then
  exit 1
fi
if rg -n '\| (FAIL|ERROR|NOT RUN|SKIPPED) \|' \
  docs/SQL_AUTH_TEST_MATRIX.md; then
  exit 1
fi
if rg -F -n \
  -e "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
  -e "${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  docs; then
  exit 1
fi
git diff --name-status
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md \
  docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md
git commit -m "docs: record canonical PoolConfig defaults"
git push -u origin docs/pool-config-default-consistency-status
git rev-list --left-right --count \
  HEAD...origin/docs/pool-config-default-consistency-status
```

Expected: parity `0 0`, author Marcel Galea, no secret or placeholder.

- [ ] **Step 6: Merge final status and require final hosted gates**

Run in the main worktree:

```bash
git switch test/sql-auth-validation
git merge --no-ff docs/pool-config-default-consistency-status \
  -m "merge: record canonical PoolConfig defaults"
pool_final_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Resolve and watch both final workflows:

```bash
pool_final_sha="$(git rev-parse test/sql-auth-validation)"
dependency_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch test/sql-auth-validation \
    --commit "${pool_final_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch test/sql-auth-validation \
    --commit "${pool_final_sha}" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${dependency_run_id}"
test -n "${rust_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${dependency_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status "${rust_run_id}"
gh run view \
  --repo galeamarcel/FastMssql \
  "${dependency_run_id}" \
  --json headSha,status,conclusion,jobs,url
gh run view \
  --repo galeamarcel/FastMssql \
  "${rust_run_id}" \
  --json headSha,status,conclusion,jobs,url
```

Require dependency-security and all three Rust/extension jobs at exactly
`pool_final_sha`, even though the final merge is documentation-only.

- [ ] **Step 7: Prove repository boundary and close only these candidates**

Run:

```bash
git remote -v
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:fix/tcp-fault-proxy-client-reset \
  --json number,state,url
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:fix/pool-config-default-consistency \
  --json number,state,url
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
```

Expected: upstream PR results `[]`, cumulative parity `0 0`, and upstream push
URL still `DISABLED`.

- [ ] **Step 8: Select the next unresolved enterprise audit item**

Read the newly committed
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`, identify the first unresolved
item in its recommended branch order, verify it against current code and
begin its isolated design branch under the user's standing inline approval.
Do not mark the global enterprise-readiness objective complete while any
required audit item remains unresolved.
