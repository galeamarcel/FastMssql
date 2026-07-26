# FastMssql Enterprise Typed SQL Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` task-by-task, use
> `superpowers:test-driven-development` for every behavior change, and use
> `superpowers:verification-before-completion` before each integration claim.

**Goal:** Implement exact, safe and observable Python-to-SQL parameter typing
through FastMssql and its vendored Tiberius RPC path while preserving true
async execution and branch-per-problem history.

**Architecture:** Independent RED/FIX or RED/FEATURE branch pairs first repair
privacy and inferred scalar types. The final typed-descriptor pair adds a
closed SQL type parser, structured Tiberius parameter metadata, negotiated
ANSI collation, typed nulls, expansion metadata and consistent execution
through connection, transaction and batch paths. A history-preserving
cumulative merge is verified against real MSSQL, load, regression, wheel,
hosted operating-system and security gates before a separate live-audit
status commit.

**Tech stack:** Rust 1.94.0, edition 2024, PyO3 0.29.0, Tokio 1.52.3, local
Tiberius 0.12.3 source, Python 3.11+, pytest, uv, maturin, Docker, Microsoft
SQL Server 2022, Bash, Git and GitHub Actions.

## Global constraints

- Work and push only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep `upstream` fetch-only and its push URL exactly `DISABLED`.
- Base the design on cumulative SHA
  `fa0ffa38be32669a838d469132e5e02b8cb5234c`.
- Preserve every branch and commit named in the design; do not squash the
  problem-specific history.
- Create each branch in its own `.worktrees/<hyphenated-branch-name>`
  worktree.
- A RED branch changes only the focused tests and, when required, the
  corresponding SQL-auth case specification.
- A FIX/FEATURE branch must descend from its RED branch and contain the
  production change needed for that focused contract.
- Do not weaken assertions, catch broad exceptions, convert required tests
  into skips, or accept a successful `CAST` as proof of the input wire type.
- Do not allow raw user SQL type text to reach `sp_executesql`.
- Do not retain a Python object, GIL-bound reference or Python callback across
  an `await`.
- Preserve the 2,098 user-parameter ceiling and bounded iterable expansion.
- Preserve pool, transaction, timeout, lifecycle and cancellation semantics.
- Keep named execution, output parameters, multiple result sets, streaming,
  TVPs, native bulk metadata and exact money input out of this feature.
- Do not change package version or publish a release.
- Update `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` only after final
  exact-SHA verification.
- Perform a self-review and `git diff --check` before every commit and merge.
- Before any focused real-MSSQL pytest command in a worktree, load the shared
  environment without printing it:

  ```bash
  set -a
  source ../../.env.sql-auth.local
  set +a
  ```

  Build/install that worktree's own extension before treating a result as
  authoritative; never let an editable install from another worktree mask
  the candidate source.

---

## Task 1: Publish the reviewed umbrella design and executable plan

**Branch:** `docs/typed-sql-parameters-design`

**Files:**

- Create:
  `docs/superpowers/specs/2026-07-26-fastmssql-typed-sql-parameters-design.md`
- Create:
  `docs/superpowers/plans/2026-07-26-fastmssql-typed-sql-parameters.md`

- [ ] **Step 1: Confirm exact ancestry and repository boundary**

Run:

```bash
git rev-parse HEAD
git merge-base --is-ancestor \
  fa0ffa38be32669a838d469132e5e02b8cb5234c HEAD
git remote get-url origin
git remote get-url --push upstream
git status --short --branch
```

Expected: HEAD is the design branch based directly on the cumulative SHA,
origin is `galeamarcel/FastMssql`, upstream push is `DISABLED`, and only the
two new documentation files are uncommitted.

- [ ] **Step 2: Self-review both artifacts**

Run:

```bash
rg -n 'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s|Similar to Tas[k]' \
  docs/superpowers/specs/2026-07-26-fastmssql-typed-sql-parameters-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-typed-sql-parameters.md
git diff --check
git diff --stat
git diff -- \
  docs/superpowers/specs/2026-07-26-fastmssql-typed-sql-parameters-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-typed-sql-parameters.md
```

Expected: no placeholder match, clean whitespace, no audit status claim, no
authorization outside the fork, and exact coverage of every branch, API,
type, error, privacy and verification invariant.

- [ ] **Step 3: Commit and push only the design branch**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-26-fastmssql-typed-sql-parameters-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-typed-sql-parameters.md
git commit -m "docs: design enterprise typed SQL parameters"
git push -u origin docs/typed-sql-parameters-design
git ls-remote --heads origin docs/typed-sql-parameters-design
git remote get-url --push upstream
```

Expected: local and fork branch SHAs match and upstream prints `DISABLED`.

---

## Task 2: Redact `Parameter.__repr__` on a dedicated test/fix pair

**Branches:**

```text
test/parameter-repr-redaction
  -> fix/parameter-repr-redaction
```

**Files:**

- Modify: `tests/test_parameters.py`
- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `src/py_parameters.rs`

- [ ] **Step 1: Create the RED worktree from the final design commit**

Run from the primary checkout:

```bash
git worktree add \
  .worktrees/test-parameter-repr-redaction \
  -b test/parameter-repr-redaction \
  docs/typed-sql-parameters-design
```

- [ ] **Step 2: Write the redaction contract**

Change unit expectations so scalar and expanded representations contain only:

```text
sql_type
direction=INPUT
expanded=True|False
value=<redacted>
```

Add a value whose `__repr__()` both returns a secret sentinel and increments a
counter. Assert the counter remains zero and the sentinel is absent.

Add `PARAM-031` to the SQL-auth design and one strict test which asserts the
same behavior for a long credential-like string, binary content, Decimal and
a custom object.

- [ ] **Step 3: Observe RED on unchanged implementation**

Build/install the unchanged branch extension if required, then run:

```bash
uv run pytest \
  tests/test_parameters.py \
  tests/sql_auth_strict/test_parameters_strict.py \
  -k 'repr or PARAM_031' -q
```

Expected: failure because current `Parameter.__repr__()` evaluates and embeds
the value representation. Record the exact failing node and sentinel
evidence.

- [ ] **Step 4: Commit and push the RED branch**

Run:

```bash
git diff --check
git add \
  tests/test_parameters.py \
  tests/sql_auth_strict/test_parameters_strict.py \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md
git commit -m "test: require parameter repr redaction"
git push -u origin test/parameter-repr-redaction
```

- [ ] **Step 5: Create FIX and remove every value repr call**

Create `fix/parameter-repr-redaction` from the RED branch. Change
`Parameter.__repr__()` to format metadata only. Do not bind the value or call
Python from the method except what PyO3 requires to enter it.

- [ ] **Step 6: Verify focused GREEN and regression**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
uv run pytest tests/test_parameters.py -q
uv run pytest \
  tests/sql_auth_strict/test_parameters_strict.py \
  -k 'conversion_error_is_stable_and_redacted or parameter_repr' -q
```

Expected: all pass and no secret sentinel appears in captured output.

- [ ] **Step 7: Commit and push FIX**

Run:

```bash
git diff --check
git add src/py_parameters.rs
git commit -m "fix: redact parameter representations"
git push -u origin fix/parameter-repr-redaction
```

---

## Task 3: Bind Python booleans as BIT

**Branches:**

```text
test/bool-parameter-wire-type
  -> fix/bool-parameter-wire-type
```

**Files:**

- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `src/parameter_conversion.rs`

- [ ] **Step 1: Create RED from `fix/parameter-repr-redaction`**

Update `PARAM-003` to query `SQL_VARIANT_PROPERTY(@P1, 'BaseType')` and assert
`bit` for both boolean values before checking the round-tripped value.

- [ ] **Step 2: Observe and commit RED**

Run the exact parameter test against the real SQL-auth container.

Expected: both cases report `bigint`. Commit only the test change as:

```text
test: prove boolean parameter wire type
```

Push only `test/bool-parameter-wire-type`.

- [ ] **Step 3: Implement the minimal type-order fix**

Create `fix/bool-parameter-wire-type` from RED. Detect `PyBool` before
`PyInt`. Keep raw integers as signed `i64`.

- [ ] **Step 4: Verify and publish**

Run focused SQL-auth, integer boundaries, unit tests, Cargo tests, fmt and
Clippy. Assert `True` is `bit` and integer `1` remains `bigint`. Commit:

```text
fix: bind Python booleans as SQL bit
```

Push only the fork branch.

---

## Task 4: Support Tiberius Numeric scale 38 without panic

**Branches:**

```text
test/tiberius-numeric-scale-38
  -> fix/tiberius-numeric-scale-38
```

**Files:**

- Modify: `vendor/tiberius/src/tds/numeric.rs`
- Modify: `tests/sql_auth_strict/test_type_mapping_strict.py`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`

- [ ] **Step 1: Add RED unit and SQL-auth coverage**

From `fix/bool-parameter-wire-type`, create the RED branch.

Add vendor unit tests for:

```text
Numeric::new_with_scale(1, 38)
precision == 38
Numeric::new_with_scale(0, 38)
DECIMAL(38,38) decode
```

Extend `TYPE-004` with real MSSQL values `1E-38`, `-1E-38`, and zero cast as
`DECIMAL(38,38)`.

- [ ] **Step 2: Observe RED safely**

Run the Rust unit first. Run the SQL-auth case in a child pytest process so a
Rust panic cannot terminate the controlling shell.

Expected: the constructor assertion or equivalent panic proves the scale-38
gap. Commit only tests on `test/tiberius-numeric-scale-38`.

- [ ] **Step 3: Fix numeric scale and precision**

Create FIX from RED. Permit scale through 38. Compute precision as:

```text
max(decimal digits in absolute coefficient, scale, 1)
```

Use checked arithmetic and reject values outside SQL Server's 38-digit
contract before `Numeric` construction in FastMssql. Do not weaken the
maximum precision.

- [ ] **Step 4: Verify and document local vendor patch**

Run vendor-focused unit tests, all Cargo tests and the real `TYPE-004` case.
Update `FASTMSSQL_PATCH.md` with the precise numeric correction and state that
no external Tiberius fork was created.

Commit:

```text
fix: support SQL numeric scale 38
```

Push only `fix/tiberius-numeric-scale-38`.

---

## Task 5: Add exact Python Decimal input

**Branches:**

```text
test/decimal-parameter-input
  -> feat/decimal-parameter-input
```

**Files:**

- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `tests/test_parameter_conversions_advanced.py`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/batch.rs`
- Modify: `src/types.rs`

- [ ] **Step 1: Replace the limitation test with exact RED contracts**

Create RED from `fix/tiberius-numeric-scale-38`.

Change `PARAM-006` to bind Python Decimal directly and verify:

```text
0
-0.0001
12.3400
1E-38
38-digit positive maximum
38-digit negative maximum
positive exponent
```

Assert the returned object is `Decimal`, the value and exponent are exact,
and `SQL_VARIANT_PROPERTY` reports `numeric` with the expected precision and
scale. Add deterministic failures for NaN, sNaN, infinities and values over
38 digits, with no value content in the error.

- [ ] **Step 2: Observe and commit RED**

Expected: `Unsupported type: Decimal`. Commit and push only the test branch.

- [ ] **Step 3: Implement exact `Decimal.as_tuple()` conversion**

Create `feat/decimal-parameter-input`.

Add a cached `decimal.Decimal` class, checked coefficient/exponent parsing,
`FastParameterValue::Numeric`, and a redacted parameter conversion error
helper. Never use `float(Decimal)` or locale-sensitive formatting.

Update bulk null inference and every exhaustive FastParameter match.

- [ ] **Step 4: Verify exactness and regression**

Run focused unit tests, `PARAM-006`, `TYPE-004`, batch tests, all Cargo tests,
fmt and Clippy. Include a repeated round trip for trailing zero scale.

Commit:

```text
feat: support exact Python decimal parameters
```

Push only the fork feature branch.

---

## Task 6: Add Python time input

**Branches:**

```text
test/time-parameter-input
  -> feat/time-parameter-input
```

**Files:**

- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/batch.rs`

- [ ] **Step 1: Make `PARAM-013` a real TIME contract**

Create RED from `feat/decimal-parameter-input`. Bind midnight, a
microsecond-bearing time and the maximum Python time. Verify the effective
type is `time(7)` and the round trip is exact. Assert an aware Python time is
rejected because SQL TIME has no offset.

- [ ] **Step 2: Observe `Unsupported type: time` and commit RED**

Commit only tests and push `test/time-parameter-input`.

- [ ] **Step 3: Implement naive time conversion**

Create feature from RED. Add `chrono::NaiveTime` and
`FastParameterValue::Time`, with aware-time detection before extraction.
Update bulk null inference.

- [ ] **Step 4: Verify and publish**

Run `PARAM-013`, `TYPE-010`, batch tests, Cargo, fmt and Clippy. Commit:

```text
feat: support Python time parameters
```

Push only `feat/time-parameter-input`.

---

## Task 7: Preserve timezone-aware datetime offsets

**Branches:**

```text
test/datetimeoffset-parameter-preservation
  -> fix/datetimeoffset-parameter-preservation
```

**Files:**

- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/batch.rs`

- [ ] **Step 1: Strengthen `PARAM-012`**

Create RED from `feat/time-parameter-input`.

Keep the naive `DATETIME2(7)` assertion. For aware values at positive,
negative and boundary offsets, assert:

```text
BaseType == datetimeoffset
Scale == 7
returned tzinfo offset equals input
returned UTC instant equals input
```

Add invalid non-minute and beyond-14-hour offset cases.

- [ ] **Step 2: Observe current DATETIME2/lost-offset RED**

Commit tests only and push the RED branch.

- [ ] **Step 3: Detect aware datetime first and retain FixedOffset**

Create FIX. Add a `DateTime<FixedOffset>` FastParameter variant, validate
whole-minute and ±14-hour offset, then bind with Tiberius
`DateTimeOffset`. Naive datetime remains `DateTime2`.

- [ ] **Step 4: Verify and publish**

Run `PARAM-012`, `TYPE-011`, `TYPE-012`, batch tests, Cargo, fmt and Clippy.
Commit:

```text
fix: preserve aware datetime parameter offsets
```

Push only the fork branch.

---

## Task 8: Make UUID input and output symmetric

**Branches:**

```text
test/uuid-parameter-symmetry
  -> feat/uuid-parameter-symmetry
```

**Files:**

- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `tests/sql_auth_strict/test_type_mapping_strict.py`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/type_mapping.rs`
- Modify: `src/batch.rs`

- [ ] **Step 1: Write symmetric UUID RED contracts**

Create RED from `fix/datetimeoffset-parameter-preservation`.

Change `PARAM-014` to bind a Python `UUID`, prove
`BaseType == uniqueidentifier`, and require a Python `UUID` result equal to
the input. Change `TYPE-013` to require Python `UUID` output.

- [ ] **Step 2: Observe input rejection and string output**

Commit tests only and push the RED branch.

- [ ] **Step 3: Implement UUID conversion in both directions**

Create feature from RED. Cache the real `uuid.UUID` class. Input reads its
16-byte representation into Rust `uuid::Uuid`; output calls cached
`uuid.UUID(bytes=...)`. Do not identify arbitrary objects solely by class
name.

Update bulk null inference.

- [ ] **Step 4: Verify and publish**

Run `PARAM-014`, `TYPE-013`, general type mapping, batch tests, Cargo, fmt and
Clippy. Commit:

```text
feat: provide symmetric UUID parameters
```

Push only `feat/uuid-parameter-symmetry`.

---

## Task 9: Add the complete typed descriptor and safe Tiberius metadata

**Branches:**

```text
test/typed-parameter-descriptor
  -> feat/typed-parameter-descriptor
```

**Test files:**

- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/test_parameters.py`
- Modify: `tests/test_parameters_edge_cases.py`
- Modify: `tests/test_parameter_conversions_advanced.py`
- Modify: `tests/sql_auth_strict/test_parameters_strict.py`
- Modify: `tests/sql_auth_strict/test_transactions_strict.py`
- Modify: `tests/sql_auth_strict/test_batch_strict.py`
- Modify: `tests/sql_auth_strict/test_resilience_load.py`

**Implementation files:**

- Create: `src/sql_parameter_type.rs`
- Modify: `src/lib.rs`
- Modify: `src/py_parameters.rs`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/batch.rs`
- Modify: `src/types.rs`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Create: `vendor/tiberius/src/sql_parameter_type.rs`
- Modify: `vendor/tiberius/src/lib.rs`
- Modify: `vendor/tiberius/src/to_sql.rs`
- Modify: `vendor/tiberius/src/client.rs`
- Modify: `vendor/tiberius/src/query.rs`
- Modify: `vendor/tiberius/src/tds/context.rs`
- Modify: `vendor/tiberius/src/tds/stream/token.rs`
- Modify: `vendor/tiberius/src/tds/codec/rpc_request.rs`
- Modify: `vendor/tiberius/src/tds/codec/column_data.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`

- [ ] **Step 1: Add SQL-auth cases `PARAM-025` through `PARAM-030`,
      `PARAM-032`, and `PARAM-033`**

Create RED from `feat/uuid-parameter-symmetry`.

Add every case ID and its exact description to the SQL-auth design in the
same commit as its one owning test node.

Use one helper query returning `BaseType`, `Precision`, `Scale` and
`MaxLength` through `SQL_VARIANT_PROPERTY`.

Coverage must include:

```text
BIT; all integer widths; REAL/FLOAT
DECIMAL/NUMERIC p,s and typed NULL
CHAR/VARCHAR/NCHAR/NVARCHAR limited and MAX
BINARY/VARBINARY limited and MAX
UNIQUEIDENTIFIER
DATE/TIME
DATETIME/SMALLDATETIME/DATETIME2/DATETIMEOFFSET
XML
```

Assert malformed and injection-shaped declarations fail before acquiring a
connection. Assert typed iterable children are all `INT`, not BIGINT.
Assert non-input direction is preserved in the descriptor but rejected by
query/execute. Assert plain `[Parameter(...)]` works.

Exercise connection query/execute, transaction query/execute and batch
execution. Add 1,000 concurrent typed operations under a bounded pool and
reconcile success count, max in-flight, physical connection/session bound and
effective types.

- [ ] **Step 2: Add Python API unit RED contracts**

Assert:

```text
canonical sql_type
direction
precision
scale
length including "MAX"
expanded and is_expanded
matching/redundant metadata
conflicting metadata errors
safe supported grammar
safe rejected grammar
Parameters.add/set metadata forwarding
to_list compatibility
```

- [ ] **Step 3: Observe and commit RED**

Run construction-only tests first, then focused real-MSSQL tests. Expected
failures include unexpected keyword arguments and effective BIGINT/NVARCHAR
types. Commit only tests/spec changes:

```text
test: require end-to-end typed SQL parameters
```

Push only `test/typed-parameter-descriptor`.

- [ ] **Step 4: Implement the closed Tiberius `SqlParameterType`**

Create `feat/typed-parameter-descriptor`.

The enum must:

- represent only the types in the approved design;
- validate all precision, scale and length ranges;
- emit canonical declaration text;
- derive compatible TDS `TypeInfo`;
- require session collation for character metadata;
- never accept a raw declaration string.

Add focused unit tests for every declaration and invalid boundary.

- [ ] **Step 5: Carry explicit metadata through the Tiberius RPC**

Add default `ToSql::sql_parameter_type() -> Option<SqlParameterType>`.
Change the internal RPC iterator to carry value plus optional type.

For typed values:

1. write enum-derived declaration into `@params`;
2. write enum-derived `TYPE_INFO` in `RpcParam`;
3. encode the value against that type info;
4. return a typed error for incompatible value/type metadata.

Existing untyped Tiberius APIs must produce byte-identical inferred behavior.
Add encoder tests for integer, decimal, ANSI/Unicode, binary, time,
datetime2, datetimeoffset and typed null metadata.

- [ ] **Step 6: Track negotiated collation**

Store `Option<Collation>` in Tiberius `Context`. Update it on
`TokenEnvChange::SqlCollation`. Preserve it across pool reset. Add tests for
login/update/reset behavior.

Eliminate every `unwrap()` on a typed ANSI collation path; missing or
unsupported encoding must return an error.

- [ ] **Step 7: Implement the FastMssql SQL type parser**

Create `src/sql_parameter_type.rs` with a hand-written closed parser.
Accept case/whitespace and only the approved grammar. Merge inline modifiers
with keyword metadata and reject conflicts.

Unit-test every accepted type, default, boundary, malformed token, comment,
semicolon, bracket, nested expression, unsupported type, out-of-range value
and arbitrary suffix.

- [ ] **Step 8: Upgrade `Parameter` and `Parameters`**

Store the parsed enum rather than an unchecked string. Expose canonical
metadata getters and the compatibility `is_expanded` alias. Implement the
new constructor and add/set signatures. Preserve `to_list()` raw values.

Accept all directions in the descriptor; execution supports INPUT only.

- [ ] **Step 9: Preserve descriptors in conversion and expansion**

Stop calling `Parameters.to_list()` from wire conversion. Read positional
Parameter objects directly. Recognize Parameter inside ordinary lists.

For each parameter:

- attach a safe index;
- reject non-input direction;
- convert according to explicit type;
- type typed `None`;
- propagate one parsed type to every expanded child;
- retain the bounded 2,098 parameter limit.

No error may evaluate or include the value representation.

- [ ] **Step 10: Implement compatible value conversion**

Implement range and kind checks for every accepted type. Use:

- `u8`, `i16`, `i32`, `i64` for exact integer widths;
- `f32`/`f64` for REAL/FLOAT with finite validation;
- exact Numeric rescaling for DECIMAL/NUMERIC;
- negotiated collation for ANSI strings;
- UTF-16 code-unit lengths for Unicode strings;
- byte lengths for binary;
- UUID and XML owned values;
- exact integer temporal rounding and rollover.

Limited values that exceed length fail; no truncation is permitted.

- [ ] **Step 11: Update stubs and vendor patch record**

Document constructor keywords, metadata properties, canonicalization,
supported declarations, direction limitations, typed nulls, expansion and
ConversionError behavior in both `.pyi` surfaces.

Document the typed RPC and collation extension in
`FASTMSSQL_PATCH.md`, explicitly retaining the no-external-fork statement.

- [ ] **Step 12: Run focused GREEN gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
uv run pytest \
  tests/test_parameters.py \
  tests/test_parameters_edge_cases.py \
  tests/test_parameter_conversions_advanced.py -q
uv run pytest \
  tests/sql_auth_strict/test_parameters_strict.py \
  tests/sql_auth_strict/test_type_mapping_strict.py \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_resilience_load.py -q
```

Expected: every focused contract passes with zero skip, error, warning or
secret sentinel.

- [ ] **Step 13: Self-review implementation boundaries**

Run:

```bash
git diff --check
git diff --stat test/typed-parameter-descriptor..HEAD
rg -n 'unwrap\\(|expect\\(|panic!|todo!|unimplemented!' \
  src/sql_parameter_type.rs \
  src/parameter_conversion.rs \
  src/py_parameters.rs \
  vendor/tiberius/src/sql_parameter_type.rs \
  vendor/tiberius/src/client.rs \
  vendor/tiberius/src/tds/codec/rpc_request.rs \
  vendor/tiberius/src/tds/codec/column_data.rs
```

Review every match manually. Existing proven internal assertions may remain
only when unreachable from user input; no new user-controlled panic may
remain.

- [ ] **Step 14: Commit and push feature**

Use logical implementation commits on the same feature branch, with the
final branch containing all required code and docs. Push only:

```text
feat/typed-parameter-descriptor
```

Verify fork SHA parity and upstream `DISABLED`.

---

## Task 10: Create and verify the exact cumulative technical merge

**Branch:** temporary detached verification worktree, then
`test/sql-auth-validation`

- [ ] **Step 1: Prove complete branch ancestry**

Run `git merge-base --is-ancestor` for every RED branch against its paired
fix/feature and for every pair against the final descriptor feature.

Expected: all commands exit zero; no branch was rebased away or squashed.

- [ ] **Step 2: Create a history-preserving merge**

Create a temporary integration branch from the current
`test/sql-auth-validation` tip and merge
`feat/typed-parameter-descriptor` with `--no-ff`.

Expected: no unrelated conflict, all named problem commits are ancestors, and
the original cumulative branch remains untouched until verification.

- [ ] **Step 3: Run Rust quality gates on the exact merge**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
```

Record exact test totals and toolchain versions.

- [ ] **Step 4: Run the complete Docker/MSSQL runner**

Run the exact merge:

```bash
scripts/sql_auth/run_all.sh
```

Required lanes:

```text
Rust
strict
async
framework
resilience
load
original-local-regression
matrix
```

All must exit zero with zero failed, error, skipped or not-run cases. Record
the exact totals and typed parameter case durations.

- [ ] **Step 5: Run dedicated typed load**

Run at least 1,000 concurrent typed operations with configured concurrency
and pool/physical connection limits. Require:

```text
submitted == completed == succeeded == 1000
failed == timed_out == cancelled == 0
max_in_flight <= configured concurrency
physical sessions <= configured pool maximum
every sampled effective type matches its descriptor
```

Increase the operation count only after the 1,000-operation correctness gate
is exact. Do not use this feature gate to benchmark SQL Server capacity.

- [ ] **Step 6: Build and install wheels**

Build the wheel from the exact merge, install it into a fresh isolated
environment and rerun installed-package parameter contracts. Verify no source
tree import masks the wheel.

- [ ] **Step 7: Run privacy and repository-boundary checks**

Scan generated artifacts, reports, logs, repr output and errors for all test
passwords and sentinel values. Confirm:

```bash
git remote get-url origin
git remote get-url --push upstream
git status --short
```

Expected: fork origin, `DISABLED`, clean tree.

- [ ] **Step 8: Publish technical merge only to the fork and inspect CI**

Push the technical branch to origin. Read public GitHub Actions state only.
Require raw Cargo, Rust tests, wheel build/install and installed contracts on
Linux, macOS and Windows, plus RustSec.

If hosted CI fails, reproduce and fix on new problem-specific branches; do
not edit the technical merge branch directly.

- [ ] **Step 9: Merge into the cumulative fork branch**

Only after local and hosted gates pass, merge the verified technical commit
into `test/sql-auth-validation`, push origin, and prove local/fork SHA parity.
Never push upstream.

---

## Task 11: Update the live audit from final evidence

**Branches:**

```text
docs/typed-sql-parameters-status
  -> test/sql-auth-validation
```

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Regenerate: `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate: `docs/SQL_AUTH_TEST_REPORT.md`

- [ ] **Step 1: Create status branch from exact verified cumulative SHA**

Do not base it on a feature branch or pre-verification merge.

- [ ] **Step 2: Regenerate evidence without hand-editing generated files**

Use the existing report generator and exact final artifacts. Verify matrix
case IDs are one-to-one with the SQL-auth specification and every case is
PASS.

- [ ] **Step 3: Update the parameter audit section**

Record:

- measured original effective types;
- each root cause;
- each RED/fix/feature branch and commit SHA;
- effective type table and exclusions;
- exact Rust/Python/Docker/load totals;
- exact hosted workflow and job links;
- privacy and fork-boundary evidence;
- remaining output/result/TVP/bulk/money work.

Remove only findings proven resolved. Do not mark the broader enterprise audit
complete while other sections remain open.

- [ ] **Step 4: Self-review status evidence**

Check every SHA, URL, count and claim against authoritative Git, JSON/XML
artifacts, command output and GitHub Actions state. Run secret and placeholder
scans plus `git diff --check`.

- [ ] **Step 5: Commit, push and merge status only on the fork**

Commit:

```text
docs: record typed SQL parameter verification
```

Push the status branch to origin, merge history-preservingly into
`test/sql-auth-validation`, push origin, and verify parity plus upstream
`DISABLED`.

---

## Final completion evidence for this audit subsystem

Before calling the parameter subsystem resolved, collect one table mapping
each explicit design requirement to:

```text
source implementation
focused RED output
focused GREEN output
real MSSQL case ID
final cumulative SHA
local full-run artifact
hosted job where applicable
live-audit line
```

Any requirement with missing, indirect, stale or contradictory evidence
remains open. Completion of this subsystem does not complete the persistent
production-readiness objective; continue with the next unresolved audit
section after the status merge.
