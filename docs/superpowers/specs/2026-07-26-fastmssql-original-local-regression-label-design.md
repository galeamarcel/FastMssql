# FastMssql Original Local Regression Label Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, plan and implementation self-review are
mandatory.

**Date:** 26 July 2026

**Source baseline:** `test/sql-auth-validation` at
`714a9924cb469fe985df3c29a0f1b54b5c491896`

**Repository boundary:** every design, RED, fix, evidence and status branch is
created and published only in `https://github.com/galeamarcel/FastMssql.git`.
The original repository remains fetch-only and its push URL stays `DISABLED`.

## Objective

Remove the ambiguous public label `upstream` from SQL-auth runner output and
generated reports. The lane executes the test suite inherited from the
original project against the local Docker SQL Server; it does not fetch,
modify, push to, or otherwise operate on the original GitHub repository.

The public display name is:

```text
original-local-regression
```

Romanian prose continues to use:

```text
regresia originală locală
```

## Measured baseline gap

`scripts/sql_auth/run_all.sh` currently logs:

```text
[sql-auth] upstream: ...
```

`scripts/sql_auth/generate_report.py` renders an execution-lane row named
`upstream`. This is technically inherited terminology, but it can be
misinterpreted as a repository operation. The current lane only invokes local
pytest files and writes local evidence.

The internal compatibility identifiers are already used by historical
evidence and tooling:

```text
upstream.exitcode
upstream.command
upstream.log
upstream.xml
fastmssql_upstream_regression
```

Renaming those identifiers would add migration churn without changing the
security boundary.

## Approaches considered

### Approach A — display alias with stable internal identifiers, selected

Map the internal lane identifier `upstream` to
`original-local-regression` at the two presentation boundaries:

- console output in `run_all.sh`;
- the `Lane` cell rendered by `generate_report.py`.

Keep artifact filenames, environment variables and the database name
unchanged.

Advantages:

- removes the user-facing ambiguity;
- keeps old evidence readers and scripts compatible;
- does not touch SQL setup, test selection or pass/fail accounting;
- produces a minimal reviewable diff.

### Approach B — rename artifacts, database and every reference

Rename all `upstream.*` files and `fastmssql_upstream_regression`.

Rejected because it changes stable evidence paths, database provisioning,
environment variables and historical documentation without improving
correctness or repository safety.

### Approach C — documentation-only clarification

Leave runner and report output unchanged and explain the term only in prose.

Rejected because the ambiguous label would remain visible in every live run
and generated report.

## Public and internal contract

The runner retains the internal call:

```bash
record upstream ...
```

`record()` derives a separate display label. For every lane except
`upstream`, the display label equals the internal name. For `upstream`, the
display label is exactly `original-local-regression`.

The report generator keeps discovering:

```text
upstream.exitcode
upstream.command
upstream.xml
```

It renders exactly:

```markdown
| original-local-regression | ... |
```

The execution command remains visible and proves that the lane runs local
pytest paths. The report must not render a lane cell `| upstream |`.

## Safety and compatibility invariants

- No Git, GitHub CLI, HTTP or network-repository command is added.
- No remote URL or repository reference is mutated.
- The original repository push URL remains `DISABLED`.
- Test selection, Azure exclusions, `-n 1`, JUnit content and exit-code
  aggregation remain byte-for-byte equivalent except for presentation text.
- Existing internal artifact filenames remain readable.
- Missing evidence remains `NOT RUN`; no outcome is promoted.
- Secret redaction remains unchanged.
- The matrix remains exactly 337 unique IDs.
- Generated evidence contains no credentials or fixed privacy sentinel.

## Deterministic TDD contract

The RED branch modifies only
`tests/sql_auth_strict/test_matrix_contract.py` and requires:

1. the runner source to contain the exact public label;
2. the baseline runner to fail because it still prints `upstream`;
3. a synthetic `upstream.exitcode`/`upstream.xml` fixture to render
   `original-local-regression`;
4. the rendered report not to contain the exact lane cell `| upstream |`;
5. internal artifact fixtures to remain named `upstream.*`;
6. the runner to contain no `git`, `gh`, `curl` or repository URL in the lane.

The fix branch changes only:

- `scripts/sql_auth/run_all.sh`;
- `scripts/sql_auth/generate_report.py`.

Focused verification:

```text
tests/sql_auth_strict/test_matrix_contract.py
shell syntax check for run_all.sh
Ruff
Python compileall
git diff --check
```

Final verification also runs the complete SQL-auth runner on the local Docker
MSSQL container. The expected lane counts remain:

```text
strict                         339/339
async                            16/16
framework                        33/33
resilience                         6/6
load                              11/11
original-local-regression       930/930
matrix                          337/337
```

## Branch and commit topology

```text
docs/original-local-regression-label-design
  -> test/original-local-regression-label
     -> fix/original-local-regression-label
        -> test/sql-auth-validation
```

The RED commit must be an ancestor of the fix. The final technical tree is
merged only into the cumulative fork branch. A documentation-only status
branch records evidence after exact-merge verification.

## Non-goals

This correction does not:

- rename the Git remote named `upstream`;
- rename historical Git branches or commits;
- rewrite already published evidence;
- rename `fastmssql_upstream_regression`;
- change which tests execute;
- modify FastMssql runtime, TDS, pool, transaction or parameter behavior;
- change dependencies, version or release metadata;
- create a branch, pull request or release in the original repository.

## Self-review record

The design was checked against the runner, report generator, matrix contract,
historical evidence paths and fork-only Git configuration.

Corrections made during self-review:

1. A full artifact/database rename was rejected to preserve evidence
   compatibility.
2. The display mapping is constrained to presentation boundaries so pass/fail
   accounting cannot change.
3. The contract checks the exact Markdown lane cell rather than forbidding the
   word `upstream` globally, because internal compatibility identifiers and
   the fetch-only Git remote remain legitimate.
4. Repository-safety assertions explicitly prohibit Git/network commands in
   the test lane.
5. The final gate retains the complete real MSSQL run instead of treating a
   static source test as sufficient evidence.

The specification contains no placeholder, ambiguous scope decision,
unbounded behavior or authorization for an original-repository write.
