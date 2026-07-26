# FastMssql Operation-Metrics Saturation Coherence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every public operation-metrics arithmetic invariant when
multiple independent outcome counters exceed the shared `u64::MAX` snapshot
capacity.

**Architecture:** Keep writer atomics and memory ordering unchanged. During
snapshot rendering, project outcomes in fixed schema order into one remaining
`u64` capacity, export `completed` as their exact sum, and permanently mark
the entry saturated whenever projection discards a raw count.

**Tech Stack:** Rust 2024, `AtomicU64`/`AtomicBool`, PyO3 0.29, Cargo unit
tests, Python installed-wheel contracts, Git worktrees.

## Global Constraints

- Baseline is `feat/operation-metrics` at
  `2a7da9bd597d5c975bd3b9a4b5072bb8a72bf665`.
- Design authority is
  `docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-saturation-coherence-design.md`.
- Use separate `docs/`, `test/` and `fix/` branches and project-local
  `.worktrees/`.
- All commits and pushes target only
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` remains fetch-only with push URL exactly `DISABLED`.
- Preserve the public schema, 13 operations, 17 bounds, default-off behavior
  and existing positional constructor order.
- Add no lock, allocation, dependency, exporter, callback or background task.
- Do not change writer atomics, outcome classification, Release/Acquire
  publication order or instrumented operation boundaries.
- Raw outcomes remain exact while their mathematical sum fits in `u64`.
- Saturated exported outcomes follow fixed order:
  `succeeded`, `errors`, `timed_out`, `cancelled`, `outcome_unknown`.
- Every snapshot must satisfy:

  ```text
  started == completed + in_flight
  completed == succeeded + errors + timed_out + cancelled + outcome_unknown
  ```

- No RED assertion may be weakened, skipped, retried or converted to an
  expected failure.
- Docker/MSSQL and hosted gates remain mandatory before cumulative integration,
  even though this unit fix performs no SQL.

---

### Task 1: Preserve the approved design and create the RED branch

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-saturation-coherence-design.md`
- Existing:
  `docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-design.md`
- Existing:
  `docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics-saturation-coherence.md`

**Interfaces:**

- Consumes: feature baseline SHA and the original operation-metrics invariants.
- Produces: immutable design/plan ancestry for RED and fix branches.

- [ ] **Step 1: Self-review design and plan**

Run:

```bash
git diff --check
if rg -n '\b(T''BD|TO''DO|FIX''ME)\b|implement la''ter|fill in deta''ils' \
  docs/superpowers/specs/2026-07-26-fastmssql-operation-metrics-saturation-coherence-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics-saturation-coherence.md; then
  exit 1
fi
```

Require no output from either failure scan.

- [ ] **Step 2: Commit the plan independently**

Run:

```bash
git add \
  docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics-saturation-coherence.md
git commit -m "docs: plan saturation-coherent operation metrics"
git push -u origin docs/operation-metrics-saturation-coherence-design
```

- [ ] **Step 3: Verify the repository boundary**

Run:

```bash
git remote get-url --push origin
git remote get-url --push upstream
git status --short --branch
```

Require the owner's fork for `origin`, exact `DISABLED` for upstream push and
a clean design branch.

- [ ] **Step 4: Create the dedicated RED worktree**

From the repository root, run:

```bash
git worktree add \
  .worktrees/test-operation-metrics-saturation-coherence \
  -b test/operation-metrics-saturation-coherence \
  docs/operation-metrics-saturation-coherence-design
```

Require exact design/plan ancestry and clean status.

---

### Task 2: Reproduce incoherent multi-outcome saturation

**Files:**

- Modify: `src/operation_metrics.rs`

**Interfaces:**

- Consumes: private `OperationMetric` atomics and
  `OperationMetricsRegistry::snapshot()`.
- Produces: one deterministic Rust regression that fails only because exported
  outcome values exceed their shared completed capacity.

- [ ] **Step 1: Add the failing saturation regression**

Inside `operation_metrics.rs`'s existing `tests` module, add:

```rust
#[test]
fn snapshot_reconciles_multiple_outcomes_into_one_saturated_total() {
    let registry = OperationMetricsRegistry::new();
    let index = metric_index(OperationName::Query).unwrap();
    let metric = &registry.operations[index];
    metric.started.store(u64::MAX, Ordering::Relaxed);
    metric.outcomes[0].store(u64::MAX - 2, Ordering::Relaxed);
    metric.outcomes[1].store(2, Ordering::Relaxed);
    metric.outcomes[2].store(1, Ordering::Relaxed);
    metric.outcomes[3].store(1, Ordering::Relaxed);
    metric.outcomes[4].store(1, Ordering::Relaxed);

    let query = &registry.snapshot().operations[index];
    assert_eq!(query.outcomes, [u64::MAX - 2, 2, 0, 0, 0]);
    assert_eq!(query.completed, u64::MAX);
    assert_eq!(
        u128::from(query.completed),
        query.outcomes.into_iter().map(u128::from).sum::<u128>()
    );
    assert_eq!(
        u128::from(query.started),
        u128::from(query.completed) + u128::from(query.in_flight)
    );
    assert!(query.saturated);

    metric.started.store(18, Ordering::Relaxed);
    for (outcome, value) in metric.outcomes.iter().zip([7, 5, 3, 2, 1]) {
        outcome.store(value, Ordering::Relaxed);
    }
    let unsaturated_values = &registry.snapshot().operations[index];
    assert_eq!(unsaturated_values.outcomes, [7, 5, 3, 2, 1]);
    assert_eq!(unsaturated_values.completed, 18);
    assert_eq!(unsaturated_values.in_flight, 0);
    assert!(unsaturated_values.saturated);
}
```

The final assertion proves saturation remains permanent after the raw values
return to a representable sum.

- [ ] **Step 2: Verify the regression is RED for the intended reason**

Run:

```bash
cargo test --locked \
  operation_metrics::tests::snapshot_reconciles_multiple_outcomes_into_one_saturated_total \
  -- --exact
```

Require one assertion failure showing current outcomes
`[u64::MAX - 2, 2, 1, 1, 1]` instead of the coherent expected projection.
Compilation errors or unrelated failures do not prove RED.

- [ ] **Step 3: Prove the new test is the only change**

Run:

```bash
git diff --check
git diff --stat docs/operation-metrics-saturation-coherence-design..HEAD
git status --short
```

Require only `src/operation_metrics.rs`.

- [ ] **Step 4: Commit and push RED only to the fork**

Run:

```bash
git add src/operation_metrics.rs
git commit -m "test: reproduce incoherent saturated outcomes"
git push -u origin test/operation-metrics-saturation-coherence
```

- [ ] **Step 5: Create the dedicated fix worktree**

From the repository root, run:

```bash
git worktree add \
  .worktrees/fix-operation-metrics-saturation-coherence \
  -b fix/operation-metrics-saturation-coherence \
  test/operation-metrics-saturation-coherence
```

---

### Task 3: Reconcile outcomes within the saturated completed capacity

**Files:**

- Modify: `src/operation_metrics.rs`

**Interfaces:**

- Consumes: five Acquire-loaded raw outcome atomics in fixed schema order.
- Produces: `[u64; 5]` exported outcomes and one exact `u64` completed total
  whose ordinary arithmetic equality always holds.

- [ ] **Step 1: Implement the minimal remaining-capacity projection**

Replace the beginning of `OperationMetric::snapshot()` through completed
derivation with:

```rust
let mut completed = 0_u64;
let outcomes = std::array::from_fn(|index| {
    let raw_outcome = self.outcomes[index].load(Ordering::Acquire);
    let remaining = u64::MAX - completed;
    let exported_outcome = raw_outcome.min(remaining);
    if exported_outcome != raw_outcome {
        self.saturated.store(true, Ordering::Release);
    }
    completed += exported_outcome;
    exported_outcome
});
```

Do not modify writer code or any other production file.

- [ ] **Step 2: Verify focused GREEN**

Run:

```bash
cargo test --locked \
  operation_metrics::tests::snapshot_reconciles_multiple_outcomes_into_one_saturated_total \
  -- --exact
```

Require one passing test.

- [ ] **Step 3: Verify all Rust and no-network contracts**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
.venv/bin/ruff check .
.venv/bin/python -m compileall -q python tests scripts/sql_auth
.venv/bin/python -m pytest --noconftest \
  tests/test_operation_metrics_contract.py -q
git diff --check
```

Require zero failures/warnings and all operation-metrics contracts passing.

- [ ] **Step 4: Self-review the fix boundary**

Run:

```bash
git diff --stat test/operation-metrics-saturation-coherence..HEAD
git diff -- src/operation_metrics.rs
git status --short --branch
```

Confirm:

```text
Acquire loads remain unchanged
no writer path changed
no clock/disabled path changed
no allocation/lock/dependency added
ordinary non-overflow outcomes remain exact
overflow projection is deterministic
completed equals the ordinary sum of exported outcomes
saturation remains permanently visible
```

- [ ] **Step 5: Commit and push the fix only to the fork**

Run:

```bash
git add src/operation_metrics.rs
git commit -m "fix: reconcile saturated operation outcomes"
git push -u origin fix/operation-metrics-saturation-coherence
```

---

### Task 4: Integrate into the feature candidate and repeat final gates

**Files:**

- Merge: design, RED and fix ancestry into `feat/operation-metrics`.
- Produce ignored wheel and SQL-auth evidence.

**Interfaces:**

- Consumes: verified fix branch.
- Produces: one new exact feature SHA eligible for hosted, Docker/MSSQL and
  stress gates.

- [ ] **Step 1: Merge the fix into the feature branch**

In the clean feature worktree, run:

```bash
git merge --no-ff fix/operation-metrics-saturation-coherence \
  -m "merge: preserve saturated operation metric invariants"
git push origin feat/operation-metrics
```

- [ ] **Step 2: Rebuild and verify the ABI3 wheel**

Run the exact isolated-wheel contract from
`2026-07-26-fastmssql-operation-metrics.md` Task 11 Step 5. Require 27/27.

- [ ] **Step 3: Repeat final graph, security and repository gates**

Run:

```bash
uvx code-review-graph build
uvx code-review-graph status
scripts/security/audit_dependencies.sh
git remote get-url --push origin
git remote get-url --push upstream
git status --short --branch
```

Require exact feature SHA, zero vulnerabilities/warnings, fork origin,
`DISABLED` upstream push and a clean worktree.

- [ ] **Step 4: Repeat Docker/MSSQL, stress and hosted gates**

Run Tasks 11 and 12 of
`docs/superpowers/plans/2026-07-26-fastmssql-operation-metrics.md` against the
new feature SHA. Require the complete SQL-auth matrix, both transaction
strategies through 99,999 operations, all six 99,999 operation-metrics trials,
the `0.15` median overhead gate and Linux/macOS/Windows hosted evidence.

- [ ] **Step 5: Continue exact technical integration**

Only after Step 4 is green, continue Tasks 13 and 14 of the parent plan. Record
the saturation-coherence design, RED, fix and updated feature SHAs in
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`.

---

## Plan Self-Review

- Every design requirement maps to Tasks 2 or 3.
- The RED test exercises real private registry state and the public snapshot
  arithmetic, with no mock.
- The expected RED failure distinguishes this defect from compilation or test
  setup failures.
- Production scope is one block in `OperationMetric::snapshot()`.
- The plan preserves memory ordering, fixed cardinality, public types and
  disabled-path cost.
- The integration task retains all original Docker/MSSQL, stress, wheel,
  RustSec, hosted and fork-boundary gates.
