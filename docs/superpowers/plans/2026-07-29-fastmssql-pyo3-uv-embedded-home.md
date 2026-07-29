# FastMssql PyO3 uv Embedded-Python Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** make the canonical local SQL-auth runner execute raw PyO3 unit
tests from a completely fresh Cargo target when the worktree uses relocatable
uv CPython.

**Architecture:** resolve the exact project interpreter and its base prefix
through `uv run python` after environment synchronization. Pass them as
command-scoped `PYO3_PYTHON` and `PYTHONHOME` only to the existing
`cargo test --locked` lane; leave all other build, Python, SQL-auth and hosted
environments unchanged.

**Tech Stack:** Bash, uv-managed CPython, PyO3 0.29, Cargo, pytest, Docker SQL
Server, Git worktrees and code-review-graph.

## Global Constraints

- Exact technical base:
  `7c6b9a3e7d40b7ab82d5ef130b64841a2864788d`.
- Exact design commit:
  `700f304ea88afac09cdadc02704143c96012276e`.
- Branch topology:

  ```text
  docs/pyo3-uv-pythonhome-design
    -> test/pyo3-uv-pythonhome
    -> fix/pyo3-uv-pythonhome
    -> feat/execute-many
  ```

- Use only project-local ignored worktrees under `.worktrees/`.
- Push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep the original repository's push URL exactly `DISABLED`.
- Do not create a pull request, publish a wheel or change package metadata.
- Keep displayed version `0.7.7`; update `VERSION.md` in every commit.
- Preserve the raw command `cargo test --locked`, its lane name and its
  nonzero failure accounting.
- Resolve Python only after `uv sync --locked --all-extras --dev`.
- Do not hard-code a user path, uv cache, Python version, framework,
  Homebrew path or standard-library path.
- Do not globally export `PYTHONHOME` or `PYO3_PYTHON`.
- Do not change PyO3 features, Cargo manifests, runtime source, hosted
  workflow or any SQL-auth behavior.
- Treat generated reports, logs, Cargo targets, environment files and wheels
  as untracked/ignored evidence only.

---

## Task 1: publish the executable corrective plan

**Branch:** `docs/pyo3-uv-pythonhome-design`

**Files:**

- retain
  `docs/superpowers/specs/2026-07-29-fastmssql-pyo3-uv-embedded-home-design.md`;
- create
  `docs/superpowers/plans/2026-07-29-fastmssql-pyo3-uv-embedded-home.md`;
- update `VERSION.md`.

**Interfaces:**

- Consumes: approved design commit `700f304`.
- Produces: immutable branch names, RED requirement, exact runner code and
  verification commands for the corrective history.

- [ ] **Step 1: verify topology and remote safety**

  Run:

  ```bash
  git rev-parse HEAD
  git status --short --branch
  git remote -v
  git config --get remote.upstream.pushurl
  ```

  Require the exact design commit, only plan/`VERSION.md` changes, fork
  `origin` and upstream push URL `DISABLED`.

- [ ] **Step 2: self-review the plan**

  Require all design requirements to map to Tasks 2–4. Scan for unfinished
  markers and ambiguous branch/SHA references:

  ```bash
  if rg -n 'T[B]D|T[O]DO|FIX[M]E|PLACEH[O]LDER|\?\?\?' \
    docs/superpowers/plans/2026-07-29-fastmssql-pyo3-uv-embedded-home.md
  then
    exit 1
  fi
  git diff --check
  ```

- [ ] **Step 3: verify unchanged PyO3 baseline**

  Run:

  ```bash
  uv run pytest tests/test_pyo3_build_contract.py -q
  ```

  Require the preexisting eight contracts to pass.

- [ ] **Step 4: commit and push only the design branch**

  Run:

  ```bash
  git add \
    docs/superpowers/plans/2026-07-29-fastmssql-pyo3-uv-embedded-home.md \
    VERSION.md
  git commit -m "docs: plan uv embedded Python bootstrap"
  git push origin docs/pyo3-uv-pythonhome-design
  ```

  Verify local and fork SHAs are identical and upstream push remains
  `DISABLED`.

---

## Task 2: create and observe the deterministic RED contract

**Branch:** `test/pyo3-uv-pythonhome`

**Base:** exact Task 1 commit.

**Files:**

- modify `tests/test_pyo3_build_contract.py`;
- update `VERSION.md`.

**Interfaces:**

- Consumes: `SQL_AUTH_RUNNER` and `_read_required()` from the existing PyO3
  contract.
- Produces:
  `test_sql_auth_runner_scopes_worktree_python_to_embedded_cargo_test()`.

- [ ] **Step 1: create the test branch**

  Run:

  ```bash
  git switch -c test/pyo3-uv-pythonhome
  ```

- [ ] **Step 2: add the failing configuration contract**

  Append this test:

  ```python
  def test_sql_auth_runner_scopes_worktree_python_to_embedded_cargo_test() -> None:
      runner = _read_required(SQL_AUTH_RUNNER)
      normalized = " ".join(runner.replace("\\", " ").split())

      assert "uv run python -c 'import sys; print(sys.executable)'" in runner
      assert "uv run python -c 'import sys; print(sys.base_prefix)'" in runner
      assert "if ! cargo_test_python=" in runner
      assert '-x "${cargo_test_python}"' in runner
      assert "if ! cargo_test_python_home=" in runner
      assert '-d "${cargo_test_python_home}"' in runner
      assert "readonly cargo_test_python" in runner
      assert "readonly cargo_test_python_home" in runner
      assert "export PYO3_PYTHON" not in runner
      assert "export PYTHONHOME" not in runner
      assert (
          'record cargo-test env '
          '"PYO3_PYTHON=${cargo_test_python}" '
          '"PYTHONHOME=${cargo_test_python_home}" '
          "cargo test --locked"
      ) in normalized
      assert runner.index("record uv-sync") < runner.index(
          "cargo_test_python="
      )
      assert runner.index("cargo_test_python_home=") < runner.index(
          "record cargo-test"
      )
  ```

  Update the old locked-command contract to require
  `"cargo test --locked"` and reject an unlocked `"cargo test\n"` without
  requiring the obsolete one-line `record` form.

- [ ] **Step 3: observe the intended RED**

  Run:

  ```bash
  uv run pytest tests/test_pyo3_build_contract.py -q
  ```

  Require exactly one failure in the new test because discovery/scoping is
  absent, with the eight preexisting contracts still passing. Collection,
  import or environment errors are invalid RED evidence.

- [ ] **Step 4: record and verify RED evidence**

  Add a `VERSION.md` entry containing:

  - fresh-target `/install`/`encodings` reproduction;
  - 116 tests compiled but no complete test run;
  - confirmation that command-scoped `PYTHONHOME=sys.base_prefix` produced
    116/116 PASS;
  - exact focused RED count.

  Run:

  ```bash
  git diff --check
  uv run ruff check tests/test_pyo3_build_contract.py
  if git diff | rg -n -i \
    'passw[o]rd\s*=|user i[d]\s*=|server=t[c]p:|FASTMSSQL_SQL_AUT[H]_.*='
  then
    exit 1
  fi
  ```

- [ ] **Step 5: commit and push the immutable RED branch**

  Run:

  ```bash
  git add tests/test_pyo3_build_contract.py VERSION.md
  git commit -m "test: require uv embedded Python bootstrap"
  git push origin test/pyo3-uv-pythonhome
  ```

  Verify the commit descends from Task 1 and fork/local SHAs match.

---

## Task 3: implement the command-scoped runner correction

**Branch:** `fix/pyo3-uv-pythonhome`

**Base:** exact Task 2 RED commit.

**Files:**

- modify `scripts/sql_auth/run_all.sh`;
- update `VERSION.md`.

**Interfaces:**

- Consumes: `uv run python`, `record()` and the existing `cargo-test` lane.
- Produces two validated readonly shell variables:
  `cargo_test_python` and `cargo_test_python_home`.

- [ ] **Step 1: create the fix branch**

  Run:

  ```bash
  git switch -c fix/pyo3-uv-pythonhome
  ```

- [ ] **Step 2: add fail-closed interpreter discovery after uv sync**

  Immediately after the `record uv-sync ...` line, add:

  ```bash
  if ! cargo_test_python="$(
    uv run python -c 'import sys; print(sys.executable)'
  )" || [[ -z "${cargo_test_python}" || ! -x "${cargo_test_python}" ]]; then
    echo "unable to resolve the worktree Python executable" >&2
    exit 2
  fi
  readonly cargo_test_python

  if ! cargo_test_python_home="$(
    uv run python -c 'import sys; print(sys.base_prefix)'
  )" || [[ -z "${cargo_test_python_home}" || ! -d "${cargo_test_python_home}" ]]; then
    echo "unable to resolve the worktree Python base prefix" >&2
    exit 2
  fi
  readonly cargo_test_python_home
  ```

- [ ] **Step 3: scope both values only to raw Cargo tests**

  Replace only the existing cargo-test invocation with:

  ```bash
  record cargo-test \
    env \
    "PYO3_PYTHON=${cargo_test_python}" \
    "PYTHONHOME=${cargo_test_python_home}" \
    cargo test --locked
  ```

  Do not change any other lane.

- [ ] **Step 4: drive the focused contract GREEN**

  Run:

  ```bash
  uv run pytest tests/test_pyo3_build_contract.py -q
  uv run ruff check tests/test_pyo3_build_contract.py
  bash -n scripts/sql_auth/run_all.sh
  ```

  Require 9/9 contracts, Ruff and Bash syntax to pass.

- [ ] **Step 5: reproduce the fixed runtime boundary in a fresh target**

  Run:

  ```bash
  diagnostic_root="$(mktemp -d \
    /private/tmp/fastmssql-pyo3-uv-pythonhome.XXXXXX)"
  selected_python="$(uv run python -c 'import sys; print(sys.executable)')"
  selected_home="$(uv run python -c 'import sys; print(sys.base_prefix)')"
  PYO3_PYTHON="${selected_python}" \
  PYTHONHOME="${selected_home}" \
  CARGO_TARGET_DIR="${diagnostic_root}/cargo" \
    cargo test --locked
  ```

  Require 116/116 tests, zero failures and no `/install` bootstrap error.
  Record the selected major/minor Python version without recording a user
  home path in tracked files.

- [ ] **Step 6: document, inspect and commit the fix**

  Add a `VERSION.md` entry for the scoped fix and its focused/fresh-target
  evidence. Then run:

  ```bash
  git diff --check
  git diff -- scripts/sql_auth/run_all.sh \
    tests/test_pyo3_build_contract.py VERSION.md
  if git diff | rg -n -i \
    'passw[o]rd\s*=|user i[d]\s*=|server=t[c]p:|FASTMSSQL_SQL_AUT[H]_.*='
  then
    exit 1
  fi
  ```

  Commit:

  ```bash
  git add scripts/sql_auth/run_all.sh VERSION.md
  git commit -m "fix: bootstrap uv Python for raw Cargo tests"
  ```

- [ ] **Step 7: rebuild and review the graph**

  Run:

  ```bash
  uvx code-review-graph build
  ```

  Require graph HEAD equality. Use `detect_changes` against the RED commit;
  manually review the shell change because graph coverage may not model Bash
  data flow.

- [ ] **Step 8: push only the fix branch**

  Run:

  ```bash
  git push origin fix/pyo3-uv-pythonhome
  ```

  Verify exact fork SHA and upstream push URL `DISABLED`.

---

## Task 4: prove the canonical clean gate and integrate history-only

**Branch:** detached verification worktree, then `feat/execute-many`.

**Base:** exact Task 3 fix commit.

**Files:**

- generated evidence only under ignored `.artifacts/sql-auth`;
- no tracked source modification.

**Interfaces:**

- Consumes: the corrected `scripts/sql_auth/run_all.sh`.
- Produces: fresh-target zero-failure evidence usable by execute-many Task 10.

- [ ] **Step 1: create an exact detached verification worktree**

  From the repository root:

  ```bash
  git worktree add \
    --detach \
    .worktrees/gate-pyo3-uv-pythonhome \
    fix/pyo3-uv-pythonhome
  ln -s ../../.env.sql-auth.local \
    .worktrees/gate-pyo3-uv-pythonhome/.env.sql-auth.local
  ```

  Require clean detached HEAD and healthy authorized Docker SQL Server.

- [ ] **Step 2: run the complete runner with a fresh Cargo target**

  From the verification worktree:

  ```bash
  gate_target="$(mktemp -d \
    /private/tmp/fastmssql-pyo3-uv-full-gate.XXXXXX)"
  CARGO_TARGET_DIR="${gate_target}/cargo" \
  UV_CACHE_DIR="${gate_target}/uv-cache" \
    scripts/sql_auth/run_all.sh
  ```

  Do not activate the worktree environment manually. This intentionally
  proves that interpreter resolution is owned by the runner rather than an
  inherited caller `VIRTUAL_ENV`.

- [ ] **Step 3: require complete fresh evidence**

  Require:

  - every `.artifacts/sql-auth/*.exitcode` equals zero;
  - root Rust 116/116;
  - canonical matrix exactly 407/407 PASS;
  - strict, true-async, framework, resilience, load and
    original-local-regression lanes have zero failures/errors/skips/not-run;
  - report generation succeeds;
  - the cargo-test command artifact contains command-scoped `PYO3_PYTHON`
    and `PYTHONHOME`;
  - no tracked generated report change is used as a feature commit.

- [ ] **Step 4: integrate only the reviewed ancestry**

  In the existing feature worktree:

  ```bash
  git merge --ff-only fix/pyo3-uv-pythonhome
  git merge-base --is-ancestor \
    test/pyo3-uv-pythonhome feat/execute-many
  git merge-base --is-ancestor \
    fix/pyo3-uv-pythonhome feat/execute-many
  ```

  Require a clean feature worktree and no merge commit.

- [ ] **Step 5: verify final remote safety**

  Run:

  ```bash
  git status --short --branch
  git remote -v
  git config --get remote.upstream.pushurl
  git diff --check
  ```

  Keep `feat/execute-many` local until its complete Task 10 source, wheel,
  graph and evidence gate is finished. Do not push the original repository.
