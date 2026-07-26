# FastMssql Original Local Regression Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans to implement this plan task-by-task. This plan
> must be executed inline; do not dispatch subagents. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Replace the ambiguous user-facing SQL-auth lane label `upstream`
with `original-local-regression` while preserving internal evidence paths,
test selection and the fork-only repository boundary.

**Architecture:** Keep `upstream` as the internal compatibility identifier.
Add one shell presentation mapping for console messages and one Python
presentation mapping for report rows. Prove the behavior through a RED static
contract, then re-run the complete Docker/MSSQL suite on the exact cumulative
merge before committing status documentation.

**Tech Stack:** Bash, Python 3.13, pytest, uv, Markdown evidence generator,
Docker Compose, SQL Server Developer, Git worktrees.

## Global Constraints

- Work and push only to `https://github.com/galeamarcel/FastMssql.git`.
- `origin` push URL must equal the fork and the original repository push URL
  must remain exactly `DISABLED`.
- Use separate design, RED, fix, technical-merge and status branches.
- The RED commit must be an ancestor of the fix commit.
- Keep `upstream.exitcode`, `upstream.command`, `upstream.log`,
  `upstream.xml` and `fastmssql_upstream_regression` unchanged.
- The public display label is exactly `original-local-regression`.
- Do not change pytest selection, Azure exclusions, `-n 1`, exit-code
  aggregation, secret redaction or the 337-case matrix.
- Do not add Git, GitHub CLI, HTTP or remote-repository commands to the lane.
- No version bump, release, package publication or original-repository PR.
- No skip, xfail, retry or swallowed exception may satisfy a required gate.
- Use apply_patch for hand-authored file edits.
- Preserve all existing user worktrees and unrelated changes.

---

### Task 1: Confirm design ancestry and create the RED branch

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-26-fastmssql-original-local-regression-label-design.md`
- Existing:
  `docs/superpowers/plans/2026-07-26-fastmssql-original-local-regression-label.md`

**Interfaces:**

- Consumes: cumulative baseline
  `714a9924cb469fe985df3c29a0f1b54b5c491896`.
- Produces: isolated branch/worktree
  `test/original-local-regression-label`.

- [ ] **Step 1: Verify the design branch is clean and published only to the
  fork**

Run:

```bash
git status --short --branch
git remote get-url --push origin
git remote get-url --push upstream
git rev-list --left-right --count \
  HEAD...origin/docs/original-local-regression-label-design
```

Require a clean tree, fork URL, `DISABLED` and `0 0`.

- [ ] **Step 2: Create the RED worktree from the exact design head**

Run from the repository root:

```bash
design_sha="$(
  git rev-parse origin/docs/original-local-regression-label-design
)"
git worktree add \
  -b test/original-local-regression-label \
  .worktrees/test-original-local-regression-label \
  "${design_sha}"
```

- [ ] **Step 3: Link only the ignored SQL-auth environment**

Run:

```bash
test ! -e \
  .worktrees/test-original-local-regression-label/.env.sql-auth.local
ln -s ../../.env.sql-auth.local \
  .worktrees/test-original-local-regression-label/.env.sql-auth.local
git -C .worktrees/test-original-local-regression-label status --short
```

The symlink must remain ignored and status must be clean.

---

### Task 2: Add and prove the RED presentation contract

**Files:**

- Modify: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**

- Consumes: `scripts/sql_auth/run_all.sh` and
  `scripts/sql_auth/generate_report.py`.
- Produces: failing public-label and repository-safety contracts.

- [ ] **Step 1: Strengthen the runner source contract**

In `test_full_runner_contract()`, keep all internal assertions and append:

```python
assert "record upstream \\" in source
assert "original-local-regression" in source
```

The first assertion preserves the internal artifact identifier. The second
must fail on the baseline because the presentation mapping is absent.

- [ ] **Step 2: Strengthen the generated-report behavior contract**

In `test_report_generator_preserves_not_run_and_redacts()`, replace:

```python
assert "upstream" in report
```

with:

```python
assert "| original-local-regression | 0 | 3 | 0 | 0 | 1 |" in report
assert "| upstream |" not in report
```

Keep the synthetic files named `upstream.exitcode` and `upstream.xml`.

- [ ] **Step 3: Add an explicit local-only lane test**

Append:

```python
def test_original_regression_lane_is_local_and_repository_safe() -> None:
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(
        encoding="utf-8"
    )
    start = runner.index("record upstream \\")
    end = runner.index("record report \\", start)
    lane = runner[start:end]

    assert "uv run pytest -n 1 tests" in lane
    assert "--ignore=tests/sql_auth_strict" in lane
    assert 'upstream.xml" -vv' in lane
    for forbidden in (
        "git ",
        "gh ",
        "curl ",
        "http://",
        "https://",
        "Rivendael/FastMssql",
    ):
        assert forbidden not in lane
```

This test checks the executable lane slice rather than globally banning the
legitimate internal word `upstream`.

- [ ] **Step 4: Run RED and inspect the exact failures**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_contract \
  tests/sql_auth_strict/test_matrix_contract.py::test_report_generator_preserves_not_run_and_redacts \
  tests/sql_auth_strict/test_matrix_contract.py::test_original_regression_lane_is_local_and_repository_safe \
  -q
```

Expected:

```text
test_full_runner_contract FAIL because original-local-regression is absent
test_report_generator_preserves_not_run_and_redacts FAIL because the report
  still renders | upstream |
test_original_regression_lane_is_local_and_repository_safe PASS
```

Any syntax/import error is not the required RED and must be corrected before
continuing.

- [ ] **Step 5: Commit and publish RED only to the fork**

Run:

```bash
git add tests/sql_auth_strict/test_matrix_contract.py
git diff --cached --check
git commit -m "test: require original local regression label"
git push -u origin test/original-local-regression-label
```

Record the exact RED SHA and confirm fork tracking parity `0 0`.

---

### Task 3: Create the fix branch with RED ancestry

**Files:**

- No content change in this task.

**Interfaces:**

- Consumes: exact RED branch head.
- Produces: isolated branch/worktree
  `fix/original-local-regression-label`.

- [ ] **Step 1: Create the fix worktree from exact RED**

Run from the repository root:

```bash
red_sha="$(git rev-parse origin/test/original-local-regression-label)"
git worktree add \
  -b fix/original-local-regression-label \
  .worktrees/fix-original-local-regression-label \
  "${red_sha}"
```

- [ ] **Step 2: Prove RED ancestry and reproduce the failure**

Run:

```bash
git merge-base --is-ancestor \
  origin/test/original-local-regression-label HEAD
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_contract \
  tests/sql_auth_strict/test_matrix_contract.py::test_report_generator_preserves_not_run_and_redacts \
  -q
```

Expected: the same two contract failures as Task 2.

---

### Task 4: Implement the minimal shell display mapping

**Files:**

- Modify: `scripts/sql_auth/run_all.sh`

**Interfaces:**

- Consumes: internal lane name passed to `record()`.
- Produces: console-only display name through
  `display_lane_name(internal_name)`.

- [ ] **Step 1: Add the pure display helper before `record()`**

Insert:

```bash
display_lane_name() {
  case "$1" in
    upstream)
      printf '%s' "original-local-regression"
      ;;
    *)
      printf '%s' "$1"
      ;;
  esac
}
```

- [ ] **Step 2: Separate display name from artifact name**

At the start of `record()`, after `local name="$1"`, add:

```bash
local display_name
display_name="$(display_lane_name "${name}")"
```

Replace only the three user-facing messages:

```bash
echo "[sql-auth] ${display_name}: $*"
echo "[sql-auth] ${display_name}: FAILED (${code}); see ${log_path}" >&2
echo "[sql-auth] ${display_name}: passed"
```

Do not change `log_path`, `exit_path`, `command_path` or `record upstream`.

- [ ] **Step 3: Validate Bash syntax**

Run:

```bash
bash -n scripts/sql_auth/run_all.sh
```

Expected: exit 0 and no output.

---

### Task 5: Implement the minimal report display mapping

**Files:**

- Modify: `scripts/sql_auth/generate_report.py`

**Interfaces:**

- Consumes: internal artifact basename `upstream`.
- Produces: report presentation name `original-local-regression`.

- [ ] **Step 1: Add the immutable display mapping**

After `STATUS_PRIORITY`, add:

```python
LANE_DISPLAY_NAMES = {
    "upstream": "original-local-regression",
}
```

- [ ] **Step 2: Map only the rendered lane name**

In `load_lanes()`, change:

```python
"name": name,
```

to:

```python
"name": LANE_DISPLAY_NAMES.get(name, name),
```

All artifact reads must continue using the internal `name` local before the
dictionary is constructed.

- [ ] **Step 3: Run the focused GREEN contract**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_contract \
  tests/sql_auth_strict/test_matrix_contract.py::test_report_generator_preserves_not_run_and_redacts \
  tests/sql_auth_strict/test_matrix_contract.py::test_original_regression_lane_is_local_and_repository_safe \
  -q
```

Expected: 3/3 PASS.

- [ ] **Step 4: Run the entire matrix contract module**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: 19/19 PASS.

- [ ] **Step 5: Run static quality gates**

Run:

```bash
uv run ruff check \
  scripts/sql_auth/generate_report.py \
  tests/sql_auth_strict/test_matrix_contract.py
uv run python -m compileall -q \
  scripts/sql_auth/generate_report.py \
  tests/sql_auth_strict/test_matrix_contract.py
bash -n scripts/sql_auth/run_all.sh
git diff --check
```

Require zero warnings and zero errors.

- [ ] **Step 6: Commit and publish the fix only to the fork**

Run:

```bash
git add \
  scripts/sql_auth/run_all.sh \
  scripts/sql_auth/generate_report.py
git diff --cached --check
git commit -m "fix: clarify original local regression lane"
git push -u origin fix/original-local-regression-label
```

The staged diff must contain no test change because the RED test is already in
ancestry.

---

### Task 6: Perform implementation self-review

**Files:**

- Review:
  `tests/sql_auth_strict/test_matrix_contract.py`
- Review: `scripts/sql_auth/run_all.sh`
- Review: `scripts/sql_auth/generate_report.py`

**Interfaces:**

- Consumes: fix branch head.
- Produces: reviewed technical candidate with no scope expansion.

- [ ] **Step 1: Prove branch ancestry**

Run:

```bash
git merge-base --is-ancestor \
  origin/docs/original-local-regression-label-design HEAD
git merge-base --is-ancestor \
  origin/test/original-local-regression-label HEAD
```

- [ ] **Step 2: Inspect the complete candidate diff**

Run:

```bash
git diff --check \
  origin/docs/original-local-regression-label-design...HEAD
git diff --stat \
  origin/docs/original-local-regression-label-design...HEAD
git diff \
  origin/docs/original-local-regression-label-design...HEAD -- \
  tests/sql_auth_strict/test_matrix_contract.py \
  scripts/sql_auth/run_all.sh \
  scripts/sql_auth/generate_report.py
```

Require only the expected test and two presentation-boundary changes.

- [ ] **Step 3: Check internal compatibility and repository safety**

Run:

```bash
rg -n \
  'record upstream|upstream\\.exitcode|upstream\\.xml|fastmssql_upstream_regression' \
  scripts/sql_auth/run_all.sh \
  tests/sql_auth_strict/test_matrix_contract.py
rg -n 'original-local-regression' \
  scripts/sql_auth/run_all.sh \
  scripts/sql_auth/generate_report.py \
  tests/sql_auth_strict/test_matrix_contract.py
git remote get-url --push origin
git remote get-url --push upstream
```

Require both internal and display identifiers, fork URL and `DISABLED`.

---

### Task 7: Integrate the exact technical candidate into the cumulative fork

**Files:**

- Merge-only task.

**Interfaces:**

- Consumes: exact fix SHA.
- Produces: one technical merge in `test/sql-auth-validation`.

- [ ] **Step 1: Confirm the cumulative worktree is clean and current**

Run in the repository root:

```bash
git switch test/sql-auth-validation
git status --short --branch
git pull --ff-only origin test/sql-auth-validation
```

- [ ] **Step 2: Merge and push only to the cumulative fork branch**

Run:

```bash
git merge --no-ff fix/original-local-regression-label \
  -m "merge: clarify original local regression lane"
technical_merge_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
```

Never merge or push to the original repository.

- [ ] **Step 3: Prove the technical merge contains exact RED/fix ancestry**

Run:

```bash
git merge-base --is-ancestor \
  origin/test/original-local-regression-label \
  "${technical_merge_sha}"
git merge-base --is-ancestor \
  origin/fix/original-local-regression-label \
  "${technical_merge_sha}"
```

---

### Task 8: Reverify the exact merge on real Docker/MSSQL

**Files:**

- Regenerate temporarily:
  `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate temporarily:
  `docs/SQL_AUTH_TEST_REPORT.md`
- Produce ignored artifacts under `.artifacts/sql-auth/`.

**Interfaces:**

- Consumes: exact technical merge SHA.
- Produces: authoritative full-suite evidence.

- [ ] **Step 1: Create a detached verification worktree**

Run:

```bash
git worktree add --detach \
  .worktrees/verify-original-local-regression-label \
  "${technical_merge_sha}"
ln -s ../../.env.sql-auth.local \
  .worktrees/verify-original-local-regression-label/.env.sql-auth.local
```

- [ ] **Step 2: Run the complete SQL-auth suite**

Run in the detached worktree:

```bash
scripts/sql_auth/run_all.sh
```

Require every lane to pass. Console output must use:

```text
[sql-auth] original-local-regression: passed
```

It must not use:

```text
[sql-auth] upstream: passed
```

- [ ] **Step 3: Validate exact evidence**

Run:

```bash
rg -n '^\\| original-local-regression \\|' \
  docs/SQL_AUTH_TEST_REPORT.md
! rg -n '^\\| upstream \\|' docs/SQL_AUTH_TEST_REPORT.md
test -f .artifacts/sql-auth/upstream.exitcode
test -f .artifacts/sql-auth/upstream.xml
test "$(<.artifacts/sql-auth/upstream.exitcode)" = "0"
rg -n '^\\| PASS \\| 337 \\|$' docs/SQL_AUTH_TEST_REPORT.md
rg -n '^\\| (FAIL|ERROR|SKIPPED|NOT RUN) \\| 0 \\|$' \
  docs/SQL_AUTH_TEST_REPORT.md
```

Require 337/337 and all failure counters zero. Record all lane counts and the
SHA-256 hashes of the matrix, report and `upstream.xml`.

- [ ] **Step 4: Scan for credentials and privacy sentinel**

Run:

```bash
if rg -l 'FASTMSSQL_OPMET_PRIVACY_SENTINEL_2026' \
  .artifacts/sql-auth \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md; then
  exit 1
fi
```

Use the existing credential-redaction contract; never print environment
secrets.

---

### Task 9: Record status in the live audit

**Files:**

- Regenerate:
  `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate:
  `docs/SQL_AUTH_TEST_REPORT.md`
- Modify:
  `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`

**Interfaces:**

- Consumes: exact technical-merge artifacts.
- Produces: documentation-only status branch and final cumulative merge.

- [ ] **Step 1: Create the status worktree from the technical merge**

Run:

```bash
git worktree add \
  -b docs/original-local-regression-label-status \
  .worktrees/docs-original-local-regression-label-status \
  "${technical_merge_sha}"
```

- [ ] **Step 2: Generate authoritative matrix/report into the status branch**

Run the report generator from the status branch with absolute paths to the
detached verification artifacts:

```bash
uv run python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results \
  /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-original-local-regression-label/.artifacts/sql-auth/strict-results.json \
  --artifact-dir \
  /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-original-local-regression-label/.artifacts/sql-auth \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete
```

- [ ] **Step 3: Update the live audit**

Add a concise verified-status subsection that records:

```text
design SHA
RED SHA
fix SHA
technical merge SHA
337/337 matrix
all lane counts
original-local-regression visible label
upstream.* internal compatibility artifacts
fork-only remote evidence
no original-repository operation
```

Also update the current-state summary so it no longer calls the local lane
`upstream`.

- [ ] **Step 4: Verify status documents**

Run:

```bash
git diff --check
rg -n 'original-local-regression|regresia originală locală|337/337|930/930' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_REPORT.md
! rg -n '^\\| upstream \\|' docs/SQL_AUTH_TEST_REPORT.md
```

Require no credential, privacy sentinel, failed row or inflated claim that
hosted CI ran MSSQL.

- [ ] **Step 5: Commit and push status only to the fork**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md
git commit -m "docs: record original local regression evidence"
evidence_sha="$(git rev-parse HEAD)"

git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
git commit -m "docs: clarify original local regression status"
git push -u origin docs/original-local-regression-label-status
```

- [ ] **Step 6: Merge status into the cumulative fork**

Run in the cumulative worktree:

```bash
git merge --no-ff docs/original-local-regression-label-status \
  -m "merge: record original local regression status"
git push origin test/sql-auth-validation
```

Require fork tracking parity `0 0`, a clean worktree and no original-repository
write, PR or release.

---

### Task 10: Transition to typed-parameters design

**Files:**

- Inspect:
  `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Inspect: `src/py_parameters.rs`
- Inspect: `src/parameter_conversion.rs`
- Inspect:
  `tests/sql_auth_strict/test_parameters_strict.py`
- Inspect:
  `tests/sql_auth_strict/test_type_mapping_strict.py`

**Interfaces:**

- Consumes: finalized terminology correction.
- Produces: a separate typed-parameters specification cycle; no parameter
  implementation belongs in this plan.

- [ ] **Step 1: Confirm the next unresolved live-audit gate**

Require the audit checklist still shows:

```text
bool transmitted as BIT
declared SQL types respected
aware datetime transmitted as DATETIMEOFFSET
```

- [ ] **Step 2: Start a new design branch from the final cumulative head**

Use:

```text
docs/typed-parameters-design
```

Do not reuse the terminology fix branch for any driver behavior.
