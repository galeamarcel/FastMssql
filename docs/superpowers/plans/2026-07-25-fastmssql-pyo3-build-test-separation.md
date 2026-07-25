# FastMssql PyO3 Build/Test Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate generic Rust build/test linking from Python extension
linking so raw Cargo works portably, while maturin continues to produce an
ABI3 FastMssql extension and a mandatory Linux/macOS/Windows hosted gate
prevents regression.

**Architecture:** Remove the deprecated permanent
`pyo3/extension-module` feature and require maturin 1.9.4 or newer to select
extension behavior only during extension builds. Encode both manifests, the
locked SQL-auth runner and a dedicated three-OS GitHub Actions workflow in one
deterministic Python contract. Prove RED on the unchanged baseline, apply only
the two manifest corrections and locked runner invocation, then require raw
Cargo, packaging, real SQL-auth, full regression and exact hosted-SHA evidence
before updating the live audit.

**Tech Stack:** Rust 1.94.0, Cargo, PyO3 0.29.0, maturin 1.14.1 with a PEP 517
floor of 1.9.4, Python 3.13, pytest, GitHub Actions, uv, macOS `otool`, and SQL
Server 2022 in the dedicated SQL-auth Docker container.

## Global Constraints

- All branches, commits and pushes target
  `https://github.com/galeamarcel/FastMssql.git`.
- The implementation baseline is `test/sql-auth-validation` at `8191fff`.
- The approved design commit is `cf4b3b1`.
- The `upstream` remote remains fetch-only with push URL `DISABLED`.
- No upstream branch, push, PR, fork or package publication is authorized.
- All commits use `Marcel Galea <galea.marcel@gmail.com>`.
- Keep design, RED test, technical fix and status branches separate.
- Do not dispatch subagents unless the user explicitly chooses the
  Subagent-Driven execution option.
- Default Cargo features exclude `pyo3/extension-module`.
- Default Cargo retains exactly `abi3-py311` and `chrono` for PyO3.
- `[lib].crate-type` remains exactly `["cdylib"]`; do not add `rlib`.
- `[build-system].requires` contains `maturin>=1.9.4,<2.0`.
- The locked development tool remains maturin 1.14.1.
- Do not upgrade PyO3, pyo3-async-runtimes or any unrelated dependency.
- Do not add `RUSTFLAGS`, `PYO3_BUILD_EXTENSION_MODULE`, a custom `build.rs`,
  a linker script, a Python framework path or an accepted CI failure.
- The hosted gate uses Rust 1.94.0 and CPython 3.13 on Linux, macOS and
  Windows.
- The hosted workflow has read-only repository permission and no secrets.
- Docker operations target only `fastmssql-sql-auth-dev`.
- Secrets remain only in ignored `.env.sql-auth.local`.
- The live audit is marked remediated only after the exact integrated SHA is
  green on all three hosted operating systems.
- The repository has no `VERSION.md`; do not invent a version-history format
  for this build/CI-only correction. Report the exception in the handoff.

---

## File Map

- `tests/test_pyo3_build_contract.py`: owns deterministic manifest, workflow
  and SQL-auth runner invariants.
- `.github/workflows/rust-unit-tests.yml`: owns the mandatory raw Cargo build
  and test matrix, independent of Python/SQL Server CI.
- `Cargo.toml`: owns PyO3's generic Cargo feature set and unchanged `cdylib`
  crate type.
- `pyproject.toml`: owns the PEP 517 maturin floor, existing ABI3 maturin
  feature and locked development tool.
- `uv.lock`: must remain byte-identical unless `uv lock --check` proves an
  update is required.
- `scripts/sql_auth/run_all.sh`: invokes the project Cargo tests with
  `--locked`.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: receives measured RED,
  GREEN, packaging, SQL-auth, hosted and residual-risk evidence only on the
  status branch.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`:
  receives the future isolated candidate record without publishing it.
- `docs/superpowers/specs/2026-07-25-fastmssql-pyo3-build-test-separation-design.md`:
  is the approved behavioral and repository-boundary source of truth.

## Branch Graph

```text
test/sql-auth-validation at 8191fff
  |
  +-- docs/pyo3-build-test-design
  |     cf4b3b1 approved design
  |     implementation plan commit
  |
  +-- test/pyo3-build-contract
  |     static RED contract
  |     three-OS workflow
  |
  +-- fix/pyo3-build-test-separation
  |     two manifest corrections and locked runner
  |
  +-- test/sql-auth-validation
  |     exact technical merge tested locally and hosted
  |
  +-- docs/pyo3-build-test-status
        live audit and upstream candidate evidence
```

The design branch is merged into the cumulative fork before the RED branch is
created. The fix branch starts from the committed RED branch. The status
branch starts from the exact hosted-green cumulative commit.

---

### Task 1: Integrate the approved design and create the RED worktree

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-25-fastmssql-pyo3-build-test-separation-design.md`
- Existing:
  `docs/superpowers/plans/2026-07-25-fastmssql-pyo3-build-test-separation.md`

**Interfaces:**

- Consumes: approved design commit `cf4b3b1` and this committed plan.
- Produces: a cumulative design baseline and isolated
  `test/pyo3-build-contract` worktree.

- [ ] **Step 1: Verify the design branch and fork boundary**

Run in `.worktrees/docs-pyo3-build-test-design`:

```bash
set -euo pipefail
git status --short --branch
git log -2 --format='%h %an <%ae> %s'
git remote -v
git rev-list --left-right --count HEAD...origin/docs/pyo3-build-test-design
git diff --check
```

Expected:

```text
branch                  docs/pyo3-build-test-design
tracked changes         none
author                  Marcel Galea <galea.marcel@gmail.com>
origin parity           0 0
upstream push           DISABLED
whitespace errors       none
```

- [ ] **Step 2: Merge only design artifacts into the cumulative fork**

Run in the main `FastMssql` worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff docs/pyo3-build-test-design \
  -m "merge: plan PyO3 build and test separation"
git push origin test/sql-auth-validation
```

Expected: the cumulative branch contains the specification and plan, with no
manifest, workflow, runner or production-source change.

- [ ] **Step 3: Create the isolated RED branch**

Run in the main `FastMssql` worktree:

```bash
git worktree add \
  .worktrees/test-pyo3-build-contract \
  -b test/pyo3-build-contract \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-pyo3-build-contract/.env.sql-auth.local
```

Expected: `test/pyo3-build-contract` is clean, and the ignored environment
symlink resolves to the existing local SQL-auth configuration.

- [ ] **Step 4: Reconfirm the unchanged failing link configuration**

Run in `.worktrees/test-pyo3-build-contract`:

```bash
env | rg '^(PYO3|RUSTFLAGS|MACOSX_DEPLOYMENT_TARGET|PYTHON_SYS_EXECUTABLE)=' \
  || true
cargo tree -e features -i pyo3
```

Expected:

```text
linker override variables       absent
pyo3 version                    0.29.0
pyo3 feature extension-module  present
```

Do not change the manifests in this task.

---

### Task 2: Add the deterministic RED build contract

**Files:**

- Create: `tests/test_pyo3_build_contract.py`
- Read: `Cargo.toml`
- Read: `pyproject.toml`
- Read: `.github/workflows/rust-unit-tests.yml`
- Read: `scripts/sql_auth/run_all.sh`

**Interfaces:**

- Consumes: Python 3.11+ `tomllib` and repository-root paths.
- Produces: five deterministic contract tests; all five fail on the unchanged
  baseline for an explicit missing or incorrect requirement.

- [ ] **Step 1: Write the complete failing contract**

Create `tests/test_pyo3_build_contract.py` with:

```python
from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CARGO_MANIFEST = ROOT / "Cargo.toml"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "rust-unit-tests.yml"
SQL_AUTH_RUNNER = ROOT / "scripts" / "sql_auth" / "run_all.sh"


def _load_toml(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing TOML contract input: {path.relative_to(ROOT)}"
    with path.open("rb") as source:
        return tomllib.load(source)


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing build gate: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_default_pyo3_features_keep_generic_cargo_linkable() -> None:
    cargo = _load_toml(CARGO_MANIFEST)
    pyo3 = cargo["dependencies"]["pyo3"]

    assert pyo3["version"] == "0.29.0"
    assert pyo3["features"] == ["abi3-py311", "chrono"]
    assert cargo["lib"]["crate-type"] == ["cdylib"]


def test_maturin_owns_extension_build_mode() -> None:
    pyproject = _load_toml(PYPROJECT)

    assert pyproject["build-system"]["requires"] == ["maturin>=1.9.4,<2.0"]
    assert pyproject["build-system"]["build-backend"] == "maturin"
    assert pyproject["tool"]["maturin"]["features"] == ["pyo3/abi3-py311"]


def test_hosted_gate_covers_all_supported_desktop_platforms() -> None:
    workflow = _read_required(WORKFLOW)

    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "runs-on: ${{ matrix.os }}" in workflow
    assert "fail-fast: false" in workflow
    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert workflow.count(f"- {runner}") == 1
    assert 'RUST_TOOLCHAIN: "1.94.0"' in workflow
    assert 'PYTHON_VERSION: "3.13"' in workflow
    assert "timeout-minutes: 30" in workflow
    assert (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        in workflow
    )
    assert "astral-sh/setup-uv@v8.3.2" in workflow
    assert "python-version: ${{ env.PYTHON_VERSION }}" in workflow
    assert "activate-environment: true" in workflow
    assert "no-project: true" in workflow
    assert "PYO3_PYTHON: python" in workflow
    assert "cargo build --locked" in workflow
    assert "cargo test --locked" in workflow


def test_hosted_gate_has_no_linker_or_failure_bypass() -> None:
    workflow = _read_required(WORKFLOW)

    forbidden = (
        "continue-on-error: true",
        "|| true",
        "RUSTFLAGS",
        "PYO3_BUILD_EXTENSION_MODULE",
        "Python.framework",
        "libpython",
        "/opt/homebrew",
    )
    for token in forbidden:
        assert token not in workflow


def test_sql_auth_runner_uses_locked_cargo_tests() -> None:
    runner = _read_required(SQL_AUTH_RUNNER)

    assert "record cargo-test cargo test --locked" in runner
    assert "record cargo-test cargo test\n" not in runner
```

- [ ] **Step 2: Run the contract and verify five independent failures**

Run:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
test_default_pyo3_features_keep_generic_cargo_linkable  FAIL
test_maturin_owns_extension_build_mode                  FAIL
test_hosted_gate_covers_all_supported_desktop_platforms FAIL
test_hosted_gate_has_no_linker_or_failure_bypass        FAIL
test_sql_auth_runner_uses_locked_cargo_tests            FAIL
total                                                    5 failed
```

The first two failures must show the old manifest values. The next two must
show the absent workflow. The final failure must show unlocked `cargo test`.

- [ ] **Step 3: Reproduce the raw Cargo linker failure**

Run without a linker workaround:

```bash
cargo build --locked
cargo test --locked
```

Expected on the approved macOS arm64 reproduction environment:

```text
cargo build --locked  nonzero; undefined _Py... symbols
cargo test --locked   nonzero; undefined _Py... symbols
Rust tests executed   0
```

Store only the command, exit code and redacted root-cause summary in
`.artifacts`; never track local absolute paths from linker output.

- [ ] **Step 4: Commit and push only the RED contract**

```bash
git add tests/test_pyo3_build_contract.py
git commit -m "test: define PyO3 build separation contract"
git push -u origin test/pyo3-build-contract
```

Expected: the commit contains one test file and no production or manifest
change.

---

### Task 3: Add the mandatory hosted gate

**Files:**

- Create: `.github/workflows/rust-unit-tests.yml`
- Test: `tests/test_pyo3_build_contract.py`

**Interfaces:**

- Consumes: exact invariants from Task 2.
- Produces: deterministic three-OS raw Cargo CI; both manifest tests and the
  locked-runner test remain RED for the fix branch.

- [ ] **Step 1: Create the cross-platform workflow**

Create `.github/workflows/rust-unit-tests.yml` with:

```yaml
name: Rust unit tests

on:
  push:
    branches:
      - master
      - test/sql-auth-validation
  pull_request:
    branches:
      - master
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: rust-unit-tests-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  RUST_TOOLCHAIN: "1.94.0"
  PYTHON_VERSION: "3.13"
  PYO3_PYTHON: python

jobs:
  cargo:
    name: Cargo on ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    strategy:
      fail-fast: false
      matrix:
        os:
          - ubuntu-latest
          - macos-latest
          - windows-latest

    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Install uv and CPython
        uses: astral-sh/setup-uv@v8.3.2
        with:
          version: "latest"
          python-version: ${{ env.PYTHON_VERSION }}
          activate-environment: true
          no-project: true
          enable-cache: false

      - name: Install pinned Rust toolchain
        run: |
          rustup toolchain install 1.94.0 --profile minimal --no-self-update
          rustup default 1.94.0

      - name: Verify toolchain inputs
        run: |
          rustc --version
          cargo --version
          python -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version"

      - name: Build through raw Cargo
        run: cargo build --locked

      - name: Run Rust unit tests
        run: cargo test --locked
```

`python-version` selects CPython 3.13, while `activate-environment: true`
creates and activates the corresponding environment for all later steps.
Consequently, `PYO3_PYTHON=python` resolves to that selected interpreter. It
does not alter extension-module link mode and is not a linker workaround.

- [ ] **Step 2: Run the contract and preserve fix-side RED**

Run:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
workflow tests  2 passed
runner test     1 failed
manifest tests  2 failed
total           2 passed, 3 failed
```

If either workflow test fails, correct only the workflow before continuing.
Do not touch the manifests or SQL-auth runner on the RED branch.

- [ ] **Step 3: Verify workflow scope and shell portability statically**

Run:

```bash
git diff --check
rg -n 'ubuntu-latest|macos-latest|windows-latest|cargo (build|test) --locked' \
  .github/workflows/rust-unit-tests.yml
if rg -n \
  'continue-on-error: true|\|\| true|RUSTFLAGS|PYO3_BUILD_EXTENSION_MODULE' \
  .github/workflows/rust-unit-tests.yml
then
  exit 1
fi
```

Expected:

```text
required runner/command matches  present
forbidden matches                none
whitespace errors                none
```

- [ ] **Step 4: Commit and push test infrastructure**

```bash
git add .github/workflows/rust-unit-tests.yml
git commit -m "test: add cross-platform raw Cargo gate"
git push origin test/pyo3-build-contract
```

Expected: `test/pyo3-build-contract` contains only the contract and workflow;
its two manifest assertions and locked-runner assertion remain intentionally
RED.

---

### Task 4: Apply the minimal PyO3/maturin fix

**Files:**

- Modify: `Cargo.toml:30`
- Modify: `pyproject.toml:2`
- Modify: `scripts/sql_auth/run_all.sh:55`
- Verify unchanged: `Cargo.lock`
- Verify unchanged unless proven otherwise: `uv.lock`
- Test: `tests/test_pyo3_build_contract.py`

**Interfaces:**

- Consumes: committed RED branch `test/pyo3-build-contract`.
- Produces: a generic Cargo feature set that links Rust artifacts normally, a
  maturin floor that owns extension builds and a locked local Cargo test lane.

- [ ] **Step 1: Create the isolated fix worktree from RED**

Run from the main `FastMssql` worktree:

```bash
git worktree add \
  .worktrees/fix-pyo3-build-test-separation \
  -b fix/pyo3-build-test-separation \
  test/pyo3-build-contract
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-pyo3-build-test-separation/.env.sql-auth.local
```

Expected: the fix worktree includes both RED commits and no uncommitted
changes.

- [ ] **Step 2: Remove only the permanent PyO3 feature**

Replace the PyO3 dependency in `Cargo.toml` with:

```toml
pyo3 = { version = "0.29.0", features = ["abi3-py311", "chrono"] }
```

Do not change dependency versions, default features, the edition or crate
types.

- [ ] **Step 3: Raise only the PEP 517 maturin floor**

Replace the build requirement in `pyproject.toml` with:

```toml
requires = ["maturin>=1.9.4,<2.0"]
```

Keep:

```toml
[tool.maturin]
features = ["pyo3/abi3-py311"]
```

and:

```toml
"maturin==1.14.1",
```

unchanged.

- [ ] **Step 4: Make the SQL-auth runner enforce Cargo.lock**

Change only line 55 of `scripts/sql_auth/run_all.sh`:

```bash
record cargo-test cargo test --locked
```

Keep `record()` behavior, the other lanes and their order unchanged.

- [ ] **Step 5: Prove that lockfiles do not need churn**

Run:

```bash
uv lock --check
git diff --exit-code -- Cargo.lock uv.lock
```

Expected:

```text
uv lock status       current
Cargo.lock diff      none
uv.lock diff         none
```

If either lockfile changes, stop and identify which resolver input requires
the delta before staging anything. Do not accept unrelated upgrades.

- [ ] **Step 6: Run the deterministic contract GREEN**

Run:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 7: Confirm the resolved feature graph**

Run:

```bash
set -euo pipefail
cargo tree -e features -i pyo3
if cargo tree -e features -i pyo3 | rg 'extension-module'; then
  exit 1
fi
```

Expected: PyO3 0.29.0 resolves `abi3-py311` and `chrono`, and no
`extension-module` line exists.

- [ ] **Step 8: Re-run both original symptoms without overrides**

Run:

```bash
set -euo pipefail
env -u RUSTFLAGS \
  -u PYO3_BUILD_EXTENSION_MODULE \
  -u PYO3_CONFIG_FILE \
  -u PYTHON_SYS_EXECUTABLE \
  cargo build --locked
env -u RUSTFLAGS \
  -u PYO3_BUILD_EXTENSION_MODULE \
  -u PYO3_CONFIG_FILE \
  -u PYTHON_SYS_EXECUTABLE \
  cargo test --locked
```

Expected:

```text
raw Cargo build       PASS
Rust unit tests       13 passed, 0 failed
linker override       none
```

- [ ] **Step 9: Run Rust static gates**

Run:

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
```

Expected: both commands exit zero with no warning accepted.

- [ ] **Step 10: Commit and push the minimal fix**

Run:

```bash
git diff --check
git diff --stat
git add Cargo.toml pyproject.toml scripts/sql_auth/run_all.sh
git commit -m "fix: separate PyO3 extension and Cargo builds"
git show --stat --oneline HEAD
git push -u origin fix/pyo3-build-test-separation
```

Expected: the fix commit changes exactly two manifest lines and one SQL-auth
runner line. Test infrastructure remains attributable to the preceding RED
branch commits.

---

### Task 5: Prove maturin packaging and a real SQL-auth query

**Files:**

- Verify: built local extension in `.venv`
- Verify: wheel under an untracked temporary directory
- Test:
  `tests/sql_auth_strict/test_parameters_strict.py::test_signed_integer_boundaries_and_overflow`

**Interfaces:**

- Consumes: fixed manifests and dedicated Docker SQL-auth environment.
- Produces: extension import, wheel dependency, parameterized SQL round-trip
  and cleanup evidence without tracked changes.

- [ ] **Step 1: Rebuild and install through maturin**

Run in `.worktrees/fix-pyo3-build-test-separation`:

```bash
uv sync --locked --all-extras --dev
uv run maturin develop --release --locked
uv run python -c \
  "import fastmssql; assert fastmssql.Connection; print(fastmssql.version())"
```

Expected: maturin 1.14.1 builds the extension, import succeeds, and the module
reports its version.

- [ ] **Step 2: Build a locked wheel into an isolated directory**

Run:

```bash
set -euo pipefail
wheel_dir="$(mktemp -d /private/tmp/fastmssql-pyo3-wheel.XXXXXX)"
uv run maturin build --release --locked --out "${wheel_dir}"
wheel_count="$(
  find "${wheel_dir}" -maxdepth 1 -type f -name '*.whl' -print |
    wc -l |
    tr -d '[:space:]'
)"
test "${wheel_count}" -eq 1
wheel_path="$(find "${wheel_dir}" -maxdepth 1 -name '*.whl' -print -quit)"
test -n "${wheel_path}"
echo "${wheel_path}"
```

Expected: exactly one FastMssql wheel path is printed and the build exits
zero.

- [ ] **Step 3: Install the wheel in a clean Python 3.13 environment**

Continue in the same shell that owns `wheel_path`:

```bash
set -euo pipefail
smoke_dir="$(mktemp -d /private/tmp/fastmssql-pyo3-smoke.XXXXXX)"
uv venv "${smoke_dir}/venv" --python 3.13
uv pip install --python "${smoke_dir}/venv/bin/python" "${wheel_path}"
extension_path="$(
  "${smoke_dir}/venv/bin/python" -c \
    'from importlib import import_module; import sys; import fastmssql; assert sys.version_info[:2] == (3, 13), sys.version; native = import_module("fastmssql.fastmssql"); assert fastmssql.Connection; print(native.__file__)'
)"
test -f "${extension_path}"
echo "${extension_path}"
```

Expected: import comes from the clean temporary environment and resolves the
compiled `fastmssql.fastmssql` extension.

- [ ] **Step 4: Reject a Python framework dependency in the wheel**

Run on the approved macOS verification host:

```bash
set -euo pipefail
otool -L "${extension_path}"
if otool -L "${extension_path}" | rg -i 'Python\.framework|libpython'; then
  exit 1
fi
```

Expected: the extension has no `Python.framework` or `libpython` dependency.
Generic Cargo linking must not leak into the distributed extension.

- [ ] **Step 5: Start and provision only the dedicated SQL Server**

Run:

```bash
docker compose \
  --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml \
  up -d sqlserver
docker inspect \
  --format '{{.State.Health.Status}}' \
  fastmssql-sql-auth-dev
scripts/sql_auth/provision.sh
```

Expected: the exact container reports `healthy`, and provisioning succeeds
without printing credentials.

- [ ] **Step 6: Execute a parameterized SQL-auth round-trip**

Run without echoing environment values:

```bash
set -a
source .env.sql-auth.local
set +a
export FASTMSSQL_TEST_CONNECTION_STRING="Server=${FASTMSSQL_SQL_AUTH_HOST},${FASTMSSQL_SQL_AUTH_PORT};Database=fastmssql_upstream_regression;User Id=${FASTMSSQL_SQL_AUTH_OWNER_USER};Password=${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD};Encrypt=True;TrustServerCertificate=True"
export FAST_MSSQL_TEST_DB_USER="${FASTMSSQL_SQL_AUTH_OWNER_USER}"
export FAST_MSSQL_TEST_DB_PASSWORD="${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}"
export FAST_MSSQL_TEST_SERVER="${FASTMSSQL_SQL_AUTH_HOST}"
export FAST_MSSQL_TEST_PORT="${FASTMSSQL_SQL_AUTH_PORT}"
export FAST_MSSQL_TEST_DATABASE="fastmssql_upstream_regression"
FASTMSSQL_SQL_AUTH_RESULTS_PATH=/private/tmp/pyo3-build-sql-auth.json \
  uv run pytest \
  tests/sql_auth_strict/test_parameters_strict.py::test_signed_integer_boundaries_and_overflow \
  -q
```

Expected: PARAM-004 passes for every signed 64-bit boundary and rejects both
overflow values with the existing typed boundary error.

- [ ] **Step 7: Verify the packaging task left source clean**

Run:

```bash
git status --short --branch
git ls-files .env.sql-auth.local .venv .artifacts
```

Expected: no tracked secret, environment, wheel or test artifact exists, and
the fix branch remains clean.

---

### Task 6: Run complete local gates and integrate the technical branch

**Files:**

- Verify: all source, test, workflow, packaging and SQL-auth surfaces.
- Generate only ignored evidence under `.artifacts/sql-auth`.

**Interfaces:**

- Consumes: clean `fix/pyo3-build-test-separation`.
- Produces: one locally verified cumulative technical merge and exact local
  evidence totals for the status task.

- [ ] **Step 1: Run the complete SQL-auth enterprise runner**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected:

```text
uv sync                 PASS
maturin develop         PASS
cargo fmt               PASS
cargo clippy            PASS
cargo test --locked     13/13 PASS
strict functional       PASS
async                   PASS
framework               PASS
resilience              PASS
load                    PASS
applicable upstream     PASS
complete report         PASS
required failed lanes   0
```

Record measured pytest totals from JUnit/JSON artifacts. Do not reuse
historical totals as final evidence.

- [ ] **Step 2: Run the exact vendored Tiberius feature set**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
export TIBERIUS_TEST_CONNECTION_STRING="server=tcp:${FASTMSSQL_SQL_AUTH_HOST},${FASTMSSQL_SQL_AUTH_PORT};database=fastmssql_upstream_regression;user=${FASTMSSQL_SQL_AUTH_OWNER_USER};password=${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD};TrustServerCertificate=true"
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,rustls,tds73
```

Expected: all executed vendored unit tests and doctests pass. Do not use
`--locked` here because the vendored library crate intentionally has no
versioned standalone `Cargo.lock`.

- [ ] **Step 3: Run remaining static and security gates**

Run:

```bash
uv run ruff check .
uv run python -m compileall -q python tests
scripts/security/audit_dependencies.sh
uvx code-review-graph build
git diff --check
```

Expected:

```text
Ruff                     PASS
compileall               PASS
RustSec findings         0
code review graph        rebuilt at current fix SHA
whitespace errors        none
```

- [ ] **Step 4: Prove no test-owned SQL sessions remain**

Use the existing SQL observer login after runner teardown and query
`sys.dm_exec_sessions`/`sys.dm_exec_connections` for the owner, readonly and
denied test logins.

Required result:

```text
owner sessions       0
readonly sessions    0
denied sessions      0
```

Exclude documented SQL Server internal sessions only; do not exclude a
test-owned session to make the count pass.

- [ ] **Step 5: Verify exact diff and repository identity**

Run:

```bash
set -euo pipefail
git status --short --branch
git diff --check test/sql-auth-validation...HEAD
git diff --stat test/sql-auth-validation...HEAD
git log --format='%h %an <%ae> %s' \
  test/sql-auth-validation..HEAD
git remote -v
git ls-files .env.sql-auth.local .artifacts
```

Expected:

```text
technical files       test contract, workflow, runner, two manifests
runtime source files  none
authors               Marcel Galea <galea.marcel@gmail.com>
tracked secrets       none
origin                galeamarcel/FastMssql
upstream push         DISABLED
```

- [ ] **Step 6: Merge the verified technical tree into the cumulative fork**

Run in the main `FastMssql` worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff fix/pyo3-build-test-separation \
  -m "merge: separate PyO3 extension and Cargo builds"
technical_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
echo "${technical_sha}"
```

Expected:

```text
origin parity  0 0
upstream push  none
```

Save `technical_sha` as the sole commit identity accepted by the hosted and
status tasks.

---

### Task 7: Require the hosted Linux/macOS/Windows result

**Files:**

- Verify: `.github/workflows/rust-unit-tests.yml`
- Verify external state: GitHub Actions for the exact `technical_sha`.

**Interfaces:**

- Consumes: the cumulative commit identity produced by Task 6.
- Produces: one successful workflow URL and three successful matrix-job
  results for that exact SHA.

- [ ] **Step 1: Locate only the exact-SHA workflow run**

Run:

```bash
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch test/sql-auth-validation \
  --commit "${technical_sha}" \
  --json databaseId,headSha,status,conclusion,url \
  --limit 5
```

Expected: exactly one relevant run has `headSha == technical_sha`. Ignore
older green runs.

- [ ] **Step 2: Wait for a terminal result without changing the run**

Read the returned `databaseId` as `run_id`, then monitor:

```bash
gh run view "${run_id}" \
  --repo galeamarcel/FastMssql \
  --json headSha,status,conclusion,url,jobs
```

Expected terminal state:

```text
headSha      exact technical_sha
status       completed
conclusion   success
```

Do not rerun, cancel or bypass a failed job. A failure enters systematic
debugging on the same fork branch before any status claim.

- [ ] **Step 3: Prove all three matrix cells independently**

Run:

```bash
gh run view "${run_id}" \
  --repo galeamarcel/FastMssql \
  --json jobs \
  --jq '.jobs[] | [.name, .conclusion] | @tsv'
```

Expected:

```text
Cargo on ubuntu-latest   success
Cargo on macos-latest    success
Cargo on windows-latest  success
```

If GitHub renders runner names with matrix suffix formatting, verify each
job's `labels`/name still resolves unambiguously to those three runner values.

- [ ] **Step 4: Preserve hosted evidence for documentation**

Record from the read-only GitHub response:

- exact `technical_sha`;
- workflow run URL;
- run ID and attempt;
- creation/completion timestamps;
- each job name and conclusion;
- Rust and Python versions printed by each job;
- `cargo build --locked` result;
- `cargo test --locked` test total.

Never copy authentication headers or tokens into an artifact or document.

---

### Task 8: Update the live audit on a separate status branch

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Consumes: design, RED, fix and technical commit IDs; Task 5 packaging/SQL
  evidence; Task 6 local totals; Task 7 exact hosted run evidence.
- Produces: a `VERIFIED_FORK` candidate record without upstream publication.

- [ ] **Step 1: Create the status worktree from hosted-green cumulative HEAD**

Run from the main `FastMssql` worktree:

```bash
git worktree add \
  .worktrees/docs-pyo3-build-test-status \
  -b docs/pyo3-build-test-status \
  test/sql-auth-validation
```

Expected: status branch HEAD equals `technical_sha`.

- [ ] **Step 2: Add the measured audit section**

Add a dedicated section titled:

```markdown
## Separarea build/test PyO3 — remediată și verificată
```

The section must state, using only evidence produced by Tasks 2–7:

- baseline `8191fff` and design `cf4b3b1`;
- RED branch and both expected manifest/linker failures;
- test branch and fix branch commit IDs;
- exact technical merge SHA;
- removal of permanent `extension-module`;
- maturin PEP 517 floor 1.9.4 and locked development version 1.14.1;
- raw Cargo build result;
- exact Rust unit-test count;
- maturin develop/wheel/import and `otool` result;
- PARAM-004 real SQL-auth result and zero post-test login sessions;
- measured strict/upstream/Tiberius/security totals;
- workflow URL and separate Linux/macOS/Windows conclusions;
- explicit absence of linker workarounds;
- unchanged runtime/API/TDS behavior;
- fork-only status and pending upstream approval.

Do not claim cross-platform success from local evidence or omit a failed/skipped
lane.

- [ ] **Step 3: Add the isolated upstream candidate record**

In the upstream roadmap, add a candidate with:

```markdown
### PyO3 build/test separation — VERIFIED_FORK

- Proposed clean branch: `fix/upstream-pyo3-build-test-separation`
- Proposed title: `fix: separate PyO3 extension and Cargo test builds`
- Scope: remove the permanent PyO3 extension feature, require maturin 1.9.4,
  add deterministic contracts, a three-OS raw Cargo gate and a locked local
  runner.
- Upstream gate: rebase on the latest upstream, reproduce RED, re-run all
  three hosted jobs, and obtain Marcel Galea's separate publication approval.
- Exclusions: runtime SQL behavior, dependency upgrades, `rlib`, custom
  linker flags, package publication and Tiberius changes.
```

Append exact fork branch/commit and verification evidence immediately below
these stable fields.

- [ ] **Step 4: Self-review status claims against evidence**

Run:

```bash
set -euo pipefail
rg -n \
  'PyO3|extension-module|maturin|cargo build --locked|cargo test --locked|Linux|macOS|Windows|VERIFIED_FORK' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git diff --check
if rg -n \
  '/Users/|Password=|SA_PASSWORD|github_pat_|ghp_' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
then
  exit 1
fi
```

Expected:

```text
required evidence terms  present
whitespace errors        none
local paths/secrets      none
```

- [ ] **Step 5: Commit and push only status documentation**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record PyO3 build and test separation"
git push -u origin docs/pyo3-build-test-status
```

Expected: the status commit contains exactly the two live-document updates.

- [ ] **Step 6: Merge status into the cumulative fork**

Run in the main `FastMssql` worktree:

```bash
set -euo pipefail
git switch test/sql-auth-validation
git merge --no-ff docs/pyo3-build-test-status \
  -m "merge: record PyO3 build and test separation"
git push origin test/sql-auth-validation
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
```

Expected:

```text
origin parity  0 0
upstream push  none
```

- [ ] **Step 7: Perform the final candidate acceptance audit**

Verify every acceptance criterion in the approved specification against:

- branch and commit state;
- raw RED/GREEN command output;
- local package/SQL/regression artifacts;
- exact hosted workflow and jobs;
- final audit and roadmap text;
- tracked-file and secret scans;
- remote parity.

Use `superpowers:verification-before-completion` before calling this candidate
complete. Use `superpowers:finishing-a-development-branch` to report the
fork-only branch disposition. The overall enterprise-readiness goal remains
active because later audit candidates are still pending.
