# FastMssql PyO3 Build/Test Separation Design

**Status:** Approved by Marcel Galea on 2026-07-25

**Source baseline:** `test/sql-auth-validation` at `8191fff`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** all branches, commits and hosted validation belong
only to Marcel Galea's fork. No upstream branch, push or pull request is
authorized by this design.

## Problem

FastMssql currently enables PyO3's `extension-module` feature permanently:

```toml
pyo3 = {
    version = "0.29.0",
    features = ["extension-module", "abi3-py311", "chrono"],
}
```

The project also allows any maturin release from 1.0:

```toml
[build-system]
requires = ["maturin>=1.0,<2.0"]
```

These settings conflate two different link modes:

1. a Python extension module built for import and distribution must not link
   `libpython` on Unix;
2. ordinary Rust libraries, binaries and test executables must link the
   selected Python runtime when they reference Python symbols.

The permanent feature makes generic Cargo builds inherit extension-module
link behavior even when Cargo is building a Rust test executable.

On the approved source baseline, with no `PYO3_*`, `RUSTFLAGS`,
`PYTHON_SYS_EXECUTABLE` or `MACOSX_DEPLOYMENT_TARGET` override in the shell,
Cargo reports `pyo3 feature "extension-module"` as enabled. Both generic
commands fail on macOS arm64:

```text
cargo build --locked       FAIL: undefined _Py... symbols
cargo test --locked        FAIL: undefined _Py... symbols
```

The link command contains the normal macOS system frameworks but no Python
framework. The Rust test failure occurs before any of FastMssql's 13 unit
tests can execute.

An explicit local linker workaround can make the tests pass, but it hard-codes
one Homebrew Python framework path and is not portable. It is evidence that
the test code is valid, not an acceptable project configuration.

The extension itself remains buildable through the installed maturin 1.14.1
path because maturin configures extension-module linking for that build.
However, no hosted CI job currently executes raw Rust tests. The existing
Python CI builds through maturin, and the dependency workflow runs only
`cargo audit`. The linker regression can therefore remain invisible while
Python tests and release builds appear healthy.

PyO3's official FAQ identifies this class of failure and recommends removing
the deprecated permanent feature, then using maturin 1.9.4 or newer. Modern
maturin sets `PYO3_BUILD_EXTENSION_MODULE` only while it builds an extension:

- [PyO3 FAQ: cargo test linker failures](https://github.com/PyO3/pyo3/blob/main/guide/src/faq.md)
- [PyO3 build and distribution configuration](https://github.com/PyO3/pyo3/blob/main/guide/src/building-and-distribution.md)
- [maturin configuration](https://github.com/PyO3/maturin/blob/main/guide/src/config.md)

## Goals

1. Make raw `cargo build --locked` link successfully without local linker
   flags.
2. Make raw `cargo test --locked` execute every FastMssql Rust unit test
   without local linker flags.
3. Preserve the importable ABI3 Python extension built by maturin.
4. Encode the manifest split in deterministic tests so the permanent feature
   cannot return silently.
5. Add a required hosted Rust gate on Linux, macOS and Windows.
6. Pin the Rust and Python inputs used by that gate.
7. Make the SQL-auth validation runner use the same locked Cargo command.
8. Prove that the rebuilt extension still imports and executes a real
   SQL-auth query against the dedicated Docker SQL Server.
9. Keep the production-readiness audit and upstream candidate roadmap
   accurate after the complete result is known.

## Non-goals

This candidate does not:

- change the FastMssql Python API, stubs or runtime behavior;
- change SQL execution, connection pooling, transactions or TDS handling;
- add Rust integration tests that import the crate as an `rlib`;
- add `rlib` to the crate types while only internal Rust unit tests exist;
- change the `abi3-py311` compatibility floor;
- change free-threaded Python behavior or the cibuildwheel platform matrix;
- upgrade PyO3, pyo3-async-runtimes, maturin's locked development version or
  unrelated dependencies;
- introduce a custom `build.rs` or platform-specific linker script;
- publish a package, create a Tiberius fork or contact the original FastMssql
  repository.

Those boundaries keep this build/CI correction independently reviewable and
revertible.

## Options considered

### Option A — Modern PyO3/maturin separation with a three-OS gate

Remove `extension-module` from the direct PyO3 dependency, require
`maturin>=1.9.4,<2.0`, and let maturin select extension behavior only for
extension builds. Add a dedicated hosted matrix that runs raw Cargo commands
on Linux, macOS and Windows.

**Selected.** This follows current upstream guidance, removes
platform-specific state from the manifest and proves the generic Cargo path
on every supported desktop platform.

### Option B — Modern separation with a Linux-only gate

Apply the same manifest correction but run hosted Rust tests only on Linux,
relying on tag-time wheel builds for macOS and Windows.

**Rejected.** The reproduced failure is linker- and platform-sensitive.
Release-only coverage would detect a regression too late and would not prove
that generic Cargo tests work on macOS or Windows.

### Option C — Explicit Cargo feature or linker workaround

Move `pyo3/extension-module` behind a project feature, pass it manually from
maturin, or add `RUSTFLAGS`/Python framework paths to tests and CI.

**Rejected.** This creates a second configuration switch that can drift,
retains deprecated behavior, or hard-codes one platform's Python layout.
Generic Cargo success must not depend on a hidden workaround.

## Approved architecture

### Two explicit build modes

FastMssql has two build modes with one manifest source of truth.

#### Generic Cargo mode

`Cargo.toml` keeps:

```toml
pyo3 = {
    version = "0.29.0",
    features = ["abi3-py311", "chrono"],
}
```

No project or transitive configuration may activate
`pyo3/extension-module` for the default Cargo feature set.

Without `PYO3_BUILD_EXTENSION_MODULE`, PyO3 performs the normal Python link
configuration needed by Rust build products and test executables. Both
commands must work in an ordinary shell:

```bash
cargo build --locked
cargo test --locked
```

Neither command may require `RUSTFLAGS`, a custom framework path,
`PYO3_CONFIG_FILE` or `PYO3_BUILD_EXTENSION_MODULE`.

#### Maturin extension mode

`pyproject.toml` requires:

```toml
[build-system]
requires = ["maturin>=1.9.4,<2.0"]
build-backend = "maturin"
```

The existing tool configuration remains:

```toml
[tool.maturin]
features = ["pyo3/abi3-py311"]
```

Maturin supplies extension-module build behavior for `maturin develop`,
`maturin build` and PEP 517 wheel/sdist builds. FastMssql does not persist
that behavior in Cargo features.

### Crate type

`crate-type = ["cdylib"]` remains unchanged. PyO3 recommends adding `rlib`
when Rust integration tests need to import the library crate. FastMssql's
current 13 Rust tests are internal module tests, so adding a second artifact
type would expand build output without serving this candidate.

If a later candidate adds external Rust integration tests, it must evaluate
`rlib` separately.

### Lockfile behavior

Maturin 1.14.1 is already pinned in the development dependency group and
resolved in `uv.lock`. Raising the PEP 517 minimum to 1.9.4 must not
gratuitously update unrelated lock entries.

`uv lock --check` decides whether a lockfile change is necessary. If the
existing lock remains valid, the fix branch leaves `uv.lock` untouched.

## Branch and commit architecture

Every phase starts from the approved cumulative baseline or the preceding
phase and is pushed only to `origin`.

### Design branch

```text
docs/pyo3-build-test-design
```

Contains this approved design and the detailed implementation plan only.

### RED test branch

```text
test/pyo3-build-contract
```

Contains the deterministic contract tests and the hosted Rust-test workflow.
It does not remove the permanent feature or raise the maturin minimum.

The branch must prove both forms of RED:

- static contract failure against the old manifests;
- raw `cargo test --locked` linker failure on the reproduced macOS
  environment.

The workflow itself is test infrastructure and belongs to the test branch.
Its green hosted result is required only after the fix branch is integrated
into the cumulative fork branch.

### Fix branch

```text
fix/pyo3-build-test-separation
```

Starts from the RED test branch and contains only:

- the PyO3 feature correction in `Cargo.toml`;
- the maturin minimum correction in `pyproject.toml`;
- the locked Cargo invocation in the SQL-auth runner;
- any lockfile delta proven necessary by `uv lock --check`.

It does not contain audit status claims.

### Status branch

```text
docs/pyo3-build-test-status
```

Starts only after local and hosted verification. It records evidence and
remaining limitations in:

- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`.

The cumulative branch receives technical changes and status changes through
separate explicit merges, preserving the existing history discipline.

## Deterministic test contract

`tests/test_pyo3_build_contract.py` uses Python 3.11+'s standard `tomllib` and
plain text checks consistent with the existing dependency-security contract
tests. It adds no parser dependency.

### Manifest assertions

The tests require:

1. the direct PyO3 feature list excludes `extension-module`;
2. `abi3-py311` and `chrono` remain enabled;
3. the PEP 517 build requirements include exactly
   `maturin>=1.9.4,<2.0`;
4. `[tool.maturin].features` retains `pyo3/abi3-py311`;
5. the crate type remains exactly `["cdylib"]`.

The assertions encode the separation, not incidental TOML formatting.

### Workflow assertions

The tests require the dedicated workflow to contain:

- `push`, `pull_request` and `workflow_dispatch` triggers;
- `permissions: contents: read`;
- an exact Linux/macOS/Windows runner matrix;
- `fail-fast: false`, so all platform evidence is collected;
- `RUST_TOOLCHAIN: "1.94.0"` across the matrix;
- CPython 3.13 across the matrix;
- the repository's existing immutable checkout reference,
  `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1`;
- the repository's existing Python setup reference,
  `astral-sh/setup-uv@v8.3.2`;
- `cargo build --locked`;
- `cargo test --locked`;
- `timeout-minutes: 30`.

The workflow contract rejects:

- `continue-on-error: true`;
- shell fallbacks such as `|| true`;
- `RUSTFLAGS`;
- `PYO3_BUILD_EXTENSION_MODULE`;
- hard-coded Python library or framework paths.

The workflow may use caches only if they do not change the commands or success
criteria. A cache miss must behave identically to a cache hit.

### SQL-auth runner assertion

The contract requires `scripts/sql_auth/run_all.sh` to record:

```text
cargo test --locked
```

instead of unlocked `cargo test`.

This keeps the local enterprise gate aligned with the hosted gate and the
repository's checked-in dependency graph.

## Hosted CI design

A dedicated workflow, `.github/workflows/rust-unit-tests.yml`, owns the raw
Rust build gate. It is separate from:

- the four-version Python/SQL Server test matrix;
- dependency security scanning;
- tag-time wheel publication.

This prevents tripling the already expensive SQL Server matrix and keeps a
Rust linker failure attributable to one job.

### Triggers

The workflow runs for:

- pushes to `master`;
- pushes to the cumulative fork branch `test/sql-auth-validation`;
- pull requests targeting `master`;
- manual `workflow_dispatch`.

The fork-specific push trigger gives the owner hosted evidence before any
upstream proposal. An eventual clean upstream candidate may drop that branch
name during rebase.

### Matrix

The matrix contains exactly:

```text
ubuntu-latest
macos-latest
windows-latest
```

`fail-fast: false` collects all three outcomes, but the matrix job fails if
any cell fails. No cell is experimental or allowed to fail.

All cells:

1. check out the exact fork commit;
2. install Rust 1.94.0, matching the dependency-security policy;
3. select CPython 3.13 through `astral-sh/setup-uv@v8.3.2`;
4. run `cargo build --locked`;
5. run `cargo test --locked`.

The job receives no SQL credentials and starts no database service.

### Failure behavior

A compile, link, unit-test or lockfile failure returns the native nonzero exit
status. The workflow contains no catch, retry, fallback linker configuration
or result rewriting.

The job timeout stops a hung toolchain or linker. It does not convert a
timeout into success.

## Verification design

### RED evidence

Before the fix:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
cargo build --locked
cargo test --locked
```

Expected:

- the manifest contract fails because `extension-module` is present and the
  maturin floor is 1.0;
- both raw Cargo commands fail at the Python link boundary on the reproduced
  macOS environment;
- no FastMssql Rust unit test executes.

The failure output must be summarized without copying platform-specific user
paths into tracked fixtures.

### GREEN local evidence

After the fix, with relevant override variables absent:

```bash
uv run pytest tests/test_pyo3_build_contract.py -q
cargo build --locked
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
uv lock --check
uv run maturin develop --release
uv run maturin build --release
```

Required results:

- all build-contract tests pass;
- all 13 current Rust tests execute and pass;
- generic Cargo build and test link without workaround;
- formatting and Clippy pass;
- the Python environment remains locked;
- maturin builds both an installed extension and a wheel.

The exact Rust test count is recorded from final output. If implementation
adds no Rust tests, a count other than the baseline 13 requires investigation.

### Python and packaging smoke

The extension built by maturin must:

1. import from the project environment;
2. expose the expected FastMssql module surface;
3. open a SQL-auth connection to `fastmssql-sql-auth-dev`;
4. execute and fully consume a fixed parameterized query;
5. close the pool and leave no test-login SQL session behind.

The built wheel is inspected and installed in a clean temporary environment
before an import smoke. On macOS, `otool -L` must also show that the extension
inside the wheel has no Python framework dependency. The equivalent native
dependency inspection is required for any additional platform on which the
wheel smoke is executed. Import success alone is not accepted as proof that
the distributed extension is independent from a local `libpython`.

### Regression suites

The integrated technical tree runs:

- the full applicable upstream Python suite;
- the complete strict SQL-auth suite;
- the focused framework/startup checks;
- the relevant Tiberius unit and doctest suite;
- Ruff and Python bytecode compilation;
- dependency security audit;
- secret and tracked-environment scans;
- `git diff --check`.

This candidate adds no SQL behavior IDs. Existing strict matrix IDs must keep
their outcomes, and the final report records measured counts rather than
assuming historical counts.

### Hosted evidence

The candidate is not complete until the exact integrated commit has a green
`cargo build --locked` and `cargo test --locked` result on:

- Linux;
- macOS;
- Windows.

A local cross-compile, a release wheel build or two green operating systems
cannot substitute for the missing third hosted result.

## Documentation and status

The status branch updates the live audit only after every required local and
hosted result is known.

The audit records:

- the root cause and official PyO3 guidance;
- RED and GREEN branch/commit identifiers;
- local macOS raw Cargo results;
- the three hosted job results and exact tested commit;
- maturin extension/wheel and SQL-auth smoke evidence;
- regression totals;
- the absence of linker workarounds;
- remaining limitations and upstream publication status.

The upstream roadmap receives a separate candidate entry with:

- proposed clean branch and PR title;
- minimal intended diff;
- upstream rebase requirements;
- explicit owner approval gate for any future publication.

No package version is changed by this build/CI correction. The FastMssql
repository has no `VERSION.md` at the approved baseline, so this candidate
does not invent an unrelated version-history format. That exception is
reported in the final handoff.

## Security and repository boundaries

- No SQL password, GitHub token, local Python framework path or home-directory
  path enters a tracked file.
- CI receives only read access to repository contents.
- The raw Rust gate does not require secrets.
- Docker access remains limited to `fastmssql-sql-auth-dev`.
- Every branch is pushed only to
  `https://github.com/galeamarcel/FastMssql.git`.
- The `upstream` remote remains fetch-only with push disabled.
- No package publication, fork creation or upstream PR is part of this
  candidate.

## Acceptance criteria

The candidate is complete only when all statements below are proven:

- [ ] The RED test branch fails for the expected manifest contract.
- [ ] Raw Cargo build and test reproduce the Python linker failure before the
      fix.
- [ ] Default Cargo features no longer include `extension-module`.
- [ ] The PEP 517 maturin minimum is 1.9.4.
- [ ] `cargo build --locked` passes without linker overrides.
- [ ] `cargo test --locked` executes and passes every Rust unit test without
      linker overrides.
- [ ] `maturin develop` produces an importable extension.
- [ ] `maturin build` produces a wheel that imports from a clean environment.
- [ ] A real SQL-auth query succeeds through the rebuilt extension.
- [ ] The SQL-auth runner invokes locked Cargo tests.
- [ ] The hosted Linux gate is green for the integrated commit.
- [ ] The hosted macOS gate is green for the integrated commit.
- [ ] The hosted Windows gate is green for the integrated commit.
- [ ] Formatting, Clippy, Python regression, strict SQL-auth, Tiberius,
      dependency-security and secret gates pass.
- [ ] The audit and upstream roadmap record measured evidence and residual
      limits on a separate status branch.
- [ ] All commits are authored as Marcel Galea and pushed only to his fork.
- [ ] No upstream publication occurs without a new explicit approval.
