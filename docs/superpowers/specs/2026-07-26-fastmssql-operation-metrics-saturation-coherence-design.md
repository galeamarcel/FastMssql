# FastMssql Operation-Metrics Saturation Coherence Design

Status: approved under the owner's standing authorization for inline
design/specification and implementation, subject to self-review.

## Context

The operation-metrics registry stores five independent saturating `AtomicU64`
outcome counters. A snapshot currently loads all five raw counters and derives
`completed` with saturating addition. If the mathematical sum of the raw
outcomes exceeds `u64::MAX`, the exported values can violate the public
invariant:

```text
completed == succeeded + errors + timed_out + cancelled + outcome_unknown
```

For example, raw outcomes `[u64::MAX, 1, 0, 0, 0]` currently export those
same five values while `completed` is only `u64::MAX`. Python integers do not
saturate when the consumer adds the exported values, so the mismatch remains
visible even though `saturated=True`.

This is a snapshot-reconciliation defect. Writers already saturate correctly,
publish duration data before the Release outcome update, and preserve the
fixed memory/resource model.

## Requirements

- Preserve the existing public schema, operation set, bucket bounds and
  default-off behavior.
- Keep every writer counter as `AtomicU64`; add no lock, allocation, exporter,
  dependency or background task.
- Keep `completed`, `started`, `in_flight` and every exported outcome bounded
  by `u64::MAX`.
- Preserve exact raw outcome values whenever their mathematical sum fits in
  `u64`.
- If the sum would overflow, export a deterministic saturated projection whose
  five values sum exactly to `u64::MAX`.
- Set the operation's permanent `saturated` flag whenever projection discards
  any raw count.
- Continue to satisfy every public arithmetic and bucket invariant during
  concurrent snapshots and after quiescence.
- Do not change outcome classification priority or writer memory ordering.

## Considered approaches

### A. Remaining-capacity projection — selected

Load outcomes with Acquire ordering in the fixed public schema order. Starting
with a remaining capacity of `u64::MAX`, export:

```text
exported_outcome = min(raw_outcome, remaining_capacity)
remaining_capacity -= exported_outcome
completed = u64::MAX - remaining_capacity
```

If `exported_outcome != raw_outcome`, permanently set `saturated=True`.

This is bounded, deterministic, non-wrapping and directly models the
contribution accepted by a saturating sum. Before saturation it is bit-for-bit
identical to the current behavior. After saturation exact category totals are
already unrecoverable; the flag makes that loss explicit.

### B. Export an unbounded Python `completed`

Summing raw outcomes into a wider integer would preserve the five loaded
values, but would contradict the documented `u64::MAX` saturation boundary
and require a different Rust/Python snapshot representation.

### C. Proportional redistribution

Scaling all outcomes into the available `u64` range would introduce invented
approximations, division and rounding rules. It is less predictable and adds
complexity without restoring exact information.

## Implementation boundary

Change only snapshot reconciliation in `src/operation_metrics.rs`. Introduce a
small private helper if that makes the invariant explicit. Do not alter
`OperationMetric::record`, `OperationGuard`, public Python types, stubs or
instrumented operation boundaries.

The fixed projection order is:

```text
succeeded errors timed_out cancelled outcome_unknown
```

This is the existing schema/storage order. It is a rendering rule only; it
does not change the typed outcome-classification priority.

## Test strategy

On a dedicated RED branch, add a Rust unit regression that seeds multiple raw
outcomes whose mathematical sum exceeds `u64::MAX`. It must prove the current
implementation violates the public equality.

On the fix branch, require:

- the exported outcomes sum exactly to `completed`;
- `completed == u64::MAX`;
- `started == completed + in_flight`;
- the deterministic projection order is exact;
- `saturated` is permanently true;
- a multi-outcome non-overflow case remains unchanged.

Then run `cargo fmt --check`, Clippy with `-D warnings`, all locked Rust tests,
the installed operation-metrics contract and the existing full candidate gates.
Real SQL behavior is unchanged, but the final Docker/MSSQL matrix and stress
evidence remain mandatory before integration.

## Git and publication boundary

Use separate fork-only branches for this design, RED reproduction and fix.
Merge the verified fix into `feat/operation-metrics` only after the focused and
full local non-SQL gates pass. Never push or open a pull request against
`Rivendael/FastMssql`.
