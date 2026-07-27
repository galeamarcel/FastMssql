use crate::deadline::OperationName;
use crate::types::{CommitOutcomeUnknown, OperationTimeoutError, ShutdownTimeoutError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::future::Future;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;
use tokio::time::Instant;

pub(crate) const METRIC_OPERATIONS: [OperationName; 13] = [
    OperationName::Connect,
    OperationName::Ping,
    OperationName::Query,
    OperationName::SimpleQuery,
    OperationName::Execute,
    OperationName::QueryBatch,
    OperationName::ExecuteBatch,
    OperationName::BulkInsert,
    OperationName::Begin,
    OperationName::Commit,
    OperationName::Rollback,
    OperationName::Close,
    OperationName::Disconnect,
];

pub(crate) const fn metric_index(operation: OperationName) -> Option<usize> {
    match operation {
        OperationName::Connect => Some(0),
        OperationName::Ping => Some(1),
        OperationName::Query => Some(2),
        OperationName::SimpleQuery => Some(3),
        OperationName::Execute => Some(4),
        OperationName::QueryBatch => Some(5),
        OperationName::ExecuteBatch => Some(6),
        OperationName::BulkInsert => Some(7),
        OperationName::Begin => Some(8),
        OperationName::Commit => Some(9),
        OperationName::Rollback => Some(10),
        OperationName::Close => Some(11),
        OperationName::Disconnect => Some(12),
        OperationName::Transaction => None,
    }
}

const BUCKET_BOUNDS_MICROS: [u64; 17] = [
    100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000,
    1_000_000, 2_500_000, 5_000_000, 10_000_000, 30_000_000,
];

const BUCKET_BOUNDS_SECONDS: [f64; 17] = [
    0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
    5.0, 10.0, 30.0,
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OperationOutcome {
    Succeeded,
    Errors,
    TimedOut,
    Cancelled,
    OutcomeUnknown,
}

impl OperationOutcome {
    const fn index(self) -> usize {
        match self {
            Self::Succeeded => 0,
            Self::Errors => 1,
            Self::TimedOut => 2,
            Self::Cancelled => 3,
            Self::OutcomeUnknown => 4,
        }
    }
}

fn saturating_atomic_add(
    atomic: &AtomicU64,
    value: u64,
    saturated: &AtomicBool,
    success_order: Ordering,
) {
    atomic
        .fetch_update(success_order, Ordering::Relaxed, |current| {
            Some(match current.checked_add(value) {
                Some(updated) => updated,
                None => {
                    saturated.store(true, Ordering::Release);
                    u64::MAX
                }
            })
        })
        .expect("saturating atomic update always returns Some");
}

struct OperationMetric {
    started: AtomicU64,
    outcomes: [AtomicU64; 5],
    duration_sum_micros: AtomicU64,
    duration_min_micros: AtomicU64,
    duration_max_micros: AtomicU64,
    raw_buckets: [AtomicU64; 17],
    saturated: AtomicBool,
}

impl OperationMetric {
    fn new() -> Self {
        Self {
            started: AtomicU64::new(0),
            outcomes: std::array::from_fn(|_| AtomicU64::new(0)),
            duration_sum_micros: AtomicU64::new(0),
            duration_min_micros: AtomicU64::new(u64::MAX),
            duration_max_micros: AtomicU64::new(0),
            raw_buckets: std::array::from_fn(|_| AtomicU64::new(0)),
            saturated: AtomicBool::new(false),
        }
    }

    fn start(&self) {
        saturating_atomic_add(&self.started, 1, &self.saturated, Ordering::Relaxed);
    }

    fn record(&self, outcome: OperationOutcome, elapsed: Duration) {
        let elapsed_micros = match u64::try_from(elapsed.as_micros()) {
            Ok(value) => value,
            Err(_) => {
                self.saturated.store(true, Ordering::Release);
                u64::MAX
            }
        };
        saturating_atomic_add(
            &self.duration_sum_micros,
            elapsed_micros,
            &self.saturated,
            Ordering::Relaxed,
        );
        self.duration_min_micros
            .fetch_min(elapsed_micros, Ordering::Relaxed);
        self.duration_max_micros
            .fetch_max(elapsed_micros, Ordering::Relaxed);

        if let Some(index) = BUCKET_BOUNDS_MICROS
            .iter()
            .position(|bound| elapsed <= Duration::from_micros(*bound))
        {
            saturating_atomic_add(
                &self.raw_buckets[index],
                1,
                &self.saturated,
                Ordering::Relaxed,
            );
        }

        saturating_atomic_add(
            &self.outcomes[outcome.index()],
            1,
            &self.saturated,
            Ordering::Release,
        );
    }

    fn snapshot(&self) -> OperationMetricSnapshot {
        let mut completed = 0_u64;
        // Public saturation projection order: succeeded, errors, timed_out,
        // cancelled, outcome_unknown.
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

        let raw_started = self.started.load(Ordering::Acquire);
        let started = raw_started.max(completed);
        let in_flight = started.saturating_sub(completed);
        let mut cumulative_buckets = [0_u64; 17];
        let mut cumulative = 0_u64;
        for (index, bucket) in self.raw_buckets.iter().enumerate() {
            cumulative = match cumulative.checked_add(bucket.load(Ordering::Acquire)) {
                Some(total) => total,
                None => {
                    self.saturated.store(true, Ordering::Release);
                    u64::MAX
                }
            };
            cumulative_buckets[index] = cumulative.min(completed);
        }

        if completed == 0 {
            return OperationMetricSnapshot {
                started,
                completed,
                in_flight,
                outcomes,
                duration_sum_micros: 0,
                duration_min_micros: None,
                duration_max_micros: None,
                cumulative_buckets: [0; 17],
                saturated: self.saturated.load(Ordering::Acquire),
            };
        }

        let loaded_max = self.duration_max_micros.load(Ordering::Acquire);
        let loaded_min = self.duration_min_micros.load(Ordering::Acquire);
        let duration_max_micros = loaded_max;
        let duration_min_micros = loaded_min.min(duration_max_micros);
        let duration_sum_micros = self
            .duration_sum_micros
            .load(Ordering::Acquire)
            .max(duration_max_micros);
        OperationMetricSnapshot {
            started,
            completed,
            in_flight,
            outcomes,
            duration_sum_micros,
            duration_min_micros: Some(duration_min_micros),
            duration_max_micros: Some(duration_max_micros),
            cumulative_buckets,
            saturated: self.saturated.load(Ordering::Acquire),
        }
    }
}

struct OperationMetricSnapshot {
    started: u64,
    completed: u64,
    in_flight: u64,
    outcomes: [u64; 5],
    duration_sum_micros: u64,
    duration_min_micros: Option<u64>,
    duration_max_micros: Option<u64>,
    cumulative_buckets: [u64; 17],
    saturated: bool,
}

pub(crate) struct OperationMetricsSnapshot {
    enabled: bool,
    operations: [OperationMetricSnapshot; 13],
}

impl OperationMetricsSnapshot {
    pub(crate) fn disabled() -> Self {
        Self {
            enabled: false,
            operations: std::array::from_fn(|_| OperationMetricSnapshot {
                started: 0,
                completed: 0,
                in_flight: 0,
                outcomes: [0; 5],
                duration_sum_micros: 0,
                duration_min_micros: None,
                duration_max_micros: None,
                cumulative_buckets: [0; 17],
                saturated: false,
            }),
        }
    }

    pub(crate) fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let root = PyDict::new(py);
        root.set_item("schema_version", 1)?;
        root.set_item("enabled", self.enabled)?;
        root.set_item(
            "bucket_bounds_seconds",
            PyList::new(py, BUCKET_BOUNDS_SECONDS)?,
        )?;
        let operations = PyDict::new(py);
        for (operation, snapshot) in METRIC_OPERATIONS.iter().zip(self.operations.iter()) {
            let entry = PyDict::new(py);
            entry.set_item("started", snapshot.started)?;
            entry.set_item("completed", snapshot.completed)?;
            entry.set_item("in_flight", snapshot.in_flight)?;
            entry.set_item("succeeded", snapshot.outcomes[0])?;
            entry.set_item("errors", snapshot.outcomes[1])?;
            entry.set_item("timed_out", snapshot.outcomes[2])?;
            entry.set_item("cancelled", snapshot.outcomes[3])?;
            entry.set_item("outcome_unknown", snapshot.outcomes[4])?;
            entry.set_item(
                "duration_seconds_sum",
                snapshot.duration_sum_micros as f64 / 1_000_000.0,
            )?;
            entry.set_item(
                "duration_seconds_min",
                snapshot
                    .duration_min_micros
                    .map(|value| value as f64 / 1_000_000.0),
            )?;
            entry.set_item(
                "duration_seconds_max",
                snapshot
                    .duration_max_micros
                    .map(|value| value as f64 / 1_000_000.0),
            )?;
            entry.set_item(
                "duration_seconds_buckets",
                PyList::new(py, snapshot.cumulative_buckets)?,
            )?;
            entry.set_item("saturated", snapshot.saturated)?;
            operations.set_item(operation.as_str(), entry)?;
        }
        root.set_item("operations", operations)?;
        Ok(root.unbind())
    }
}

pub(crate) struct OperationMetricsRegistry {
    operations: [OperationMetric; 13],
}

impl OperationMetricsRegistry {
    pub(crate) fn new() -> Self {
        Self {
            operations: std::array::from_fn(|_| OperationMetric::new()),
        }
    }

    pub(crate) fn snapshot(&self) -> OperationMetricsSnapshot {
        OperationMetricsSnapshot {
            enabled: true,
            operations: std::array::from_fn(|index| self.operations[index].snapshot()),
        }
    }
}

struct OperationGuard {
    registry: Arc<OperationMetricsRegistry>,
    operation_index: usize,
    started_at: Instant,
    armed: bool,
}

/// A movable operation observation that may outlive the Python method which
/// created it.
///
/// Result-stream producers keep this observer until the complete TDS
/// response has released or retired its physical connection. Dropping an
/// unfinished observer records one cancellation.
pub(crate) struct OperationObserver {
    guard: Option<OperationGuard>,
}

impl OperationObserver {
    pub(crate) fn start(
        metrics: Option<Arc<OperationMetricsRegistry>>,
        operation: OperationName,
    ) -> Self {
        let guard = metrics.and_then(|registry| {
            metric_index(operation).map(|operation_index| {
                OperationGuard::start(registry, operation_index, Instant::now())
            })
        });
        Self { guard }
    }

    fn finish(mut self, outcome: OperationOutcome) {
        if let Some(guard) = self.guard.take() {
            let elapsed = guard.elapsed();
            guard.finish(outcome, elapsed);
        }
    }

    pub(crate) fn success(self) {
        self.finish(OperationOutcome::Succeeded);
    }

    pub(crate) fn error(self, error: &PyErr) {
        self.finish(classify_error(error));
    }

    pub(crate) fn cancel(mut self) {
        // Taking and dropping the still-armed guard records cancellation.
        drop(self.guard.take());
    }

    pub(crate) fn finish_result<T>(self, result: &PyResult<T>) {
        match result {
            Ok(_) => self.success(),
            Err(error) => self.error(error),
        }
    }
}

impl OperationGuard {
    fn start(
        registry: Arc<OperationMetricsRegistry>,
        operation_index: usize,
        started_at: Instant,
    ) -> Self {
        registry.operations[operation_index].start();
        Self {
            registry,
            operation_index,
            started_at,
            armed: true,
        }
    }

    fn elapsed(&self) -> Duration {
        self.started_at.elapsed()
    }

    fn finish(mut self, outcome: OperationOutcome, elapsed: Duration) {
        self.registry.operations[self.operation_index].record(outcome, elapsed);
        self.armed = false;
    }
}

impl Drop for OperationGuard {
    fn drop(&mut self) {
        if self.armed {
            let elapsed = self.started_at.elapsed();
            self.registry.operations[self.operation_index]
                .record(OperationOutcome::Cancelled, elapsed);
            self.armed = false;
        }
    }
}

fn classify_error(error: &PyErr) -> OperationOutcome {
    Python::attach(|py| {
        if error.is_instance_of::<CommitOutcomeUnknown>(py) {
            OperationOutcome::OutcomeUnknown
        } else if error.is_instance_of::<OperationTimeoutError>(py)
            || error.is_instance_of::<ShutdownTimeoutError>(py)
        {
            OperationOutcome::TimedOut
        } else {
            OperationOutcome::Errors
        }
    })
}

#[cfg(test)]
fn classify_result<T>(result: &PyResult<T>) -> OperationOutcome {
    match result {
        Ok(_) => OperationOutcome::Succeeded,
        Err(error) => classify_error(error),
    }
}

pub(crate) async fn observe_operation<F, T>(
    metrics: Option<Arc<OperationMetricsRegistry>>,
    operation: OperationName,
    future: F,
) -> PyResult<T>
where
    F: Future<Output = PyResult<T>>,
{
    let observer = OperationObserver::start(metrics, operation);
    let result = future.await;
    observer.finish_result(&result);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::exceptions::PyValueError;
    use std::sync::Arc;
    use std::sync::atomic::Ordering;
    use std::thread;
    use std::time::Duration;
    use tokio::time::Instant;

    #[test]
    fn active_operation_mapping_is_stable_and_complete() {
        let names: Vec<_> = METRIC_OPERATIONS
            .iter()
            .map(|operation| operation.as_str())
            .collect();
        assert_eq!(
            names,
            vec![
                "connect",
                "ping",
                "query",
                "simple_query",
                "execute",
                "query_batch",
                "execute_batch",
                "bulk_insert",
                "begin",
                "commit",
                "rollback",
                "close",
                "disconnect",
            ]
        );
        assert_eq!(metric_index(OperationName::Transaction), None);
    }

    #[test]
    fn finite_buckets_use_unrounded_inclusive_duration_bounds() {
        let registry = OperationMetricsRegistry::new();
        let index = metric_index(OperationName::Query).unwrap();
        let metric = &registry.operations[index];
        for elapsed in [
            Duration::from_micros(99),
            Duration::from_micros(100),
            Duration::from_micros(101),
            Duration::from_micros(250),
            Duration::from_secs(30),
            Duration::from_secs(30) + Duration::from_micros(1),
        ] {
            metric.start();
            metric.record(OperationOutcome::Succeeded, elapsed);
        }

        let snapshot = registry.snapshot();
        let query = &snapshot.operations[index];
        assert_eq!(query.started, 6);
        assert_eq!(query.completed, 6);
        assert_eq!(query.in_flight, 0);
        assert_eq!(query.outcomes, [6, 0, 0, 0, 0]);
        assert_eq!(query.duration_sum_micros, 60_000_551);
        assert_eq!(query.duration_min_micros, Some(99));
        assert_eq!(query.duration_max_micros, Some(30_000_001));
        assert_eq!(
            query.cumulative_buckets,
            [2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5]
        );
        assert!(!query.saturated);
    }

    #[test]
    fn atomic_updates_saturate_without_wrapping_and_stay_flagged() {
        let registry = OperationMetricsRegistry::new();
        let index = metric_index(OperationName::Execute).unwrap();
        let metric = &registry.operations[index];
        metric.started.store(u64::MAX, Ordering::Relaxed);
        metric.outcomes[OperationOutcome::Succeeded.index()].store(u64::MAX, Ordering::Relaxed);
        metric
            .duration_sum_micros
            .store(u64::MAX, Ordering::Relaxed);
        metric.raw_buckets[0].store(u64::MAX, Ordering::Relaxed);

        metric.start();
        metric.record(OperationOutcome::Succeeded, Duration::from_micros(1));

        let saturated = registry.snapshot();
        let execute = &saturated.operations[index];
        assert_eq!(execute.started, u64::MAX);
        assert_eq!(execute.completed, u64::MAX);
        assert_eq!(execute.in_flight, 0);
        assert_eq!(execute.duration_sum_micros, u64::MAX);
        assert_eq!(execute.cumulative_buckets[0], u64::MAX);
        assert!(execute.saturated);

        metric.started.store(0, Ordering::Relaxed);
        metric.outcomes[OperationOutcome::Succeeded.index()].store(0, Ordering::Relaxed);
        metric.duration_sum_micros.store(0, Ordering::Relaxed);
        metric.raw_buckets[0].store(0, Ordering::Relaxed);
        assert!(registry.snapshot().operations[index].saturated);
    }

    #[test]
    fn snapshot_reconciles_multiple_outcomes_into_one_saturated_total() {
        let registry = OperationMetricsRegistry::new();
        let index = metric_index(OperationName::Query).unwrap();
        let metric = &registry.operations[index];
        metric.started.store(u64::MAX, Ordering::Relaxed);
        metric.outcomes[0].store(u64::MAX - 2, Ordering::Relaxed);
        metric.outcomes[1].store(3, Ordering::Relaxed);
        metric.outcomes[2].store(1, Ordering::Relaxed);

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

    #[test]
    fn snapshot_keeps_exact_capacity_outcomes_unsaturated() {
        let registry = OperationMetricsRegistry::new();
        let index = metric_index(OperationName::Query).unwrap();
        let metric = &registry.operations[index];
        metric.started.store(u64::MAX, Ordering::Relaxed);
        metric.outcomes[0].store(u64::MAX - 2, Ordering::Relaxed);
        metric.outcomes[1].store(2, Ordering::Relaxed);

        let query = &registry.snapshot().operations[index];
        assert_eq!(query.outcomes, [u64::MAX - 2, 2, 0, 0, 0]);
        assert_eq!(query.completed, u64::MAX);
        assert_eq!(query.in_flight, 0);
        assert!(!query.saturated);
    }

    #[test]
    fn armed_guard_drop_records_exactly_one_cancellation() {
        let registry = Arc::new(OperationMetricsRegistry::new());
        let index = metric_index(OperationName::Query).unwrap();
        {
            let _guard = OperationGuard::start(Arc::clone(&registry), index, Instant::now());
        }

        let query = &registry.snapshot().operations[index];
        assert_eq!(query.started, 1);
        assert_eq!(query.completed, 1);
        assert_eq!(query.in_flight, 0);
        assert_eq!(query.outcomes, [0, 0, 0, 1, 0]);
    }

    #[test]
    fn result_classification_preserves_typed_outcome_categories() {
        Python::initialize();
        assert_eq!(
            classify_result(&Ok::<(), PyErr>(())),
            OperationOutcome::Succeeded
        );
        assert_eq!(
            classify_result(&Err::<(), _>(PyValueError::new_err("ordinary"))),
            OperationOutcome::Errors
        );
        assert_eq!(
            classify_result(&Err::<(), _>(OperationTimeoutError::new_err("timeout"))),
            OperationOutcome::TimedOut
        );
        assert_eq!(
            classify_result(&Err::<(), _>(ShutdownTimeoutError::new_err("shutdown"))),
            OperationOutcome::TimedOut
        );
        assert_eq!(
            classify_result(&Err::<(), _>(CommitOutcomeUnknown::new_err("unknown"))),
            OperationOutcome::OutcomeUnknown
        );
    }

    #[test]
    fn snapshot_reconciles_independent_atomic_observations() {
        let registry = OperationMetricsRegistry::new();
        let index = metric_index(OperationName::Query).unwrap();
        let metric = &registry.operations[index];
        metric.started.store(1, Ordering::Relaxed);
        metric.outcomes[0].store(2, Ordering::Relaxed);
        metric.outcomes[1].store(3, Ordering::Relaxed);
        metric.duration_sum_micros.store(2, Ordering::Relaxed);
        metric.duration_min_micros.store(9, Ordering::Relaxed);
        metric.duration_max_micros.store(4, Ordering::Relaxed);
        metric.raw_buckets[0].store(8, Ordering::Relaxed);

        let query = &registry.snapshot().operations[index];
        assert_eq!(query.completed, 5);
        assert_eq!(query.started, 5);
        assert_eq!(query.in_flight, 0);
        assert_eq!(query.duration_min_micros, Some(4));
        assert_eq!(query.duration_max_micros, Some(4));
        assert_eq!(query.duration_sum_micros, 4);
        assert_eq!(query.cumulative_buckets, [5; 17]);
    }

    #[test]
    fn concurrent_writers_and_scraper_preserve_every_arithmetic_invariant() {
        const WRITERS: usize = 4;
        const COMPLETIONS_PER_WRITER: usize = 10_000;
        let registry = Arc::new(OperationMetricsRegistry::new());
        let operation_index = metric_index(OperationName::Query).unwrap();
        let writers: Vec<_> = (0..WRITERS)
            .map(|_| {
                let registry = Arc::clone(&registry);
                thread::spawn(move || {
                    for _ in 0..COMPLETIONS_PER_WRITER {
                        let metric = &registry.operations[operation_index];
                        metric.start();
                        metric.record(OperationOutcome::Succeeded, Duration::from_micros(100));
                    }
                })
            })
            .collect();

        while writers.iter().any(|writer| !writer.is_finished()) {
            let query = &registry.snapshot().operations[operation_index];
            assert_eq!(
                query.started,
                query.completed.saturating_add(query.in_flight)
            );
            assert_eq!(query.completed, query.outcomes.into_iter().sum::<u64>());
            assert!(
                query
                    .cumulative_buckets
                    .windows(2)
                    .all(|pair| pair[0] <= pair[1])
            );
            assert!(
                query
                    .cumulative_buckets
                    .iter()
                    .all(|count| *count <= query.completed)
            );
        }
        for writer in writers {
            writer.join().unwrap();
        }

        let query = &registry.snapshot().operations[operation_index];
        let expected = (WRITERS * COMPLETIONS_PER_WRITER) as u64;
        assert_eq!(query.started, expected);
        assert_eq!(query.completed, expected);
        assert_eq!(query.in_flight, 0);
        assert_eq!(query.outcomes, [expected, 0, 0, 0, 0]);
        assert_eq!(query.cumulative_buckets, [expected; 17]);
    }
}
