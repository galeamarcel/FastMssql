# FastMssql Operation Duration and Outcome Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Marcel Galea selected inline
> execution through standing authorization; delegated subagents are not
> permitted by the active repository instructions.

**Goal:** Add opt-in, bounded, connection-local duration histograms and
mutually exclusive outcome counters for every asynchronous public
`Connection` operation and every transaction operation owned by its pool.

**Architecture:** A default-off `OperationMetricsConfig` controls allocation
of one fixed atomic registry per `Connection`. A generic Rust RAII observer
measures the accepted async body, publishes one typed outcome on completion or
cancellation, and renders reconciled fixed-schema snapshots without locks,
labels, SQL or callbacks. Pooled transactions inherit the owner registry;
standalone direct transactions remain outside this candidate.

**Tech Stack:** Rust 2024, PyO3 0.29, pyo3-async-runtimes 0.29, Tokio 1.52,
bb8 0.9.1, vendored Tiberius, Python 3.11+, pytest/pytest-asyncio, FastAPI,
Flask, WsgiToAsgi, Docker SQL Server with SQL authentication, GitHub Actions
Linux/macOS/Windows ABI3 wheel contracts.

## Global Constraints

- Source baseline is `test/sql-auth-validation` at
  `c3311b66a85f7f4fbbda7a9087506375f761ba8f`.
- Design authority is
  `docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-design.md`
  initially committed at
  `5df2b48e7a267904a0a237f97fa35a99dd4b2072`; the elapsed-boundary
  self-review amendment is committed together with this plan.
- All branches, commits, pushes, workflow runs and artifacts target only
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` remains fetch-only with push URL exactly `DISABLED`.
- No upstream branch, pull request, package publication, dependency fork or
  version bump is authorized.
- Use project-local `.worktrees/`; preserve every existing worktree and dirty
  user file.
- Keep design/plan, RED tests, GREEN implementation and final status on
  distinct branches.
- RED assertions may not be removed, weakened, skipped, retried or converted
  to expected failures in GREEN.
- Use only the approved Docker SQL Server with SQL authentication for SQL
  behavior. Mocks and SQLite cannot substitute for any `OPMET-*` case.
- Never print, copy, add or commit `.env.sql-auth.local`, credentials,
  connection strings or privacy sentinels.
- `OperationMetricsConfig(enabled=False)` is the exact default and accepts
  only Python `bool`.
- `operation_metrics_config` is the final `Connection` constructor parameter;
  all existing positional parameters keep their positions.
- A disabled connection allocates no registry and reaches the original future
  before any clock read or atomic update.
- The fixed active operation set is exactly:

  ```text
  connect ping query simple_query execute query_batch execute_batch
  bulk_insert begin commit rollback close disconnect
  ```

- `OperationName::Transaction`, diagnostic getters, `pool_stats()`,
  `operation_stats()`, `is_connected()` and synchronous parameter/batch
  conversion are not instrumented.
- A pooled `Transaction` shares its owning `Connection` registry. The direct
  `Transaction(...)` compatibility constructor has no metrics registry.
- Outcome counters are mutually exclusive with this exact priority:

  ```text
  Ok -> succeeded
  CommitOutcomeUnknown -> outcome_unknown
  OperationTimeoutError | ShutdownTimeoutError -> timed_out
  every other returned PyErr -> errors
  dropped started Rust future -> cancelled
  ```

- The registry lifetime is the logical `Connection` lifetime across
  disconnect/reconnect generations. It has no reset or runtime toggle.
- The 17 finite bucket bounds in whole microseconds are:

  ```text
  100 250 500 1_000 2_500 5_000 10_000 25_000 50_000
  100_000 250_000 500_000 1_000_000 2_500_000 5_000_000
  10_000_000 30_000_000
  ```

- `completed` is the implicit positive-infinity histogram bucket.
- All counters and duration additions saturate at `u64::MAX`; they never wrap.
- Writers update duration data before a Release update of the final outcome.
  Readers use Acquire loads and reconcile public arithmetic without locks.
- Every snapshot satisfies:

  ```text
  started == completed + in_flight
  completed == succeeded + errors + timed_out + cancelled + outcome_unknown
  buckets are cumulative, non-decreasing and <= completed
  ```

- Concurrent snapshots are arithmetically coherent and exact after
  quiescence; they are not represented as one multi-counter atomic instant.
- Metric keys/values contain no SQL, parameters, connection identifiers,
  database/application/login names, credentials, exception text or arbitrary
  labels.
- No OpenTelemetry/Prometheus SDK, callback, exporter, logger, background task
  or dependency is added.
- Run graph-first impact review before source editing and rebuild the graph for
  final self-review.
- The repository has no `VERSION.md`; do not create one.

---

## Exact Public Schema

All implementation, stub and test code uses these canonical constants:

```python
OPERATION_NAMES = (
    "connect",
    "ping",
    "query",
    "simple_query",
    "execute",
    "query_batch",
    "execute_batch",
    "bulk_insert",
    "begin",
    "commit",
    "rollback",
    "close",
    "disconnect",
)

BUCKET_BOUNDS_SECONDS = (
    0.0001,
    0.00025,
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

ENTRY_KEYS = {
    "started",
    "completed",
    "in_flight",
    "succeeded",
    "errors",
    "timed_out",
    "cancelled",
    "outcome_unknown",
    "duration_seconds_sum",
    "duration_seconds_min",
    "duration_seconds_max",
    "duration_seconds_buckets",
    "saturated",
}
```

The exact runtime shape is:

```python
{
    "schema_version": 1,
    "enabled": bool,
    "bucket_bounds_seconds": list(BUCKET_BOUNDS_SECONDS),
    "operations": {
        operation: {
            "started": int,
            "completed": int,
            "in_flight": int,
            "succeeded": int,
            "errors": int,
            "timed_out": int,
            "cancelled": int,
            "outcome_unknown": int,
            "duration_seconds_sum": float,
            "duration_seconds_min": float | None,
            "duration_seconds_max": float | None,
            "duration_seconds_buckets": list[int],
            "saturated": bool,
        }
        for operation in OPERATION_NAMES
    },
}
```

Both stub layers define these private typing helpers with every field written
out:

```python
class _OperationStatsEntry(TypedDict):
    started: int
    completed: int
    in_flight: int
    succeeded: int
    errors: int
    timed_out: int
    cancelled: int
    outcome_unknown: int
    duration_seconds_sum: float
    duration_seconds_min: Optional[float]
    duration_seconds_max: Optional[float]
    duration_seconds_buckets: List[int]
    saturated: bool

class _OperationStatsByName(TypedDict):
    connect: _OperationStatsEntry
    ping: _OperationStatsEntry
    query: _OperationStatsEntry
    simple_query: _OperationStatsEntry
    execute: _OperationStatsEntry
    query_batch: _OperationStatsEntry
    execute_batch: _OperationStatsEntry
    bulk_insert: _OperationStatsEntry
    begin: _OperationStatsEntry
    commit: _OperationStatsEntry
    rollback: _OperationStatsEntry
    close: _OperationStatsEntry
    disconnect: _OperationStatsEntry

class _OperationStatsSnapshot(TypedDict):
    schema_version: Literal[1]
    enabled: bool
    bucket_bounds_seconds: List[float]
    operations: _OperationStatsByName
```

The method annotation is:

```python
def operation_stats(
    self,
) -> Coroutine[Any, Any, _OperationStatsSnapshot]: ...
```

## File Map and Ownership Boundaries

### New implementation files

- `src/operation_metrics_config.rs` — exact default-off PyO3 configuration,
  bool property and repr.
- `src/operation_metrics.rs` — fixed registry, saturating atomics, RAII
  observer, error classification, coherent snapshot and Python dictionary
  rendering.

### Existing implementation/API files

- `src/lib.rs` — register/export `OperationMetricsConfig` and both new modules.
- `src/connection.rs` — own the optional registry, constructor/property/
  snapshot API, wrap all public connection futures and pass the registry to
  pooled transactions/batches.
- `src/transaction.rs` — store an optional owner registry only for pooled
  transactions and wrap query/simple-query/execute/batches/begin/commit/
  rollback/close.
- `src/batch.rs` — accept the owner registry and wrap the whole public
  query-batch, execute-batch and bulk-insert futures once.
- `python/fastmssql/__init__.py` — export config, forward `operation_stats`,
  expose config copy and document semantics.
- `python/fastmssql/fastmssql.pyi` — core config class, private TypedDicts,
  constructor/property/method annotations.
- `python/fastmssql/__init__.pyi` — wrapper imports, private TypedDicts,
  constructor/property/method annotations and `__all__`.
- `README.md` — opt-in usage, schema, lifecycle, consistency, privacy and
  performance boundaries.

### New test and stress files

- `tests/test_operation_metrics_contract.py` — no-network runtime, stub,
  wrapper, README, disabled-hot-path and installed-wheel contract.
- `tests/sql_auth_strict/operation_metrics_assertions.py` — one canonical
  schema/invariant/delta helper shared by strict, load and framework tests.
- `tests/sql_auth_strict/test_operation_metrics.py` — deterministic
  `OPMET-001..010` and `OPMET-012`.
- `scripts/sql_auth/operation_metrics_stress.py` — three fixed interleaved
  99,999-operation enabled/disabled pairs and JSON evidence.
- `scripts/sql_auth/run_operation_metrics_stress.sh` — opt-in SQL-auth wrapper
  with exact defaults and source SHA.

### Existing tests/orchestration files

- `tests/sql_auth_strict/test_transactions_strict.py` — extend the existing
  deterministic pooled commit ACK-loss case as `OPMET-013`.
- `tests/sql_auth_strict/test_resilience_load.py` — add `OPMET-011`, 10,000
  bounded operations with continuous operation snapshot scraping.
- `tests/sql_auth_strict/framework_apps.py` — allow a framework state to opt in
  without changing default fixtures.
- `tests/sql_auth_strict/test_framework_integration.py` — add native ASGI,
  Flask WSGI and Flask-through-ASGI cases `OPMET-014..016`.
- `tests/sql_auth_strict/conftest.py` — allow exactly `OPMET-011` in load
  metrics and `OPMET-014..016` in framework metrics.
- `tests/sql_auth_strict/test_matrix_contract.py` — authoritative total
  `321 -> 337`, stress bounds and exact lane routing.
- `tests/test_pyo3_build_contract.py` — hosted installed-wheel command includes
  the new contract.
- `scripts/sql_auth/run_all.sh` — strict functional lane includes the focused
  operation-metrics file.
- `.github/workflows/rust-unit-tests.yml` — Linux/macOS/Windows installed-wheel
  command includes the new contract.

### Existing specification and final status files

- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — exact `OPMET-001..016` registry.
- `docs/SQL_AUTH_TEST_MATRIX.md` — regenerate only from the exact technical
  merge.
- `docs/SQL_AUTH_TEST_REPORT.md` — regenerate only from the exact technical
  merge.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — measured
  `VERIFIED_FORK` status and remaining limitations.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` — future
  independently reviewable candidate; no PR creation.

---

### Task 1: Preserve the design/plan and create the RED worktree

**Files:**

- Verify:
  `docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-design.md`
- Verify:
  `docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics.md`

**Interfaces:**

- Consumes: cumulative baseline
  `c3311b66a85f7f4fbbda7a9087506375f761ba8f`.
- Produces: clean pushed `docs/operation-metrics-design` and isolated
  `test/operation-metrics`.

- [ ] **Step 1: Verify branch, remotes, ancestry and current checks**

Run:

```bash
git status --short --branch
git remote -v
git rev-parse HEAD
git merge-base --is-ancestor \
  c3311b66a85f7f4fbbda7a9087506375f761ba8f HEAD
git diff --check
cargo test --locked
```

Expected:

```text
branch docs/operation-metrics-design
origin fetch/push galeamarcel/FastMssql
upstream fetch Rivendael/FastMssql
upstream push DISABLED
baseline is an ancestor
zero diff errors
all inherited Rust tests pass
```

- [ ] **Step 2: Run the complete design/plan self-review**

Run:

```bash
rg -n \
  "OPMET-0(0[1-9]|1[0-6])|337|99,999|0.15|u64::MAX|DISABLED" \
  docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics.md
git diff --check
```

Require:

```text
16 unique OPMET IDs
13 exact operation names
17 exact finite bounds
337 exact strict IDs
default disabled before clock/atomics
one fixed outcome priority
one fixed stress order and formula
no callback/exporter/dependency
no upstream write
```

- [ ] **Step 3: Commit and push the plan only to the fork**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics.md
git commit -m "docs: plan opt-in operation metrics"
git push origin docs/operation-metrics-design
git rev-list --left-right --count \
  HEAD...origin/docs/operation-metrics-design
```

Expected: `0 0`; only the plan and the documented self-review amendment enter
this commit.

- [ ] **Step 4: Create the RED branch/worktree**

From the main repository worktree:

```bash
git worktree add .worktrees/test-operation-metrics \
  -b test/operation-metrics docs/operation-metrics-design
```

- [ ] **Step 5: Link the ignored SQL-auth environment without reading it**

Inside the RED worktree:

```bash
test -f ../../.env.sql-auth.local
test ! -e .env.sql-auth.local
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git check-ignore .env.sql-auth.local
```

Do not display the environment file.

- [ ] **Step 6: Verify the inherited RED baseline**

Run:

```bash
git status --short --branch
cargo test --locked
```

Expected: the symlink is ignored and all inherited Rust tests pass.

---

### Task 2: Add RED public, matrix and hosted-wheel contracts

**Files:**

- Create: `tests/test_operation_metrics_contract.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `scripts/sql_auth/run_all.sh`

**Interfaces:**

- Consumes: exact schema/constants in this plan.
- Produces: no-network RED API contract, 16 authoritative case IDs and
  cross-platform installed-wheel enforcement.

- [ ] **Step 1: Add the authoritative OPMET registry**

Add this exact section after OBS in the SQL-auth specification:

```markdown
### OPMET — operation duration and outcome metrics

- `OPMET-001`: exported configuration API, exact bool/default/repr behavior,
  constructor compatibility and isolated-copy semantics.
- `OPMET-002`: disabled exact schema remains zero across success, error and
  cancellation; returned snapshots are mutation-isolated.
- `OPMET-003`: successful connect/query/simple-query/execute calls publish
  exact counts, durations and cumulative buckets.
- `OPMET-004`: returned SQL/lifecycle errors increment only `errors`, while
  synchronous pre-future validation is excluded.
- `OPMET-005`: a pool acquisition deadline increments only `timed_out` and
  preserves recovery.
- `OPMET-006`: operation and graceful-shutdown deadline errors increment only
  `timed_out` for their public operations and preserve lifecycle recovery.
- `OPMET-007`: server-confirmed cancellation increments only `cancelled`,
  safely retires the transport and preserves recovery.
- `OPMET-008`: pooled transaction operations aggregate into their owner and
  standalone direct transactions do not.
- `OPMET-009`: connect, ping, context, disconnect and reconnect keep one
  connection-lifetime registry without internal-operation double counting.
- `OPMET-010`: query batch, dedicated-socket execute batch and bulk insert each
  publish one whole-call metric with exact effects.
- `OPMET-011`: 10,000 bounded concurrent operations plus continuous scraping
  preserve every invariant, event-loop progress and session cleanup.
- `OPMET-012`: statistics and evidence contain no SQL, parameters,
  identifiers, credentials, errors or arbitrary labels.
- `OPMET-013`: deterministic COMMIT acknowledgement loss publishes exactly one
  `outcome_unknown` and preserves the typed error/retirement contract.
- `OPMET-014`: FastAPI/native ASGI concurrency publishes exact operation
  deltas without blocking event-loop progress.
- `OPMET-015`: Flask async under WSGI publishes exact deltas while preserving
  its distinct per-request event-loop limitation.
- `OPMET-016`: Flask through WsgiToAsgi publishes exact concurrent deltas on a
  persistent event loop.
```

Change current authoritative count assertions and generated-report fixtures
from 321 to 337. Update exact error text such as:

```text
missing evidence for 337 case(s)
| NOT RUN | 337 |
```

Do not rewrite historical measured prose that correctly describes an earlier
321-case run.

- [ ] **Step 2: Create the no-network runtime/schema helper**

Start `tests/test_operation_metrics_contract.py` with:

```python
from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from pathlib import Path

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
RUST_METRICS = ROOT / "src/operation_metrics.rs"
OPERATION_NAMES = (
    "connect", "ping", "query", "simple_query", "execute",
    "query_batch", "execute_batch", "bulk_insert", "begin",
    "commit", "rollback", "close", "disconnect",
)
BUCKET_BOUNDS_SECONDS = (
    0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005,
    0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
    5.0, 10.0, 30.0,
)
ENTRY_KEYS = {
    "started", "completed", "in_flight", "succeeded", "errors",
    "timed_out", "cancelled", "outcome_unknown",
    "duration_seconds_sum", "duration_seconds_min",
    "duration_seconds_max", "duration_seconds_buckets", "saturated",
}


def public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def assert_zero_snapshot(snapshot: dict[str, object], *, enabled: bool) -> None:
    assert set(snapshot) == {
        "schema_version", "enabled", "bucket_bounds_seconds", "operations"
    }
    assert snapshot["schema_version"] == 1
    assert snapshot["enabled"] is enabled
    assert tuple(snapshot["bucket_bounds_seconds"]) == BUCKET_BOUNDS_SECONDS
    assert tuple(snapshot["operations"]) == OPERATION_NAMES
    for entry in snapshot["operations"].values():
        assert set(entry) == ENTRY_KEYS
        assert entry["started"] == entry["completed"] == 0
        assert entry["in_flight"] == 0
        assert all(
            entry[key] == 0
            for key in (
                "succeeded", "errors", "timed_out", "cancelled",
                "outcome_unknown",
            )
        )
        assert entry["duration_seconds_sum"] == 0.0
        assert entry["duration_seconds_min"] is None
        assert entry["duration_seconds_max"] is None
        assert entry["duration_seconds_buckets"] == [0] * 17
        assert entry["saturated"] is False
```

Use `dict[str, object]` only inside the runtime test helper; public stubs use
the exact private `TypedDict` contract.

- [ ] **Step 3: Add RED config/default/copy tests**

Add:

```python
def test_operation_metrics_config_exact_runtime_contract() -> None:
    config_type = public_type("OperationMetricsConfig")
    assert config_type.__text_signature__ == "(enabled=False)"
    signature = inspect.signature(config_type)
    assert tuple(signature.parameters) == ("enabled",)
    assert signature.parameters["enabled"].default is False
    assert repr(config_type()) == "OperationMetricsConfig(enabled=False)"
    assert repr(config_type(enabled=True)) == (
        "OperationMetricsConfig(enabled=True)"
    )
    for invalid in (0, 1, "true", None, object()):
        with pytest.raises(TypeError):
            config_type(enabled=invalid)
        mutable = config_type()
        with pytest.raises(TypeError):
            mutable.enabled = invalid
        assert mutable.enabled is False


def test_connection_appends_and_copies_operation_metrics_config() -> None:
    config_type = public_type("OperationMetricsConfig")
    core = importlib.import_module("fastmssql.fastmssql")
    parameters = tuple(inspect.signature(core.Connection).parameters.values())
    assert parameters[-1].name == "operation_metrics_config"
    assert parameters[-1].default is None

    external = config_type(enabled=True)
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
        operation_metrics_config=external,
    )
    external.enabled = False
    assert connection.operation_metrics_config.enabled is True
    exposed = connection.operation_metrics_config
    exposed.enabled = False
    assert connection.operation_metrics_config.enabled is True
    with pytest.raises(AttributeError):
        connection.operation_metrics_config = config_type(enabled=False)
```

On the baseline, `public_type` must fail only because the class is absent.

- [ ] **Step 4: Add RED disabled snapshot/mutation tests**

Add:

```python
async def disabled_snapshots() -> tuple[
    dict[str, object],
    dict[str, object],
]:
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
    )
    first = await connection.operation_stats()
    first["bucket_bounds_seconds"].append(99.0)
    first["operations"]["query"]["started"] = 99
    first["operations"]["query"]["duration_seconds_buckets"].append(99)
    second = await connection.operation_stats()
    return first, second


def test_disabled_snapshot_is_exact_zero_and_mutation_isolated() -> None:
    first, second = asyncio.run(disabled_snapshots())
    assert first["bucket_bounds_seconds"][-1] == 99.0
    assert first["operations"]["query"]["started"] == 99
    assert_zero_snapshot(second, enabled=False)
```

This performs no network call; port 1 must never be contacted.

- [ ] **Step 5: Add exact stub/wrapper/README and disabled-source tests**

Parse both stubs with `ast` and require one class each named
`_OperationStatsEntry`, `_OperationStatsByName`,
`_OperationStatsSnapshot` and `OperationMetricsConfig`. Assert exact field
names from the schema above, `Literal[1]`, the constructor's final parameter,
the property and method annotation.

Add source checks:

```python
def test_disabled_branch_precedes_clock_and_atomic_recording() -> None:
    source = RUST_METRICS.read_text(encoding="utf-8")
    observer = source.index("pub(crate) async fn observe_operation")
    disabled = source.index("None => future.await", observer)
    clock = source.index("Instant::now()", observer)
    assert disabled < clock
    assert "Python callback" not in source
    assert "opentelemetry" not in source.lower()


def test_wrapper_stubs_and_readme_publish_operation_metrics() -> None:
    wrapper = (ROOT / "python/fastmssql/__init__.py").read_text(
        encoding="utf-8"
    )
    readme = README.read_text(encoding="utf-8")
    for token in (
        "OperationMetricsConfig",
        "operation_metrics_config",
        "operation_stats",
        "outcome_unknown",
        "duration_seconds_buckets",
        "connection lifetime",
        "weakly consistent",
    ):
        assert token in wrapper or token in readme
```

- [ ] **Step 6: Wire the installed contract on every hosted OS**

Append `tests/test_operation_metrics_contract.py` to the installed-wheel pytest
command in `.github/workflows/rust-unit-tests.yml`. Update
`tests/test_pyo3_build_contract.py` so the normalized exact command is:

```text
tests/test_pool_config_default_contract.py
tests/test_timeout_config_contract.py
tests/test_lifecycle_contract.py
tests/test_pool_observability_contract.py
tests/test_operation_metrics_contract.py -q
```

- [ ] **Step 7: Wire the focused SQL-auth file**

Add:

```bash
tests/sql_auth_strict/test_operation_metrics.py
```

to `strict_functional` immediately after
`tests/sql_auth_strict/test_pool_observability.py`.

- [ ] **Step 8: Run the static matrix/build contracts**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_pyo3_build_contract.py -q
```

The matrix contract becomes green only after Tasks 3–5 attach every new ID.
Before then, missing-source-ID errors are expected static incompleteness, not
the behavioral RED proof.

---

### Task 3: Add canonical assertions and RED core operation cases

**Files:**

- Create: `tests/sql_auth_strict/operation_metrics_assertions.py`
- Create: `tests/sql_auth_strict/test_operation_metrics.py`

**Interfaces:**

- Produces:
  `assert_operation_stats(snapshot, enabled)`,
  `operation_delta(before, after, operation)` and `zero_outcomes()`.
- Produces SQL-auth cases `OPMET-001..007`.

- [ ] **Step 1: Implement the one canonical invariant helper**

Create `operation_metrics_assertions.py` with the exact constants from the
public schema and:

```python
from __future__ import annotations

import math


OUTCOME_KEYS = (
    "succeeded", "errors", "timed_out", "cancelled", "outcome_unknown"
)
COUNT_KEYS = (
    "started", "completed", "in_flight", *OUTCOME_KEYS
)


def assert_operation_stats(
    snapshot: dict[str, object],
    *,
    enabled: bool,
) -> None:
    assert set(snapshot) == {
        "schema_version", "enabled", "bucket_bounds_seconds", "operations"
    }
    assert type(snapshot["schema_version"]) is int
    assert snapshot["schema_version"] == 1
    assert snapshot["enabled"] is enabled
    bounds = snapshot["bucket_bounds_seconds"]
    assert tuple(bounds) == BUCKET_BOUNDS_SECONDS
    assert all(
        math.isfinite(value) and value > 0.0
        for value in bounds
    )
    assert all(left < right for left, right in zip(bounds, bounds[1:]))
    operations = snapshot["operations"]
    assert tuple(operations) == OPERATION_NAMES

    for entry in operations.values():
        assert set(entry) == ENTRY_KEYS
        assert all(type(entry[key]) is int for key in COUNT_KEYS)
        assert all(entry[key] >= 0 for key in COUNT_KEYS)
        assert entry["started"] == (
            entry["completed"] + entry["in_flight"]
        )
        assert entry["completed"] == sum(
            entry[key] for key in OUTCOME_KEYS
        )
        buckets = entry["duration_seconds_buckets"]
        assert len(buckets) == len(bounds)
        assert all(type(value) is int and value >= 0 for value in buckets)
        assert all(
            left <= right for left, right in zip(buckets, buckets[1:])
        )
        assert all(value <= entry["completed"] for value in buckets)
        assert type(entry["duration_seconds_sum"]) is float
        assert type(entry["saturated"]) is bool
        if entry["completed"] == 0:
            assert entry["duration_seconds_sum"] == 0.0
            assert entry["duration_seconds_min"] is None
            assert entry["duration_seconds_max"] is None
            assert buckets == [0] * len(bounds)
        else:
            minimum = entry["duration_seconds_min"]
            maximum = entry["duration_seconds_max"]
            total = entry["duration_seconds_sum"]
            assert type(minimum) is float
            assert type(maximum) is float
            assert all(math.isfinite(value) for value in (minimum, maximum, total))
            assert 0.0 <= minimum <= maximum <= total


def operation_delta(
    before: dict[str, object],
    after: dict[str, object],
    operation: str,
) -> dict[str, int]:
    keys = ("started", "completed", "in_flight", *OUTCOME_KEYS)
    left = before["operations"][operation]
    right = after["operations"][operation]
    delta = {
        key: right[key] - left[key]
        for key in keys
        if key != "in_flight"
    }
    assert all(value >= 0 for value in delta.values())
    assert right["in_flight"] >= 0
    return delta


def zero_outcomes(**changes: int) -> dict[str, int]:
    expected = {key: 0 for key in OUTCOME_KEYS}
    expected.update(changes)
    return expected
```

Include `OPERATION_NAMES`, `BUCKET_BOUNDS_SECONDS` and `ENTRY_KEYS` exactly as
listed at the top of this plan.

- [ ] **Step 2: Add a connection factory that never hides configuration**

Start `test_operation_metrics.py` with:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig, TimeoutConfig
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.operation_metrics_assertions import (
    OPERATION_NAMES,
    assert_operation_stats,
    operation_delta,
    zero_outcomes,
)


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def metrics_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    enabled: bool,
    max_size: int = 4,
    timeout_config: TimeoutConfig | None = None,
    lifecycle_config=None,
) -> Connection:
    kwargs = {}
    if timeout_config is not None:
        kwargs["timeout_config"] = timeout_config
    if lifecycle_config is not None:
        kwargs["lifecycle_config"] = lifecycle_config
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
        operation_metrics_config=metrics_type(enabled=enabled),
        **kwargs,
    )
```

Add monotonic polling helpers for lifecycle state, pool active count,
operation outcome delta and SQL request visibility. Reuse
`wait_for_sql_request` from `framework_apps.py` when its token semantics fit;
do not replace a server-visible gate with a scheduler sleep.

- [ ] **Step 3: Add `OPMET-001` exact public config contract**

Use the same runtime assertions as the no-network contract, but attach:

```python
@case("OPMET-001")
def test_operation_metrics_config_and_connection_copy_contract() -> None:
```

Assert exact bool validation, signature, repr, final constructor position,
isolated property copies and no setter. This case performs no SQL but is
collected in the strict contract as the public API authority.

- [ ] **Step 4: Add `OPMET-002` disabled success/error/cancellation**

Name the test:

```python
@case("OPMET-002")
@pytest.mark.asyncio
async def test_disabled_operation_metrics_remain_zero_across_work() -> None:
```

Create a disabled unique-application connection. Capture an exact zero
snapshot, execute `SELECT @P1`, provoke SQL error 208, then start:

```python
cancellation_token = unique_sql_name("strict_opmet_disabled_cancel")
query_task = asyncio.create_task(
    connection.query(
        f"""
        /* {cancellation_token} */
        WAITFOR DELAY '00:00:05';
        SELECT 1;
        """
    )
)
```

Wait until the request is visible through the privileged observer, cancel the
task and require `CancelledError`. Assert another success query recovers.

After all work:

```python
after = await connection.operation_stats()
assert_operation_stats(after, enabled=False)
assert after == before
after["operations"]["query"]["started"] = 999
assert await connection.operation_stats() == before
```

Disconnect in `finally` and require the unique application session count to
converge to zero.

- [ ] **Step 5: Add `OPMET-003` successful durations and buckets**

Name the test:

```python
@case("OPMET-003")
@pytest.mark.asyncio
async def test_successful_operations_record_durations_and_buckets() -> None:
```

Create the table through the observer so setup is outside measured deltas.
For an enabled connection:

```python
before = await connection.operation_stats()
assert await connection.connect() is True
assert await scalar(connection, "SELECT @P1", [3]) == 3
simple = await connection.simple_query(
    "WAITFOR DELAY '00:00:00.050'; SELECT 33"
)
assert simple.fetchone()[0] == 33
assert await connection.execute(
    f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
    [3, 30],
) == 1
after = await connection.operation_stats()
```

Require one success and no other outcome for each of `connect`, `query`,
`simple_query`, `execute`. Require `simple_query.duration_seconds_max >= 0.04`,
non-zero sum, and a cumulative bucket that reaches one at or above 0.25
seconds. Require all entries and global invariants after quiescence.

- [ ] **Step 6: Add `OPMET-004` returned errors and pre-future exclusion**

Name the test:

```python
@case("OPMET-004")
@pytest.mark.asyncio
async def test_errors_are_classified_and_preflight_is_excluded() -> None:
```

First capture the query entry, then require missing-table `SqlError` and:

```python
delta = operation_delta(before, after_sql_error, "query")
assert delta["started"] == delta["completed"] == 1
assert {key: delta[key] for key in zero_outcomes()} == zero_outcomes(
    errors=1
)
```

For synchronous exclusion, call:

```python
with pytest.raises(Exception):
    connection.query("SELECT @P1", [object()])
with pytest.raises(Exception):
    connection.execute_batch([("SELECT @P1", [object()])])
```

Do not await these calls: conversion rejects them before a Rust future exists.
Assert query/execute-batch entries did not change.

For a returned lifecycle error, start a server-visible one-second query, begin
`disconnect()`, wait for `ConnectionLifecycleState.CLOSING`, then await a new
query and require `ConnectionLifecycleError`. That rejected query increments
only `errors`. Await the original query and shutdown tasks; no exception or
session is abandoned.

- [ ] **Step 7: Add `OPMET-005` exact acquisition timeout**

Name the test:

```python
@case("OPMET-005")
@pytest.mark.asyncio
async def test_acquire_timeout_is_counted_once_and_recovers() -> None:
```

Use `max_size=1` and:

```python
TimeoutConfig(
    connect_timeout_secs=2.0,
    acquire_timeout_secs=0.20,
    operation_timeout_secs=2.0,
    transaction_timeout_secs=2.0,
    rollback_timeout_secs=1.0,
)
```

Begin one pooled transaction to reserve the lease and require
`pool_stats()["active_connections"] == 1`. Capture the query metric, then:

```python
with pytest.raises(fastmssql.OperationTimeoutError) as captured:
    await connection.query("SELECT @P1", [5])
assert captured.value.phase == "acquire"
assert captured.value.operation == "query"
```

Require query delta exactly one `timed_out`, no other outcome, zero in-flight.
Rollback/close the holder, execute a healthy query and prove the timeout count
does not increase again.

- [ ] **Step 8: Add `OPMET-006` operation and shutdown timeouts**

Name the test:

```python
@case("OPMET-006")
@pytest.mark.asyncio
async def test_operation_and_shutdown_timeouts_keep_exact_types() -> None:
```

For operation timeout, use an enabled connection with
`operation_timeout_secs=0.15`, run a one-second WAITFOR query and assert one
query `timed_out`.

For shutdown timeout, use a separate enabled connection with unbounded
operation timeout and:

```python
fastmssql.LifecycleConfig(
    shutdown_timeout_secs=0.15,
    force_timeout_secs=1.0,
)
```

Start a five-second query, wait until SQL Server sees it, and call
`disconnect()`. Require:

```python
with pytest.raises(fastmssql.ShutdownTimeoutError):
    await shutdown
```

The disconnect series increments exactly one `timed_out`. The forced query
returns its existing typed lifecycle failure and increments query `errors`,
not disconnect `timed_out`. A new-generation query succeeds and all sessions
clean up.

- [ ] **Step 9: Add `OPMET-007` server-confirmed cancellation**

Name the test:

```python
@case("OPMET-007")
@pytest.mark.asyncio
async def test_server_confirmed_cancellation_is_counted_and_retires() -> None:
```

Record the original `connection_id`, start a five-second query with a unique
comment, and wait for it in `sys.dm_exec_requests`. Cancel only then:

```python
query_task.cancel()
with pytest.raises(asyncio.CancelledError):
    await query_task
```

Poll until query `cancelled` delta is one. Require no query success/error/
timeout/unknown in that measured delta. Execute a recovery query, require a
different physical `connection_id`, zero active leases and zero application
sessions after disconnect.

---

### Task 4: Add RED transaction, lifecycle, batch, privacy and commit cases

**Files:**

- Modify: `tests/sql_auth_strict/test_operation_metrics.py`
- Modify: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**

- Consumes: canonical assertion helpers from Task 3.
- Produces: `OPMET-008..010`, `OPMET-012`, `OPMET-013`.

- [ ] **Step 1: Add `OPMET-008` pooled transaction aggregation**

Name the test:

```python
@case("OPMET-008")
@pytest.mark.asyncio
async def test_pooled_transaction_metrics_aggregate_into_owner_only() -> None:
```

Create tables through the observer. Use separate pooled transactions to cover:

```text
begin -> query -> simple_query -> execute -> query_batch ->
execute_batch -> commit -> close

begin -> execute -> rollback -> close

async with transaction -> execute -> automatic commit -> close

async with transaction -> execute -> raised application error ->
automatic rollback -> close
```

Capture the owner snapshot before/after and assert exact deltas for all nine
transaction operation names. Each public batch increments once regardless of
its item count. Check committed and rolled-back database effects exactly.

Create a standalone direct `fastmssql.Transaction(...)`, run begin/query/
execute/rollback/close, and assert the owner `Connection.operation_stats()`
snapshot remains byte-for-byte equal across that direct work.

- [ ] **Step 2: Add `OPMET-009` connection-lifetime generations**

Name the test:

```python
@case("OPMET-009")
@pytest.mark.asyncio
async def test_operation_metrics_persist_across_connection_generations() -> None:
```

Execute this exact sequence on one enabled object:

```python
assert await connection.connect(validate=False) is True
assert await connection.ping() is True
assert await connection.disconnect() is True
assert await scalar(connection, "SELECT 9") == 9  # lazy new generation
assert await connection.disconnect() is True
async with connection:
    assert await connection.ping() is True
assert await connection.disconnect() is False
```

Require final successful deltas:

```text
connect=2
ping=2
query=1
disconnect=4
```

The final no-resource disconnect is still one successful public call.
Readiness SQL inside connect/ping creates no query/simple-query metric. All
counters survive generation resets.

- [ ] **Step 3: Add `OPMET-010` whole-call batch and bulk semantics**

Name the test:

```python
@case("OPMET-010")
@pytest.mark.asyncio
async def test_batch_metrics_count_each_whole_public_call_once() -> None:
```

Create source/target tables through the observer. Capture metrics, then run:

```python
query_results = await connection.query_batch([
    ("SELECT @P1", [10]),
    ("SELECT @P1", [11]),
])
execute_results = await connection.execute_batch([
    (f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)", [10, 100]),
    (f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)", [11, 110]),
])
await connection.bulk_insert(
    raw_table_name,
    ["id", "value"],
    [[12, 120], [13, 130]],
)
```

Require exact results/database rows and one success delta each for
`query_batch`, `execute_batch`, `bulk_insert`. Require no internal
begin/commit/query/execute metric. Confirm the dedicated execute-batch session
does not prevent owner aggregation or leak after return.

- [ ] **Step 4: Add `OPMET-012` fixed-cardinality privacy**

Name the test:

```python
@case("OPMET-012")
@pytest.mark.asyncio
async def test_operation_metrics_snapshot_is_fixed_and_private() -> None:
```

Use a unique non-credential sentinel in application name, SQL comment and
parameter. Inspect:

```python
snapshot
repr(snapshot)
tuple(snapshot)
tuple(snapshot["operations"])
tuple(
    value
    for entry in snapshot["operations"].values()
    for value in entry.values()
)
repr(connection.operation_metrics_config)
```

Require only fixed keys and numeric/bool/None/list values. Check sentinel,
host, database and login are absent. For real passwords, avoid assertion
rewriting:

```python
rendered = repr(snapshot)
if sql_auth_config.owner_password in rendered:
    raise AssertionError("credential leaked into operation statistics")
```

Use sentinel `FASTMSSQL_OPMET_PRIVACY_SENTINEL_2026` so final artifact scans
never need to print a credential.

- [ ] **Step 5: Extend deterministic pooled commit ACK loss as `OPMET-013`**

Change the existing decorator:

```python
@case("TX-028", "OPMET-013")
```

Extend `_pooled_transaction_connection` with an optional
`operation_metrics_config` keyword forwarded to `Connection`. In TX-028,
construct:

```python
metrics_type = getattr(fastmssql, "OperationMetricsConfig")
connection = _pooled_transaction_connection(
    sql_auth_config,
    max_size=1,
    application_name=unique_sql_name("strict_commit_unknown_pool_app"),
    server=proxy.host,
    port=proxy.port,
    operation_metrics_config=metrics_type(enabled=True),
)
```

Capture the commit entry immediately before `committing.commit()`. Preserve
every existing fault-proxy, database durability, exception cause and physical
replacement assertion. After the typed error, require:

```python
delta = operation_delta(before, after, "commit")
assert delta["started"] == delta["completed"] == 1
assert {key: delta[key] for key in zero_outcomes()} == zero_outcomes(
    outcome_unknown=1
)
```

The timeout/I/O cause must not increment another outcome. Do not add a second
fault-proxy scenario.

---

### Task 5: Add RED concurrent load, framework and 99,999 stress contracts

**Files:**

- Modify: `tests/sql_auth_strict/test_resilience_load.py`
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`
- Modify: `tests/sql_auth_strict/conftest.py`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Create: `scripts/sql_auth/operation_metrics_stress.py`
- Create: `scripts/sql_auth/run_operation_metrics_stress.sh`

**Interfaces:**

- Produces: `OPMET-011`, `OPMET-014..016` and opt-in bounded performance
  evidence.

- [ ] **Step 1: Permit only the approved cross-lane metric IDs**

Change the load recorder predicate to:

```python
if not (
    case_id.startswith("LOAD-")
    or case_id in {"OBS-009", "OPMET-011"}
):
    raise ValueError(f"not a load case ID: {case_id}")
```

Change the framework recorder predicate to:

```python
if not (
    case_id.startswith("FRAME-")
    or case_id in {"OPMET-014", "OPMET-015", "OPMET-016"}
):
    raise ValueError(f"not a framework case ID: {case_id}")
```

Do not permit general `OBS-*` or `OPMET-*` prefixes.

- [ ] **Step 2: Add `OPMET-011` with bounded worker count**

In `test_resilience_load.py`, import the canonical assertion helper and add:

```python
@case("OPMET-011")
@pytest.mark.load
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_operation_metrics_survive_ten_thousand_queries_and_scrapes(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    operation_count = 10_000
    worker_count = 100
    max_size = 20
```

Create an enabled connection with a unique application name. Warm it and
capture `before`. Workers use deterministic strides:

```python
async def worker(worker_id: int) -> list[int]:
    values = []
    for value in range(worker_id, operation_count, worker_count):
        values.append(
            await scalar(
                connection,
                """
                IF @P1 < 100
                    WAITFOR DELAY '00:00:00.050';
                SELECT @P1;
                """,
                [value],
            )
        )
    return values
```

Only 100 worker tasks exist; never allocate 10,000 tasks. The first 100
server-side waits create a deterministic observation window for non-zero
in-flight work without changing the exact 10,000 public-call count.

- [ ] **Step 3: Scrape operation/pool stats and tick concurrently**

Use:

```python
async def scrape() -> tuple[int, int, int]:
    samples = 0
    maximum_in_flight = 0
    maximum_connections = 0
    while not stop.is_set():
        operation_stats = await connection.operation_stats()
        assert_operation_stats(operation_stats, enabled=True)
        pool_stats = await connection.pool_stats()
        samples += 1
        maximum_in_flight = max(
            maximum_in_flight,
            operation_stats["operations"]["query"]["in_flight"],
        )
        maximum_connections = max(
            maximum_connections,
            pool_stats["connections"],
        )
        await asyncio.sleep(0)
    return samples, maximum_in_flight, maximum_connections


async def tick() -> int:
    ticks = 0
    while not stop.is_set():
        ticks += 1
        await asyncio.sleep(0)
    return ticks
```

Await workers under 90 seconds. In `finally`, set the stop event and await both
observer tasks so no assertion failure is lost.

- [ ] **Step 4: Assert exact load deltas and teardown**

Require:

```python
assert sorted(values) == list(range(operation_count))
delta = operation_delta(before, final, "query")
assert delta["started"] == delta["completed"] == operation_count
assert delta["succeeded"] == operation_count
assert all(
    delta[key] == 0
    for key in ("errors", "timed_out", "cancelled", "outcome_unknown")
)
assert final["operations"]["query"]["in_flight"] == 0
assert samples > 0
assert maximum_in_flight > 0
assert maximum_in_flight <= worker_count
assert maximum_connections <= max_size
assert ticks > 10
```

Run a post-load health query after capturing the measured final snapshot.
Disconnect and require zero unique application sessions. Record elapsed,
queries/second, scrapes, ticks, max in-flight, max connections and final
histogram under `OPMET-011`.

- [ ] **Step 5: Allow framework states to opt in without changing defaults**

Add `operation_metrics_config=None` to `FrameworkState.create`. Forward it only
when non-None:

```python
if operation_metrics_config is not None:
    connection_kwargs["operation_metrics_config"] = (
        operation_metrics_config
    )
```

Existing fixtures pass nothing and remain default-off.

- [ ] **Step 6: Add `OPMET-014` FastAPI/native ASGI**

Name the test:

```python
@case("OPMET-014")
@pytest.mark.asyncio
async def test_fastapi_operation_metrics_are_exact_under_concurrency() -> None:
```

Create a dedicated enabled `FrameworkState`, build the FastAPI app and enter
`LifespanManager`. Capture stats after startup connect, then issue 100
concurrent `/wait/{value}?profile=none` requests with 20 bounded worker tasks.
Run the existing event-loop ticker concurrently.

Require exact returned values, query success delta 100, zero other outcomes,
zero in-flight, ticker progress and pool maximum. After lifespan exit, require
one successful disconnect delta and zero application sessions. Record only
numeric/bool evidence under `OPMET-014`.

- [ ] **Step 7: Add `OPMET-015` Flask async under WSGI**

Name the test:

```python
@case("OPMET-015")
@pytest.mark.asyncio
async def test_flask_wsgi_operation_metrics_preserve_loop_limits() -> None:
```

Create a dedicated enabled state and `create_flask_app`. Send two `/loop`
requests and 40 `/value/{value}` requests through `flask_request`, bounded by
10 worker tasks. Capture before/after around requests.

Require:

```text
query successes = 42
errors/timeouts/cancelled/unknown = 0
two /loop requests used distinct event loops
all values exact
```

Explicitly disconnect, require zero sessions and record
`execution_model="WSGI per-request event loop"` plus numeric counts under
`OPMET-015`.

- [ ] **Step 8: Add `OPMET-016` Flask through WsgiToAsgi**

Name the test:

```python
@case("OPMET-016")
@pytest.mark.asyncio
async def test_flask_asgi_operation_metrics_use_persistent_loop() -> None:
```

Use `adapted_flask_lifespan` with an enabled state. Issue two `/loop` requests
and 40 bounded concurrent `/value/{value}` requests through `asgi_client`.
Require exact query success delta 42, no other outcomes, non-zero ticker
progress and the same loop object for both loop routes. After lifespan exit,
require disconnect success and zero sessions. Record
`execution_model="persistent ASGI loop around Flask/WSGI"` under
`OPMET-016`.

- [ ] **Step 9: Create the bounded operation-metrics stress program**

`operation_metrics_stress.py` must:

```text
accept --operations, --workers, --pool-size, --pairs,
       --maximum-median-degradation, --source-sha, --metrics-output
bound operations to 1..99,999
bound workers and pool size to 1..500
require pairs == 3 for the approved gate
require source SHA as exactly 40 lowercase hexadecimal characters
run mode order [(disabled, enabled), (enabled, disabled),
                (disabled, enabled)]
use one untimed warm-up per fresh Connection
run 99,999 parameterized one-row SELECT operations per timed trial
use exactly 200 bounded workers and max_size=100 by default
perform no operation_stats scrape during the timed interval
verify exact result count and arithmetic sum
verify enabled query metric delta == 99,999 successes
verify disabled snapshot remains exact zero
verify pool/session maxima <= 100 and event-loop ticker > 10
run a post-load health query after timing
disconnect and wait for zero application sessions between trials
write partial evidence after every completed trial
calculate each paired degradation exactly as approved
require median degradation <= 0.15
write schema_version, source_sha, non-identifying runtime metadata, trial order,
throughput, ratios and zero-session evidence without credentials
```

Use `statistics.median`, `time.perf_counter`, `asyncio.TaskGroup` and a unique
application name per trial. Repeat the exact fixed schema constants and
invariants locally in the standalone script; it must not import from
`tests/sql_auth_strict`, because that test package is not on the script
runner's import path. `test_matrix_contract.py` compares the script constants
to the canonical test constants. The script exits non-zero after writing
evidence when the median gate fails.

Runtime metadata is limited to platform family/release, Python/Rust-wheel
version, logical CPU count and total memory. Do not record hostname, OS user,
paths, environment variables, database/login/application names or connection
settings.

- [ ] **Step 10: Create the opt-in shell wrapper**

`run_operation_metrics_stress.sh` uses:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly source_sha="$(git -C "${root_dir}" rev-parse HEAD)"

cd "${root_dir}"
test -f "${env_file}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a
mkdir -p "${artifact_dir}"

uv run python scripts/sql_auth/operation_metrics_stress.py \
  --operations 99999 \
  --workers 200 \
  --pool-size 100 \
  --pairs 3 \
  --maximum-median-degradation 0.15 \
  --source-sha "${source_sha}" \
  --metrics-output \
    "${artifact_dir}/operation-metrics-stress.json"
```

Make and verify it executable:

```bash
chmod +x scripts/sql_auth/run_operation_metrics_stress.sh
test -x scripts/sql_auth/run_operation_metrics_stress.sh
```

It remains opt-in and is not added to `run_all.sh`.

- [ ] **Step 11: Strengthen orchestration static contracts**

In `test_matrix_contract.py`:

- assert both stress files exist and the shell file is executable;
- assert `99_999`, three pairs, 200 workers, pool 100 and 0.15 gate appear;
- run the Python script with `--help` and require exit zero;
- assert the wrapper is absent from `run_all.sh`;
- route `OPMET-011` to `pytest.mark.load`;
- route `OPMET-014..016` to `pytest.mark.framework`;
- require source registration exactly once for all 337 IDs.

Run:

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: exact 337-ID/source/lane contract.

---

### Task 6: Prove RED, self-review tests and create the GREEN worktree

**Files:** every RED file from Tasks 2–5.

**Interfaces:**

- Produces: pushed `test/operation-metrics` with failures caused only by the
  absent public/registry contract.
- Produces: isolated `feat/operation-metrics` from the exact RED head.

- [ ] **Step 1: Build the unchanged baseline extension**

Run:

```bash
uv sync --locked --all-extras --dev
uv run maturin develop --release
```

- [ ] **Step 2: Prove the no-network contract is RED**

Run:

```bash
uv run pytest tests/test_operation_metrics_contract.py -q
```

Expected: failures identify absent `OperationMetricsConfig`,
`operation_metrics_config`, `operation_stats` and source module. No network,
syntax, import-environment or plugin failure is accepted.

- [ ] **Step 3: Prove focused real-SQL behavior is RED**

Start/provision only the approved container:

```bash
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
uv run pytest tests/sql_auth_strict/test_operation_metrics.py -vv
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k pooled_commit_ack_loss -vv
uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -k operation_metrics_survive_ten_thousand_queries_and_scrapes -vv
uv run pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k operation_metrics -vv
```

Expected:

```text
existing selected non-OPMET assertions remain green
new cases fail on absent config/method/registry behavior
no case passes because SQL setup failed
no cleanup exception, task or SQL session is lost
```

- [ ] **Step 4: Perform the RED test self-review**

Inspect the diff against the design branch and require:

```text
16 IDs each occur in exactly one source decorator
all snapshot checks use exact key sets
all outcomes are mutually exclusive
all cancellation/timeout gates are server/pool/lifecycle visible
all polling uses monotonic bounded deadlines
all started tasks are awaited in success and cleanup paths
all SQL objects/sessions are cleaned in finally
no credential can enter assertion rewriting or artifact values
10,000 operations use only 100 workers
99,999 stress uses only 200 workers and is opt-in
framework WSGI and ASGI semantics remain distinct
no skip, xfail, retry or swallowed test exception
hosted wheel contract covers every OS
all inherited IDs/assertions remain present
```

Run:

```bash
git diff --check
git diff --stat docs/operation-metrics-design...HEAD
git status --short
```

- [ ] **Step 5: Commit RED in reviewable slices**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/test_operation_metrics_contract.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_pyo3_build_contract.py \
  scripts/sql_auth/run_all.sh \
  .github/workflows/rust-unit-tests.yml
git commit -m "test: define operation metrics contract"

git add \
  tests/sql_auth_strict/operation_metrics_assertions.py \
  tests/sql_auth_strict/test_operation_metrics.py \
  tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: reproduce missing operation metrics"

git add \
  tests/sql_auth_strict/conftest.py \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py \
  tests/sql_auth_strict/test_resilience_load.py \
  scripts/sql_auth/operation_metrics_stress.py \
  scripts/sql_auth/run_operation_metrics_stress.sh
git commit -m "test: add operation metrics load gates"

git push -u origin test/operation-metrics
```

- [ ] **Step 6: Create the GREEN worktree**

From the main repository:

```bash
git worktree add .worktrees/feat-operation-metrics \
  -b feat/operation-metrics test/operation-metrics
```

Link the ignored SQL-auth environment exactly as Task 1, verify the RED SHA is
an ancestor and run `cargo test --locked`.

---

### Task 7: Implement config and the fixed atomic registry through Rust TDD

**Files:**

- Create: `src/operation_metrics_config.rs`
- Create: `src/operation_metrics.rs`
- Modify: `src/lib.rs`

**Interfaces:**

- Produces:
  `PyOperationMetricsConfig`,
  `OperationMetricsRegistry`,
  `OperationMetricsSnapshot`,
  `observe_operation`.
- Consumed later by connection, transaction and batch instrumentation.

- [ ] **Step 1: Read the TDD skill and run graph-first impact analysis**

Read `superpowers:test-driven-development` completely. Then run:

```bash
uvx code-review-graph build
uvx code-review-graph status
```

Inspect callers/ownership for `OperationName`, every `future_into_py`,
`PyConnection::transaction`, `Transaction::from_pool`, public batch helpers,
timeout exception types and Python exports.

- [ ] **Step 2: Add failing Rust tests for config and stable mapping**

Before implementation, declare module-local tests that require:

```rust
#[test]
fn operation_metrics_config_defaults_disabled() {
    let config = PyOperationMetricsConfig::default();
    assert!(!config.enabled);
}

#[test]
fn active_operation_mapping_is_stable_and_complete() {
    let names: Vec<_> = METRIC_OPERATIONS
        .iter()
        .map(|operation| operation.as_str())
        .collect();
    assert_eq!(
        names,
        vec![
            "connect", "ping", "query", "simple_query", "execute",
            "query_batch", "execute_batch", "bulk_insert", "begin",
            "commit", "rollback", "close", "disconnect",
        ]
    );
    assert_eq!(metric_index(OperationName::Transaction), None);
}
```

Run:

```bash
cargo test --locked operation_metrics -- --nocapture
```

Expected: compile/test failure because the modules/types do not exist.

- [ ] **Step 3: Implement exact default-off PyO3 config**

Create:

```rust
use pyo3::prelude::*;

#[pyclass(name = "OperationMetricsConfig", from_py_object)]
#[derive(Clone, Debug, Default)]
pub struct PyOperationMetricsConfig {
    pub(crate) enabled: bool,
}

#[pymethods]
impl PyOperationMetricsConfig {
    #[new]
    #[pyo3(
        signature = (enabled = false),
        text_signature = "(enabled=False)"
    )]
    fn new(enabled: bool) -> Self {
        Self { enabled }
    }

    #[getter]
    fn enabled(&self) -> bool {
        self.enabled
    }

    #[setter]
    fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
    }

    fn __repr__(&self) -> String {
        let enabled = if self.enabled { "True" } else { "False" };
        format!("OperationMetricsConfig(enabled={enabled})")
    }
}
```

Register module/export/class in `src/lib.rs`. PyO3 0.29's `bool` extractor
rejects non-bool values with `TypeError`; do not add truthiness coercion.

- [ ] **Step 4: Add failing tests for bucket and saturation algorithms**

Add tests that manually record these elapsed durations:

```text
99 us, 100 us, 101 us, 250 us, 30 s, 30 s + 1 us
```

Require exact finite cumulative counts and `completed == 6`. Add near-maximum
counter/sum tests that prove saturation, no wrap, coherent arithmetic and a
permanently true `saturated` flag. Add a cancellation-guard Drop test that
produces one cancelled outcome.

Run focused tests; expect failure until the registry exists.

- [ ] **Step 5: Implement fixed constants, mapping and saturating helpers**

In `operation_metrics.rs`, define:

```rust
pub(crate) const METRIC_OPERATIONS: [OperationName; 13] = [
    OperationName::Connect,
    OperationName::Ping,
    OperationName::Query,
    OperationName::SimpleQuery,
    OperationName::Execute,
    OperationName::QueryBatch,
    OperationName::ExecuteBatch,
    OperationName::BulkInsert,
    OperationName::Begin,
    OperationName::Commit,
    OperationName::Rollback,
    OperationName::Close,
    OperationName::Disconnect,
];

const BUCKET_BOUNDS_MICROS: [u64; 17] = [
    100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000,
    50_000, 100_000, 250_000, 500_000, 1_000_000,
    2_500_000, 5_000_000, 10_000_000, 30_000_000,
];
```

Implement `metric_index` as one exhaustive `match` returning `None` only for
reserved `Transaction`. Implement saturating atomic add via
`AtomicU64::fetch_update`; set `AtomicBool saturated` whenever checked
addition/conversion cannot fit. Never use wrapping arithmetic.

- [ ] **Step 6: Implement registry entry and coherent snapshot**

Use:

```rust
struct OperationMetric {
    started: AtomicU64,
    outcomes: [AtomicU64; 5],
    duration_sum_micros: AtomicU64,
    duration_min_micros: AtomicU64,
    duration_max_micros: AtomicU64,
    raw_buckets: [AtomicU64; 17],
    saturated: AtomicBool,
}

pub(crate) struct OperationMetricsRegistry {
    operations: [OperationMetric; 13],
}
```

Initialize min to `u64::MAX`; all other atomics to zero. `record` must:

```text
convert elapsed to saturated whole microseconds
update sum before max
fetch_min/fetch_max
increment only the first finite bound containing the unrounded Duration
publish exactly one outcome with Release ordering
```

Snapshot loads outcomes with Acquire, saturating-sums `completed`, reconciles
`started=max(raw_started, completed)`, derives in-flight, builds cumulative
buckets and clamps them to completed. For completed zero, return
`0.0/None/None/zero buckets`. Otherwise reconcile `min <= max <= sum`.

- [ ] **Step 7: Implement RAII observation and typed error classification**

Use an owning guard:

```rust
struct OperationGuard {
    registry: Arc<OperationMetricsRegistry>,
    operation_index: usize,
    started_at: Instant,
    armed: bool,
}
```

`Drop` records `Cancelled` only while armed. `finish` records a supplied
outcome and disarms.

Implement:

```rust
pub(crate) async fn observe_operation<F, T>(
    metrics: Option<Arc<OperationMetricsRegistry>>,
    operation: OperationName,
    future: F,
) -> PyResult<T>
where
    F: Future<Output = PyResult<T>>,
{
    match metrics {
        None => future.await,
        Some(registry) => {
            let Some(operation_index) = metric_index(operation) else {
                return future.await;
            };
            let started_at = Instant::now();
            let guard = OperationGuard::start(
                registry,
                operation_index,
                started_at,
            );
            let result = future.await;
            let elapsed = guard.elapsed();
            let outcome = classify_result(&result);
            guard.finish(outcome, elapsed);
            result
        }
    }
}
```

The literal `None => future.await` must precede `Instant::now()` in this
function. The reserved `Transaction` variant takes the unchanged future path
without a clock, atomic update or panic. Elapsed time is captured immediately
after the original future returns, before Python exception classification or
metric publication, so observer overhead is outside the reported duration.
`classify_result` checks `CommitOutcomeUnknown` first, then timeout classes,
only while handling `Err`. It returns `Errors` for all other exceptions. Do
not inspect messages/attributes or causes.

- [ ] **Step 8: Render fresh Python snapshots**

`OperationMetricsSnapshot::to_python(py)` builds one fresh root `PyDict`, one
fresh bounds `PyList`, one operations `PyDict`, 13 entry dicts and 13 fresh
bucket lists. Convert microseconds using `/ 1_000_000.0`.

Provide:

```rust
OperationMetricsSnapshot::disabled()
OperationMetricsRegistry::snapshot()
```

Neither performs SQL, locks, callbacks or metric updates.

- [ ] **Step 9: Run focused Rust GREEN and commit the core**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked operation_metrics -- --nocapture
cargo test --locked
```

Then:

```bash
git add \
  src/operation_metrics_config.rs \
  src/operation_metrics.rs \
  src/lib.rs
git commit -m "feat: add bounded operation metrics registry"
```

---

### Task 8: Instrument Connection paths and expose the public snapshot

**Files:**

- Modify: `src/connection.rs`

**Interfaces:**

- Consumes: registry/config/observer from Task 7.
- Produces: enabled registry ownership, config copy, `operation_stats()` and
  instrumentation for all connection-owned non-batch operations.

- [ ] **Step 1: Run the no-network contract to confirm remaining RED**

Build and run:

```bash
uv run maturin develop --release
uv run pytest tests/test_operation_metrics_contract.py -q
```

Expected: config export may pass, but constructor/property/snapshot tests
remain RED.

- [ ] **Step 2: Append constructor policy without positional breakage**

Add fields:

```rust
operation_metrics_config: PyOperationMetricsConfig,
operation_metrics: Option<Arc<OperationMetricsRegistry>>,
```

Append to PyO3 signature and Rust arguments:

```rust
operation_metrics_config: Option<PyOperationMetricsConfig>
```

Build:

```rust
let operation_metrics_config =
    operation_metrics_config.unwrap_or_default();
let operation_metrics = operation_metrics_config
    .enabled
    .then(|| Arc::new(OperationMetricsRegistry::new()));
```

Store both. Do not place the registry inside `ConnectionHandles`; each public
method clones exactly one optional owner `Arc` separately so disabled handles
remain unchanged.

- [ ] **Step 3: Add isolated config property and operation snapshot method**

Add:

```rust
#[getter]
pub fn operation_metrics_config(&self) -> PyOperationMetricsConfig {
    self.operation_metrics_config.clone()
}
```

Add async `operation_stats()` that clones only the optional registry, snapshots
inside the Rust future, attaches Python only to create the fresh dictionaries,
and returns the disabled fixed schema when `None`. It never passes through
`observe_operation`.

- [ ] **Step 4: Wrap query/simple-query/execute**

For each method, keep synchronous parameter conversion before
`future_into_py`. Clone:

```rust
let metrics = self.operation_metrics.clone();
```

Inside the bridge:

```rust
observe_operation(metrics, OperationName::Query, async move {
    // exact existing body, unchanged
})
.await
```

Repeat with exact operation names. Do not create a nested metric around
`execute_query_async_gil_free` or readiness helpers.

- [ ] **Step 5: Wrap connect/ping/context/disconnect**

Instrument public async bodies exactly once:

```text
connect -> Connect
__aenter__ -> Connect
ping -> Ping
disconnect -> Disconnect
__aexit__ -> Disconnect
```

Repeated/no-resource disconnect `Ok(false)` is a success. Internal readiness
and lifecycle shutdown helpers receive no observer.

- [ ] **Step 6: Pass registry ownership to transaction and batch entry points**

`Connection.transaction()` passes `self.operation_metrics.clone()` as the final
`Transaction::from_pool` argument.

Pass a cloned optional registry to public `query_batch`, `execute_batch` and
`bulk_insert` helpers. These helpers will remain RED until Task 9 wraps their
futures.

- [ ] **Step 7: Run focused Connection GREEN**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest tests/test_operation_metrics_contract.py -q
uv run pytest \
  tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_config_and_connection_copy_contract \
  tests/sql_auth_strict/test_operation_metrics.py::test_disabled_operation_metrics_remain_zero_across_work \
  tests/sql_auth_strict/test_operation_metrics.py::test_successful_operations_record_durations_and_buckets \
  tests/sql_auth_strict/test_operation_metrics.py::test_errors_are_classified_and_preflight_is_excluded \
  tests/sql_auth_strict/test_operation_metrics.py::test_acquire_timeout_is_counted_once_and_recovers \
  tests/sql_auth_strict/test_operation_metrics.py::test_operation_and_shutdown_timeouts_keep_exact_types \
  tests/sql_auth_strict/test_operation_metrics.py::test_server_confirmed_cancellation_is_counted_and_retires \
  tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_persist_across_connection_generations \
  -vv
```

Expect those selected IDs green and transaction/batch IDs still RED.

- [ ] **Step 8: Commit Connection instrumentation**

Run:

```bash
git diff --check
git add src/connection.rs
git commit -m "feat: instrument connection operation metrics"
```

---

### Task 9: Instrument pooled transactions and public batch calls

**Files:**

- Modify: `src/transaction.rs`
- Modify: `src/batch.rs`

**Interfaces:**

- Consumes: optional owner registry from `Connection`.
- Produces: exact owner aggregation for pooled transaction and batch calls;
  direct transaction remains `None`.

- [ ] **Step 1: Add optional registry only to pooled Transaction ownership**

Add to `Transaction`:

```rust
operation_metrics: Option<Arc<OperationMetricsRegistry>>,
```

The direct public constructor initializes it to `None`.
`Transaction::from_pool` receives and stores the optional registry.
`TransactionHandles` remains metric-free; each public transaction method
clones `self.operation_metrics` separately.

- [ ] **Step 2: Wrap transaction data methods once**

Wrap the entire existing async body for:

```text
query -> Query
simple_query -> SimpleQuery
execute -> Execute
query_batch -> QueryBatch
execute_batch -> ExecuteBatch
```

Synchronous conversion remains before observer creation. Preserve inner
`TransactionCancellationGuard` unchanged; dropping the outer observer counts
cancellation while the inner guard retains its transport-retirement duty.

- [ ] **Step 3: Wrap transaction settlement/lifecycle methods once**

Wrap:

```text
begin -> Begin
commit -> Commit
rollback -> Rollback
close -> Close
```

Instrument the final top-level `PyResult` after existing commit-unknown
conversion. Do not instrument `execute_transaction_command` itself, because
that would double-count public methods and internal calls.

- [ ] **Step 4: Extend batch signatures and wrap whole futures**

Add final or adjacent internal parameters:

```rust
operation_metrics: Option<Arc<OperationMetricsRegistry>>
```

to `query_batch`, `execute_batch`, `bulk_insert`. Keep all synchronous list,
parameter, identifier and row conversion before `future_into_py`.

Wrap each existing future once with the matching public operation. Internal
batch elements, BEGIN/COMMIT/ROLLBACK cleanup SQL and dedicated connection
setup do not get separate metrics.

- [ ] **Step 5: Run transaction/batch GREEN and fault proxy**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest \
  tests/sql_auth_strict/test_operation_metrics.py::test_pooled_transaction_metrics_aggregate_into_owner_only \
  tests/sql_auth_strict/test_operation_metrics.py::test_batch_metrics_count_each_whole_public_call_once \
  tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_snapshot_is_fixed_and_private \
  -vv
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k pooled_commit_ack_loss -vv
```

Expected: `OPMET-008`, `010`, `012`, `013` pass with exact database,
retirement and outcome assertions.

- [ ] **Step 6: Self-review ownership and commit**

Review:

```text
direct Transaction constructor always stores None
only from_pool receives owner registry
one observer per public method
no internal SQL double-count
no observer changes cancellation guard order or lease cleanup
commit classification sees final CommitOutcomeUnknown
batch preflight remains excluded
dedicated execute-batch remains owner-counted
```

Then:

```bash
git diff --check
git add src/transaction.rs src/batch.rs
git commit -m "feat: aggregate pooled transaction operation metrics"
```

---

### Task 10: Publish Python/docs API and turn every focused RED case GREEN

**Files:**

- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `README.md`

**Interfaces:**

- Produces: complete public/export/type/documentation contract.

- [ ] **Step 1: Export config and wrapper accessors**

Import `OperationMetricsConfig` from the compiled module and add it to
`__all__`. Add:

```python
async def operation_stats(self):
    """Return fixed connection-lifetime operation metrics without SQL."""
    return await self._conn.operation_stats()

@property
def operation_metrics_config(self):
    """Return an isolated copy of the metrics policy."""
    return self._conn.operation_metrics_config
```

Do not cache/mutate snapshots in Python.

- [ ] **Step 2: Add exact private TypedDicts to both stubs**

Import `Literal` and `TypedDict`. Write every field exactly as listed in the
schema section. Add `OperationMetricsConfig`, final constructor argument,
property and method. Add the class to top-level import/`__all__`.

The direct `Transaction` stub receives no config or stats method.

- [ ] **Step 3: Document operational semantics in README**

Add an opt-in example:

```python
metrics = OperationMetricsConfig(enabled=True)
connection = Connection(
    connection_string,
    operation_metrics_config=metrics,
)
stats = await connection.operation_stats()
query = stats["operations"]["query"]
print(query["succeeded"], query["timed_out"])
```

Document:

```text
default-off/no registry/no clock or atomics
13 fixed operations and 17 fixed bounds
five mutually exclusive outcomes and their priority
end-to-end Rust async-body timing boundary
connection lifetime across reconnect generations
pooled transaction aggregation/direct transaction exclusion
weakly consistent concurrent snapshot/exact after quiescence
completed as +Inf histogram bucket
saturation behavior
no reset/runtime toggle
pull-only/no exporter/no labels/privacy
Flask WSGI limitation remains an application execution-model concern
```

- [ ] **Step 4: Run installed/public contract GREEN**

Run:

```bash
uv run maturin develop --release
uv run pytest \
  tests/test_operation_metrics_contract.py \
  tests/test_pyo3_build_contract.py -q
```

- [ ] **Step 5: Run every focused SQL-auth/framework/load case GREEN**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_operation_metrics.py -vv
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  -k pooled_commit_ack_loss -vv
uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -k operation_metrics_survive_ten_thousand_queries_and_scrapes -vv
uv run pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k "operation_metrics" -vv
```

Expected: all `OPMET-001..016` pass; zero skip/xfail/leaked session.

- [ ] **Step 6: Commit public contract and push feature**

Run:

```bash
git diff --check
git add \
  python/fastmssql/__init__.py \
  python/fastmssql/fastmssql.pyi \
  python/fastmssql/__init__.pyi \
  README.md
git commit -m "docs: publish operation metrics contract"
git push -u origin feat/operation-metrics
```

RED tests remain inherited commits and are not amended into GREEN commits.

---

### Task 11: Run complete local enterprise, stress and self-review gates

**Files produced by commands:**

- `.artifacts/sql-auth/*` — ignored raw evidence.
- `docs/SQL_AUTH_TEST_MATRIX.md` and `docs/SQL_AUTH_TEST_REPORT.md` — generated
  locally but not committed on the feature branch.

**Interfaces:**

- Produces: exact local evidence tied to feature SHA.

- [ ] **Step 1: Run source-quality and dependency-security gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run ruff check .
uv run python -m compileall -q python tests scripts/sql_auth
scripts/security/audit_dependencies.sh
```

Require zero failures, warnings and denied security findings.

- [ ] **Step 2: Run the complete SQL-auth harness**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Require:

```text
strict 337/337 complete PASS evidence
true-async lane PASS
FastAPI/native ASGI PASS
Flask async under WSGI PASS with its limitation stated
Flask via persistent WsgiToAsgi loop PASS
resilience PASS
load PASS including OPMET-011
applicable upstream regression PASS
report completeness PASS
every harness lane exit code zero
```

- [ ] **Step 3: Run all established transaction stress profiles**

Run persistent and pooled strategies:

```bash
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
FASTMSSQL_TRANSACTION_STRESS_STRATEGY="persistent" \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE="100" \
scripts/sql_auth/run_transaction_stress.sh

FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
FASTMSSQL_TRANSACTION_STRESS_STRATEGY="pooled" \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE="100" \
scripts/sql_auth/run_transaction_stress.sh
```

Require exact commit/rollback totals, bounded sessions, event-loop progress,
zero failures/leaks and healthy smoke queries.

- [ ] **Step 4: Run the interleaved operation-metrics overhead gate**

Run:

```bash
scripts/sql_auth/run_operation_metrics_stress.sh
```

Inspect `.artifacts/sql-auth/operation-metrics-stress.json` without printing
credentials. Require:

```text
source_sha == feature HEAD
six trials in approved alternating order
99,999 exact results per trial
enabled metric delta 99,999 successes per enabled trial
disabled snapshots zero
workers 200, pool 100
median paired degradation <= 0.15
event-loop progress each trial
maximum SQL sessions <= 100
zero remaining candidate sessions each trial
post-load smoke PASS
```

- [ ] **Step 5: Build and verify an isolated ABI3 wheel**

Run:

```bash
uv run maturin build --release --locked --out dist
wheel_path="$(find dist -maxdepth 1 -name 'fastmssql-*.whl' -print -quit)"
test -n "${wheel_path}"
wheel_root="$(mktemp -d)"
python3 -m venv "${wheel_root}/venv"
"${wheel_root}/venv/bin/pip" install pytest==9.1.1 "${wheel_path}"
"${wheel_root}/venv/bin/python" -m pytest --noconftest \
  tests/test_pool_config_default_contract.py \
  tests/test_timeout_config_contract.py \
  tests/test_lifecycle_contract.py \
  tests/test_pool_observability_contract.py \
  tests/test_operation_metrics_contract.py -q
```

- [ ] **Step 6: Scan evidence for the fixed privacy sentinel**

Run:

```bash
if rg -l "FASTMSSQL_OPMET_PRIVACY_SENTINEL_2026" \
  .artifacts/sql-auth \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md; then
  printf 'operation-metrics privacy sentinel leaked into evidence\n' >&2
  exit 1
fi
```

Use the existing credential-redaction contract. Never put an actual password
on a command line or in expected output.

- [ ] **Step 7: Rebuild graph and perform implementation self-review**

Run:

```bash
uvx code-review-graph build
uvx code-review-graph status
git diff --check docs/operation-metrics-design...HEAD
git status --short --branch
git log --oneline --decorate docs/operation-metrics-design..HEAD
```

Review line-by-line:

```text
disabled match branch precedes Instant/atomic access
no registry allocation when disabled
13 operations/17 bounds in one canonical Rust source
no Transaction reserved variant accidentally mapped
all arithmetic saturates and never wraps
duration writes precede Release outcome publication
snapshot Acquire/reconciliation preserves every invariant
completed zero hides unpublished duration
fresh Python containers prevent mutation aliasing
success/cancellation paths never attach Python
error classification uses exact public types and priority
one observer around every public call, none around internal SQL
pre-future validation remains excluded
pooled transactions share owner Arc; direct transaction stores None
registry persists across lifecycle generations
no reset/toggle/global registry
no SQL/identifier/error/credential labels or values
no callback/exporter/dependency/lockfile change
all RED assertions remain exact
no skip/xfail/retry/swallowed test failure
no upstream write target
```

- [ ] **Step 8: Restore only generated feature-worktree reports**

After recording successful results/hashes, restore only runner-generated
tracked reports:

```bash
git restore --source=HEAD -- \
  docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git status --short --branch
```

Do this only if those are the sole uncommitted tracked changes. Preserve and
investigate any other change.

---

### Task 12: Run hosted Linux/macOS/Windows and RustSec gates

**Interfaces:**

- Produces: hosted evidence tied to exact feature SHA.

- [ ] **Step 1: Push and verify exact feature SHA**

Run:

```bash
git push origin feat/operation-metrics
feature_sha="$(git rev-parse HEAD)"
remote_sha="$(
  git ls-remote origin refs/heads/feat/operation-metrics |
  awk '{print $1}'
)"
test "${remote_sha}" = "${feature_sha}"
```

- [ ] **Step 2: Dispatch and watch cross-platform Rust/wheel workflow**

Run:

```bash
gh workflow run rust-unit-tests.yml \
  --repo galeamarcel/FastMssql \
  --ref feat/operation-metrics
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch feat/operation-metrics \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${rust_run_id}"
gh run watch "${rust_run_id}" \
  --repo galeamarcel/FastMssql --exit-status
gh run view "${rust_run_id}" \
  --repo galeamarcel/FastMssql \
  --json headSha,status,conclusion,jobs
```

Require Ubuntu, macOS and Windows:

```text
raw Cargo build PASS
all Rust tests PASS
ABI3 wheel build/install PASS
all five installed contracts PASS
headSha == feature_sha
```

- [ ] **Step 3: Dispatch and watch dependency security**

Run:

```bash
gh workflow run dependency-security.yml \
  --repo galeamarcel/FastMssql \
  --ref feat/operation-metrics
security_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch feat/operation-metrics \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${security_run_id}"
gh run watch "${security_run_id}" \
  --repo galeamarcel/FastMssql --exit-status
gh run view "${security_run_id}" \
  --repo galeamarcel/FastMssql \
  --json headSha,status,conclusion
```

Require exact feature SHA, zero vulnerabilities and zero warnings.

- [ ] **Step 4: Confirm hosted repository boundary**

Run:

```bash
git remote get-url --push origin
git remote get-url --push upstream
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/operation-metrics \
  --state all \
  --json number,url,state
```

Expected:

```text
origin push is galeamarcel/FastMssql
upstream push is DISABLED
upstream PR list is []
```

---

### Task 13: Integrate the exact technical candidate and reverify

**Interfaces:**

- Produces: one technical merge in `test/sql-auth-validation`, verified from a
  clean exact-SHA worktree before status prose.

- [ ] **Step 1: Record exact ancestry**

Run:

```bash
design_sha="$(git rev-parse origin/docs/operation-metrics-design)"
red_sha="$(git rev-parse origin/test/operation-metrics)"
feature_sha="$(git rev-parse origin/feat/operation-metrics)"
git merge-base --is-ancestor "${design_sha}" "${red_sha}"
git merge-base --is-ancestor "${red_sha}" "${feature_sha}"
```

- [ ] **Step 2: Merge only into the cumulative fork branch**

In the clean cumulative worktree:

```bash
git switch test/sql-auth-validation
git pull --ff-only origin test/sql-auth-validation
git merge --no-ff feat/operation-metrics \
  -m "merge: add operation duration and outcome metrics"
technical_merge_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
```

Never merge or push to `upstream/master`.

- [ ] **Step 3: Create detached exact-verification worktree**

Run:

```bash
git worktree add --detach \
  .worktrees/verify-operation-metrics "${technical_merge_sha}"
```

Link the ignored environment, then require clean status and exact HEAD.

- [ ] **Step 4: Re-run exact-merge source and SQL-auth gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest tests/test_operation_metrics_contract.py -q
scripts/sql_auth/run_all.sh
```

Require 337/337 and every complete lane. These reports are the authoritative
status inputs.

- [ ] **Step 5: Re-run exact-merge operation stress**

Run:

```bash
scripts/sql_auth/run_operation_metrics_stress.sh
```

Require artifact `source_sha == technical_merge_sha` and the same 0.15 median
gate. Also run the established 10,000/99,999 persistent and pooled transaction
profiles if the technical merge tree differs from feature.

If the merge adds no other technical tree change, prove:

```bash
git diff --exit-code "${feature_sha}" "${technical_merge_sha}" -- \
  src python tests scripts .github README.md \
  Cargo.toml Cargo.lock pyproject.toml
```

The exact-merge SQL-auth harness and operation stress remain mandatory even
when tree content is identical.

---

### Task 14: Record live audit status and finish the cumulative fork

**Files:**

- Regenerate: `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Produces: pushed `docs/operation-metrics-status`, documentation-only merge,
  fork parity and explicit next limitation.

- [ ] **Step 1: Create status worktree from exact technical merge**

Run:

```bash
git worktree add .worktrees/docs-operation-metrics-status \
  -b docs/operation-metrics-status "${technical_merge_sha}"
```

Generate matrix/report only from an artifact set whose metadata and source
HEAD equal `technical_merge_sha`. Copy no raw artifact, symlink or secret into
Git.

- [ ] **Step 2: Update live production-readiness audit**

Record exact measured facts:

```text
design, RED, feature, technical merge and final status SHAs
Rust test count and all quality/security results
337/337 strict PASS count and every lane count
OPMET-001..016 exact outcomes
10,000 scrape load: elapsed/QPS/samples/ticks/max in-flight/max sessions/zero leak
three enabled/disabled 99,999 pairs and each throughput/degradation
median degradation and <=0.15 gate
persistent/pooled transaction stress profiles
isolated installed ABI3 wheel contract
hosted Linux/macOS/Windows run ID and exact SHA
RustSec run ID, dependency count, zero warnings/vulnerabilities
fork-only branch/PR safety evidence
```

State `VERIFIED_FORK`. Do not claim hosted MSSQL validation, universal SQL
Server capacity, upstream inclusion or release publication.

Remaining limitations must include:

```text
metrics opt-in and connection-local
direct standalone Transaction excluded
fixed buckets/no reset
weakly consistent concurrent snapshots
no tracing/exporter/OpenTelemetry bridge
hosted CI has no real SQL Server
```

- [ ] **Step 3: Add the future upstream-sized roadmap candidate**

Add an entry containing:

```text
Operation duration/outcome metrics
default-off and dependency-free
fixed 13-operation/17-bucket schema
RAII cancellation and typed outcomes
pooled owner aggregation
RED, real SQL-auth, framework, load and fault-proxy evidence
Linux/macOS/Windows installed-wheel evidence
privacy and measured overhead boundary
no upstream PR opened
```

Keep a future telemetry bridge/tracing as separate candidates.

- [ ] **Step 4: Self-review status and generated evidence**

Run:

```bash
git diff --check
rg -n "NOT RUN|FAIL|ERROR|missing evidence" \
  docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
rg -n \
  "337|OPMET-001|OPMET-016|99,999|0.15|VERIFIED_FORK|hosted" \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
```

Require no failed/missing row, no privacy sentinel, no credential, no inflated
hosted-MSSQL claim and exact SHAs/run IDs.

- [ ] **Step 5: Commit and push status only to fork**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record verified operation metrics"
git push -u origin docs/operation-metrics-status
```

- [ ] **Step 6: Merge documentation status into cumulative**

In the clean cumulative worktree:

```bash
git switch test/sql-auth-validation
git merge --no-ff docs/operation-metrics-status \
  -m "merge: record operation metrics verification"
git push origin test/sql-auth-validation
```

- [ ] **Step 7: Run final fork/upstream safety and parity audit**

Run:

```bash
git status --short --branch
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
git remote get-url --push origin
git remote get-url --push upstream
git ls-remote upstream refs/heads/master
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/operation-metrics \
  --state all \
  --json number,url,state
test ! -e VERSION.md
```

Expected:

```text
clean cumulative branch
fork parity 0 0
origin is galeamarcel/FastMssql
upstream push is DISABLED
upstream master only read
no upstream PR
no VERSION.md
```

---

## Plan Self-Review Record

The completed plan was checked section-by-section against the design before
execution.

| Design contract | Implemented/tested by |
|---|---|
| default-off exact config and copy semantics | Tasks 2, 3, 7, 8, 10 |
| exact snapshot schema and mutation isolation | Tasks 2, 3, 7, 8, 10 |
| operation timing boundary and typed outcomes | Tasks 3, 4, 7, 8, 9 |
| pooled owner/direct exclusion | Tasks 4 and 9 |
| lifecycle-generation persistence | Tasks 3 and 8 |
| saturating atomic publication/reconciliation | Task 7 |
| privacy and fixed cardinality | Tasks 2, 4, 7, 10, 11 |
| 10,000 concurrent scrape | Tasks 5, 10, 11 |
| FastAPI/Flask WSGI/Flask ASGI | Tasks 5, 10, 11 |
| interleaved 99,999 overhead gate | Tasks 5, 11, 13 |
| cross-platform wheel and RustSec | Tasks 2, 12 |
| live audit, fork-only integration and limitations | Tasks 13 and 14 |

Corrections made during plan self-review:

1. Direct Rust bool formatting was replaced with explicit `True`/`False`
   strings so `repr` matches the Python contract.
2. Elapsed time is captured immediately after the original future returns,
   before PyO3 exception classification, so observer overhead cannot inflate
   an error duration.
3. The reserved `OperationName::Transaction` path returns the original future
   without clock/atomics or panic.
4. `Instant::now()` is explicit inside `observe_operation`, after disabled and
   reserved branches, making the hot-path source contract auditable without
   depending on file layout.
5. The standalone performance script repeats validated schema constants
   locally instead of importing a test package that is absent from its normal
   script import path.
6. Every focused test received one exact function name, and intermediate
   commands use node IDs rather than fragile numeric `-k` fragments.
7. The 10,000-operation case adds a bounded first-wave server WAITFOR so
   non-zero in-flight observation is deterministic while the public call total
   stays exactly 10,000.
8. Framework metric recorders permit only the three declared cross-lane IDs;
   the load recorder permits only `OPMET-011`.
9. The stress wrapper explicitly sets and verifies its executable bit and
   remains outside the normal harness.
10. The design amendment that narrows elapsed-time capture is included in the
    plan commit rather than left as undocumented implementation judgment.
11. Static count review confirms 16 new IDs, 13 operation series, 17 bounds
    and a strict total of 337.
12. Scans found no deferred implementation instruction, ambiguous type name
    or alternative API choice.

---

## Final Self-Review Record Required at Execution Time

Before marking the candidate `VERIFIED_FORK`, append evidence to the live audit
and confirm all of the following:

1. `OperationMetricsConfig` is exact-bool, default-off, copied at construction
   and appended without positional breakage.
2. Disabled operations allocate no registry and reach the original future
   before any clock or atomic access.
3. The schema is identical in Rust, wrapper, both stubs, README, no-network
   contract, SQL-auth, framework and load tests.
4. Exactly 13 operations and 17 finite bounds exist; completed is the implicit
   positive-infinity bucket.
5. Every atomic addition saturates; none wraps; saturation remains visible.
6. Outcome publication is final and Release-ordered after duration data.
7. Concurrent snapshots preserve arithmetic/bucket invariants and become exact
   after quiescence.
8. A completed-zero snapshot cannot expose unpublished duration data.
9. Success, error, timeout, unknown commit and cancellation each increment
   exactly one outcome.
10. `CommitOutcomeUnknown` outranks its cause and all existing typed exception
    metadata remains unchanged.
11. Cancellation Drop records without allocation, GIL, blocking or panic.
12. Each public operation is wrapped once; internal readiness/batch/cleanup
    commands never double-count.
13. Synchronous parameter and batch validation remains outside metrics.
14. Pooled transaction operations share the owner registry; direct
    `Transaction(...)` has no hidden/inaccessible registry.
15. Registry counts persist across disconnect/reconnect lifecycle generations.
16. Returned dict/list mutation cannot alter the registry or later snapshots.
17. Metrics contain no SQL, parameters, identifiers, errors, credentials or
    arbitrary labels.
18. FastAPI/native ASGI, Flask WSGI and Flask via ASGI preserve their distinct
    execution models and exact operation deltas.
19. Ten-thousand-operation concurrent scraping preserves event-loop progress,
    pool bounds, exact results and zero sessions.
20. Three interleaved 99,999 enabled/disabled pairs meet the exact 0.15 median
    gate with 200 workers and a pool maximum of 100.
21. Existing transaction stress remains green in persistent and pooled modes.
22. Raw Cargo, Rust tests, installed ABI3 wheel and public contracts pass on
    Linux, macOS and Windows at the exact feature SHA.
23. Dependency audit reports zero vulnerabilities and zero warnings.
24. All 337 strict IDs have complete PASS evidence with no skip or swallowed
    exception.
25. Design, RED, feature, status and cumulative branches exist only on the
    user's fork; upstream push remains `DISABLED` and no upstream PR exists.
