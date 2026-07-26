# FastMssql Lifecycle Shutdown Waiter Test Race Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing shutdown-waiter lifecycle test deterministic by
proving that both later callers subscribed to the shared shutdown round before
the operation permit is released, without changing production behavior.

**Architecture:** A dedicated RED branch adds an opt-in shell stress runner
that repeatedly executes the real isolated Rust test and exposes the measured
scheduler race. A descendant FIX branch adds one bounded helper inside the
existing Rust `#[cfg(test)]` module and uses the existing Tokio `watch`
receiver count as the exact synchronization condition. The verified fix is
merged into the cumulative fork branch, followed by the complete
Docker/MSSQL, raw Cargo, wheel, matrix and hosted-platform gates.

**Tech Stack:** Rust 1.94.0, Tokio, PyO3 0.27.2, Cargo, Bash, Docker, Microsoft
SQL Server 2022, pytest, Git and GitHub Actions.

## Global Constraints

- Work and push only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep the original repository fetch-only and keep its push URL exactly
  `DISABLED`.
- Base all lifecycle-race branches on cumulative SHA
  `eaacc504be8582e99edab4fd05633458b8190984`.
- Preserve branch ancestry:
  `docs/lifecycle-shutdown-waiter-test-race-design` →
  `test/lifecycle-shutdown-waiter-race` →
  `fix/lifecycle-shutdown-waiter-test-race` →
  `test/sql-auth-validation`.
- The reproduction script commit must be an ancestor of the fix commit.
- Change no production-visible lifecycle method, field, synchronization
  point, timeout, return value or public API.
- Keep the synchronization helper entirely inside `#[cfg(test)] mod tests`
  in `src/lifecycle.rs`.
- Wait for exactly two shutdown-result receivers with a one-second Tokio
  timeout before releasing the operation permit.
- Keep the repeated stress gate opt-in and outside
  `scripts/sql_auth/run_all.sh`.
- Accept
  `FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS` only as an integer from
  `1` through `1000`; use `200` by default.
- Preserve internal compatibility artifact names such as `upstream.xml`;
  user-visible output must call that lane `original-local-regression`.
- Do not change dependencies, lockfile, package version or release metadata.
- Use real tests and observed behavior; do not swallow exceptions or turn a
  failing required lane into a skip.
- Execute inline under Marcel Galea's standing approval and perform a
  self-review before every integration commit.

---

### Task 1: Publish the reviewed design and executable plan

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race-design.md`
- Create:
  `docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race.md`

**Interfaces:**

- Consumes: cumulative SHA
  `eaacc504be8582e99edab4fd05633458b8190984` and the measured `91/100`
  unsynchronized versus `200/200` synchronized experiment.
- Produces: an immutable design/plan parent for both the RED and FIX
  branches.

- [ ] **Step 1: Confirm the design branch has the exact cumulative parent**

Run:

```bash
git rev-parse HEAD^
git merge-base --is-ancestor \
  eaacc504be8582e99edab4fd05633458b8190984 HEAD
git status --short --branch
```

Expected: `HEAD^` is
`eaacc504be8582e99edab4fd05633458b8190984`, the ancestry command exits zero,
and only this plan is uncommitted.

- [ ] **Step 2: Self-review specification and plan coverage**

Run:

```bash
rg -n 'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s|Similar to Tas[k]' \
  docs/superpowers/specs/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race.md
git diff --check
git diff -- \
  docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race.md
```

Expected: `rg` finds no placeholder, `git diff --check` is clean, and the
plan covers RED evidence, the test-only fix, local gates, hosted gates,
integration and live-audit evidence.

- [ ] **Step 3: Commit the implementation plan**

Run:

```bash
git add \
  docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race.md
git commit -m "docs: plan lifecycle shutdown waiter test race fix"
```

Expected: one documentation commit on
`docs/lifecycle-shutdown-waiter-test-race-design`.

- [ ] **Step 4: Publish only the design branch to the fork**

Run:

```bash
git push -u origin docs/lifecycle-shutdown-waiter-test-race-design
git ls-remote --heads origin \
  docs/lifecycle-shutdown-waiter-test-race-design
git remote get-url --push upstream
```

Expected: the fork ref equals local `HEAD`; the last command prints
`DISABLED`.

---

### Task 2: Add and observe the RED scheduler-race reproduction

**Files:**

- Create: `scripts/test_lifecycle_shutdown_waiter_race.sh`
- Exercise:
  `src/lifecycle.rs::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor`

**Interfaces:**

- Consumes: the real Cargo test executable and environment variable
  `FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS`.
- Produces: executable
  `scripts/test_lifecycle_shutdown_waiter_race.sh`, returning zero only if
  every requested isolated test iteration passes and non-zero at the first
  failure.

- [ ] **Step 1: Create an isolated RED branch from the exact design head**

Run from the primary checkout:

```bash
git worktree add \
  .worktrees/test-lifecycle-shutdown-waiter-race \
  -b test/lifecycle-shutdown-waiter-race \
  docs/lifecycle-shutdown-waiter-test-race-design
```

Then run in the new worktree:

```bash
git rev-parse HEAD
git status --short --branch
```

Expected: `HEAD` equals the final design/plan SHA and the worktree is clean.

- [ ] **Step 2: Write the opt-in reproduction script**

Create `scripts/test_lifecycle_shutdown_waiter_race.sh` with exactly this
behavior:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly iterations="${FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS:-200}"
readonly test_name="lifecycle::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor"

if [[ ! "${iterations}" =~ ^[0-9]+$ ]] \
  || (( iterations < 1 || iterations > 1000 )); then
  printf '%s\n' \
    'FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS must be an integer from 1 through 1000' \
    >&2
  exit 2
fi

cd "${project_root}"

for ((iteration = 1; iteration <= iterations; iteration++)); do
  if output="$(cargo test --locked "${test_name}" -- --exact 2>&1)"; then
    printf '[lifecycle-waiter-stress] %d/%d passed\n' \
      "${iteration}" "${iterations}"
  else
    printf '%s\n' "${output}" >&2
    printf '[lifecycle-waiter-stress] failed at iteration %d/%d\n' \
      "${iteration}" "${iterations}" >&2
    exit 1
  fi
done
```

Then run:

```bash
chmod +x scripts/test_lifecycle_shutdown_waiter_race.sh
bash -n scripts/test_lifecycle_shutdown_waiter_race.sh
```

Expected: syntax passes and the file is executable.

- [ ] **Step 3: Verify the script's bounded input contract**

Run:

```bash
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=0 \
  scripts/test_lifecycle_shutdown_waiter_race.sh
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=1001 \
  scripts/test_lifecycle_shutdown_waiter_race.sh
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=invalid \
  scripts/test_lifecycle_shutdown_waiter_race.sh
```

Expected: each command exits `2`, prints the same explicit range error and
does not invoke Cargo.

- [ ] **Step 4: Run the unchanged test repeatedly and observe RED**

Run:

```bash
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=200 \
  scripts/test_lifecycle_shutdown_waiter_race.sh
```

Expected: non-zero exit on the unchanged source, with the real assertion
failure from `shared shutdown must remain graceful`. If one 200-iteration
batch happens to pass due scheduler timing, rerun another 200-iteration batch
up to four additional times. Record the exact first failing iteration and
output; do not edit `src/lifecycle.rs` on the RED branch.

- [ ] **Step 5: Review and commit the RED harness**

Run:

```bash
git diff --check
git diff -- src/lifecycle.rs
git diff -- scripts/test_lifecycle_shutdown_waiter_race.sh
git status --short
```

Expected: `src/lifecycle.rs` has no diff and the only changed path is the
reproduction script.

Run:

```bash
git add scripts/test_lifecycle_shutdown_waiter_race.sh
git commit -m "test: reproduce lifecycle shutdown waiter race"
git push -u origin test/lifecycle-shutdown-waiter-race
```

Expected: the RED commit is published only on the fork.

---

### Task 3: Synchronize the test's claimed precondition

**Files:**

- Modify: `src/lifecycle.rs:765`
- Modify: `src/lifecycle.rs:847`
- Test: `scripts/test_lifecycle_shutdown_waiter_race.sh`

**Interfaces:**

- Consumes: `ConnectionLifecycle::lock_inner()`,
  `shutdown_sender: Option<watch::Sender<bool>>` and
  `watch::Sender::receiver_count()`.
- Produces:
  `async fn wait_until_shutdown_waiters(&ConnectionLifecycle, usize)` inside
  the private Rust test module; it returns `()` after the exact receiver count
  is observed or panics after one second with a precise test failure.

- [ ] **Step 1: Create the FIX worktree as a descendant of RED**

Run from the primary checkout:

```bash
git worktree add \
  .worktrees/fix-lifecycle-shutdown-waiter-test-race \
  -b fix/lifecycle-shutdown-waiter-test-race \
  test/lifecycle-shutdown-waiter-race
```

Then run:

```bash
git merge-base --is-ancestor \
  test/lifecycle-shutdown-waiter-race HEAD
git status --short --branch
```

Expected: ancestry exits zero and the worktree is clean.

- [ ] **Step 2: Add the minimal test-only synchronization helper**

Inside `#[cfg(test)] mod tests`, immediately after `wait_until_state`, add:

```rust
async fn wait_until_shutdown_waiters(
    lifecycle: &ConnectionLifecycle,
    expected_receivers: usize,
) {
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            let receivers = lifecycle
                .lock_inner()
                .shutdown_sender
                .as_ref()
                .map_or(0, |sender| sender.receiver_count());
            if receivers == expected_receivers {
                return;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("shutdown waiters must subscribe before work is released");
}
```

This is test code: a mutation that removes the receiver-count condition or
moves the permit release before this helper must make the repeated real test
fail again.

- [ ] **Step 3: Apply the helper to the failing scenario**

After spawning `second` and `third`, and immediately before `drop(permit)`,
add:

```rust
wait_until_shutdown_waiters(&lifecycle, 2).await;
```

Do not add sleeps, production barriers or acceptance of `Ok(false)`.

- [ ] **Step 4: Verify the focused GREEN test**

Run:

```bash
cargo fmt --check
cargo test --locked \
  lifecycle::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor \
  -- --exact
```

Expected: formatting and the isolated test pass.

- [ ] **Step 5: Verify 200 deterministic iterations**

Run:

```bash
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=200 \
  scripts/test_lifecycle_shutdown_waiter_race.sh
```

Expected: the script reaches and prints `200/200 passed`.

- [ ] **Step 6: Run the complete Rust quality gate**

Run:

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
```

Expected: all commands pass; the Rust suite reports `54/54`.

- [ ] **Step 7: Prove the fix is test-only and self-review it**

Run:

```bash
git diff --check
git diff test/lifecycle-shutdown-waiter-race...HEAD -- src/lifecycle.rs
git diff --stat test/lifecycle-shutdown-waiter-race...HEAD
git status --short
```

Review requirements:

- every Rust addition is below `#[cfg(test)]`;
- the helper is bounded by one second;
- the observed condition is exactly two real receivers;
- `drop(permit)` remains after the condition;
- shutdown production behavior and public API are byte-for-byte unchanged;
- no dependency, lockfile, package or release file changed.

- [ ] **Step 8: Commit and publish the FIX branch**

Run:

```bash
git add src/lifecycle.rs
git commit -m "test: synchronize lifecycle shutdown waiters"
git push -u origin fix/lifecycle-shutdown-waiter-test-race
```

Expected: the fix commit has the RED harness in its ancestry and exists only
on the fork.

---

### Task 4: Integrate and verify the exact cumulative candidate

**Files:**

- Merge into: `test/sql-auth-validation`
- Generate during verification:
  `docs/SQL_AUTH_TEST_REPORT.md`
- Generate during verification:
  `docs/SQL_AUTH_TEST_MATRIX.md`
- Read during verification: `.artifacts/sql-auth/*.exitcode`
- Read during verification: `.artifacts/sql-auth/*.xml`

**Interfaces:**

- Consumes: the reviewed FIX branch and existing local SQL-auth secret
  configuration without printing it.
- Produces: one history-preserving merge SHA on the cumulative fork branch,
  plus authoritative local evidence for that exact SHA.

- [ ] **Step 1: Merge the FIX history into the cumulative branch**

In the primary checkout, run:

```bash
git switch test/sql-auth-validation
git status --short --branch
git merge --no-ff fix/lifecycle-shutdown-waiter-test-race \
  -m "merge: stabilize lifecycle shutdown waiter test"
```

Expected: a two-parent merge whose first parent is
`eaacc504be8582e99edab4fd05633458b8190984` and whose second-parent history
contains both the RED and FIX commits.

- [ ] **Step 2: Inspect the exact integration diff before publication**

Run:

```bash
git log -1 --format='%H%n%P%n%s'
git diff --check HEAD^1..HEAD
git diff --stat HEAD^1..HEAD
git diff HEAD^1..HEAD -- \
  scripts/test_lifecycle_shutdown_waiter_race.sh \
  src/lifecycle.rs \
  docs/superpowers/specs/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-shutdown-waiter-test-race.md
```

Expected: only the design, plan, opt-in reproduction script and test-only
Rust synchronization belong to this lifecycle correction.

- [ ] **Step 3: Publish the cumulative merge only to the fork**

Run:

```bash
git push origin test/sql-auth-validation
git ls-remote --heads origin test/sql-auth-validation
git remote get-url --push upstream
```

Expected: fork parity at the exact merge SHA and `DISABLED` for original
repository push.

- [ ] **Step 4: Create a detached verification worktree**

Run:

```bash
git worktree add --detach \
  .worktrees/verify-lifecycle-shutdown-waiter-test-race HEAD
```

In the detached worktree, link the existing local SQL-auth environment file
without reading or printing its content:

```bash
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git rev-parse HEAD
git status --short
```

Expected: detached `HEAD` equals the published cumulative merge and tracked
files are initially clean.

- [ ] **Step 5: Re-run the dedicated stress gate at the exact merge**

Run:

```bash
FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS=200 \
  scripts/test_lifecycle_shutdown_waiter_race.sh
```

Expected: `200/200 passed`.

- [ ] **Step 6: Run the complete Docker/MSSQL SQL-auth gate**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected: every required lane passes, including raw Cargo, strict,
true-async, FastAPI/ASGI, Flask/WSGI compatibility, Flask via ASGI,
resilience, load, visible `original-local-regression`, report generation and
the final matrix contract. No exception is swallowed.

- [ ] **Step 7: Verify authoritative result artifacts**

Run:

```bash
python3 - <<'PY'
from collections import Counter
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

artifact_dir = Path(".artifacts/sql-auth")
exit_codes = {
    path.stem: int(path.read_text(encoding="utf-8").strip())
    for path in artifact_dir.glob("*.exitcode")
}
failed = {name: code for name, code in exit_codes.items() if code != 0}
assert not failed, failed

def junit_summary(path: Path) -> tuple[int, int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    return tuple(
        sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    )

lane_counts = {
    path.stem: junit_summary(path)
    for path in artifact_dir.glob("*.xml")
}
assert all(
    failures == 0 and errors == 0
    for _, failures, errors, _ in lane_counts.values()
), lane_counts

source_sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
report = Path("docs/SQL_AUTH_TEST_REPORT.md").read_text(encoding="utf-8")
matrix = Path("docs/SQL_AUTH_TEST_MATRIX.md").read_text(encoding="utf-8")
assert f"- Git commit: `{source_sha}`" in report
assert "| original-local-regression | 0 |" in report
matrix_statuses = Counter(
    line.split("|")[2].strip()
    for line in matrix.splitlines()
    if line.startswith("| `")
)
assert set(matrix_statuses) == {"PASS"}, matrix_statuses
print(source_sha)
print(sorted(exit_codes.items()))
print(sorted(lane_counts.items()))
print(dict(matrix_statuses))
PY
git diff --check
```

Expected: no failed lane, every JUnit lane has zero failures/errors, the
visible original-local-regression name is present, the report Git commit
equals detached `HEAD`, every matrix case is `PASS`, and all observed counts
are recorded for the audit update.

---

### Task 5: Confirm Linux, macOS, Windows and RustSec gates

**Files:**

- Read-only: `.github/workflows/rust-unit-tests.yml`
- Read-only: `.github/workflows/dependency-security.yml`
- External read-only: public GitHub Actions metadata for the fork.

**Interfaces:**

- Consumes: the exact cumulative SHA pushed in Task 4.
- Produces: run URLs and per-platform conclusions tied to that SHA; no
  workflow dispatch, mutation, PR or original-repository write.

- [ ] **Step 1: Identify workflow runs for the exact SHA**

Use the public GitHub Actions API to list runs for:

```text
galeamarcel/FastMssql
head_sha=<exact cumulative SHA>
```

Expected: runs for Rust unit/build/wheel contracts and dependency security
appear for the exact fork SHA.

- [ ] **Step 2: Wait boundedly for terminal conclusions**

Poll only public read-only run/job endpoints at intervals no longer than
60 seconds until each required run is terminal.

Expected:

- Linux raw Cargo/build/wheel/contracts: `success`;
- macOS raw Cargo/build/wheel/contracts: `success`;
- Windows raw Cargo/build/wheel/contracts: `success`;
- dependency security / RustSec: `success`.

If a run fails, inspect its public logs, identify whether the lifecycle
candidate caused it, and apply the debugging/TDD branch discipline before
claiming completion.

- [ ] **Step 3: Record exact hosted evidence**

Record:

- cumulative source SHA;
- workflow run IDs and URLs;
- Linux, macOS and Windows job conclusions;
- RustSec conclusion;
- the explicit boundary that hosted CI does not claim a real MSSQL run.

Expected: every statement is supported by an exact public fork URL.

---

### Task 6: Update the live production-readiness audit

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`

**Interfaces:**

- Consumes: observed RED iteration, `200/200` GREEN stress, Rust count,
  complete local lane counts, exact cumulative SHA and hosted run URLs.
- Produces: a status section that distinguishes a test-harness race from a
  production lifecycle defect and closes the temporarily blocked terminology
  verification.

- [ ] **Step 1: Create a separate status branch from the verified merge**

Run:

```bash
git worktree add \
  .worktrees/docs-lifecycle-shutdown-waiter-test-race-status \
  -b docs/lifecycle-shutdown-waiter-test-race-status \
  test/sql-auth-validation
```

Expected: the status branch begins at the exact verified cumulative SHA.

- [ ] **Step 2: Add the evidence-backed live-audit update**

Document:

- the original one-off full-run failure and the isolated RED reproduction;
- the root cause: `tokio::spawn` did not guarantee waiter subscription;
- why `Ok(false)` remained correct for a caller entering after `Closed`;
- why the accepted correction is test-only and does not weaken lifecycle;
- branch names and exact design, RED, FIX and merge SHAs;
- the first observed RED failing iteration;
- `200/200` post-fix stress evidence;
- raw Cargo and complete Docker/MSSQL lane counts;
- the visible `original-local-regression` label and unchanged internal
  compatibility artifact names;
- Linux/macOS/Windows and RustSec run URLs;
- the invariant that every push went only to the fork and original push
  remained `DISABLED`.

Use `VERIFIED_FORK` for the final state only after every local and hosted gate
above is green.

- [ ] **Step 3: Self-review the audit against artifacts**

Run:

```bash
rg -n 'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
git diff --check
git diff -- docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
```

Then compare every count and SHA in the diff with the JSON/JUnit artifacts,
Git history and public GitHub Actions records. Correct any mismatch before
commit.

- [ ] **Step 4: Commit and publish the status branch**

Run:

```bash
git add docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md
git commit -m "docs: record lifecycle waiter race verification"
git push -u origin docs/lifecycle-shutdown-waiter-test-race-status
```

Expected: one live-audit commit, only on the fork.

- [ ] **Step 5: Merge the audited status into the cumulative branch**

In the primary checkout, run:

```bash
git switch test/sql-auth-validation
git merge --no-ff docs/lifecycle-shutdown-waiter-test-race-status \
  -m "merge: record lifecycle waiter race verification"
git push origin test/sql-auth-validation
```

Expected: the cumulative fork branch contains the implementation and its
verified live-document evidence; the original repository remains untouched.

- [ ] **Step 6: Final lifecycle-race scope check**

Run:

```bash
git status --short --branch
git log --graph --oneline --decorate -12
git diff --check \
  eaacc504be8582e99edab4fd05633458b8190984..HEAD
git remote get-url origin
git remote get-url --push upstream
```

Expected: clean cumulative branch, reviewable branch ancestry, no whitespace
errors, fork origin, and original push `DISABLED`.

After this check, resume the next open item in
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: enterprise typed SQL
parameters, under a new design → RED → implementation → status branch chain.
