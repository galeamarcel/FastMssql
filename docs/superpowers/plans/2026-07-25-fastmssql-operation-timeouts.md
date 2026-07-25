# FastMssql Operation Timeouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed, phase-specific, fail-closed deadlines for physical connection setup, pool acquisition, SQL operations, transaction lifetime, and rollback/close without changing FastMssql's existing no-retry and COMMIT-outcome contracts.

**Architecture:** A compiled `TimeoutConfig` is resolved once at `Connection` or direct `Transaction` construction and copied into Rust-owned handles. A small deadline module owns absolute Tokio deadlines and typed timeout metadata, while the existing pooled-operation and transaction cancellation guards remain responsible for retiring incomplete TDS sessions. Pool creation, checkout, buffered operations, batch/bulk calls, and transaction settlement each receive the relevant budget explicitly; no Python-side timer or background transaction timer is introduced.

**Tech Stack:** Rust 2024, PyO3 0.29 ABI3 for Python 3.11+, Tokio 1.52, bb8 0.9.1, vendored Tiberius, Python 3.11–3.14, pytest/pytest-asyncio, SQL Server 2022 Developer in Docker, FastAPI, Flask, asgiref, GitHub Actions on Linux/macOS/Windows.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-07-25-fastmssql-operation-timeouts-design.md` at commit `462cdb0d65dc9d9a0b60922a750edbc97fddefb1`.
- The cumulative implementation baseline is `2f6d0c53ebe4f3d5bc617606730f6aed0f143619`; merge the design/plan branch into it before creating the RED branch.
- Use exactly these branches: `docs/operation-timeouts-design`, `test/operation-timeouts`, `feat/operation-timeouts`, and `docs/operation-timeouts-status`.
- Every push targets only `origin`, whose URL must be `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` must remain fetch-only with push URL exactly `DISABLED`; do not create an upstream branch, push, or pull request.
- Keep version sources at `0.7.7`; this candidate does not publish a release.
- `TimeoutConfig` has exact defaults `30.0`, `30.0`, `None`, `None`, `30.0` for connect, acquire, operation, transaction, and rollback respectively.
- Reject booleans, non-finite values, zero, and negative values before opening a pool or socket; support finite positive subsecond floats.
- `acquire_timeout_secs` is always finite and positive; the other four fields accept `None` to omit the FastMssql deadline for that phase.
- An omitted `TimeoutConfig` derives connect/acquire from `PoolConfig.connection_timeout_secs` or `30.0`, leaves operation/transaction unbounded, and uses `30.0` for rollback.
- An explicit `TimeoutConfig.acquire_timeout_secs` wins over `PoolConfig.connection_timeout_secs` and is copied into the internal bb8 pool configuration.
- Calculate one absolute `tokio::time::Instant` per public operation; never restart a batch, bulk, routing, or transaction-lifetime budget at an internal await.
- An operation timeout after a request starts retires the physical connection; no timed-out TDS response is returned to the pool.
- FastMssql performs no new automatic SQL retry.
- A possibly delivered COMMIT whose response times out raises top-level `CommitOutcomeUnknown` with `OperationTimeoutError` as `__cause__`.
- Explicit rollback and `close()` use the independent rollback budget even after transaction-lifetime expiry.
- Keep lifecycle (`Open | Closing | Closed`), telemetry/metrics, TDS `ATTENTION`, per-call timeout overrides, and Python context-manager exception aggregation outside this candidate.
- Register exactly ten new SQL-auth IDs, `TIME-001` through `TIME-010`; the registry grows from 285 to 295 unique IDs.
- Preserve every existing test and case ID; do not weaken, skip, rename, or remove a RED assertion in GREEN.
- Tests use SQL username/password authentication against only `fastmssql-sql-auth-dev`; no test may target an unrelated Docker container.
- Timing checks use `time.monotonic()` and bounded tolerances; an outer watchdog cannot be the reason a timeout test passes.
- Do not put credentials, tokens, complete secret-bearing connection strings, or local environment values in source, logs committed to git, or Markdown evidence.

---

## File and Responsibility Map

### New files

- `src/timeout_config.rs` — compiled `TimeoutConfig`, finite-positive conversion, defaults, compatibility derivation, cloning, getters/setters, and `repr`.
- `src/deadline.rs` — stable timeout phases, absolute deadline selection, generic timeout execution, and conversion to typed Python timeout errors.
- `tests/test_timeout_config_contract.py` — isolated wheel-safe API/default/validation/stub/export/README contracts.
- `tests/sql_auth_strict/timeout_fixtures.py` — local handshake blackhole and bounded polling helpers used only by timeout integration tests.
- `tests/sql_auth_strict/test_operation_timeouts.py` — real SQL-auth cases `TIME-001` through `TIME-009`.

### Modified Rust files

- `src/lib.rs` — register and export `PyTimeoutConfig` and `OperationTimeoutError`.
- `src/types.rs` — declare the timeout exception as a subclass of `SqlConnectionError` and attach all stable metadata.
- `src/pool_manager.rs` — bound physical setup, configure acquisition duration, retain phase identity, and map checkout failures using the public operation name.
- `src/connection.rs` — store the effective policy, expose a clone, separate acquisition and operation phases, and propagate policy into batches and transactions.
- `src/batch.rs` — use one operation deadline across every statement/chunk and bound direct physical setup and cleanup.
- `src/transaction.rs` — store policy and transaction-lifetime deadline, select the earlier active deadline, and preserve epoch-checked fail-closed settlement.

### Modified Python/API/docs files

- `python/fastmssql/__init__.py` — package-root exports only; timeout behavior remains in Rust.
- `python/fastmssql/fastmssql.pyi` — compiled API types, constructor keywords, properties, and error metadata.
- `python/fastmssql/__init__.pyi` — wrapper-level re-exports, constructor keywords, and properties.
- `README.md` — five-phase timeout policy, compatibility rules, exception metadata, and COMMIT reconciliation.

### Modified test/harness/CI files

- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md` — canonical `TIME-001`–`TIME-010` registry entries.
- `tests/sql_auth_strict/test_matrix_contract.py` — exact registry/report expectations change from 285 to 295.
- `tests/sql_auth_strict/framework_apps.py` — allow test application state to receive a `TimeoutConfig` without importing framework policy into FastMssql.
- `tests/sql_auth_strict/test_framework_integration.py` — `TIME-010`, including FastAPI and Flask-through-ASGI typed timeout/recovery load.
- `scripts/sql_auth/run_all.sh` — route `test_operation_timeouts.py` through the strict lane.
- `.github/workflows/rust-unit-tests.yml` — run the installed-wheel timeout contract on all three supported hosted operating systems.
- `tests/test_pyo3_build_contract.py` — statically require the hosted timeout wheel contract.

### Modified evidence/status files after technical verification

- `docs/SQL_AUTH_TEST_MATRIX.md` — generated 295-case matrix from exact technical SHA.
- `docs/SQL_AUTH_TEST_REPORT.md` — generated lane and environment evidence from exact technical SHA.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — mark only operation-timeout safety as verified and leave lifecycle/observability open.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` — record the fork candidate and prerequisites for any later, separately approved upstream proposal.

---

### Task 1: Preserve the design and create isolated RED execution state

**Files:**
- Verify: `docs/superpowers/specs/2026-07-25-fastmssql-operation-timeouts-design.md`
- Verify: `docs/superpowers/plans/2026-07-25-fastmssql-operation-timeouts.md`
- Worktree: repository root for `test/sql-auth-validation`
- Create worktree: `.worktrees/test-operation-timeouts`
- Branch: `test/operation-timeouts`

**Interfaces:**
- Consumes: design commit `462cdb0d65dc9d9a0b60922a750edbc97fddefb1` and cumulative baseline `2f6d0c53ebe4f3d5bc617606730f6aed0f143619`.
- Produces: one cumulative documentation merge SHA and an isolated RED branch rooted exactly at that merge.

- [ ] **Step 1: Verify fork-only remote safety before any merge**

Run:

```bash
git remote -v
git config --get remote.origin.url
git config --get remote.upstream.pushurl
git status --short --branch
```

Expected:

```text
origin fetch/push: https://github.com/galeamarcel/FastMssql.git
upstream fetch: https://github.com/Rivendael/FastMssql.git
upstream push URL: DISABLED
working tree: clean
```

- [ ] **Step 2: Verify the design branch has exactly the approved design and plan**

Run:

```bash
git log --oneline --decorate -3
git diff --check
git rev-list --left-right --count HEAD...origin/docs/operation-timeouts-design
```

Expected: the top two branch commits are the design and this implementation plan, `git diff --check` is silent, and fork parity is `0	0`.

- [ ] **Step 3: Merge the documentation branch into the cumulative fork branch**

Run from `/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql`:

```bash
git status --short --branch
git merge --no-ff docs/operation-timeouts-design -m "merge: preserve operation timeout design"
git push origin test/sql-auth-validation
```

Expected: one merge commit on `test/sql-auth-validation`; no source or test implementation is present yet.

- [ ] **Step 4: Record the exact RED baseline and existing counts**

Run:

```bash
git rev-parse HEAD
uv run pytest tests/sql_auth_strict --collect-only -q
uv run pytest tests --ignore=tests/sql_auth_strict --collect-only -q
cargo test --locked -- --list
```

Expected baseline evidence:

```text
strict pytest tests: 296
registered SQL-auth IDs: 285 unique
applicable upstream Python tests: 906
Rust unit tests: 14
```

If collection differs because a previously approved cumulative documentation merge changed only discovery output, record the exact observed count in the RED commit message and require GREEN to preserve every collected baseline node.

- [ ] **Step 5: Create the RED branch and worktree from the exact cumulative documentation merge**

Run:

```bash
git worktree add .worktrees/test-operation-timeouts -b test/operation-timeouts test/sql-auth-validation
git -C .worktrees/test-operation-timeouts rev-parse HEAD
git -C .worktrees/test-operation-timeouts status --short --branch
```

Expected: the new worktree is clean and its SHA equals the cumulative merge from Step 3.

---

### Task 2: Write RED public configuration, error, registry, and wheel contracts

**Files:**
- Create: `tests/test_timeout_config_contract.py`
- Modify: `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `tests/test_pyo3_build_contract.py`

**Interfaces:**
- Consumes: existing `fastmssql` installed extension, AST-based stub inspection, and SQL-auth case parsing.
- Produces: exact public names `TimeoutConfig` and `OperationTimeoutError`, exact default/signature contract, exact 295-case registry, and an isolated installed-wheel gate.

- [ ] **Step 1: Add a wheel-safe timeout API contract without importing repository `conftest.py`**

Create `tests/test_timeout_config_contract.py` with this structure:

```python
from __future__ import annotations

import ast
import importlib
import inspect
import math
from pathlib import Path

import fastmssql


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
DEFAULTS = {
    "connect_timeout_secs": 30.0,
    "acquire_timeout_secs": 30.0,
    "operation_timeout_secs": None,
    "transaction_timeout_secs": None,
    "rollback_timeout_secs": 30.0,
}
TEXT_SIGNATURE = (
    "(connect_timeout_secs=30.0, acquire_timeout_secs=30.0, "
    "operation_timeout_secs=None, transaction_timeout_secs=None, "
    "rollback_timeout_secs=30.0)"
)


def _public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def _class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_timeout_config_exact_defaults_signature_and_repr() -> None:
    timeout_type = _public_type("TimeoutConfig")
    signature = inspect.signature(timeout_type)
    assert timeout_type.__text_signature__ == TEXT_SIGNATURE
    assert tuple(signature.parameters) == tuple(DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == DEFAULTS
    config = timeout_type()
    assert {name: getattr(config, name) for name in DEFAULTS} == DEFAULTS
    assert repr(config) == (
        "TimeoutConfig(connect_timeout_secs=30.0, "
        "acquire_timeout_secs=30.0, operation_timeout_secs=None, "
        "transaction_timeout_secs=None, rollback_timeout_secs=30.0)"
    )


def test_timeout_config_validation_and_mutable_properties() -> None:
    timeout_type = _public_type("TimeoutConfig")
    for field in DEFAULTS:
        optional = field != "acquire_timeout_secs"
        for invalid in (True, False, 0, 0.0, -0.1, math.nan, math.inf, -math.inf):
            kwargs = {field: invalid}
            try:
                timeout_type(**kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{field} accepted invalid value {invalid!r}")
        config = timeout_type()
        setattr(config, field, 0.125)
        assert getattr(config, field) == 0.125
        if optional:
            setattr(config, field, None)
            assert getattr(config, field) is None
        else:
            try:
                setattr(config, field, None)
            except ValueError:
                pass
            else:
                raise AssertionError("acquire_timeout_secs accepted None")
    unbounded = timeout_type(
        connect_timeout_secs=None,
        operation_timeout_secs=None,
        transaction_timeout_secs=None,
        rollback_timeout_secs=None,
    )
    assert unbounded.connect_timeout_secs is None
    assert unbounded.operation_timeout_secs is None
    assert unbounded.transaction_timeout_secs is None
    assert unbounded.rollback_timeout_secs is None


def test_timeout_error_is_structured_connection_error() -> None:
    timeout_error = _public_type("OperationTimeoutError")
    assert issubclass(timeout_error, fastmssql.SqlConnectionError)


def test_compiled_connection_types_append_timeout_config() -> None:
    core = importlib.import_module("fastmssql.fastmssql")
    for public_type in (core.Connection, core.Transaction):
        parameters = tuple(inspect.signature(public_type).parameters.values())
        assert parameters[-1].name == "timeout_config"
        assert parameters[-1].default is None


def test_timeout_stubs_and_readme_match_runtime_contract() -> None:
    timeout_class = _class_node(CORE_STUB, "TimeoutConfig")
    assert _class_node(CORE_STUB, "OperationTimeoutError")
    initializer = next(
        node
        for node in timeout_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameters = initializer.args.args[1:]
    assert tuple(parameter.arg for parameter in parameters) == tuple(DEFAULTS)
    assert {
        parameter.arg: ast.literal_eval(default)
        for parameter, default in zip(
            parameters, initializer.args.defaults, strict=True
        )
    } == DEFAULTS
    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert "timeout_config: Optional[TimeoutConfig] = None" in text
        assert "def timeout_config(self) -> TimeoutConfig" in text
    wrapper = WRAPPER_STUB.read_text(encoding="utf-8")
    assert "    OperationTimeoutError," in wrapper
    assert "    TimeoutConfig," in wrapper
    readme = README.read_text(encoding="utf-8")
    for token in (
        "connect_timeout_secs",
        "acquire_timeout_secs",
        "operation_timeout_secs",
        "transaction_timeout_secs",
        "rollback_timeout_secs",
        "CommitOutcomeUnknown",
    ):
        assert token in readme
```

The final file may split assertions into more focused test functions, but it must preserve every exact value and negative case above.

- [ ] **Step 2: Add the ten canonical matrix entries to the existing SQL-auth design**

Add this category to `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md` before the defect-workflow section:

```markdown
### TIME — operation timeout and deadline safety

- `TIME-001`: typed configuration, compatibility fallback, precedence, exports, and clone isolation.
- `TIME-002`: an unanswered TDS pre-login handshake expires in the physical-connect phase.
- `TIME-003`: saturated pool checkout expires in the acquire phase without starting application SQL.
- `TIME-004`: a timed-out query retires its physical session and the bounded pool recovers.
- `TIME-005`: a timed-out parameterized write is submitted once and reconciled by business key.
- `TIME-006`: batch and bulk work consume one absolute operation budget rather than one budget per item.
- `TIME-007`: idle transaction-lifetime expiry retires the lease and rolls back the uncommitted write.
- `TIME-008`: the earlier operation or transaction deadline wins for in-flight transaction work.
- `TIME-009`: COMMIT timeout preserves unknown-outcome precedence and rollback timeout remains visible.
- `TIME-010`: FastAPI and Flask-through-ASGI preserve typed errors and recover after 1,000 bounded operations.
```

- [ ] **Step 3: Change only exact matrix totals from 285 to 295**

In `tests/sql_auth_strict/test_matrix_contract.py`, replace the four fixed 285 expectations with 295:

```python
assert sum(line.startswith("| `") for line in matrix.splitlines()) == 295
assert "missing evidence for 295 case(s)" in completed.stderr
assert "| NOT RUN | 295 |" in report_output.read_text(encoding="utf-8")
assert len(ids) == 295
```

Do not change the set-equality and one-source-occurrence gates.

- [ ] **Step 4: Require the installed wheel to execute both configuration contracts**

Change the final workflow command in `.github/workflows/rust-unit-tests.yml` to:

```yaml
      - name: Verify installed Python configuration contracts
        shell: bash
        run: |
          "${POOL_CONTRACT_PYTHON}" \
            -m pytest --noconftest \
            tests/test_pool_config_default_contract.py \
            tests/test_timeout_config_contract.py -q
```

Change the static assertion in `tests/test_pyo3_build_contract.py` to require the complete command and retain the `--noconftest` isolation:

```python
normalized_workflow = " ".join(
    workflow.replace("\\", " ").split()
)
assert (
    "-m pytest --noconftest "
    "tests/test_pool_config_default_contract.py "
    "tests/test_timeout_config_contract.py -q"
    in normalized_workflow
)
assert "-m pytest tests/test_timeout_config_contract.py" not in workflow
```

The normalized assertion is platform-independent and still proves the exact file order and `--noconftest` isolation.

- [ ] **Step 5: Run the focused RED contracts**

Run:

```bash
uv run pytest --noconftest tests/test_timeout_config_contract.py -q
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected RED:

```text
timeout contract: FAIL because package export TimeoutConfig is missing
matrix contract: FAIL because ten TIME IDs have no attached strict test sources
PyO3 build contract: PASS for the newly written workflow/static contract
```

The PyO3 static gate is allowed to pass in RED because it proves the new hosted command is present; the public API contract must fail for the intended missing symbol.

---

### Task 3: Write RED real SQL-auth timeout fixtures and cases TIME-001 through TIME-009

**Files:**
- Create: `tests/sql_auth_strict/timeout_fixtures.py`
- Create: `tests/sql_auth_strict/test_operation_timeouts.py`
- Modify: `scripts/sql_auth/run_all.sh`

**Interfaces:**
- Consumes: `SqlAuthConfig`, `CleanupRegistry`, `DownstreamGateProxy`, `case()`, `scalar()`, real SQL Server DMVs, and the future public timeout API.
- Produces: nine deterministic SQL-auth cases, a bounded local blackhole fixture, and strict-runner routing.

- [ ] **Step 1: Add an unanswered pre-login fixture with explicit teardown**

Create `tests/sql_auth_strict/timeout_fixtures.py`:

```python
from __future__ import annotations

import asyncio
from contextlib import suppress
import time


class HandshakeBlackhole:
    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.accepted = asyncio.Event()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        assert self._server is not None and self._server.sockets
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def open_connections(self) -> int:
        return len(self._writers)

    async def __aenter__(self):
        self._server = await asyncio.start_server(
            self._accept, self.host, 0
        )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
        self._writers.clear()

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._writers.add(writer)
        self.accepted.set()
        try:
            await reader.read()
        finally:
            self._writers.discard(writer)
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()


async def wait_until(
    predicate,
    *,
    timeout: float = 3.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("bounded condition was not reached")
```

No fixture sleeps to create the race; events and DMV predicates establish readiness.

- [ ] **Step 2: Add common timeout-test constructors and metadata assertions**

Start `tests/sql_auth_strict/test_operation_timeouts.py` with:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig, Transaction
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy
from sql_auth_strict.timeout_fixtures import HandshakeBlackhole, wait_until


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def timeout_api():
    assert hasattr(fastmssql, "TimeoutConfig")
    assert hasattr(fastmssql, "OperationTimeoutError")
    return fastmssql.TimeoutConfig, fastmssql.OperationTimeoutError


def assert_timeout(
    error: BaseException,
    *,
    phase: str,
    operation: str,
    retryable: bool,
    discarded: bool,
    outcome_unknown: bool,
) -> None:
    _, error_type = timeout_api()
    assert isinstance(error, error_type)
    assert isinstance(error, fastmssql.SqlConnectionError)
    assert error.phase == phase
    assert error.operation == operation
    assert error.retryable is retryable
    assert error.connection_discarded is discarded
    assert error.outcome_unknown is outcome_unknown
    assert error.timeout_seconds > 0.0
    assert error.message


def timeout_connection(
    config: SqlAuthConfig,
    *,
    pool_config: PoolConfig,
    timeout_config,
    application_name: str,
    host: str | None = None,
    port: int | None = None,
) -> Connection:
    return Connection(
        server=host or config.host,
        port=port or config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config,
        timeout_config=timeout_config,
        application_name=application_name,
    )
```

- [ ] **Step 3: Add TIME-001 configuration fallback, precedence, and clone isolation**

Write one `@case("TIME-001")` async test that:

```python
TimeoutConfig, _ = timeout_api()
legacy = PoolConfig(
    max_size=1,
    min_idle=0,
    connection_timeout_secs=2,
    test_on_check_out=False,
    retry_connection=False,
)
external = TimeoutConfig(acquire_timeout_secs=0.375)
connection = timeout_connection(
    sql_auth_config,
    pool_config=legacy,
    timeout_config=external,
    application_name=unique_sql_name("strict_timeout_config"),
)
assert connection.timeout_config.acquire_timeout_secs == 0.375
external.acquire_timeout_secs = 9.0
assert connection.timeout_config.acquire_timeout_secs == 0.375
clone = connection.timeout_config
clone.acquire_timeout_secs = 8.0
assert connection.timeout_config.acquire_timeout_secs == 0.375
with pytest.raises(AttributeError):
    connection.timeout_config = TimeoutConfig()
transaction = connection.transaction()
assert transaction.timeout_config.acquire_timeout_secs == 0.375
with pytest.raises(AttributeError):
    transaction.timeout_config = TimeoutConfig()
await connection.connect()
assert await scalar(connection, "SELECT 1") == 1
await transaction.close()
await connection.disconnect()

legacy_connection = timeout_connection(
    sql_auth_config,
    pool_config=legacy,
    timeout_config=None,
    application_name=unique_sql_name("strict_timeout_legacy"),
)
assert legacy_connection.timeout_config.connect_timeout_secs == 2.0
assert legacy_connection.timeout_config.acquire_timeout_secs == 2.0
assert legacy_connection.timeout_config.operation_timeout_secs is None
await legacy_connection.disconnect()

bb8_default_connection = timeout_connection(
    sql_auth_config,
    pool_config=PoolConfig(
        max_size=1,
        min_idle=0,
        connection_timeout_secs=None,
    ),
    timeout_config=None,
    application_name=unique_sql_name("strict_timeout_bb8_default"),
)
assert bb8_default_connection.timeout_config.connect_timeout_secs == 30.0
assert bb8_default_connection.timeout_config.acquire_timeout_secs == 30.0
await bb8_default_connection.disconnect()
```

Also construct:

```python
direct_default = Transaction(
    server=sql_auth_config.host,
    port=sql_auth_config.port,
    database=sql_auth_config.database,
    username=sql_auth_config.owner_user,
    password=sql_auth_config.owner_password,
    ssl_config=SslConfig.development(),
)
assert direct_default.timeout_config.acquire_timeout_secs == 30.0

direct_explicit = Transaction(
    server=sql_auth_config.host,
    port=sql_auth_config.port,
    database=sql_auth_config.database,
    username=sql_auth_config.owner_user,
    password=sql_auth_config.owner_password,
    ssl_config=SslConfig.development(),
    timeout_config=external,
)
assert direct_explicit.timeout_config.acquire_timeout_secs == 9.0
external.acquire_timeout_secs = 7.0
assert direct_explicit.timeout_config.acquire_timeout_secs == 9.0
await direct_default.close()
await direct_explicit.close()
```

This proves direct defaults and clone isolation without opening sockets.

- [ ] **Step 4: Add TIME-002 native physical-connect timeout**

Use `HandshakeBlackhole` with `PoolConfig(max_size=1, min_idle=1, retry_connection=False)` and `TimeoutConfig(connect_timeout_secs=0.2, acquire_timeout_secs=1.0)`. Call `connection.connect(validate=False)` without an inner `asyncio.wait_for`, measure with `time.monotonic()`, and assert:

```python
with pytest.raises(fastmssql.OperationTimeoutError) as captured:
    await connection.connect(validate=False)
elapsed = time.monotonic() - started
assert 0.15 <= elapsed < 1.0
assert_timeout(
    captured.value,
    phase="connect",
    operation="connect",
    retryable=True,
    discarded=False,
    outcome_unknown=False,
)
assert captured.value.timeout_seconds == pytest.approx(0.2)
assert blackhole.accepted.is_set()
assert await connection.is_connected() is False
```

After the exception, poll `blackhole.open_connections == 0` through an async predicate passed to `wait_until`. Use an outer `asyncio.wait_for(..., timeout=2.0)` only as a test-process watchdog around the whole case; catch and assert the native timeout inside it.

- [ ] **Step 5: Add TIME-003 saturated pool acquisition timeout**

Create a pool with `max_size=1`, `min_idle=0`, legacy `connection_timeout_secs=2`, explicit `acquire_timeout_secs=0.2`, and `operation_timeout_secs=2.0`. Start a holder query containing `WAITFOR DELAY '00:00:00.600'`, poll `pool_stats()["active_connections"] == 1`, then make the second query.

Assert:

```python
assert_timeout(
    captured.value,
    phase="acquire",
    operation="query",
    retryable=True,
    discarded=False,
    outcome_unknown=False,
)
assert 0.15 <= elapsed < 0.8
assert await holder == 1
assert await scalar(connection, "SELECT 3") == 3
```

Make the second call a query batch that inserts a unique key into a test log and then returns `2`. Assert the log remains empty after the acquire timeout; unlike a transient DMV sample, that persistent side effect proves no application SQL started.

- [ ] **Step 6: Add TIME-004 operation timeout and physical-session retirement**

With a one-connection pool and `operation_timeout_secs=0.2`, record `@@SPID` and `connection_id`, execute a tokenized `WAITFOR DELAY '00:00:05'; SELECT 4`, and assert:

```python
assert_timeout(
    captured.value,
    phase="operation",
    operation="query",
    retryable=False,
    discarded=True,
    outcome_unknown=True,
)
```

Poll `sys.dm_exec_requests` and `sys.dm_exec_sessions` through `sa_connection` until the timed-out request/session is absent. Then prove recovery returns `4`, uses a different `connection_id`, keeps `connections <= max_size`, and leaves zero application sessions after `disconnect()`.

- [ ] **Step 7: Add TIME-005 no-retry write reconciliation**

Create:

```sql
CREATE TABLE [business] (
    business_key NVARCHAR(100) PRIMARY KEY,
    value INT NOT NULL
);
CREATE TABLE [attempts] (
    attempt_id INT IDENTITY PRIMARY KEY,
    business_key NVARCHAR(100) NOT NULL
);
CREATE PROCEDURE [apply_once]
    @business_key NVARCHAR(100),
    @value INT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO [attempts] (business_key) VALUES (@business_key);
    IF NOT EXISTS (
        SELECT 1 FROM [business] WHERE business_key = @business_key
    )
        INSERT INTO [business] (business_key, value)
        VALUES (@business_key, @value);
END
```

Connect through `DownstreamGateProxy`, warm the pool, pause downstream, execute the parameterized procedure once with an operation budget of `0.2`, wait until `sa_connection` observes the business key, and assert the timeout metadata has `retryable=False`, `connection_discarded=True`, and `outcome_unknown=True`.

Resume the proxy and reconcile:

```python
assert await scalar(
    sa_connection,
    f"SELECT COUNT(*) FROM {attempts} WHERE business_key = @P1",
    [business_key],
) == 1
assert await scalar(
    sa_connection,
    f"SELECT COUNT(*) FROM {business} WHERE business_key = @P1",
    [business_key],
) == 1
```

The test must never reissue the procedure itself. Cleanup drops the procedure before both tables.

- [ ] **Step 8: Add TIME-006 absolute batch and bulk budgets**

Exercise two subcases under the same case ID:

1. `execute_batch` receives three statements, each containing a 120 ms `WAITFOR` followed by a parameterized insert, with a 250 ms operation budget.
2. `bulk_insert` inserts more than one internal chunk into a table with an `INSTEAD OF INSERT` trigger that waits 160 ms per invocation, with a 250 ms operation budget.

For each subcase, assert:

```python
assert 0.18 <= elapsed < 0.75
assert_timeout(
    captured.value,
    phase="operation",
    operation=public_operation,
    retryable=False,
    discarded=True,
    outcome_unknown=True,
)
```

Use a distinct SQL token per batch item and poll `sys.dm_exec_requests` every 5 ms while the call runs; the third statement waits long enough that its token would be sampled if submitted. For bulk, record distinct `(session_id, request_id, start_time)` values while the trigger holds each chunk and assert only the first chunk request starts before timeout. Verify transactional rows are either fully absent or reconciled according to the existing API contract; do not claim a new atomicity guarantee.

- [ ] **Step 9: Add TIME-007 idle transaction-lifetime expiry**

Begin a pooled transaction with `transaction_timeout_secs=0.25`, insert one uncommitted business row, start another transaction waiting for the only pool slot, and wait until the lifetime has elapsed. The next operation on the first transaction must fail before sending its unique SQL token:

```python
assert_timeout(
    captured.value,
    phase="transaction",
    operation="query",
    retryable=False,
    discarded=True,
    outcome_unknown=False,
)
assert first.is_connected() is False
```

Prove the waiting transaction acquires a different connection, the first row is absent through `sa_connection`, the token never appears in `sys.dm_exec_requests`, and all sessions disappear after close/disconnect.

- [ ] **Step 10: Add TIME-008 earlier-deadline selection for in-flight work**

Run two fresh pooled transactions:

```text
subcase A: operation=0.20 s, transaction=2.00 s -> phase operation
subcase B: operation=2.00 s, transaction=0.35 s; start WAITFOR after ~0.20 s -> phase transaction
```

Each issues a five-second tokenized `WAITFOR`, asserts the winning phase and configured `timeout_seconds`, observes connection retirement, and recovers with a fresh transaction. Reuse the existing epoch-cancellation invariants by immediately starting a later transaction and proving delayed cleanup cannot close its different `connection_id`.

- [ ] **Step 11: Add TIME-009 COMMIT and rollback/close precedence**

For the COMMIT subcase:

1. connect through `DownstreamGateProxy`;
2. begin and insert a unique row;
3. pause downstream immediately before `commit()`;
4. wait from `sa_connection` until the durable row is visible;
5. let the operation deadline expire.

Assert:

```python
assert type(error).__name__ == "CommitOutcomeUnknown"
assert error.operation == "commit"
assert error.retryable is False
assert error.connection_discarded is True
assert isinstance(error.__cause__, fastmssql.OperationTimeoutError)
assert error.__cause__.phase in {"operation", "transaction"}
assert error.__cause__.operation == "commit"
assert error.__cause__.outcome_unknown is True
```

Do not call rollback or commit again before reconciling the row.

For rollback and `close()` subcases, hold the rollback response with the proxy and configure `rollback_timeout_secs=0.2`. Assert a top-level `OperationTimeoutError` with phase/operation `rollback` or phase `rollback`, operation `close`, `connection_discarded=True`, and no swallowed error. Prove the uncommitted row disappears after transport closure.

- [ ] **Step 12: Route the new strict file**

Add `tests/sql_auth_strict/test_operation_timeouts.py` to `strict_functional` in `scripts/sql_auth/run_all.sh`, immediately after `test_transactions_strict.py`.

- [ ] **Step 13: Run the real RED cases against the dedicated Docker database**

Run:

```bash
docker compose --env-file .env.sql-auth.local -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
uv run pytest tests/sql_auth_strict/test_operation_timeouts.py -vv
```

Expected RED: all nine `TIME-*` nodes collect, and each fails first on the missing `TimeoutConfig`/`OperationTimeoutError` API or rejected `timeout_config` constructor keyword. There must be no skip, xfail, outer-watchdog pass, leaked session, or Docker action against another target.

---

### Task 4: Write RED framework/load case TIME-010 and preserve the entire RED branch

**Files:**
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`
- Commit: all RED tests and harness contracts on `test/operation-timeouts`

**Interfaces:**
- Consumes: existing `FrameworkState`, FastAPI lifespan, Flask `WsgiToAsgi`, HTTPX `ASGITransport`, event-loop ticker, and real SQL-auth pool/session helpers.
- Produces: one 1,000-operation, concurrency-100 framework recovery gate with typed error propagation and a pushed RED commit.

- [ ] **Step 1: Allow framework test state to receive timeout policy explicitly**

Change `FrameworkState.create()` in `tests/sql_auth_strict/framework_apps.py` so its test-only signature is:

```python
@classmethod
def create(
    cls,
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 4,
    min_idle: int = 0,
    pool_config: PoolConfig | None = None,
    timeout_config=None,
) -> FrameworkState:
    connection = Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config or PoolConfig(
            max_size=max_size,
            min_idle=min_idle,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=3,
            retry_connection=False,
        ),
        timeout_config=timeout_config,
        application_name=application_name,
    )
    return cls(config, application_name, connection)
```

Retain the existing application-name validation and every call-site default. This change is test application wiring, not a FastAPI/Flask runtime dependency in the library.

- [ ] **Step 2: Add timeout-aware framework routes**

In the test app factories, add routes that invoke:

```python
await state.connection.query(
    "WAITFOR DELAY '00:00:00.250'; SELECT @P1 AS value",
    [value],
)
```

and serialize `OperationTimeoutError` metadata only in the test response:

```python
{
    "type": type(error).__name__,
    "phase": error.phase,
    "operation": error.operation,
    "retryable": error.retryable,
    "connection_discarded": error.connection_discarded,
    "outcome_unknown": error.outcome_unknown,
}
```

FastAPI/native ASGI and Flask-through-`WsgiToAsgi` must use the same persistent `FrameworkState.connection`. Do not add this load route to plain Flask/WSGI because that mode has a per-request event loop and is not the true-async claim under test.

- [ ] **Step 3: Add TIME-010 framework concurrency and recovery**

Add `test_framework_operation_timeout_recovery_load`, decorated with `@case("TIME-010")`, `@pytest.mark.framework`, and `@pytest.mark.asyncio`, to `tests/sql_auth_strict/test_framework_integration.py`.

Configure:

```python
TimeoutConfig = getattr(fastmssql, "TimeoutConfig", None)
assert TimeoutConfig is not None
pool_config = PoolConfig(
    max_size=20,
    min_idle=5,
    test_on_check_out=False,
    retry_connection=False,
)
timeout_config = TimeoutConfig(
    acquire_timeout_secs=0.10,
    operation_timeout_secs=0.12,
)
```

Run exactly 500 FastAPI requests and 500 Flask-through-ASGI requests in barrier-controlled waves with total task concurrency 100. At the start of each framework lane, launch 20 blocking requests, poll until `active_connections == 20`, and only then release the remaining 80 tasks in that wave; this deterministically creates checkout pressure. Use this mix:

```text
60% immediate SELECT success
20% 250 ms SQL wait -> operation timeout
20% pool pressure -> acquire timeout
```

The operation mix must be generated from `index % 5`, not randomness. Start an event-loop ticker at 10 ms, record every response/exception class, and assert:

```python
assert total_operations == 1000
assert successful > 0
assert acquire_timeouts > 0
assert operation_timeouts > 0
assert {payload["type"] for payload in timeout_payloads} == {
    "OperationTimeoutError"
}
assert max_observed_pool_connections <= 20
assert len(ticks) >= 20
assert await scalar(state.connection, "SELECT 1010") == 1010
```

Run a 10 ms pool-stat sampler alongside each wave and assign its maximum `connections` value to `max_observed_pool_connections`. For operation timeouts, record the pre-request `connection_id` when available and prove it is not returned by a later success. At each lifespan shutdown, poll `sys.dm_exec_sessions` until the candidate `application_name` has zero rows.

Also execute one plain Flask/WSGI timeout request as functional compatibility and record its result separately; do not include its timing in the 1,000 true-async operations.

- [ ] **Step 4: Run the focused framework RED node**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_framework_integration.py::test_framework_operation_timeout_recovery_load \
  -vv
```

Expected RED: failure at the missing `TimeoutConfig` export or constructor keyword before the 1,000-operation loop starts.

- [ ] **Step 5: Prove exact registry coverage on RED**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
env PYTHONPATH=tests uv run python -c "from pathlib import Path; from sql_auth_strict.cases import spec_case_ids; p=Path('docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md'); ids=spec_case_ids(p); assert len(ids)==295; assert {f'TIME-{n:03d}' for n in range(1,11)} <= ids; print(len(ids))"
```

Expected: the matrix contract passes, prints `295`, and every new ID occurs in exactly one `test_*.py` source.

- [ ] **Step 6: Inspect RED changes for accidental implementation**

Run:

```bash
git status --short
git diff --check
git diff -- src python README.md
git diff --stat
```

Expected: `src/`, `python/`, and `README.md` have no behavior implementation changes; only test, harness, spec-registry, workflow, and runner files differ.

- [ ] **Step 7: Commit and push deterministic RED exclusively to the fork**

Run:

```bash
git add \
  .github/workflows/rust-unit-tests.yml \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/run_all.sh \
  tests/test_pyo3_build_contract.py \
  tests/test_timeout_config_contract.py \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_operation_timeouts.py \
  tests/sql_auth_strict/timeout_fixtures.py
git commit -m "test: require operation timeout safety"
git push -u origin test/operation-timeouts
git rev-list --left-right --count HEAD...origin/test/operation-timeouts
```

Expected: one RED commit, push target `galeamarcel/FastMssql`, parity `0	0`.

---

### Task 5: Implement `TimeoutConfig`, absolute deadline primitives, and typed errors

**Files:**
- Create: `src/timeout_config.rs`
- Create: `src/deadline.rs`
- Modify: `src/types.rs`
- Modify: `src/lib.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Create worktree: `.worktrees/feat-operation-timeouts`
- Branch: `feat/operation-timeouts`

**Interfaces:**
- Consumes: RED commit from `test/operation-timeouts` and `PyPoolConfig.connection_timeout`.
- Produces:
  - `PyTimeoutConfig::explicit_default() -> Self`
  - `PyTimeoutConfig::from_pool_compatibility(&PyPoolConfig) -> Self`
  - `PyTimeoutConfig::align_pool_config(&self, &PyPoolConfig) -> PyPoolConfig`
  - `deadline_from(TimeoutPhase, Option<Duration>) -> Option<Deadline>`
  - `earliest_deadline(Option<Deadline>, Option<Deadline>) -> Option<Deadline>`
  - `run_until(Option<Deadline>, Future) -> Result<T, DeadlineElapsed>`
  - `create_operation_timeout_error(...) -> PyResult<PyErr>`

- [ ] **Step 1: Create the GREEN feature worktree from the exact RED commit**

Run from the repository root:

```bash
git worktree add .worktrees/feat-operation-timeouts -b feat/operation-timeouts test/operation-timeouts
git -C .worktrees/feat-operation-timeouts rev-parse HEAD
git -C .worktrees/feat-operation-timeouts status --short --branch
```

Expected: feature HEAD equals the pushed RED commit and the worktree is clean.

- [ ] **Step 2: Add seconds extraction that distinguishes omission, explicit `None`, and booleans**

In `src/timeout_config.rs`, define private PyO3 extraction wrappers:

```rust
#[derive(Clone, Copy)]
struct OptionalSeconds(Option<f64>);

#[derive(Clone, Copy)]
struct RequiredSeconds(f64);

impl OptionalSeconds {
    const fn finite(value: f64) -> Self {
        Self(Some(value))
    }
}

impl RequiredSeconds {
    const fn finite(value: f64) -> Self {
        Self(value)
    }
}
```

Implement `FromPyObject` for both wrappers. Before extracting `f64`, reject `PyBool`; for optional values accept `None`. Validate with one shared function:

```rust
impl<'a, 'py> FromPyObject<'a, 'py> for OptionalSeconds {
    type Error = PyErr;

    fn extract(
        object: Borrowed<'a, 'py, PyAny>,
    ) -> Result<Self, Self::Error> {
        if object.is_instance_of::<PyBool>() {
            return Err(PyValueError::new_err(
                "timeout values do not accept booleans",
            ));
        }
        if object.is_none() {
            return Ok(Self(None));
        }
        object
            .extract::<f64>()
            .map(|value| Self(Some(value)))
            .map_err(|_| {
                PyValueError::new_err(
                    "timeout values must be numbers or None",
                )
            })
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for RequiredSeconds {
    type Error = PyErr;

    fn extract(
        object: Borrowed<'a, 'py, PyAny>,
    ) -> Result<Self, Self::Error> {
        if object.is_none() || object.is_instance_of::<PyBool>() {
            return Err(PyValueError::new_err(
                "acquire_timeout_secs must be a number greater than 0",
            ));
        }
        object.extract::<f64>().map(Self).map_err(|_| {
            PyValueError::new_err(
                "acquire_timeout_secs must be a number greater than 0",
            )
        })
    }
}

fn positive_finite_seconds(name: &str, value: f64) -> PyResult<Duration> {
    if !value.is_finite() || value <= 0.0 {
        return Err(PyValueError::new_err(format!(
            "{name} must be a finite number greater than 0"
        )));
    }
    Duration::try_from_secs_f64(value).map_err(|_| {
        PyValueError::new_err(format!(
            "{name} must fit in a Rust Duration"
        ))
    })
}
```

Boolean and nonnumeric extraction errors are `ValueError`; finite-positive validation names the public field.

- [ ] **Step 3: Implement the compiled timeout configuration**

Define:

```rust
#[pyclass(name = "TimeoutConfig", from_py_object)]
#[derive(Clone, Debug)]
pub struct PyTimeoutConfig {
    pub(crate) connect_timeout: Option<Duration>,
    pub(crate) acquire_timeout: Duration,
    pub(crate) operation_timeout: Option<Duration>,
    pub(crate) transaction_timeout: Option<Duration>,
    pub(crate) rollback_timeout: Option<Duration>,
}
```

Use a PyO3 constructor whose Rust defaults preserve omission while the explicit text signature is exactly:

```rust
#[pyo3(
    signature = (
        connect_timeout_secs = OptionalSeconds::finite(30.0),
        acquire_timeout_secs = RequiredSeconds::finite(30.0),
        operation_timeout_secs = OptionalSeconds(None),
        transaction_timeout_secs = OptionalSeconds(None),
        rollback_timeout_secs = OptionalSeconds::finite(30.0)
    ),
    text_signature = "(connect_timeout_secs=30.0, acquire_timeout_secs=30.0, operation_timeout_secs=None, transaction_timeout_secs=None, rollback_timeout_secs=30.0)"
)]
```

PyO3 0.29 parses signature defaults as Rust expressions, so these wrapper expressions are the implementation; retain the explicit `text_signature` and do not collapse explicit `None` into omission.

Add getters returning `Option<f64>`/`f64`, setters that call the same validation, and exact `repr`:

```rust
fn render_optional(value: Option<Duration>) -> String {
    value
        .map(|duration| format!("{:?}", duration.as_secs_f64()))
        .unwrap_or_else(|| "None".to_string())
}
```

Normalize integer-valued floats to include `.0` and preserve subsecond values without integer rounding.

- [ ] **Step 4: Implement compatibility derivation and acquisition precedence**

Add:

```rust
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

impl PyTimeoutConfig {
    pub(crate) fn explicit_default() -> Self {
        Self {
            connect_timeout: Some(DEFAULT_TIMEOUT),
            acquire_timeout: DEFAULT_TIMEOUT,
            operation_timeout: None,
            transaction_timeout: None,
            rollback_timeout: Some(DEFAULT_TIMEOUT),
        }
    }

    pub(crate) fn from_pool_compatibility(pool: &PyPoolConfig) -> Self {
        let legacy = pool.connection_timeout.unwrap_or(DEFAULT_TIMEOUT);
        Self {
            connect_timeout: Some(legacy),
            acquire_timeout: legacy,
            operation_timeout: None,
            transaction_timeout: None,
            rollback_timeout: Some(DEFAULT_TIMEOUT),
        }
    }

    pub(crate) fn align_pool_config(
        &self,
        pool: &PyPoolConfig,
    ) -> PyPoolConfig {
        let mut aligned = pool.clone();
        aligned.connection_timeout = Some(self.acquire_timeout);
        aligned
    }
}
```

- [ ] **Step 5: Implement absolute deadline types without resource ownership**

In `src/deadline.rs`:

```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TimeoutPhase {
    Connect,
    Acquire,
    Operation,
    Transaction,
    Rollback,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OperationName {
    Connect,
    Ping,
    Query,
    SimpleQuery,
    Execute,
    QueryBatch,
    ExecuteBatch,
    BulkInsert,
    Begin,
    Commit,
    Rollback,
    Close,
    Transaction,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Deadline {
    pub(crate) at: tokio::time::Instant,
    pub(crate) timeout: Duration,
    pub(crate) phase: TimeoutPhase,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DeadlineElapsed {
    pub(crate) timeout: Duration,
    pub(crate) phase: TimeoutPhase,
}
```

Implement:

```rust
pub(crate) fn deadline_from(
    phase: TimeoutPhase,
    timeout: Option<Duration>,
) -> Option<Deadline> {
    timeout.map(|duration| Deadline {
        at: tokio::time::Instant::now() + duration,
        timeout: duration,
        phase,
    })
}

pub(crate) fn earliest_deadline(
    first: Option<Deadline>,
    second: Option<Deadline>,
) -> Option<Deadline> {
    match (first, second) {
        (Some(left), Some(right)) if right.at < left.at => Some(right),
        (Some(left), Some(_)) => Some(left),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

pub(crate) async fn run_until<F, T>(
    deadline: Option<Deadline>,
    future: F,
) -> Result<T, DeadlineElapsed>
where
    F: Future<Output = T>,
{
    match deadline {
        Some(deadline) => tokio::time::timeout_at(deadline.at, future)
            .await
            .map_err(|_| DeadlineElapsed {
                timeout: deadline.timeout,
                phase: deadline.phase,
            }),
        None => Ok(future.await),
    }
}
```

Implement `TimeoutPhase::as_str()` and `OperationName::as_str()` with the exact approved lowercase spellings. Do not spawn a timer task and do not store a Python object in `Deadline`.

- [ ] **Step 6: Declare and construct the typed timeout exception**

In `src/types.rs`:

```rust
create_exception!(
    crate::fastmssql,
    OperationTimeoutError,
    SqlConnectionError
);

pub(crate) struct TimeoutErrorMetadata {
    pub(crate) operation: OperationName,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
}

pub(crate) fn create_operation_timeout_error(
    elapsed: DeadlineElapsed,
    metadata: TimeoutErrorMetadata,
) -> PyResult<PyErr> {
    let phase = elapsed.phase.as_str();
    let seconds = elapsed.timeout.as_secs_f64();
    let message = format!(
        "{} timed out in {} phase after {:.6} seconds",
        metadata.operation.as_str(), phase, seconds
    );
    Python::attach(|py| {
        let error = OperationTimeoutError::new_err(message.clone());
        let value = error.value(py);
        value.setattr("message", message)?;
        value.setattr("operation", metadata.operation.as_str())?;
        value.setattr("phase", phase)?;
        value.setattr("timeout_seconds", seconds)?;
        value.setattr("retryable", metadata.retryable)?;
        value.setattr(
            "connection_discarded",
            metadata.connection_discarded,
        )?;
        value.setattr("outcome_unknown", metadata.outcome_unknown)?;
        Ok(error)
    })
}
```

- [ ] **Step 7: Register compiled classes and package-root exports**

In `src/lib.rs`, add modules and exports:

```rust
mod deadline;
mod timeout_config;

pub use timeout_config::PyTimeoutConfig;
pub use types::OperationTimeoutError;
```

Register `PyTimeoutConfig` with `m.add_class`, and add `OperationTimeoutError` next to `SqlConnectionError`.

In `python/fastmssql/__init__.py`, import and include:

```python
OperationTimeoutError
TimeoutConfig
```

in `__all__`. Do not change the wrapper's context-manager exception behavior in this candidate.

- [ ] **Step 8: Add exact type-stub surfaces**

Define these classes in the compiled stub `python/fastmssql/fastmssql.pyi`:

```python
class TimeoutConfig:
    connect_timeout_secs: Optional[float]
    acquire_timeout_secs: float
    operation_timeout_secs: Optional[float]
    transaction_timeout_secs: Optional[float]
    rollback_timeout_secs: Optional[float]

    def __init__(
        self,
        connect_timeout_secs: Optional[float] = 30.0,
        acquire_timeout_secs: float = 30.0,
        operation_timeout_secs: Optional[float] = None,
        transaction_timeout_secs: Optional[float] = None,
        rollback_timeout_secs: Optional[float] = 30.0,
    ) -> None: ...

class OperationTimeoutError(SqlConnectionError):
    message: str
    operation: str
    phase: str
    timeout_seconds: float
    retryable: bool
    connection_discarded: bool
    outcome_unknown: bool
```

Import both names from `.fastmssql` in `python/fastmssql/__init__.pyi`; do not duplicate their class definitions in the wrapper stub. Append `timeout_config: Optional[TimeoutConfig] = None` as the final constructor parameter for both compiled/wrapper `Connection` and `Transaction`. Add:

```python
@property
def timeout_config(self) -> TimeoutConfig: ...
```

to both types and add both new public names to each `__all__`.

- [ ] **Step 9: Add focused Rust unit tests**

In `src/timeout_config.rs` and `src/deadline.rs`, add unit tests for:

```rust
#[test]
fn compatibility_uses_legacy_pool_timeout() { /* assert 2 s */ }

#[test]
fn explicit_acquire_timeout_aligns_internal_pool() { /* assert 125 ms */ }

#[tokio::test]
async fn earliest_absolute_deadline_wins() { /* transaction before operation */ }

#[test]
fn phase_and_operation_names_are_stable() {
    /* all five phase names and all thirteen operation names */
}
```

Use concrete constructed values and assertions; do not use wall-clock equality for `Instant`.

- [ ] **Step 10: Run the API-focused GREEN cycle**

Run:

```bash
cargo fmt --check
cargo test --locked timeout -- --nocapture
uv run maturin develop --release
uv run pytest --noconftest tests/test_timeout_config_contract.py -q
```

Expected: Rust timeout unit tests and the isolated API contract pass. SQL-auth TIME cases still fail because constructors and operations do not yet consume the policy.

- [ ] **Step 11: Commit the foundational API**

Run:

```bash
git add \
  src/deadline.rs \
  src/lib.rs \
  src/timeout_config.rs \
  src/types.rs \
  python/fastmssql/__init__.py \
  python/fastmssql/__init__.pyi \
  python/fastmssql/fastmssql.pyi
git commit -m "feat: add typed timeout policy"
```

Expected: one reviewable commit containing only configuration, deadlines, exception construction, and public declarations.

---

### Task 6: Bound physical connection setup and pool acquisition

**Files:**
- Modify: `src/pool_manager.rs`
- Modify: `src/connection.rs`
- Modify: `src/batch.rs`
- Modify: `src/transaction.rs`
- Modify: `python/fastmssql/__init__.py`

**Interfaces:**
- Consumes: `PyTimeoutConfig`, `Deadline`, `DeadlineElapsed`, `TimeoutErrorMetadata`.
- Produces:
  - `connect_client_with_timeout(config, credential, connect_timeout, OperationName) -> PyResult<TiberiusClient>`
  - `map_pool_checkout_error(error, OperationName, acquire_timeout) -> PyErr`
  - `acquire_owned_connection(pool, OperationName, acquire_timeout) -> PyResult<OwnedPooledConnection>`
  - policy-aware `ensure_pool_initialized_with_auth(...)`.

- [ ] **Step 1: Represent physical connect expiry inside the bb8 manager error**

Extend `PoolConnectionError`:

```rust
Timeout {
    timeout: Duration,
},
```

Its display is:

```text
physical connection timed out after <seconds> seconds
```

Do not convert it through the generic message-only `create_connection_error`.

- [ ] **Step 2: Factor the complete authenticated connection future**

Create a private function in `src/pool_manager.rs`:

```rust
async fn connect_client_inner(
    base_config: &Config,
    azure_credential: Option<&Arc<PyAzureCredential>>,
) -> Result<TiberiusClient, PoolConnectionError>
```

Move into it, in this exact order:

1. Azure credential cache/refresh and auth assignment;
2. address lookup/TCP connect;
3. `TCP_NODELAY`;
4. Tiberius pre-login/TLS/login;
5. one routing reconnect using the same mutable config.

No internal step receives a reset duration.

- [ ] **Step 3: Apply one physical-connect deadline**

Add:

```rust
async fn connect_client_bounded(
    base_config: &Config,
    azure_credential: Option<&Arc<PyAzureCredential>>,
    timeout: Option<Duration>,
) -> Result<TiberiusClient, PoolConnectionError> {
    let deadline = deadline_from(TimeoutPhase::Connect, timeout);
    run_until(
        deadline,
        connect_client_inner(base_config, azure_credential),
    )
    .await
    .map_err(|elapsed| PoolConnectionError::Timeout {
        timeout: elapsed.timeout,
    })?
}
```

`AzureConnectionManager` stores `connect_timeout: Option<Duration>` and its `connect()` calls this function once. Add a direct public-in-crate adapter that maps a physical timeout with the calling public operation name to `OperationTimeoutError`.

- [ ] **Step 4: Configure bb8 with only the acquisition budget**

Change:

```rust
AzureConnectionManager::new(
    base_config.clone(),
    azure_credential,
    timeout_config.connect_timeout,
)
```

and always set:

```rust
builder = builder.connection_timeout(timeout_config.acquire_timeout);
```

Change `establish_pool`, `ensure_pool_initialized_with_auth`, and `warmup_pool` to receive `&PyTimeoutConfig` and an `OperationName`. Keep warmup's overall 120-second safety cap, but map any manager physical timeout as phase `connect`, and any bb8 checkout expiry as phase `acquire`.

- [ ] **Step 5: Map checkout errors with stable structured metadata**

Use:

```rust
pub(crate) fn map_pool_checkout_error(
    error: bb8::RunError<PoolConnectionError>,
    operation: OperationName,
    acquire_timeout: Duration,
) -> PyErr
```

Mapping:

```text
RunError::TimedOut
  -> OperationTimeoutError(phase=acquire, retryable=true,
     connection_discarded=false, outcome_unknown=false)

RunError::User(PoolConnectionError::Timeout { timeout })
  -> OperationTimeoutError(phase=connect, retryable=true,
     connection_discarded=false, outcome_unknown=false)

other RunError::User
  -> existing typed SQL/TLS/protocol/connection mapping
```

Because `map_err` requires a concrete `PyErr`, convert the fallible metadata constructor without suppression:

```rust
fn timeout_error_or_metadata_failure(
    elapsed: DeadlineElapsed,
    metadata: TimeoutErrorMetadata,
) -> PyErr {
    match create_operation_timeout_error(elapsed, metadata) {
        Ok(timeout) => timeout,
        Err(metadata_failure) => metadata_failure,
    }
}
```

A Python allocation/attribute failure therefore becomes the surfaced error; it is not discarded. No branch starts an application SQL retry.

- [ ] **Step 6: Resolve and store effective policy in `PyConnection`**

Append the final PyO3 constructor argument:

```rust
timeout_config: Option<PyTimeoutConfig>,
```

After building the base pool config:

```rust
let original_pool_config = pool_config.unwrap_or_default();
let effective_timeout = timeout_config
    .unwrap_or_else(|| {
        PyTimeoutConfig::from_pool_compatibility(&original_pool_config)
    });
let effective_pool_config =
    effective_timeout.align_pool_config(&original_pool_config);
```

Store `effective_timeout` on `PyConnection` and `ConnectionHandles`. Add:

```rust
#[getter]
pub fn timeout_config(&self) -> PyTimeoutConfig {
    self.timeout_config.clone()
}
```

This getter must return a clone, not a reference to mutable internal state.

- [ ] **Step 7: Make acquisition operation-aware**

Change:

```rust
async fn get_pool_connection(
    pool: &ConnectionPool,
    timeouts: &PyTimeoutConfig,
    operation: OperationName,
) -> PyResult<PooledOperationGuard<'_>>
```

and call:

```rust
pool.get()
    .await
    .map_err(|error| {
        map_pool_checkout_error(
            error,
            operation,
            timeouts.acquire_timeout,
        )
    })
```

Use this exact mapping at every call site:

```text
connect       -> OperationName::Connect
ping          -> OperationName::Ping
query         -> OperationName::Query
simple_query  -> OperationName::SimpleQuery
execute       -> OperationName::Execute
query_batch   -> OperationName::QueryBatch
execute_batch -> OperationName::ExecuteBatch
bulk_insert   -> OperationName::BulkInsert
begin         -> OperationName::Begin
commit        -> OperationName::Commit
rollback      -> OperationName::Rollback
close         -> OperationName::Close
transaction   -> OperationName::Transaction
```

- [ ] **Step 8: Bound direct batch and direct transaction physical setup**

Replace duplicated Azure/TCP/TLS connection construction in `src/batch.rs` and the direct path of `Transaction::ensure_connected_inner` with `connect_client_with_timeout`. Pass public operation `execute_batch` for the dedicated batch path and the exact direct-transaction caller name listed below.

Change the transaction handle interface to:

```rust
async fn ensure_connected(
    &self,
    operation: OperationName,
) -> PyResult<()>
```

and pass the exact enum variant from `begin`, `query`, `simple_query`, `execute`, `execute_batch`, and `query_batch`. For a direct transaction, `begin()` is the normal first connector and therefore reports `OperationName::Begin`.

In the same configuration commit, append `timeout_config: Option<PyTimeoutConfig>` to the direct `Transaction` constructor, store `PyTimeoutConfig::explicit_default()` on omission, copy `PyConnection`'s effective policy through `Transaction::from_pool`, `SharedPoolSource`, and `TransactionHandles`, and add the Rust clone getter. Add explicit package-wrapper forwarding properties:

```python
class Connection:
    @property
    def timeout_config(self):
        return self._conn.timeout_config


class Transaction:
    @property
    def timeout_config(self):
        return self._rust_conn.timeout_config
```

This makes TIME-001 inheritance and clone isolation GREEN before transaction-lifetime behavior is implemented.

- [ ] **Step 9: Run physical-connect and acquisition GREEN tests**

Run:

```bash
uv run maturin develop --release
uv run pytest \
  tests/sql_auth_strict/test_operation_timeouts.py \
  -k "TIME_001 or TIME_002 or TIME_003" -vv
uv run pytest \
  tests/sql_auth_strict/test_pool.py \
  tests/sql_auth_strict/test_connection.py \
  -q
cargo test --locked
```

Expected: TIME-001/002/003 pass with native typed errors. Existing pool/readiness tests pass; any prior assertion matching only the old pool-timeout message is updated to assert the superclass plus stable semantics, without weakening the timeout outcome.

- [ ] **Step 10: Commit physical and acquisition deadline behavior**

Run:

```bash
git add \
  src/pool_manager.rs \
  src/connection.rs \
  src/batch.rs \
  src/transaction.rs \
  python/fastmssql/__init__.py
git commit -m "feat: bound connection and pool acquisition"
```

---

### Task 7: Enforce one fail-closed deadline across pooled, batch, and bulk operations

**Files:**
- Modify: `src/connection.rs`
- Modify: `src/batch.rs`
- Modify: `src/pool_manager.rs`

**Interfaces:**
- Consumes: operation duration from `PyTimeoutConfig`, operation-aware acquisition, and `PooledOperationGuard`.
- Produces: `run_pooled_operation` behavior that creates exactly one deadline after checkout and leaves the guard incomplete on timeout.

- [ ] **Step 1: Separate readiness acquisition from readiness SQL execution**

Change `validate_pool_readiness` to:

```rust
async fn validate_pool_readiness(
    pool: &ConnectionPool,
    timeouts: &PyTimeoutConfig,
    operation: OperationName,
) -> PyResult<()>
```

First acquire with the acquire phase. Then create:

```rust
let deadline = deadline_from(
    TimeoutPhase::Operation,
    timeouts.operation_timeout,
);
```

and wrap only the complete `SELECT 1` send/consume future in `run_until`. On elapsed deadline, return metadata:

```text
operation = calling public method (`OperationName::Connect` or `OperationName::Ping`)
retryable = false
connection_discarded = true
outcome_unknown = false
```

Leave `PooledOperationGuard` incomplete so its drop path marks the connection unusable.

- [ ] **Step 2: Apply operation deadlines to query, simple query, and execute**

Extend the three internal helpers to receive `&PyTimeoutConfig` and an `OperationName`. Construct the deadline only after `get_pool_connection` returns.

For an elapsed operation:

```rust
return create_operation_timeout_error(
    elapsed,
    TimeoutErrorMetadata {
        operation,
        retryable: false,
        connection_discarded: true,
        outcome_unknown: true,
    },
)
.and_then(Err);
```

`ping` remains the explicit `outcome_unknown=false` exception. Complete/needs-reset logic runs only after the full response has been consumed.

- [ ] **Step 3: Preserve panic and SQL error classification**

The nested result order is:

```text
run_until(deadline, catch_driver_panic(driver_future))
  -> deadline elapsed: typed timeout, incomplete guard
  -> driver panic: existing panic error, incomplete/broken guard
  -> SQL result: existing complete_with_result_and_retirement
```

Do not map SQL Server errors to timeout errors and do not call `complete()` after deadline expiry.

- [ ] **Step 4: Apply one deadline to `query_batch`**

Pass `PyTimeoutConfig` into `query_batch`. Acquire first, create one deadline once, and wrap the entire `query_batch_on_connection` loop. The loop must not accept a duration or create its own deadline.

On elapsed deadline, use operation `query_batch`, `outcome_unknown=true`, and let the pooled guard retire the lease.

- [ ] **Step 5: Apply one deadline to dedicated `execute_batch`**

After the bounded physical connection succeeds, create one operation deadline and place the following complete future inside it:

```text
BEGIN response consumption
all batch command sends/responses
COMMIT response consumption
```

On timeout, drop the direct client and raise `OperationTimeoutError(operation="execute_batch", phase="operation", outcome_unknown=true)`. Do not issue a second COMMIT, rollback, or retry from the timeout branch.

For a deterministic SQL error before timeout, bound the existing cleanup rollback with `rollback_timeout`. If cleanup times out, preserve the original SQL error as top-level and attach the rollback timeout as context without hiding either error; the separate context-manager aggregation defect remains outside this task.

Use one explicit helper for that precedence:

```rust
fn attach_cleanup_cause(primary: PyErr, cleanup: PyErr) -> PyErr {
    Python::attach(|py| {
        primary.set_cause(py, Some(cleanup));
    });
    primary
}
```

There is no fallible Python attribute mutation in this helper; `PyErr::set_cause` returns `()`.

- [ ] **Step 6: Apply one deadline to `bulk_insert`**

After pool checkout, create one operation deadline around:

```text
BEGIN response
all generated INSERT chunks
COMMIT response
```

No chunk receives a fresh timer. A timeout leaves the guard incomplete and causes server-side rollback through transport retirement.

For a SQL error, perform the existing rollback inside a deadline derived from `rollback_timeout`; only call `conn.complete()` after the rollback response is fully consumed. If rollback fails or times out, return the primary SQL error with the cleanup failure installed as `__cause__`, and leave the pooled guard incomplete so the lease is retired.

- [ ] **Step 7: Prove TIME-004, TIME-005, and TIME-006 GREEN**

Run:

```bash
uv run maturin develop --release
uv run pytest \
  tests/sql_auth_strict/test_operation_timeouts.py \
  -k "TIME_004 or TIME_005 or TIME_006" -vv
uv run pytest \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/test_batch_operations.py \
  tests/test_batch_operations_advanced.py \
  -q
```

Expected: native timeout metadata, retired session IDs, exactly one stored-procedure attempt, absolute batch/bulk timing, and all pre-existing batch/bulk behavior passing.

- [ ] **Step 8: Commit pooled and compound operation deadlines**

Run:

```bash
git add src/connection.rs src/batch.rs src/pool_manager.rs
git commit -m "feat: enforce fail-closed operation deadlines"
```

---

### Task 8: Enforce transaction lifetime, operation, COMMIT, and rollback deadlines

**Files:**
- Modify: `src/transaction.rs`
- Modify: `src/connection.rs`
- Modify: `src/types.rs`

**Interfaces:**
- Consumes: `PyTimeoutConfig`, absolute `Deadline`, existing `TransactionCancellationGuard`, and existing `create_commit_outcome_unknown`.
- Produces:
  - `TransactionSession.lifetime_deadline: Option<Deadline>`
  - `begin_data_operation(session, operation, timeouts) -> PyResult<(TransactionState, u64, Option<Deadline>)>`
  - timeout-aware `execute_transaction_command`
  - timeout-aware `close`.

- [ ] **Step 1: Verify the immutable transaction policy boundary before adding timers**

Confirm Task 6 already placed `timeout_config: PyTimeoutConfig` on `SharedPoolSource`, `TransactionHandles`, and `Transaction`; the direct constructor uses `explicit_default()`, pooled transactions clone the connection policy, and both Rust/package properties return clones. Add no second resolution path and never read a mutable Python config during an operation.

- [ ] **Step 2: Store and clear the absolute transaction-lifetime deadline**

Extend `TransactionSession`:

```rust
lifetime_deadline: Option<Deadline>,
```

Initialize it to `None`. Clear it whenever the state reaches `Committed`, `RolledBack`, `Failed`, `Closing`, or reusable `Idle`. Do not create a task that fires while the transaction is idle.

After a complete successful `BEGIN` response:

```rust
session.lifetime_deadline = deadline_from(
    TimeoutPhase::Transaction,
    timeout_config.transaction_timeout,
);
```

The deadline is created after response consumption, not before `BEGIN`.

- [ ] **Step 3: Detect an already expired transaction before application SQL**

Before `connection.begin_operation()` and before changing state to `Executing`, inspect `session.lifetime_deadline`.

If `deadline.at <= Instant::now()`:

```rust
if let Some(connection) = session.conn.as_mut() {
    connection.mark_unusable();
}
session.conn.take();
session.state = TransactionState::Failed;
session.lifetime_deadline = None;
return create_operation_timeout_error(
    DeadlineElapsed {
        timeout: deadline.timeout,
        phase: TimeoutPhase::Transaction,
    },
    TimeoutErrorMetadata {
        operation,
        retryable: false,
        connection_discarded: true,
        outcome_unknown: false,
    },
)
.and_then(Err);
```

No SQL token may appear in `sys.dm_exec_requests` for this path.

- [ ] **Step 4: Select the earlier in-flight deadline once**

For data operations and COMMIT:

```rust
let operation_deadline = deadline_from(
    TimeoutPhase::Operation,
    timeout_config.operation_timeout,
);
let active_deadline = earliest_deadline(
    operation_deadline,
    session.lifetime_deadline,
);
```

For `BEGIN`, use only the operation deadline. For `ROLLBACK` and `close()`, ignore operation/lifetime and use:

```rust
deadline_from(
    TimeoutPhase::Rollback,
    timeout_config.rollback_timeout,
)
```

- [ ] **Step 5: Fail closed if BEGIN itself expires**

Wrap the complete `BEGIN TRANSACTION` send and response consumption in the operation deadline. If it expires, mark/take the connection, set `state=Failed`, leave `lifetime_deadline=None`, disarm the cancellation guard after retirement, and surface:

```text
phase = operation
operation = begin
retryable = false
connection_discarded = true
outcome_unknown = false
```

Create the transaction-lifetime deadline only on the successful response branch.

- [ ] **Step 6: Retire deterministically when an in-flight data operation expires**

Wrap the complete send/consume future in `run_until`. On elapsed deadline:

1. mark the transaction connection unusable;
2. remove it from `session.conn`;
3. set `state=Failed`;
4. clear `lifetime_deadline`;
5. disarm `TransactionCancellationGuard` only after those mutations;
6. return typed metadata with the winning phase and `outcome_unknown=true`.

Use operation names `query`, `simple_query`, `execute`, `execute_batch`, or `query_batch`. A stale epoch cleanup must see no matching in-flight connection and remain idempotent.

- [ ] **Step 7: Preserve COMMIT unknown-outcome precedence**

For COMMIT, the elapsed deadline cause is:

```rust
let timeout = create_operation_timeout_error(
    elapsed,
    TimeoutErrorMetadata {
        operation: OperationName::Commit,
        retryable: false,
        connection_discarded: true,
        outcome_unknown: true,
    },
)?;
```

Retire the connection and call:

```rust
create_commit_outcome_unknown(timeout).and_then(Err)
```

The top-level exception remains `CommitOutcomeUnknown`. Never call rollback, COMMIT again, or any retry after the elapsed COMMIT future was dropped.

Keep deterministic SQL Server COMMIT rejections (severity 0–19) as top-level `SqlError`, matching the existing contract.

- [ ] **Step 8: Make explicit rollback use only its cleanup budget**

`rollback()` must still validate that an active transaction exists, but an expired lifetime does not reject cleanup. Wrap the complete rollback response in the rollback deadline.

On success:

```text
state = RolledBack
lifetime_deadline = None
release pooled lease only after full response
```

On timeout/error:

```text
mark unusable
remove/drop direct socket or owned lease
state = Failed
lifetime_deadline = None
surface OperationTimeoutError(phase=rollback) or original typed error
```

The rollback/close timeout metadata is always `retryable=false`, `connection_discarded=true`, and `outcome_unknown=false`.

- [ ] **Step 9: Make `close()` visible, bounded, and idempotent**

At entry, atomically take `session.conn`, set `state=Closing`, and clear the lifetime deadline. If the previous state is `Active`, issue `IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION` under the rollback deadline.

Return behavior:

```text
no connection -> success and Idle
clean Idle/Committed/RolledBack connection -> release and success
successful rollback -> release and success
rollback timeout -> release broken/direct connection, set Idle only after drop,
                    and return OperationTimeoutError(operation=close, phase=rollback)
other rollback error -> release broken/direct connection and return that error
in-flight/failed state -> mark unusable, drop, reset to Idle, return success
```

Do not swallow the rollback timeout. Repeated `close()` after the surfaced error returns success because no connection remains.

- [ ] **Step 10: Preserve the Python wrapper's typed exception identity**

No wrapper method catches `OperationTimeoutError`; `query`, `execute`, `begin`, `commit`, `rollback`, and `close` continue to await the Rust method directly.

The existing broad exception suppression in `Transaction.__aexit__` is not changed here. Its remediation remains a separate defect candidate already identified by the audit.

- [ ] **Step 11: Add Rust state-machine unit coverage**

Extend the transaction unit module with deterministic tests:

```rust
#[test]
fn expired_lifetime_retires_only_current_epoch() { /* exact epoch assertion */ }

#[test]
fn rollback_deadline_is_independent_of_lifetime() { /* phase rollback */ }

#[test]
fn terminal_state_clears_lifetime_deadline() { /* every terminal state */ }
```

Factor the pure state transitions into private helpers so these tests do not require a live SQL socket.

- [ ] **Step 12: Prove TIME-007, TIME-008, and TIME-009 GREEN**

Run:

```bash
uv run maturin develop --release
uv run pytest \
  tests/sql_auth_strict/test_operation_timeouts.py \
  -k "TIME_007 or TIME_008 or TIME_009" -vv
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/test_transaction.py \
  tests/test_transaction_flags.py \
  tests/test_transaction_forwarded_methods.py \
  tests/test_context_manager_edge_cases.py \
  -q
cargo test --locked transaction -- --nocapture
```

Expected: lifetime expiry occurs before later SQL, earlier-deadline phase selection is exact, COMMIT timeout keeps the special top-level exception, rollback/close timeout remains visible, and all existing transaction contracts pass.

- [ ] **Step 13: Commit transaction deadline safety**

Run:

```bash
git add \
  src/connection.rs \
  src/transaction.rs \
  src/types.rs
git commit -m "feat: enforce transaction deadline safety"
```

---

### Task 9: Document the API and make TIME-010 GREEN

**Files:**
- Modify: `README.md`
- Modify: `tests/sql_auth_strict/framework_apps.py` only if implementation-facing constructor use requires a signature correction already represented in RED
- Modify: `tests/sql_auth_strict/test_framework_integration.py` only to correct a demonstrably invalid RED harness, never to weaken assertions

**Interfaces:**
- Consumes: complete Rust timeout behavior and RED framework routes.
- Produces: user-facing timeout guidance and verified FastAPI/Flask-through-ASGI recovery under 1,000 operations.

- [ ] **Step 1: Add one authoritative timeout section to README**

Place it after connection pooling and before transactions. Include:

```python
from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    TimeoutConfig,
)

database = Connection(
    connection_string,
    pool_config=PoolConfig(max_size=20),
    timeout_config=TimeoutConfig(
        connect_timeout_secs=5.0,
        acquire_timeout_secs=1.0,
        operation_timeout_secs=10.0,
        transaction_timeout_secs=30.0,
        rollback_timeout_secs=5.0,
    ),
)
```

Document:

```text
connect    Azure token + DNS/TCP + TLS/TDS login + routing reconnect
acquire    bb8 lease wait + checkout validation/reset
operation  full send and response consumption for one public call
transaction absolute lifetime beginning after successful BEGIN
rollback   explicit rollback and close cleanup
```

State that `None` omits only the named FastMssql deadline, that `acquire` cannot be `None`, and that external infrastructure timeouts may still apply.

- [ ] **Step 2: Document compatibility and no-retry behavior**

Include these exact semantics:

```text
Omitted TimeoutConfig:
  connect/acquire = PoolConfig.connection_timeout_secs or 30.0
  operation/transaction = None
  rollback = 30.0

Explicit TimeoutConfig.acquire_timeout_secs overrides the legacy
PoolConfig.connection_timeout_secs for the internal pool.
```

Explain that `OperationTimeoutError` subclasses `SqlConnectionError`, exposes structured metadata, and never implies that FastMssql retried SQL.

- [ ] **Step 3: Document COMMIT reconciliation**

Add a short example:

```python
try:
    await transaction.commit()
except CommitOutcomeUnknown as error:
    timeout = error.__cause__
    # Reconcile with an idempotency/business key. Do not blindly retry COMMIT.
    raise
```

State that a timed-out general write has `outcome_unknown=True` and must also be reconciled by business key.

- [ ] **Step 4: Run the isolated API/static docs contract**

Run:

```bash
uv run pytest --noconftest tests/test_timeout_config_contract.py -q
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected: every runtime/stub/export/README and hosted-command assertion passes.

- [ ] **Step 5: Run TIME-010 under real SQL-auth**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k "operation_timeout or timeout_recovery" -vv
```

Expected evidence:

```text
1,000 true-async operations
task concurrency 100
pool.max_size 20
successful, acquire-timeout, and operation-timeout outcomes all nonzero
event-loop ticker progresses
post-load SELECT succeeds
zero candidate sessions at teardown
plain Flask/WSGI compatibility recorded separately
```

- [ ] **Step 6: Commit docs and any strictly necessary harness correction separately**

If no RED harness correction was necessary:

```bash
git add README.md
git commit -m "docs: explain operation timeout policy"
```

If a RED harness bug was proven, commit its correction first with the failing evidence in the commit body, then commit README independently. Do not combine a test relaxation with feature code.

---

### Task 10: Self-review implementation and run every local production gate

**Files:**
- Review: every file changed from `test/operation-timeouts` to `feat/operation-timeouts`
- Verify: all source, test, workflow, docs, lock, and evidence inputs
- Artifact output: `.artifacts/sql-auth/` and a temporary wheel directory, both ignored

**Interfaces:**
- Consumes: complete feature branch.
- Produces: a reviewed exact technical feature SHA with zero local gate failures and zero leaked SQL sessions.

- [ ] **Step 1: Perform a fresh diff-based self-review before tests**

Run:

```bash
git diff --stat test/operation-timeouts...HEAD
git diff --check test/operation-timeouts...HEAD
git diff test/operation-timeouts...HEAD -- src python tests README.md
rg -n "except Exception|except BaseException|\\.ok\\(\\)|let _ =|unwrap\\(|expect\\(" \
  src python/fastmssql
```

Review every match and explicitly verify:

```text
all five phases have one stable spelling
all public operations pass the correct operation name
every timeout after request start retires the connection
no timeout path calls complete/mark-clean
no internal batch/chunk resets its deadline
transaction lifetime starts only after BEGIN response
rollback ignores expired transaction lifetime
COMMIT timeout is wrapped exactly once
no SQL retry loop was introduced
no new exception is swallowed
no secret-bearing value is formatted into an error
```

Fix each discovered issue inline, rerun the focused test that would detect it, and keep the correction in the relevant feature commit or a separate `fix:` commit if already committed.

- [ ] **Step 2: Run formatting, lint, compile, Rust, and security gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
cargo test --manifest-path vendor/tiberius/Cargo.toml
uv run ruff check python tests scripts
uv run python -m compileall -q python tests scripts
scripts/security/audit_dependencies.sh
```

Expected: every command exits 0, Cargo remains locked, and RustSec reports zero findings. If the vendored manifest has no independent lockfile, run it without `--locked` as shown and record that exact command.

- [ ] **Step 3: Run focused timeout contracts and all ten TIME IDs**

Run:

```bash
uv run pytest --noconftest tests/test_timeout_config_contract.py -q
uv run pytest tests/test_pyo3_build_contract.py -q
uv run pytest tests/sql_auth_strict/test_operation_timeouts.py -vv
uv run pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k "operation_timeout or timeout_recovery" -vv
```

Expected: all focused tests pass; strict result artifacts contain `TIME-001` through `TIME-010` with PASS.

- [ ] **Step 4: Run the complete SQL-auth orchestrator**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected lane results at the feature SHA:

```text
uv sync: PASS
maturin develop: PASS
cargo fmt: PASS
cargo clippy: PASS
cargo test: PASS
Docker compose/provision: PASS
strict functional including TIME-001..009: PASS
async: PASS
framework including TIME-010: PASS
resilience: PASS
load: PASS
applicable upstream: PASS
295/295 unique SQL-auth IDs: PASS
report generation with --require-complete: PASS
```

The precise pytest totals are recorded from JUnit rather than predicted. They must be at least the preserved baseline plus the newly collected nodes, with no missing prior node.

- [ ] **Step 5: Re-run the opt-in 99,999-transaction bounded stress evidence**

Run:

```bash
env FASTMSSQL_TRANSACTION_STRESS_PROFILES=99_999:100 \
  scripts/sql_auth/run_transaction_stress.sh
```

Expected: 99,999 completed transactions at bounded concurrency 100, exact committed/rolled-back state, event-loop responsiveness, post-load recovery, and zero candidate sessions. Record elapsed time and throughput as environment-specific evidence, not a universal performance guarantee.

- [ ] **Step 6: Build and test a clean ABI3 wheel**

Run:

```bash
uv run maturin build --release --locked --out .artifacts/operation-timeout-wheel
uv venv --python 3.13 .artifacts/operation-timeout-wheel-venv
uv pip install \
  --python .artifacts/operation-timeout-wheel-venv/bin/python \
  pytest==9.1.1 \
  .artifacts/operation-timeout-wheel/*.whl
.artifacts/operation-timeout-wheel-venv/bin/python \
  -m pytest --noconftest \
  tests/test_pool_config_default_contract.py \
  tests/test_timeout_config_contract.py -q
```

On Windows hosted runners the workflow chooses `Scripts/python.exe`; this local macOS command intentionally uses `bin/python`.

- [ ] **Step 7: Verify no candidate server work remains**

Query through the SQL-auth administrator fixture or `sqlcmd`:

```sql
SELECT COUNT_BIG(*) AS candidate_sessions
FROM sys.dm_exec_sessions
WHERE program_name LIKE N'fastmssql_timeout_%';

SELECT COUNT_BIG(*) AS candidate_requests
FROM sys.dm_exec_requests AS request
JOIN sys.dm_exec_sessions AS session
  ON session.session_id = request.session_id
WHERE session.program_name LIKE N'fastmssql_timeout_%';
```

Expected: both values are zero after every connection/proxy/lifespan is closed.

- [ ] **Step 8: Scan tracked changes and generated reports for secrets**

Run:

```bash
git status --short
git diff --check
git grep -n -I -E \
  "(Password=|Pwd=|MSSQL_SA_PASSWORD|OWNER_PASSWORD|BEGIN PRIVATE KEY|access[_-]?token)"
```

Review each configuration-name match; no credential value may appear. Confirm `.env.sql-auth.local` and `.artifacts/sql-auth/` remain ignored with `git check-ignore`.

- [ ] **Step 9: Record and push the exact feature SHA only to the fork**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git push -u origin feat/operation-timeouts
git rev-list --left-right --count HEAD...origin/feat/operation-timeouts
git config --get remote.upstream.pushurl
```

Expected: clean tree, feature parity `0	0`, upstream push URL `DISABLED`.

---

### Task 11: Merge the exact technical candidate and verify hosted gates

**Files:**
- Merge target: `test/sql-auth-validation`
- Hosted workflows: `.github/workflows/rust-unit-tests.yml`, `.github/workflows/dependency-security.yml`
- Detached verification worktree: `.worktrees/verify-operation-timeouts`

**Interfaces:**
- Consumes: locally verified exact feature SHA.
- Produces: one technical cumulative merge SHA with local detached verification and green Linux/macOS/Windows plus RustSec evidence.

- [ ] **Step 1: Merge the feature branch without modifying its tested tree**

Run from `/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql`:

```bash
git status --short --branch
git merge --no-ff feat/operation-timeouts -m "merge: add operation timeout safety"
git rev-parse HEAD
git diff --stat feat/operation-timeouts..HEAD
```

Expected: the diff from feature tree to merge tree is empty; only merge ancestry changed.

- [ ] **Step 2: Create detached verification at the exact technical merge**

Run:

```bash
git worktree add --detach .worktrees/verify-operation-timeouts HEAD
git -C .worktrees/verify-operation-timeouts rev-parse HEAD
git -C .worktrees/verify-operation-timeouts diff --check
```

Expected: detached SHA exactly equals the technical merge.

- [ ] **Step 3: Re-run high-signal gates in the detached worktree**

Run from `.worktrees/verify-operation-timeouts`:

```bash
cargo test --locked
uv run pytest --noconftest tests/test_timeout_config_contract.py -q
uv run pytest tests/sql_auth_strict/test_operation_timeouts.py -vv
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: results match feature-branch evidence at the exact merge tree.

- [ ] **Step 4: Push only the cumulative fork branch**

Run:

```bash
git push origin test/sql-auth-validation
git rev-list --left-right --count HEAD...origin/test/sql-auth-validation
```

Expected: parity `0	0`. This push triggers hosted branch gates on the fork.

- [ ] **Step 5: Inspect hosted cross-platform and dependency-security runs**

Run:

```bash
gh run list \
  --repo galeamarcel/FastMssql \
  --branch test/sql-auth-validation \
  --limit 10
```

Identify the runs whose `headSha` equals the technical merge SHA. Inspect each:

```bash
gh run view RUN_ID --repo galeamarcel/FastMssql
gh run view RUN_ID --repo galeamarcel/FastMssql --json headSha,status,conclusion,jobs,url
```

Expected:

```text
Rust unit tests: success
  Ubuntu raw Cargo/Rust/wheel timeout contract: success
  macOS raw Cargo/Rust/wheel timeout contract: success
  Windows raw Cargo/Rust/wheel timeout contract: success
Dependency security: success
  cargo audit --deny warnings: success
headSha: exact technical merge SHA
```

Do not treat a run from an earlier SHA as evidence.

- [ ] **Step 6: If a hosted-only defect appears, preserve hosted RED before fixing**

Create a new commit on `test/operation-timeouts` that reproduces the hosted failure locally or as an isolated static/wheel contract. Merge that RED into `feat/operation-timeouts`, fix it in a subsequent feature commit, rerun local gates, and repeat the exact technical merge/hosted inspection.

Never amend or hide the failed hosted run. Record both failed and successful run IDs in the status evidence.

- [ ] **Step 7: Prove no upstream PR or push exists**

Run:

```bash
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/operation-timeouts \
  --json number,title,url,state
git config --get remote.upstream.pushurl
```

Expected: `[]` and `DISABLED`.

---

### Task 12: Generate evidence, update the live audit, and close only this candidate

**Files:**
- Create branch/worktree: `docs/operation-timeouts-status`, `.worktrees/docs-operation-timeouts-status`
- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify: `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**
- Consumes: exact technical merge SHA, local artifact files, hosted run/job URLs, and no-upstream-PR proof.
- Produces: generated 295-case evidence, a live-audit status that does not overclaim lifecycle/observability, and a final documentation-only cumulative merge.

- [ ] **Step 1: Create the status branch from the exact technical merge**

Run:

```bash
git worktree add \
  .worktrees/docs-operation-timeouts-status \
  -b docs/operation-timeouts-status \
  test/sql-auth-validation
git -C .worktrees/docs-operation-timeouts-status rev-parse HEAD
```

Expected: branch base equals the hosted/local verified technical merge.

- [ ] **Step 2: Generate reports from copied exact-SHA artifacts**

Copy only non-secret `.artifacts/sql-auth` result/JUnit/command/exit-code/metrics files from the verified feature worktree into the status worktree's ignored artifact directory:

```bash
mkdir -p .artifacts/sql-auth
cp -R \
  ../feat-operation-timeouts/.artifacts/sql-auth/. \
  .artifacts/sql-auth/
```

Review the copied file list and redaction before report generation. Then run:

```bash
uv run python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results .artifacts/sql-auth/strict-results.json \
  --artifact-dir .artifacts/sql-auth \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete
```

Expected:

```text
295 matrix rows
295 PASS
0 NOT RUN
0 SKIPPED
0 FAIL
0 ERROR
TIME-001 through TIME-010 each point to one exact pytest node
report git SHA equals the technical merge base used for artifacts
```

- [ ] **Step 3: Update only the operation-timeout portion of the live audit**

In `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`:

1. split the former combined `feat/timeouts-lifecycle-observability` item into separately visible timeout, lifecycle, and observability candidates;
2. mark operation timeout/deadline safety `VERIFIED_FORK`;
3. list design, plan, RED, feature, technical merge, evidence, and status SHAs;
4. list exact local totals and the 99,999 stress result;
5. list hosted run/job URLs and exact head SHA;
6. state `origin` fork and `upstream push URL DISABLED`;
7. preserve `Open | Closing | Closed`, metrics/hooks, TDS `ATTENTION`, and context-manager exception aggregation as open work;
8. record any measured residual timing/platform limitation honestly.

Do not call the whole library enterprise production-ready from this one candidate.

- [ ] **Step 4: Update the future upstream roadmap without publishing**

In `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`, add the operation-timeout candidate with:

```text
fork branch and exact feature SHA
ten TIME case IDs
public API surface
compatibility guarantee
no-retry and COMMIT precedence
local and hosted evidence
known non-goals/residual risks
requirement for a fresh branch from then-current upstream/master
requirement for a fresh RED reproduction
requirement for Marcel Galea's separate explicit upstream-publication approval
current upstream PR state: none
```

- [ ] **Step 5: Self-review generated evidence and status claims**

Run:

```bash
git diff --check
git diff -- docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git diff -- docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
git diff -- docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
rg -n "TIME-00[1-9]|TIME-010|VERIFIED_FORK|Open \\| Closing \\| Closed|DISABLED" docs
```

Verify:

```text
every count comes from an artifact, not an estimate
every hosted claim has a URL and exact SHA
failed intermediate runs remain visible
no lifecycle or observability claim was closed
no credential value appears
no upstream publication is implied
```

- [ ] **Step 6: Commit and push status documentation only to the fork**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record operation timeout verification"
git push -u origin docs/operation-timeouts-status
git rev-list --left-right --count \
  HEAD...origin/docs/operation-timeouts-status
```

Expected: status parity `0	0`.

- [ ] **Step 7: Merge status docs into the cumulative fork and verify tree identity**

Run from `/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql`:

```bash
git merge --no-ff \
  docs/operation-timeouts-status \
  -m "merge: record operation timeout verification"
git diff --check
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Expected: final cumulative parity `0	0`; this merge changes only documentation relative to the technical merge.

- [ ] **Step 8: Confirm final hosted docs-only merge and fork isolation**

Inspect hosted runs at the final cumulative SHA exactly as in Task 11. Require all three operating-system jobs and dependency security to pass again because the workflow/test registry files are part of the saved tree.

Run the final upstream isolation proof:

```bash
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/operation-timeouts \
  --json number,title,url,state
git config --get remote.upstream.pushurl
```

Expected: `[]`, `DISABLED`.

---

## Plan Self-Review Checklist

- [x] Every requirement in the approved design maps to at least one task and one verification command.
- [x] `TIME-001` through `TIME-010` occur exactly once in strict test source and once in the canonical registry.
- [x] Runtime, compiled stub, wrapper stub, package export, README, and installed wheel expose the same defaults and types.
- [x] Physical connect and acquisition remain distinguishable.
- [x] Operation timeout begins after acquisition and covers full response consumption.
- [x] Batch/bulk deadlines are absolute across all internal work.
- [x] Transaction lifetime starts after successful `BEGIN`, remains absolute, and never blocks rollback cleanup.
- [x] COMMIT timeout retains `CommitOutcomeUnknown` as top-level with typed cause.
- [x] Every uncertain/incomplete wire response retires the direct socket or pooled lease.
- [x] No new retry, lifecycle state, telemetry surface, TDS `ATTENTION`, per-call override, version bump, or wrapper exception aggregation entered scope.
- [x] Local gates include Docker SQL-auth, all existing strict/upstream lanes, 99,999 bounded transactions, Rust/Tiberius, clean ABI3 wheel, lint, compile, and dependency audit.
- [x] Hosted gates cover Ubuntu, macOS, and Windows at exact SHAs.
- [x] Design, RED, GREEN, status, and cumulative histories remain separately reviewable.
- [x] Every push and branch is on `galeamarcel/FastMssql`; upstream remains fetch-only and has no PR.
- [x] Status language closes only operation timeout safety and leaves lifecycle/observability visible.

Author self-review result: PASS. The review corrected the wrapper-stub boundary, replaced raw operation strings with one Rust enum, made timeout metadata construction non-swallowing, preserved framework helper compatibility, and fixed the exact stress-harness invocation.

## Inline Execution Handoff

The user has already selected inline execution and pre-approved design/spec/plan artifacts plus implementation. Execute this plan in the current session with `superpowers:executing-plans`, retaining self-review and verification checkpoints but without pausing for another artifact approval.
