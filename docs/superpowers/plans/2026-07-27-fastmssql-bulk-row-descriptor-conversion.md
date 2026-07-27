# FastMssql Typed Bulk Row Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Use
> `superpowers:systematic-debugging` for unexpected results,
> `superpowers:test-driven-development` for the behavior change, and
> `superpowers:verification-before-completion` before every commit or
> completion claim. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make every compatibility `bulk_insert()` cell use the same closed
raw/typed conversion family as ordinary query execution, reject descriptors
which cannot represent one input cell, and preserve privacy-safe global bulk
position context on conversion errors.

**Architecture:** extract the non-expanded, INPUT-only descriptor conversion
from the existing query parameter path into one single-value helper. The
ordinary query path keeps its existing expansion branch and calls the helper
only for non-expanded descriptors. Compatibility bulk calls a second
single-cell entry point which detects `Parameter`, delegates to that helper,
and annotates the original exception with zero-based global row, column and
flattened parameter positions. It does not replace the exception class,
message, SQL type or reason.

**Tech Stack:** Rust 2024, PyO3 0.29, Tiberius 0.12.3, Python 3.11+,
pytest, maturin ABI3 and Docker SQL Server 2022 with SQL authentication.

**Program design:**
`docs/superpowers/specs/2026-07-27-fastmssql-batch-bulk-design.md`

**Documentation base:**
`2b0b206eac4b2165307d6369d7c0601a4ea33186`

## Global Constraints

- Work and push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep `Rivendael/FastMssql` fetch-only with push URL exactly `DISABLED`.
- Use project-local ignored worktrees and separate documentation, RED, fix
  and status branches.
- Base `fix/bulk-row-descriptor-conversion` on the observed RED commit.
- A RED commit may change tests, canonical specification and `VERSION.md`,
  but not production behavior.
- Update `VERSION.md` for every branch without changing the displayed
  package version `0.7.7` or publishing an artifact.
- Preserve the bounded one-chunk conversion, one atomic transaction, absolute
  deadline, cancellation, rollback and retirement behavior established by
  `fix/bulk-bounded-buffering`.
- Preserve ordinary query/batch descriptor behavior, including iterable
  expansion and exact existing typed-conversion errors.
- Never place Python values, SQL text, table names, column names or
  credentials in exception text or attributes.
- Do not retry after any earlier bulk chunk may have reached SQL Server.
- Build the native extension from each exact worktree before treating Python
  output as authoritative.
- Use Docker SQL Server with SQL authentication for every SQL behavior claim.
- Do not commit credentials, build products, generated Cargo lockfiles or
  transient test artifacts.
- Rebuild code-review-graph after implementation and use `detect_changes`,
  `get_affected_flows` and `tests_for` during self-review.

---

## Locked Error Contract

All positions are zero-based and refer to the complete public `rows` input,
not to the current SQL chunk:

```text
row_index
column_index
parameter_index = row_index * column_count + column_index
sql_type
reason
```

The original exception object is retained. In particular:

- typed conversion failures remain `ConversionError`;
- `parameter_index`, `sql_type`, `reason`, `message` and `retryable` keep the
  meaning already used by ordinary execution;
- first-chunk preflight failures have `wire_sent=False`;
- a conversion failure after a previous chunk was fully sent/drained has
  `wire_sent=True`, even though the enclosing atomic transaction is rolled
  back;
- first-chunk preflight retains `connection_discarded=False`; the existing
  conservative pool policy retires a session after a late conversion error,
  so that error must report `connection_discarded=True` and recovery must use
  a replacement physical session;
- successful rollback does not imply that no bytes reached the wire;
- `row_index` and `column_index` are added without table or column names;
- raw-value exceptions may retain their existing class, but receive safe
  position fields and the defaults `sql_type="INFERRED"` and
  `reason="bulk_conversion_failed"` only when those fields do not already
  exist.

An expanded descriptor is invalid for one bulk cell and raises
`ConversionError` with `reason="expanded_not_supported"`. `OUTPUT`,
`INPUT_OUTPUT` and `RETURN_VALUE` retain the existing
`reason="unsupported_direction"` contract.

## File Structure

- `tests/test_bulk_row_descriptor_conversion.py`: deterministic offline RED
  for typed conversion, expansion/direction rejection and privacy-safe
  position metadata.
- `tests/sql_auth_strict/test_batch_strict.py`: real SQL Server cases
  `BULK-003` through `BULK-005`.
- `tests/sql_auth_strict/test_matrix_contract.py`: canonical count from 374
  to 377.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`:
  canonical definitions of `BULK-003` through `BULK-005`.
- `src/parameter_conversion.rs`: shared single-value descriptor converter.
- `src/batch.rs`: bulk cell dispatch, position annotation and explicit-null
  preservation.
- `VERSION.md`: exact RED/fix/status evidence with no version change.

## Task 1: Publish this executable plan

**Branch:** `docs/bulk-row-descriptor-conversion`

- [ ] Verify the branch descends from `docs/batch-bulk-status` at
  `2b0b206eac4b2165307d6369d7c0601a4ea33186`.
- [ ] Verify `origin` is the user fork and upstream push is `DISABLED`.
- [ ] Confirm against local source that compatibility bulk currently calls
  `python_to_fast_parameter()` while list/`Parameters` query paths detect
  `Py<Parameter>` and use `append_parameter_descriptor()`.
- [ ] Confirm against PyO3 0.29 documentation that `Bound<PyAny>` can extract
  `Py<Parameter>`, that it can be borrowed while attached, and that attributes
  can be added through the existing `PyErr::value(py)` without replacing the
  exception.
- [ ] Run `git diff --check`, the plan self-review and code-review-graph.
- [ ] Commit as `docs: plan typed bulk row conversion`.
- [ ] Push only `docs/bulk-row-descriptor-conversion` and verify exact remote
  SHA.

## Task 2: Create the RED branch and offline contracts

**Branch:** `test/bulk-row-descriptor-conversion`

**Base:** final commit of `docs/bulk-row-descriptor-conversion`

- [ ] Create `.worktrees/test-bulk-row-descriptor-conversion`.
- [ ] Prove the documentation branch is an ancestor and the worktree path is
  ignored.
- [ ] Add `tests/test_bulk_row_descriptor_conversion.py` with an unreachable
  one-second connection, operation metrics enabled and no caught/ignored
  exceptions.
- [ ] Require `Parameter("MustNotLeakValue", "INT")` in bulk to raise the
  existing `ConversionError` with:

```text
row_index=0
column_index=0
parameter_index=0
sql_type="INT"
reason="wrong_value_kind"
wire_sent=False
retryable=False
```

- [ ] Require table sentinel, column sentinel and value sentinel to be absent
  from `str(error)`, `repr(error)` and every rendered exception attribute.
- [ ] Place `Parameter([2, 3], "INT")` at row 1, column 1 and require
  `expanded_not_supported`, positions `1/1/3`, zero pool initialization and
  zero wire activity.
- [ ] Parameterize `OUTPUT`, `INPUT_OUTPUT` and `RETURN_VALUE` and require
  `unsupported_direction` with the same safe position contract.
- [ ] Build/install the unchanged native extension from the exact RED
  worktree and run:

```bash
PYTHONPATH=python ../../.venv/bin/pytest \
  tests/test_bulk_row_descriptor_conversion.py -q
```

Expected: focused failures because the unchanged bulk converter reports
`ValueError: Unsupported type: Parameter`; connection/import/fixture errors
are not acceptable RED reasons.

## Task 3: Register and reproduce `BULK-003` through `BULK-005`

**Files:**

- `tests/sql_auth_strict/test_batch_strict.py`
- `tests/sql_auth_strict/test_matrix_contract.py`
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- `VERSION.md`

- [ ] Register:

```markdown
- `BULK-003`: non-expanded INPUT `Parameter` descriptors and typed NULLs
  preserve exact numeric, temporal and UUID values through compatibility bulk.
- `BULK-004`: expanded and non-input descriptors are rejected locally as
  typed, privacy-safe conversion failures with global bulk positions.
- `BULK-005`: a typed conversion failure beyond the first chunk reports
  global positions and prior wire activity, leaks no identifiers/value, and
  rolls back every earlier row.
```

- [ ] Update every exact matrix-contract count/name/diagnostic from 374 to
  377. Do not regenerate the full matrix/report on the RED branch.
- [ ] Implement `BULK-003` with a real table containing `DECIMAL(19,4)`,
  `DATE`, `TIME(7)`, `DATETIME2(3)` and `UNIQUEIDENTIFIER`. Insert one row of
  explicitly typed values and one row of explicitly typed NULLs. Read back
  the exact `Decimal`, `date`, `time`, millisecond-precision `datetime` and
  `UUID` values and assert the NULL row.
- [ ] Include an explicitly typed `TINYINT` NULL in the SQL case. Treat its
  persisted NULL only as integration evidence; the internal metadata
  preservation claim requires the Rust unit test in Task 6.
- [ ] Implement `BULK-004` against a real empty table. Exercise one expanded
  descriptor and all three non-input directions, require the locked
  `ConversionError` attributes and prove the table remains empty.
- [ ] Implement `BULK-005` with two columns and 1,001 rows so the invalid
  `Parameter("MustNotLeakValue", "INT")` is row 1,000, column 1, flattened
  parameter 2,001. Use an isolated pool of size one; require
  `wire_sent=True`, `connection_discarded=True`, exact safe metadata, full
  rollback and a successful post-error `SELECT 1` on a replacement SPID.
- [ ] Scan captured output and exception dictionaries for the table, column,
  value and credential sentinels.
- [ ] Run the three new cases on Docker SQL Server and observe the intended
  `Unsupported type: Parameter` RED while all 22 predecessor batch/bulk
  strict cases remain green.
- [ ] Run all matrix-contract tests; expect 377 unique registered cases and
  no duplicate/missing ID.
- [ ] Record exact RED evidence in `VERSION.md`, explicitly stating that
  runtime behavior and displayed version remain unchanged.
- [ ] Run `git diff --check`, privacy/placeholder scan and graph review.
- [ ] Commit as `test: require typed bulk row conversion`.
- [ ] Push only `test/bulk-row-descriptor-conversion`; verify remote SHA.

## Task 4: Create the fix branch with exact RED ancestry

**Branch:** `fix/bulk-row-descriptor-conversion`

**Base:** exact RED commit from Task 3

- [ ] Create `.worktrees/fix-bulk-row-descriptor-conversion` directly from
  `test/bulk-row-descriptor-conversion`.
- [ ] Verify with `git merge-base --is-ancestor` that the complete RED branch
  is an ancestor.
- [ ] Build the unchanged extension in the fix worktree and reproduce the
  focused RED once before editing production code.

## Task 5: Extract the shared single-cell converter

**Files:**

- `src/parameter_conversion.rs`
- focused Rust tests if needed

- [ ] Extract a helper that converts one non-expanded `Parameter` at a caller
  supplied `parameter_index`.
- [ ] Reject non-INPUT direction through the existing typed conversion error
  without changing its class, message, type metadata or reason.
- [ ] Reject `expanded=True` through `ConversionError`,
  `reason="expanded_not_supported"`, before touching the descriptor value.
- [ ] For an explicit `sql_type`, call
  `python_to_typed_fast_parameter_value()`; for an inferred descriptor, call
  `python_to_fast_parameter_at()`.
- [ ] Refactor `append_parameter_descriptor()` so its existing expansion
  branch stays unchanged and its non-expanded branch delegates to the new
  helper.
- [ ] Add a bulk-facing single-cell entry point which first detects
  `Py<Parameter>` and otherwise delegates to the existing raw converter.
- [ ] Keep query and batch parameter-limit accounting unchanged; a global
  bulk diagnostic index is not subject to the one-RPC 2,098-parameter cap.

## Task 6: Add bulk position and wire context

**Files:**

- `src/batch.rs`
- `VERSION.md`

- [ ] Enumerate each row and cell using global input indexes, including
  chunks after the first.
- [ ] Compute the flattened diagnostic index as
  `row_index * column_count + column_index` with checked multiplication and
  addition; return a local `OverflowError` rather than permit a Rust panic.
- [ ] On conversion failure, mutate and return the same `PyErr`: add
  `row_index` and `column_index`; retain existing typed fields; add only the
  locked safe defaults when an untyped error lacks fields.
- [ ] Set `wire_sent` from operation history: false during first-chunk
  preflight, true once any prior chunk was sent and drained.
- [ ] Preserve the current conservative late-error retirement rule and make
  `connection_discarded` truthful: false before pool acquisition, true for a
  post-first-chunk conversion failure whose session is retired.
- [ ] Do not add SQL, table or column text to the exception.
- [ ] Change untyped-NULL column inference so it patches only parameters with
  no explicit SQL type. Never rewrite an explicitly typed NULL placeholder.
- [ ] Add a Rust unit test which places an explicitly typed `TINYINT` NULL
  beside an inferred non-null integer in the same synthetic column and proves
  `fix_bulk_null_types()` leaves both its U8 null payload and explicit SQL
  type metadata unchanged.
- [ ] Preserve one converted chunk, explicit drop-before-next-conversion,
  transaction settlement, deadline and retirement behavior.
- [ ] Record the implementation and non-claims in `VERSION.md`.

## Task 7: Focused GREEN and regression verification

- [ ] Build/install the native module from the exact fix worktree:

```bash
env \
  CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-row-descriptor-fix-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-bulk-row-descriptor-fix-uv-cache \
  PYO3_PYTHON=../../.venv/bin/python \
  ../../.venv/bin/maturin develop --release
```

- [ ] Confirm `fastmssql.__file__` and the native module path both resolve to
  the fix worktree.
- [ ] Run focused offline tests and require all pass.
- [ ] Run `BULK-003` through `BULK-005` on Docker SQL Server and require all
  pass without skip.
- [ ] Run all 25 batch/bulk SQL-auth cases, all strict parameter cases and
  all matrix-contract tests.
- [ ] Run the predecessor bounded-buffering tests and the 1,000-row bulk
  resource smoke to ensure the shared converter did not restore eager
  conversion.
- [ ] Run:

```bash
env CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-row-descriptor-fix-target \
  cargo test --locked
env CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-row-descriptor-fix-target \
  cargo fmt --all --check
env CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-row-descriptor-fix-target \
  cargo clippy --locked --all-targets -- -D warnings
../../.venv/bin/ruff check \
  tests/test_bulk_row_descriptor_conversion.py \
  tests/sql_auth_strict/test_batch_strict.py
../../.venv/bin/ruff format --check \
  tests/test_bulk_row_descriptor_conversion.py \
  tests/sql_auth_strict/test_batch_strict.py
git diff --check
```

- [ ] Rebuild code-review-graph at the exact fix SHA, then inspect
  `detect_changes`, `get_affected_flows`, impact radius and `tests_for` for
  both single-cell conversion and `bulk_insert`.
- [ ] Self-review privacy, error identity, first/late
  `wire_sent`/`connection_discarded`, explicit typed NULL preservation,
  replacement-SPID recovery, row-index behavior across chunk boundaries and
  ordinary query expansion.
- [ ] Commit as `fix: share typed conversion with bulk rows`.
- [ ] Push only `fix/bulk-row-descriptor-conversion`; verify exact remote SHA
  and upstream push URL `DISABLED`.

## Task 8: Live status without an overall completion claim

**Branch:** `docs/bulk-row-descriptor-status`

- [ ] Base the status branch on the exact verified fix SHA.
- [ ] Update `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` with RED/GREEN
  SHAs, exact local/Docker evidence and the remaining five batch/bulk slices.
- [ ] Keep the latest complete hosted gates tied to their actual ancestor
  unless a new exact-SHA hosted run exists.
- [ ] Update `VERSION.md`, run `git diff --check`, rebuild the graph and
  self-review the documentation diff.
- [ ] Commit as `docs: record typed bulk conversion status`.
- [ ] Push only the status branch and verify exact remote SHA.
- [ ] Do not claim `feat/batch-bulk` complete, regenerate a full matrix
  without a full run, open a PR, publish a release/wheel or push upstream.

## Success Criteria

- The unchanged implementation fails for the intended descriptor mismatch.
- The fix branch descends from the committed RED evidence.
- Raw values and non-expanded INPUT `Parameter` values use one closed
  conversion family.
- Expanded/non-input descriptors fail locally with stable, privacy-safe
  metadata.
- First-chunk and post-first-chunk failures report truthful `wire_sent` and
  `connection_discarded`.
- Typed NULL and exact numeric/temporal/UUID values round-trip on real SQL
  Server.
- Compatibility bulk remains bounded and atomic.
- Ordinary query/batch descriptor expansion and errors are unchanged.
- Every committed branch exists only on the user fork and `VERSION.md`
  remains explicit that package version `0.7.7` and release state did not
  change.
