# FastMssql PoolConfig Default Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `PoolConfig()` and `Connection(..., pool_config=None)` expose
the same canonical `15/3/1800/300/30` pool profile without changing explicit
arguments, presets or existing `None` translation.

**Architecture:** Define one private, typed Rust default profile and route
`PyPoolConfig::default()` plus omitted PyO3 constructor arguments through it.
Keep a deliberate manual PyO3 text signature because optional Rust expressions
otherwise render as ellipses, then enforce runtime, introspection, stub, README
and real SQL Server behavior with deterministic RED/GREEN contracts. Validate
the rebuilt extension locally and on Linux, macOS and Windows before updating
the live production-readiness audit.

**Tech Stack:** Rust 1.94.0, Cargo, PyO3 0.29.0, bb8 0.9.1, maturin 1.14.1,
Python 3.13, pytest 9.1.1, Ruff, uv, GitHub Actions and SQL Server 2022 in the
dedicated `fastmssql-sql-auth-dev` Docker container.

## Global Constraints

- All branches, commits and pushes target
  `https://github.com/galeamarcel/FastMssql.git`.
- The implementation baseline is `test/sql-auth-validation` at `f35176c`.
- The approved design commit is `9e59249`.
- The approved design is
  `docs/superpowers/specs/2026-07-25-fastmssql-pool-config-default-consistency-design.md`.
- The `upstream` remote remains fetch-only with push URL `DISABLED`.
- No upstream branch, push, pull request, package publication or dependency
  fork is authorized.
- All commits use `Marcel Galea <galea.marcel@gmail.com>`.
- Keep design, RED tests, technical fix and live-status documentation on
  separate branches.
- Do not dispatch subagents unless the user explicitly chooses the
  Subagent-Driven execution option.
- The canonical profile is exactly
  `15/3/1800/300/30/None/None` for max size, minimum idle, maximum lifetime,
  idle timeout, acquisition timeout, checkout validation and retry.
- `Connection(..., pool_config=None)` retains its current runtime profile.
- Each explicitly provided argument retains its current value and validation.
- Explicit `None` continues to leave the corresponding FastMssql bb8 builder
  override unset; this candidate does not redefine `None`.
- `PoolConfig.one()`, `low_resource()`, `development()`,
  `high_throughput()`, `performance()` and `adaptive()` do not change.
- `connection_timeout_secs=None` remains accepted; operation-timeout API
  semantics remain a separate candidate.
- `PoolConfig.__text_signature__` and `inspect.signature(PoolConfig)` contain
  concrete defaults and no `...`/`Ellipsis`.
- No new runtime exception, fallback or swallowed failure is introduced.
- The strict SQL-auth registry remains exactly `285` unique IDs; `POOL-001`
  is strengthened rather than adding a case ID.
- Docker operations target only `fastmssql-sql-auth-dev`.
- SQL-authentication secrets remain only in ignored `.env.sql-auth.local`.
- The Linux/macOS/Windows hosted gate builds the raw Rust crate, executes Rust
  tests, builds the Python extension and runs the PoolConfig metadata contract.
- No version bump or release publication occurs in this candidate.
- The live audit is marked remediated only after the exact technical
  cumulative SHA is locally and hosted green.

---

## File Map

- `src/pool_config.rs`: owns the typed canonical constants, private default
  helper, PyO3 runtime/default signature and focused Rust unit contract.
- `tests/test_pool_config.py`: owns public constructor, explicit legacy-profile
  and representation behavior.
- `tests/test_pool_config_validation.py`: owns the second upstream-facing
  constructor-default contract.
- `tests/test_pool_config_default_contract.py`: owns runtime introspection,
  `.pyi` AST and README consistency.
- `tests/test_pyo3_build_contract.py`: owns the required three-OS extension
  contract in the hosted workflow.
- `tests/sql_auth_strict/test_pool.py`: owns real MSSQL-auth equivalence,
  saturation, parameterized-query and session-cleanup evidence under
  `POOL-001`.
- `.github/workflows/rust-unit-tests.yml`: owns the Linux/macOS/Windows raw
  Cargo and rebuilt-extension gate.
- `python/fastmssql/fastmssql.pyi`: owns public constructor types, defaults and
  accurate `None` documentation.
- `README.md`: owns the public default profile and migration example.
- `docs/SQL_AUTH_TEST_MATRIX.md`: receives regenerated exact case evidence.
- `docs/SQL_AUTH_TEST_REPORT.md`: receives regenerated exact lane counts and
  commands.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: remains the authoritative
  live enterprise status document.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`: records
  this as future PR-21 without publishing it.
- `docs/superpowers/specs/2026-07-25-fastmssql-pool-config-default-consistency-design.md`:
  remains the behavioral source of truth.

## Branch Graph

```text
test/sql-auth-validation at f35176c
  |
  +-- docs/pool-config-default-consistency-design
  |     9e59249 approved design
  |     this implementation-plan commit
  |
  +-- test/pool-config-default-consistency
  |     pure/static/Rust RED contract
  |     real SQL-auth RED contract and hosted test gate
  |
  +-- fix/pool-config-default-consistency
  |     canonical Rust profile, PyO3 signature, stub and README
  |
  +-- test/sql-auth-validation
  |     exact technical merge validated locally and hosted
  |
  +-- docs/pool-config-default-consistency-status
        generated matrix/report, live audit and PR-21 candidate record
```

The design branch is merged into the cumulative fork before the RED branch is
created. The fix branch starts from both committed RED test commits. The
status branch starts from the exact locally and hosted-green technical
cumulative commit.

## Authoritative Baselines

Fresh collection at `f35176c` establishes:

```text
focused pure PoolConfig              89 selected, 16 integration deselected
focused PoolConfig integration       16 selected
PyO3 build contract                   6
Rust unit tests                       13
strict functional                    295
async strict                          16
framework                             28
resilience                             6
load                                   9
SQL-auth specification IDs           285
applicable upstream regression       902
```

This candidate adds three tests in
`tests/test_pool_config_default_contract.py` and one hosted-gate test in
`tests/test_pyo3_build_contract.py`. No other Python test function is added or
removed. Expected final counts are therefore:

```text
focused pure PoolConfig              92
focused PoolConfig integration       16
PyO3 build contract                   7
Rust unit tests                      14
strict functional                   295
async strict                         16
framework                            28
resilience                            6
load                                  9
SQL-auth specification IDs          285
applicable upstream regression      906
```

Any different count is investigated and recorded; it is never silently
rounded to a percentage.

---

### Task 1: Integrate the approved design and create the RED worktree

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-25-fastmssql-pool-config-default-consistency-design.md`
- Existing:
  `docs/superpowers/plans/2026-07-25-fastmssql-pool-config-default-consistency.md`

**Interfaces:**

- Consumes: approved design commit `9e59249` and this committed plan.
- Produces: a cumulative documentation baseline plus isolated
  `test/pool-config-default-consistency` worktree.

- [ ] **Step 1: Verify the design branch and fork boundary**

Run in `.worktrees/docs-pool-config-default-consistency-design`:

```bash
set -euo pipefail
git status --short --branch
git log -2 --format='%h %an <%ae> %s'
git remote -v
git rev-list --left-right --count \
  HEAD...origin/docs/pool-config-default-consistency-design
git diff --check
```

Expected:

```text
branch             docs/pool-config-default-consistency-design
tracked changes    none
origin parity      0 0
origin push        galeamarcel/FastMssql
upstream push      DISABLED
author             Marcel Galea <galea.marcel@gmail.com>
```

- [ ] **Step 2: Merge only design artifacts into the cumulative fork**

Run in the main `FastMssql` worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff docs/pool-config-default-consistency-design \
  -m "merge: plan canonical PoolConfig defaults"
git diff HEAD^ --name-only
git push origin test/sql-auth-validation
```

Expected: the merge changes only the approved specification and plan. It does
not modify Rust, Python, tests, workflows, stubs or README.

- [ ] **Step 3: Create the isolated RED branch/worktree**

Run in the main `FastMssql` worktree:

```bash
git worktree add \
  .worktrees/test-pool-config-default-consistency \
  -b test/pool-config-default-consistency \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-pool-config-default-consistency/.env.sql-auth.local
```

Expected: the new branch is clean and the ignored environment symlink resolves
to the existing local SQL-auth configuration.

- [ ] **Step 4: Build the exact unchanged baseline in the RED worktree**

Run in `.worktrees/test-pool-config-default-consistency`:

```bash
set -euo pipefail
uv sync --locked --all-extras --dev
uv run maturin develop --release
cargo build --locked
cargo test --locked
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  -m "not integration" -q
```

Expected:

```text
cargo build        PASS
cargo test         13/13 PASS
PoolConfig pure    89 PASS, 16 deselected
```

- [ ] **Step 5: Reproduce the public divergence without a network**

Run:

```bash
uv run python -c \
  "import inspect; from fastmssql import PoolConfig; c=PoolConfig(); print((c.max_size,c.min_idle,c.max_lifetime_secs,c.idle_timeout_secs,c.connection_timeout_secs,c.test_on_check_out,c.retry_connection)); print(PoolConfig.__text_signature__); print(inspect.signature(PoolConfig))"
```

Expected unchanged evidence:

```text
(20, 2, None, None, 30, None, None)
(max_size=20, min_idle=..., max_lifetime_secs=None, idle_timeout_secs=None, connection_timeout_secs=..., test_on_check_out=None, retry_connection=None)
(max_size=20, min_idle=Ellipsis, max_lifetime_secs=None, idle_timeout_secs=None, connection_timeout_secs=Ellipsis, test_on_check_out=None, retry_connection=None)
```

- [ ] **Step 6: Verify the existing real SQL-auth baseline**

Run with the already approved localhost/Docker permission:

```bash
set -euo pipefail
set -a
source .env.sql-auth.local
set +a
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
export FASTMSSQL_TEST_CONNECTION_STRING="Server=${FASTMSSQL_SQL_AUTH_HOST},${FASTMSSQL_SQL_AUTH_PORT};Database=fastmssql_upstream_regression;User Id=${FASTMSSQL_SQL_AUTH_OWNER_USER};Password=${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD};Encrypt=True;TrustServerCertificate=True"
export FAST_MSSQL_TEST_DB_USER="${FASTMSSQL_SQL_AUTH_OWNER_USER}"
export FAST_MSSQL_TEST_DB_PASSWORD="${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}"
export FAST_MSSQL_TEST_SERVER="${FASTMSSQL_SQL_AUTH_HOST}"
export FAST_MSSQL_TEST_PORT="${FASTMSSQL_SQL_AUTH_PORT}"
export FAST_MSSQL_TEST_DATABASE="fastmssql_upstream_regression"
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  -m integration -q
```

Expected: `16 passed, 89 deselected`. A sandbox
`PermissionError: Operation not permitted` on `127.0.0.1:14334` is rerun
outside the network sandbox; it is not classified as a driver failure.

---

### Task 2: Commit the deterministic pure/static/Rust RED contract

**Files:**

- Modify: `tests/test_pool_config.py:13-49`
- Modify: `tests/test_pool_config_validation.py:40-47`
- Create: `tests/test_pool_config_default_contract.py`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `src/pool_config.rs:303-315`

**Interfaces:**

- Consumes: current imported `fastmssql.PoolConfig`, Python `ast` and
  `inspect`, the public stub and README.
- Produces: exact canonical contract constants for tests, three new
  upstream tests, one new hosted-workflow contract and one Rust unit test.

- [ ] **Step 1: Update the existing constructor and explicit-override tests**

Replace `TestPoolConfigConstructor.test_default_constructor` in
`tests/test_pool_config.py` with:

```python
def test_default_constructor(self):
    """PoolConfig() uses the canonical managed profile."""
    config = PoolConfig()
    assert (
        config.max_size,
        config.min_idle,
        config.max_lifetime_secs,
        config.idle_timeout_secs,
        config.connection_timeout_secs,
        config.test_on_check_out,
        config.retry_connection,
    ) == (15, 3, 1800, 300, 30, None, None)
    rendered = repr(config)
    for field in (
        "max_size=15",
        "min_idle=Some(3)",
        "max_lifetime_secs=Some(1800)",
        "idle_timeout_secs=Some(300)",
        "connection_timeout_secs=Some(30)",
        "test_on_check_out=None",
        "retry_connection=None",
    ):
        assert field in rendered
```

Replace `TestPoolConfigConstructor.test_none_values` with the explicit
historical-profile recovery contract:

```python
def test_none_values(self):
    """The historical direct profile remains available explicitly."""
    config = PoolConfig(
        max_size=20,
        min_idle=2,
        max_lifetime_secs=None,
        idle_timeout_secs=None,
        connection_timeout_secs=30,
    )
    assert (
        config.max_size,
        config.min_idle,
        config.max_lifetime_secs,
        config.idle_timeout_secs,
        config.connection_timeout_secs,
    ) == (20, 2, None, None, 30)
```

Existing getter/setter tests continue to prove `min_idle=None` and
`connection_timeout_secs=None`; do not delete or weaken them.

- [ ] **Step 2: Update the second focused constructor contract**

Replace `test_pool_config_default_values` in
`tests/test_pool_config_validation.py` with:

```python
def test_pool_config_default_values():
    """PoolConfig exposes the same canonical defaults as Connection."""
    config = PoolConfig()
    assert (
        config.max_size,
        config.min_idle,
        config.max_lifetime_secs,
        config.idle_timeout_secs,
        config.connection_timeout_secs,
        config.test_on_check_out,
        config.retry_connection,
    ) == (15, 3, 1800, 300, 30, None, None)
```

This removes the stale comment that mentions an older `10/1` profile.

- [ ] **Step 3: Add the complete runtime/stub/README contract**

Create `tests/test_pool_config_default_contract.py` with:

```python
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fastmssql import PoolConfig


ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "python" / "fastmssql" / "fastmssql.pyi"
README = ROOT / "README.md"
CANONICAL_DEFAULTS = {
    "max_size": 15,
    "min_idle": 3,
    "max_lifetime_secs": 1800,
    "idle_timeout_secs": 300,
    "connection_timeout_secs": 30,
    "test_on_check_out": None,
    "retry_connection": None,
}
CANONICAL_TEXT_SIGNATURE = (
    "(max_size=15, min_idle=3, max_lifetime_secs=1800, "
    "idle_timeout_secs=300, connection_timeout_secs=30, "
    "test_on_check_out=None, retry_connection=None)"
)


def _pool_config_stub_init() -> ast.FunctionDef:
    tree = ast.parse(STUB.read_text(encoding="utf-8"), filename=str(STUB))
    pool_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PoolConfig"
    )
    return next(
        node
        for node in pool_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )


def test_runtime_constructor_signature_matches_canonical_profile() -> None:
    signature = inspect.signature(PoolConfig)
    assert PoolConfig.__text_signature__ == CANONICAL_TEXT_SIGNATURE
    assert tuple(signature.parameters) == tuple(CANONICAL_DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == CANONICAL_DEFAULTS
    assert all(
        parameter.default is not Ellipsis
        for parameter in signature.parameters.values()
    )
    config = PoolConfig()
    assert {
        name: getattr(config, name)
        for name in CANONICAL_DEFAULTS
    } == CANONICAL_DEFAULTS


def test_pool_config_stub_matches_runtime_defaults_and_optional_types() -> None:
    init = _pool_config_stub_init()
    parameters = init.args.args[1:]
    assert tuple(parameter.arg for parameter in parameters) == tuple(
        CANONICAL_DEFAULTS
    )
    assert len(parameters) == len(init.args.defaults)
    assert {
        parameter.arg: ast.literal_eval(default)
        for parameter, default in zip(
            parameters, init.args.defaults, strict=True
        )
    } == CANONICAL_DEFAULTS
    annotations = {
        parameter.arg: ast.unparse(parameter.annotation)
        for parameter in parameters
    }
    assert annotations == {
        "max_size": "int",
        "min_idle": "Optional[int]",
        "max_lifetime_secs": "Optional[int]",
        "idle_timeout_secs": "Optional[int]",
        "connection_timeout_secs": "Optional[int]",
        "test_on_check_out": "Optional[bool]",
        "retry_connection": "Optional[bool]",
    }
    stub = STUB.read_text(encoding="utf-8")
    assert "default: None = unlimited" not in stub
    assert "default: None = no timeout" not in stub


def test_readme_states_one_complete_default_profile() -> None:
    readme = README.read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())
    assert (
        "smart defaults (default max_size=15, min_idle=3)"
        in readme
    )
    assert (
        "Default pool (if omitted or constructed with `PoolConfig()`): "
        "`max_size=15`, `min_idle=3`, `max_lifetime_secs=1800`, "
        "`idle_timeout_secs=300`, `connection_timeout_secs=30`."
        in normalized_readme
    )
    assert (
        "smart defaults (default max_size=20, min_idle=2)"
        not in readme
    )
```

- [ ] **Step 4: Add the hosted-extension workflow contract**

Append this test to `tests/test_pyo3_build_contract.py`:

```python
def test_hosted_gate_builds_extension_and_checks_pool_defaults() -> None:
    workflow = _read_required(WORKFLOW)

    assert "uvx --from maturin==1.14.1 maturin build" in workflow
    assert "uv pip install" in workflow
    assert '--python "${contract_python}"' in workflow
    assert (
        "-m pytest tests/test_pool_config_default_contract.py -q"
        in workflow
    )
    assert workflow.index("cargo test --locked") < workflow.index(
        "uvx --from maturin==1.14.1 maturin build"
    )
    assert workflow.index("uv pip install") < workflow.index(
        "tests/test_pool_config_default_contract.py"
    )
```

- [ ] **Step 5: Add the Rust default-profile unit contract**

Append to `src/pool_config.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_pool_profile_is_canonical() {
        let config = PyPoolConfig::default();

        assert_eq!(config.max_size, 15);
        assert_eq!(config.min_idle, Some(3));
        assert_eq!(
            config.max_lifetime,
            Some(std::time::Duration::from_secs(1800))
        );
        assert_eq!(
            config.idle_timeout,
            Some(std::time::Duration::from_secs(300))
        );
        assert_eq!(
            config.connection_timeout,
            Some(std::time::Duration::from_secs(30))
        );
        assert_eq!(config.test_on_check_out, None);
        assert_eq!(config.retry_connection, None);
    }
}
```

Do not add constants or alter production behavior on the RED branch.

- [ ] **Step 6: Run the pure/static contracts and confirm exact RED**

Run:

```bash
cargo fmt --check
cargo test --locked
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  -m "not integration" -q
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
Rust                            14/14 PASS
PoolConfig pure                 87 PASS, 5 FAIL, 16 deselected
failing existing contracts      direct constructor still 20/2/None/None/30
failing new runtime contract    signature/default mismatch
failing new stub contract       lifetime/idle defaults mismatch
failing new README contract     contradictory 20/2 versus incomplete 15/3
PyO3 contract                   6 PASS, 1 FAIL
hosted-workflow failure         extension/default step absent
```

If a failure is caused by import/build state, a missing file or a skipped test,
rebuild the unchanged extension and rerun; do not count it as RED evidence.

- [ ] **Step 7: Commit only the deterministic RED contract**

Run:

```bash
git diff --check
git diff -- src/pool_config.rs
git add \
  src/pool_config.rs \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  tests/test_pyo3_build_contract.py
git commit -m "test: define canonical PoolConfig default contract"
```

Review `src/pool_config.rs` before the commit: its only delta must be under
`#[cfg(test)]`.

---

### Task 3: Commit the real SQL-auth RED reproduction and hosted test lane

**Files:**

- Modify: `tests/sql_auth_strict/test_pool.py:26-173`
- Modify: `.github/workflows/rust-unit-tests.yml`

**Interfaces:**

- Consumes: `SqlAuthConfig`, the existing `_connection`,
  `_assert_pool_invariants`, `_wait_for_active`, `_application_sessions`,
  `scalar`, the `sa_connection` fixture and unique application-name fixture.
- Produces: one strengthened `POOL-001` test with the same matrix ID and a
  three-OS extension contract.

- [ ] **Step 1: Add an implicit-default connection helper**

Add after `_connection` in `tests/sql_auth_strict/test_pool.py`:

```python
def _default_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
    )
```

The helper intentionally omits the `pool_config` keyword; it does not pass a
constructed profile or duplicate default values.

- [ ] **Step 2: Add bounded application-session cleanup**

Add after `_application_sessions`:

```python
async def _wait_for_application_sessions_absent(
    sa_connection: Connection,
    application_name: str,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    remaining: set[int] = set()
    while time.monotonic() < deadline:
        remaining = await _application_sessions(
            sa_connection, application_name
        )
        if not remaining:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"application sessions remained for {application_name}: "
        f"{sorted(remaining)}"
    )
```

No broad `except`, skipped assertion or unbounded sleep is allowed.

- [ ] **Step 3: Replace `POOL-001` with the real equivalence/saturation test**

Replace `test_default_pool_config_matches_runtime` with:

```python
@case("POOL-001")
@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_default_pool_config_matches_runtime(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    explicit_config = PoolConfig()
    canonical = (15, 3, 1800, 300, 30, None, None)
    observed: dict[str, dict[str, object]] = {}

    for mode in ("explicit", "implicit"):
        application_name = unique_sql_name(
            f"fastmssql_pool_defaults_{mode}"
        )
        assert (
            await _application_sessions(sa_connection, application_name)
            == set()
        )
        connection = (
            _connection(
                sql_auth_config,
                explicit_config,
                application_name=application_name,
            )
            if mode == "explicit"
            else _default_connection(
                sql_auth_config,
                application_name=application_name,
            )
        )
        tasks: list[asyncio.Task[int]] = []
        disconnected = False
        try:
            assert await connection.connect() is True
            initial = await connection.pool_stats()
            _assert_pool_invariants(initial)
            tasks = [
                asyncio.create_task(
                    scalar(
                        connection,
                        "WAITFOR DELAY '00:00:00.750'; SELECT @P1",
                        [value],
                    )
                )
                for value in range(30)
            ]
            try:
                saturated = await _wait_for_active(
                    connection,
                    int(initial["max_size"]),
                    timeout=4.0,
                )
                sessions = await _application_sessions(
                    sa_connection, application_name
                )
            finally:
                values = await asyncio.gather(*tasks)
            _assert_pool_invariants(saturated)
            observed[mode] = {
                "max_size": initial["max_size"],
                "min_idle": initial["min_idle"],
                "warm_connections": initial["connections"],
                "active_connections": saturated["active_connections"],
                "connections": saturated["connections"],
                "server_sessions": len(sessions),
                "values": values,
            }
        finally:
            disconnected = await connection.disconnect()
            await _wait_for_application_sessions_absent(
                sa_connection, application_name
            )
        assert disconnected is True

    assert (
        explicit_config.max_size,
        explicit_config.min_idle,
        explicit_config.max_lifetime_secs,
        explicit_config.idle_timeout_secs,
        explicit_config.connection_timeout_secs,
        explicit_config.test_on_check_out,
        explicit_config.retry_connection,
    ) == canonical
    for result in observed.values():
        assert result["max_size"] == 15
        assert result["min_idle"] == 3
        assert 3 <= int(result["warm_connections"]) <= 15
        assert result["active_connections"] == 15
        assert result["connections"] == 15
        assert result["server_sessions"] == 15
        assert result["values"] == list(range(30))
    for stable_key in (
        "max_size",
        "min_idle",
        "active_connections",
        "connections",
        "server_sessions",
        "values",
    ):
        assert (
            observed["explicit"][stable_key]
            == observed["implicit"][stable_key]
        )
```

The 30 tasks deliberately exceed both historical pool ceilings. The test
waits for each runtime-reported ceiling before checking the server, so the RED
branch reaches real MSSQL behavior: explicit construction records 20 sessions
and implicit construction records 15 before the canonical assertions fail.

- [ ] **Step 4: Extend the hosted workflow after raw Cargo tests**

Append these platform-neutral wheel steps after `Run Rust unit tests` in
`.github/workflows/rust-unit-tests.yml`:

```yaml
      - name: Build Python extension wheel
        shell: bash
        run: |
          mkdir -p .artifacts/pool-contract-wheel
          uvx --from maturin==1.14.1 maturin build \
            --release \
            --locked \
            --out .artifacts/pool-contract-wheel

      - name: Install extension contract environment
        shell: bash
        run: |
          uv venv --python "${PYTHON_VERSION}" .pool-contract-venv
          if [[ "${RUNNER_OS}" == "Windows" ]]; then
            contract_python=".pool-contract-venv/Scripts/python.exe"
          else
            contract_python=".pool-contract-venv/bin/python"
          fi
          uv pip install \
            --python "${contract_python}" \
            pytest==9.1.1 \
            .artifacts/pool-contract-wheel/*.whl
          echo "POOL_CONTRACT_PYTHON=${contract_python}" >> "${GITHUB_ENV}"

      - name: Verify PoolConfig Python contract
        shell: bash
        run: |
          "${POOL_CONTRACT_PYTHON}" \
            -m pytest tests/test_pool_config_default_contract.py -q
```

Keep the existing three-runner matrix, pinned Rust/CPython inputs,
read-only permissions and raw `cargo build --locked` /
`cargo test --locked` steps unchanged. Do not use `uv sync --all-extras` in
this hosted matrix: the project currently declares `uvloop` as a development
dependency and uvloop does not support Windows. The isolated wheel environment
requires only pinned `pytest` and therefore tests the distributable extension
without importing platform-specific development dependencies.

- [ ] **Step 5: Verify the workflow contract is GREEN while PoolConfig stays RED**

Run:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected: `7 passed`.

- [ ] **Step 6: Run `POOL-001` against real SQL Server and confirm RED**

Source the SQL-auth environment exactly as in Task 1, then run:

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/pool-default-red.json \
  uv run pytest \
  tests/sql_auth_strict/test_pool.py::test_default_pool_config_matches_runtime \
  -vv
```

Expected:

```text
real owner SQL authentication        used
explicit runtime max/min             20 / 2
implicit runtime max/min             15 / 3
parameterized results                0 through 29
post-disconnect candidate sessions   zero
POOL-001                              FAIL on canonical equality
```

Confirm through the failure output that both database paths executed before
the assertion failed. Do not accept an authentication, TLS, timeout, sandbox
or fixture error as the reproduction.

- [ ] **Step 7: Verify strict tests still reject swallowed exceptions**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_strict_tests_do_not_swallow_failures \
  -q
```

Expected: `1 passed`.

- [ ] **Step 8: Commit and publish only the RED/test infrastructure**

Run:

```bash
git diff --check
git add \
  tests/sql_auth_strict/test_pool.py \
  .github/workflows/rust-unit-tests.yml
git commit -m "test: reproduce PoolConfig default divergence"
git push -u origin test/pool-config-default-consistency
git status --short --branch
```

Expected: the test branch remains RED only because production runtime, stub
and README still expose divergent defaults. Push target is the user's fork.

---

### Task 4: Create the fix branch and implement the canonical profile

**Files:**

- Modify: `src/pool_config.rs:1-82,303-315`
- Modify: `python/fastmssql/fastmssql.pyi:16-52`
- Modify: `README.md:25,408-449`

**Interfaces:**

- Consumes: the two committed RED commits and the approved seven-field profile.
- Produces: private Rust constants, `PyPoolConfig::canonical_default()`,
  concrete PyO3 metadata, accurate stub/default documentation and a migration
  example.

- [ ] **Step 1: Create the isolated fix worktree from the RED branch**

Run in the main worktree:

```bash
git worktree add \
  .worktrees/fix-pool-config-default-consistency \
  -b fix/pool-config-default-consistency \
  test/pool-config-default-consistency
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-pool-config-default-consistency/.env.sql-auth.local
```

Expected: both RED commits are ancestors of the fix branch and the worktree is
clean.

- [ ] **Step 2: Define the private typed canonical profile**

Add near the top of `src/pool_config.rs`, after imports:

```rust
const DEFAULT_MAX_SIZE: u32 = 15;
const DEFAULT_MIN_IDLE: u32 = 3;
const DEFAULT_MAX_LIFETIME_SECS: u64 = 1800;
const DEFAULT_IDLE_TIMEOUT_SECS: u64 = 300;
const DEFAULT_CONNECTION_TIMEOUT_SECS: u64 = 30;
const DEFAULT_TEST_ON_CHECK_OUT: Option<bool> = None;
const DEFAULT_RETRY_CONNECTION: Option<bool> = None;
```

Add a non-PyO3 inherent implementation before `#[pymethods]`:

```rust
impl PyPoolConfig {
    fn canonical_default() -> Self {
        Self {
            max_size: DEFAULT_MAX_SIZE,
            min_idle: Some(DEFAULT_MIN_IDLE),
            max_lifetime: Some(std::time::Duration::from_secs(
                DEFAULT_MAX_LIFETIME_SECS,
            )),
            idle_timeout: Some(std::time::Duration::from_secs(
                DEFAULT_IDLE_TIMEOUT_SECS,
            )),
            connection_timeout: Some(std::time::Duration::from_secs(
                DEFAULT_CONNECTION_TIMEOUT_SECS,
            )),
            test_on_check_out: DEFAULT_TEST_ON_CHECK_OUT,
            retry_connection: DEFAULT_RETRY_CONNECTION,
        }
    }
}
```

Do not export these constants or add a second public preset.

- [ ] **Step 3: Route omitted Python arguments through the constants**

Replace the existing one-line PyO3 signature with:

```rust
#[pyo3(
    signature = (
        max_size = DEFAULT_MAX_SIZE,
        min_idle = Some(DEFAULT_MIN_IDLE),
        max_lifetime_secs = Some(DEFAULT_MAX_LIFETIME_SECS),
        idle_timeout_secs = Some(DEFAULT_IDLE_TIMEOUT_SECS),
        connection_timeout_secs = Some(DEFAULT_CONNECTION_TIMEOUT_SECS),
        test_on_check_out = DEFAULT_TEST_ON_CHECK_OUT,
        retry_connection = DEFAULT_RETRY_CONNECTION
    ),
    text_signature = "(max_size=15, min_idle=3, max_lifetime_secs=1800, idle_timeout_secs=300, connection_timeout_secs=30, test_on_check_out=None, retry_connection=None)"
)]
```

Keep the existing `new(...) -> PyResult<Self>` parameter types, validation and
field conversion unchanged. A Python caller that explicitly passes `None`
must still deliver `None` to each optional Rust argument.

- [ ] **Step 4: Route the Rust `Default` implementation through the helper**

Replace the existing `Default` body with:

```rust
impl Default for PyPoolConfig {
    fn default() -> Self {
        Self::canonical_default()
    }
}
```

Do not duplicate numeric values in this implementation.

- [ ] **Step 5: Align the public stub**

Update the `PoolConfig` attribute descriptions in
`python/fastmssql/fastmssql.pyi` to:

```python
max_size: Maximum number of connections in the pool (default: 15)
min_idle: Minimum idle connections to maintain (default: 3; None leaves the FastMssql override unset)
max_lifetime_secs: Maximum connection lifetime in seconds (default: 1800; None leaves the FastMssql override unset)
idle_timeout_secs: Idle connection timeout in seconds (default: 300; None leaves the FastMssql override unset)
connection_timeout_secs: Pool acquisition timeout in seconds (default: 30; None leaves the FastMssql override unset)
test_on_check_out: Whether to test connections when checking out (default: None)
retry_connection: Whether to retry connection attempts (default: None)
```

Replace the constructor signature with:

```python
def __init__(
    self,
    max_size: int = 15,
    min_idle: Optional[int] = 3,
    max_lifetime_secs: Optional[int] = 1800,
    idle_timeout_secs: Optional[int] = 300,
    connection_timeout_secs: Optional[int] = 30,
    test_on_check_out: Optional[bool] = None,
    retry_connection: Optional[bool] = None,
) -> None: ...
```

The ellipsis above is executable `.pyi` syntax, not an unspecified plan item.

- [ ] **Step 6: Align README defaults and migration guidance**

Change the feature summary to:

```markdown
- Connection pooling: bb8‑based, smart defaults (default max_size=15, min_idle=3)
```

Replace the incomplete omitted-pool sentence with:

````markdown
Default pool (if omitted or constructed with `PoolConfig()`):
`max_size=15`, `min_idle=3`, `max_lifetime_secs=1800`,
`idle_timeout_secs=300`, `connection_timeout_secs=30`.

Explicit values always win. `None` leaves the corresponding FastMssql bb8
override unset; it does not by itself guarantee an unlimited timeout. To
retain the historical direct-constructor field profile explicitly:

```python
legacy_direct_profile = PoolConfig(
    max_size=20,
    min_idle=2,
    max_lifetime_secs=None,
    idle_timeout_secs=None,
    connection_timeout_secs=30,
)
```
````

Keep the adaptive-sizing recommendation and every named preset value
unchanged.

- [ ] **Step 7: Format and compile before running Python**

Run:

```bash
cargo fmt
cargo build --locked
cargo test --locked
uv run maturin develop --release
```

Expected: raw build PASS, Rust `14/14` PASS and extension rebuild PASS.

---

### Task 5: Prove focused GREEN and commit the technical fix

**Files:**

- Technical fix only:
  `src/pool_config.rs`,
  `python/fastmssql/fastmssql.pyi`,
  `README.md`
- Tests inherited unchanged from `test/pool-config-default-consistency`

**Interfaces:**

- Consumes: rebuilt candidate extension and healthy SQL-auth Docker instance.
- Produces: a single technical GREEN commit and published fix branch on the
  user's fork.

- [ ] **Step 1: Run the focused pure/default contracts**

Run:

```bash
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  -m "not integration" -q
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
PoolConfig pure       92 PASS, 16 deselected
PyO3 contract          7/7 PASS
Ellipsis defaults      zero
```

- [ ] **Step 2: Re-run every focused upstream integration test**

Source the SQL-auth variables exactly as in Task 1, with approved localhost
permission, then run:

```bash
uv run pytest \
  tests/test_pool_config.py \
  tests/test_pool_config_validation.py \
  tests/test_pool_config_default_contract.py \
  -m integration -q
```

Expected: `16 passed, 92 deselected`.

- [ ] **Step 3: Prove `POOL-001` GREEN on the real server**

Run:

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/pool-default-green.json \
  uv run pytest \
  tests/sql_auth_strict/test_pool.py::test_default_pool_config_matches_runtime \
  -vv
```

Expected:

```text
POOL-001 explicit profile          15/3/1800/300/30
POOL-001 implicit profile          15/3/1800/300/30
peak active/connections/sessions   15/15/15 for each path
parameterized results              0 through 29 for each path
post-disconnect sessions           zero for each unique application name
result                             1 PASS
```

- [ ] **Step 4: Re-prove explicit values and presets**

Run:

```bash
uv run pytest \
  tests/test_pool_config.py::TestPoolConfigConstructor::test_custom_values \
  tests/test_pool_config.py::TestPoolConfigConstructor::test_none_values \
  tests/test_pool_config.py::TestPoolConfigPresets \
  tests/test_pool_config.py::TestPoolConfigAdaptive \
  -q
```

Expected: all selected tests PASS; named/adaptive values are unchanged.

- [ ] **Step 5: Inspect the exact technical diff**

Run:

```bash
git diff --check
git diff --stat test/pool-config-default-consistency...HEAD
git diff test/pool-config-default-consistency...HEAD -- \
  src/pool_config.rs \
  python/fastmssql/fastmssql.pyi \
  README.md
git status --short --branch
```

Expected: only the three approved technical/public-contract files differ from
the RED branch; no workflow or test assertion is weakened on the fix branch.

- [ ] **Step 6: Commit and publish the technical fix**

Run:

```bash
git add \
  src/pool_config.rs \
  python/fastmssql/fastmssql.pyi \
  README.md
git commit -m "fix: align PoolConfig default construction"
git push -u origin fix/pool-config-default-consistency
git rev-list --left-right --count \
  HEAD...origin/fix/pool-config-default-consistency
```

Expected: parity `0 0`, author Marcel Galea, and no upstream operation.

---

### Task 6: Execute the full local enterprise gate at the fix commit

**Files:**

- Read/execute: all project sources and tests
- Generated only in disposable verification worktree:
  `.artifacts/sql-auth/**`,
  `docs/SQL_AUTH_TEST_MATRIX.md`,
  `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**

- Consumes: exact `fix/pool-config-default-consistency` commit.
- Produces: isolated full-run artifacts used later by the status branch;
  no additional technical commit.

- [ ] **Step 1: Create a detached verification worktree**

Run in the main worktree:

```bash
git worktree add --detach \
  .worktrees/verify-pool-config-default-consistency \
  fix/pool-config-default-consistency
ln -s ../../.env.sql-auth.local \
  .worktrees/verify-pool-config-default-consistency/.env.sql-auth.local
```

Record and compare:

```bash
git -C .worktrees/verify-pool-config-default-consistency rev-parse HEAD
git rev-parse fix/pool-config-default-consistency
```

Expected: identical full SHA values.

- [ ] **Step 2: Run raw Rust, style, security and static Python gates**

Run in `.worktrees/verify-pool-config-default-consistency`:

```bash
set -euo pipefail
uv sync --locked --all-extras --dev
cargo build --locked
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo audit --deny warnings
uv run ruff check .
uv run python -m compileall -q python tests scripts
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
Rust unit tests       14/14 PASS
PyO3 contract          7/7 PASS
Cargo build/fmt/lint  PASS
RustSec findings       0
Ruff/compileall       PASS
```

- [ ] **Step 3: Build, install and inspect a clean ABI3 wheel**

Run:

```bash
mkdir -p .artifacts/pool-config-wheel
uv run maturin build \
  --release \
  --locked \
  --out .artifacts/pool-config-wheel
uv venv .artifacts/pool-config-wheel-venv --python 3.13
uv pip install \
  --python .artifacts/pool-config-wheel-venv/bin/python \
  .artifacts/pool-config-wheel/*.whl
env -C /private/tmp \
  "${PWD}/.artifacts/pool-config-wheel-venv/bin/python" \
  -c "import inspect; from fastmssql import PoolConfig; c=PoolConfig(); assert (c.max_size,c.min_idle,c.max_lifetime_secs,c.idle_timeout_secs,c.connection_timeout_secs)==(15,3,1800,300,30); assert Ellipsis not in [p.default for p in inspect.signature(PoolConfig).parameters.values()]"
```

Expected: wheel filename contains `cp311-abi3`, clean install/import succeeds
and the installed wheel exposes concrete canonical defaults.

- [ ] **Step 4: Run the complete locked SQL-auth orchestrator**

Run with approved Docker/localhost access:

```bash
scripts/sql_auth/run_all.sh
```

Expected exact final lanes:

```text
cargo test              14/14 PASS
strict functional      295/295 PASS
async                    16/16 PASS
framework                28/28 PASS
resilience                6/6 PASS
load                      9/9 PASS
specification IDs       285/285 PASS
upstream regression     906/906 PASS
required runner lanes   all exit 0
```

The load lane continues to include the approved high-load transaction,
readiness and pool-bounding evidence; this candidate does not replace it with
the 30-task focused check.

- [ ] **Step 5: Verify generated evidence and absence of secrets**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
jq -e \
  '.cases | length == 285 and all(.[]; .outcome == "passed")' \
  .artifacts/sql-auth/strict-results.json
if rg -n '\| (FAIL|ERROR|NOT RUN) \|' \
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

Expected:

```text
285 case records       all passed
matrix/report failures none
credential matches     none
```

Both negative scans exit nonzero if a failed case or credential value is
found; absence is the only passing result.

- [ ] **Step 6: Preserve artifacts and inspect worktree-only generated changes**

Run:

```bash
git status --short --branch
git diff --check
git diff --name-only
```

Expected tracked changes are limited to the generated matrix/report in the
detached verification worktree. Do not commit from this worktree and do not
delete it until the status branch has consumed its artifacts.

---

### Task 7: Merge the technical branch and require hosted GREEN

**Files:**

- Merge commits only; no new file edit.

**Interfaces:**

- Consumes: full locally green fix commit and published fork branches.
- Produces: exact technical cumulative SHA plus required hosted Linux/macOS/
  Windows and RustSec evidence.

- [ ] **Step 1: Verify branch ancestry and subject isolation**

Run in the main worktree:

```bash
set -euo pipefail
git status --short --branch
git merge-base --is-ancestor \
  test/pool-config-default-consistency \
  fix/pool-config-default-consistency
git diff --check \
  test/sql-auth-validation...fix/pool-config-default-consistency
git diff --name-status \
  test/sql-auth-validation...fix/pool-config-default-consistency
git log --format='%h %an <%ae> %s' \
  test/sql-auth-validation..fix/pool-config-default-consistency
```

Expected diff contains only:

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

- [ ] **Step 2: Merge and push only to the cumulative fork branch**

Run:

```bash
git switch test/sql-auth-validation
git merge --no-ff fix/pool-config-default-consistency \
  -m "merge: align PoolConfig default construction"
technical_sha="$(git rev-parse HEAD)"
printf 'technical_sha=%s\n' "${technical_sha}"
git remote get-url --push origin
git remote get-url --push upstream
git push origin test/sql-auth-validation
```

Expected: origin is the user's fork, upstream is `DISABLED`, and the push
creates no original-repository branch or PR.

- [ ] **Step 3: Wait for the RustSec hosted gate at the exact SHA**

Run:

```bash
technical_sha="$(git rev-parse test/sql-auth-validation)"
dependency_security_run_id="$(
  gh run list \
  --repo galeamarcel/FastMssql \
  --workflow dependency-security.yml \
  --branch test/sql-auth-validation \
  --commit "${technical_sha}" \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId'
)"
test -n "${dependency_security_run_id}"
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow dependency-security.yml \
  --branch test/sql-auth-validation \
  --commit "${technical_sha}" \
  --limit 1 \
  --json databaseId,headSha,status,conclusion,url
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status \
  "${dependency_security_run_id}"
```

Expected: `cargo-audit` success at the exact technical SHA.

- [ ] **Step 4: Wait for all three hosted extension jobs at the exact SHA**

Run:

```bash
technical_sha="$(git rev-parse test/sql-auth-validation)"
rust_extension_run_id="$(
  gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch test/sql-auth-validation \
  --commit "${technical_sha}" \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId'
)"
test -n "${rust_extension_run_id}"
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch test/sql-auth-validation \
  --commit "${technical_sha}" \
  --limit 1 \
  --json databaseId,headSha,status,conclusion,url
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status \
  "${rust_extension_run_id}"
gh run view \
  --repo galeamarcel/FastMssql \
  "${rust_extension_run_id}" \
  --json headSha,status,conclusion,jobs,url
```

Expected on Ubuntu, macOS and Windows:

```text
cargo build --locked                         PASS
cargo test --locked                          14/14 PASS
maturin ABI3 wheel build/install             PASS
tests/test_pool_config_default_contract.py    3/3 PASS
job conclusion                               success
```

Do not infer one platform from another and do not accept a run for a different
SHA.

---

### Task 8: Update generated evidence, live audit and future PR roadmap

**Files:**

- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Consumes: exact technical SHA, local full-run artifacts and hosted run URLs.
- Produces: `docs/pool-config-default-consistency-status` with measured,
  secret-free evidence and PR-21 classified for future approval only.

- [ ] **Step 1: Create the status worktree from the hosted-green technical SHA**

Run in the main worktree:

```bash
git worktree add \
  .worktrees/docs-pool-config-default-consistency-status \
  -b docs/pool-config-default-consistency-status \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/docs-pool-config-default-consistency-status/.env.sql-auth.local
```

Verify:

```bash
git -C .worktrees/docs-pool-config-default-consistency-status rev-parse HEAD
```

Expected: exact `technical_sha`.

- [ ] **Step 2: Regenerate matrix/report from the verified artifacts**

Run in `.worktrees/docs-pool-config-default-consistency-status`:

```bash
set -euo pipefail
set -a
source .env.sql-auth.local
set +a
verification_artifacts="../verify-pool-config-default-consistency/.artifacts/sql-auth"
uv run python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results "${verification_artifacts}/strict-results.json" \
  --artifact-dir "${verification_artifacts}" \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete
```

Expected: `285/285` ID records are PASS and all runner lanes have exit code
zero. No credential value appears in either document.

- [ ] **Step 3: Add the measured PoolConfig audit section**

Add a section named
`## Consistența defaulturilor PoolConfig — remediată și verificată` to
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` containing:

1. the RED values `20/2/None/None/30` versus
   `15/3/1800/300/30`;
2. root cause: PyO3 constructor literals diverged from
   `PyPoolConfig::default()`;
3. upstream provenance commit `2c5620a`;
4. canonical seven-field profile and explicit-`None` boundary;
5. exact design/test/fix/status branch names and commit IDs;
6. focused `92` pure, `16` integration and `POOL-001` real-server results;
7. saturation at exactly 15 sessions for both construction paths and zero
   candidate sessions after disconnect;
8. Rust `14/14`, PyO3 contract `7/7`, strict `295/295`, async `16/16`,
   framework `28/28`, resilience `6/6`, load `9/9`, specification
   `285/285`, upstream `906/906`;
9. wheel, Ruff, compileall, Clippy and RustSec evidence;
10. hosted dependency-security URL and hosted Linux/macOS/Windows URL at
    `technical_sha`;
11. the remaining separate `None`/operation-timeout semantic candidate;
12. explicit statement that nothing was pushed or proposed upstream.

Use measured commit IDs, SHA and URLs from the completed tasks, not forecast
values.

- [ ] **Step 4: Update the audit order and acceptance checklist**

In `## Ordinea recomandată a branch-urilor`, insert:

```markdown
12. `fix/pool-config-default-consistency` — **defaulturile explicite și
    implicite canonice, introspecția, stubul și limitele reale de pool
    finalizate și verificate hosted**
```

Renumber the remaining unimplemented items. Add this checked acceptance item:

```markdown
- [x] `PoolConfig()` și `Connection(..., pool_config=None)` folosesc același
  profil `15/3/1800/300/30`, iar pool-ul real rămâne limitat la 15 sesiuni;
```

Remove the duplicated consecutive
`răspunsul pierdut după COMMIT produce CommitOutcomeUnknown` checklist line,
retaining one complete checked entry.

Update `## Starea verificată curentă` with `technical_sha`, exact local counts
and hosted URLs while preserving the distinction between technical and
documentation-only commits.

- [ ] **Step 5: Record the future upstream candidate as PR-21**

In
`docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`:

- add `PR-21 PoolConfig default consistency` to the proposed order;
- add a Candidate intake row;
- add a focused PR-21 task with:

```text
fork implementation branch   fix/pool-config-default-consistency
future clean branch          fix/upstream-pool-config-default-consistency
future title                 fix: align PoolConfig default construction
classification               VERIFIED_FORK / requires fresh upstream rebase
public API change            omitted PoolConfig arguments become canonical
compatibility note           historical direct profile remains explicit
required evidence            RED, 906 upstream, 295 strict, 285 IDs,
                             14 Rust, 7 PyO3, three hosted OS jobs
publication                  forbidden until a new explicit user approval
```

Do not create the future clean branch or PR.

- [ ] **Step 6: Self-review all status documentation**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
git diff --check
if rg -n 'TO''DO|T''BD|FIX''ME|PLACE''HOLDER' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md; then
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
git diff --stat
git status --short --branch
```

Expected: no placeholders, whitespace failures or secret values. Only the four
approved documentation artifacts differ.

- [ ] **Step 7: Commit and publish the status branch to the fork**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record canonical PoolConfig defaults"
git push -u origin docs/pool-config-default-consistency-status
git rev-list --left-right --count \
  HEAD...origin/docs/pool-config-default-consistency-status
```

Expected: parity `0 0`, commit author Marcel Galea, push only to the fork.

---

### Task 9: Integrate status evidence and close the candidate safely

**Files:**

- Merge commits only.

**Interfaces:**

- Consumes: published status commit and all local/hosted evidence.
- Produces: final cumulative fork SHA, final hosted confirmation and clean
  branch/publication handoff.

- [ ] **Step 1: Merge the status branch into the cumulative fork**

Run in the main worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff docs/pool-config-default-consistency-status \
  -m "merge: record canonical PoolConfig defaults"
final_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  HEAD...origin/test/sql-auth-validation
```

Expected: parity `0 0`; the final merge adds documentation only relative to
the hosted-green technical SHA.

- [ ] **Step 2: Require final hosted gates at the documentation merge SHA**

Find and watch both workflows exactly as in Task 7, but require
`headSha == final_sha`:

```bash
final_sha="$(git rev-parse test/sql-auth-validation)"
dependency_security_run_id="$(
  gh run list \
  --repo galeamarcel/FastMssql \
  --workflow dependency-security.yml \
  --branch test/sql-auth-validation \
  --commit "${final_sha}" \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId'
)"
rust_extension_run_id="$(
  gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch test/sql-auth-validation \
  --commit "${final_sha}" \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId'
)"
test -n "${dependency_security_run_id}"
test -n "${rust_extension_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status \
  "${dependency_security_run_id}"
gh run watch \
  --repo galeamarcel/FastMssql \
  --exit-status \
  "${rust_extension_run_id}"
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow dependency-security.yml \
  --branch test/sql-auth-validation \
  --commit "${final_sha}" \
  --limit 1 \
  --json databaseId,headSha,status,conclusion,url
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch test/sql-auth-validation \
  --commit "${final_sha}" \
  --limit 1 \
  --json databaseId,headSha,status,conclusion,url
```

Expected: RustSec success plus Ubuntu/macOS/Windows success at `final_sha`.

- [ ] **Step 3: Verify no upstream branch or PR was created**

Run:

```bash
git remote -v
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:fix/pool-config-default-consistency
gh pr list \
  --repo Rivendael/FastMssql \
  --state all \
  --head galeamarcel:fix/upstream-pool-config-default-consistency
```

Expected:

```text
origin push          https://github.com/galeamarcel/FastMssql.git
upstream push        DISABLED
matching upstream PR none
```

- [ ] **Step 4: Remove only the disposable verification worktree**

First verify the exact target and that its generated evidence is represented
by the committed status documents:

```bash
git worktree list
git -C .worktrees/verify-pool-config-default-consistency status --short
git log -1 --oneline docs/pool-config-default-consistency-status
```

Then remove only that disposable detached worktree:

```bash
git worktree remove --force \
  .worktrees/verify-pool-config-default-consistency
git worktree prune
```

Do not remove design, RED, fix or status worktrees; they remain auditable.

- [ ] **Step 5: Produce the final candidate handoff**

Report:

1. design, plan, RED, fix, technical merge, status and final merge commit IDs;
2. every fork branch and parity;
3. exact local counts and real MSSQL session/concurrency evidence;
4. wheel and static-quality evidence;
5. final RustSec and Linux/macOS/Windows workflow URLs/SHA;
6. explicit-`None` semantics still deferred;
7. no original-repository push, branch or PR;
8. the next enterprise candidate:
   `feat/operation-timeouts`, subject to its own approved design.

Do not describe the entire production-readiness objective as complete; only
this PoolConfig candidate closes.
