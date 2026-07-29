# FastMssql PyO3 uv Embedded-Python Home Design

**Status:** approved under the standing design/specification authorization;
ready for a separate test-first corrective branch.

**Date:** 2026-07-29

## Purpose

Make the canonical local SQL-auth runner execute raw PyO3 Rust unit tests
reliably when the project environment uses a relocatable CPython installed by
uv. The correction must preserve raw Cargo link mode, remain local to the
`cargo test` lane, and avoid relying on a caller-provided environment
workaround.

## Reproduction and root cause

The complete `execute_many` gate ran from clean candidate
`7c6b9a3e7d40b7ab82d5ef130b64841a2864788d` with a fresh external Cargo
target. Every lane except `cargo test --locked` passed.

The Rust test executable linked successfully to:

```text
~/.local/share/uv/python/cpython-3.12.10-macos-aarch64-none/lib/libpython3.12.dylib
```

It then failed at the first `Python::initialize()` call before a Rust
assertion ran:

```text
sys.base_prefix = '/install'
ModuleNotFoundError: No module named 'encodings'
```

The uv-managed library is relocatable, but an embedded test executable is not
the Python executable and cannot derive the relocated standard-library home
from its own path. With no `PYTHONHOME`, CPython therefore retained its build
prefix `/install`.

One-variable confirmation used the selected interpreter's real
`sys.base_prefix` as `PYTHONHOME`. The same already-built test executable then
passed all 116 Rust tests. A previously green cached target was not equivalent
evidence: it linked to Homebrew Python 3.13 rather than the worktree's uv
environment.

## Required behavior

The canonical local runner must:

1. synchronize the locked project environment before resolving Python;
2. resolve both `sys.executable` and `sys.base_prefix` through
   `uv run python`, so they belong to the current worktree rather than an
   inherited virtual environment;
3. pass the resolved executable as `PYO3_PYTHON` and the resolved base prefix
   as `PYTHONHOME`;
4. scope both variables only to `cargo test --locked`;
5. preserve the existing raw Cargo command, lock enforcement, lane name,
   failure accounting, logging and command evidence;
6. leave Cargo fmt/Clippy, maturin, vendored Tiberius, pytest, SQL-auth and
   report-generation environments unchanged;
7. fail closed if interpreter discovery fails or produces an empty path.

The runner must not hard-code a user path, Python version, Homebrew prefix,
uv cache path, framework path or platform-specific standard-library
directory.

## Considered approaches

### 1. Require callers to export `PYTHONHOME`

Rejected. It makes a clean gate depend on undocumented shell state, permits a
different Python than the worktree environment, and would reproduce the
current cached-target ambiguity.

### 2. Resolve and scope the environment in the runner

Selected. `uv run python` is already part of the locked runner and identifies
the exact project interpreter. `sys.base_prefix` is CPython's portable
boundary for the base installation, including virtual environments.
Command-scoped variables fix embedded initialization without contaminating
later Python processes.

### 3. Configure every Rust test's `PyConfig`

Rejected for this defect. It would duplicate environment discovery across
many modules, change production-adjacent test initialization, and still need
an external source of the relocated home. The failure is at the runner-to-
embedded-runtime boundary and should be fixed there.

## Design

After `uv sync` and before the raw Cargo test lane, the runner resolves and
validates two values before marking them readonly:

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

The explicit conditional is required because the runner intentionally does
not use `set -e`; combining discovery and `readonly` in one declaration could
mask a failed command substitution. The existing lane becomes semantically:

```bash
record cargo-test \
  env \
  "PYO3_PYTHON=${cargo_test_python}" \
  "PYTHONHOME=${cargo_test_python_home}" \
  cargo test --locked
```

Using `env` keeps the values out of the runner's global environment. The
existing `record()` function remains responsible for the exact exit code,
log and command artifact.

## Test strategy

The RED branch extends the existing PyO3 build contract before changing the
runner. It requires:

- discovery through `uv run python`;
- both `sys.executable` and `sys.base_prefix`;
- command-scoped `PYO3_PYTHON` and `PYTHONHOME`;
- `cargo test --locked` unchanged;
- discovery after `uv sync`;
- no global `export PYTHONHOME` or hard-coded installation path.

The fix branch must first make that focused contract green. Runtime
verification then uses a fresh external Cargo target and proves:

- the test executable links to the worktree-selected Python;
- all 116 Rust tests execute and pass;
- the complete canonical SQL-auth runner has zero failed required lanes;
- generated evidence reports exactly 407/407 required SQL-auth cases;
- no repository build cache, environment file or generated lockfile is
  committed.

## Branch topology

```text
docs/pyo3-uv-pythonhome-design
  -> test/pyo3-uv-pythonhome
  -> fix/pyo3-uv-pythonhome
  -> feat/execute-many
```

Every repository modification updates `VERSION.md`. All branches and commits
may be pushed only to `galeamarcel/FastMssql`; the original repository's push
URL remains `DISABLED`.

## Non-goals

- changing FastMssql runtime behavior or public APIs;
- changing PyO3 feature selection or extension-module ownership;
- changing the hosted Linux/macOS/Windows workflow that is already green;
- supporting arbitrary manually activated environments instead of the
  worktree's locked uv environment;
- publishing a wheel, release or upstream pull request.

## Acceptance criteria

- The focused contract is observed RED before the runner edit.
- Interpreter discovery is dynamic, non-empty and worktree-local.
- `PYO3_PYTHON` and `PYTHONHOME` are scoped only to raw Cargo tests.
- A fresh target runs 116/116 Rust tests without `/install` bootstrap errors.
- The canonical runner completes with zero required failed lanes.
- All generated matrix entries are complete and exactly 407/407 PASS.
- Git diff, credential, artifact and remote-safety checks are clean.
