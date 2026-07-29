use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_async_runtimes::tokio::future_into_py;
use std::sync::{Arc, Mutex as StdMutex, MutexGuard as StdMutexGuard};
use tokio::sync::Mutex as AsyncMutex;
use tokio::time::Instant;

use crate::deadline::{
    Deadline, DeadlineElapsed, OperationName, TimeoutPhase, earliest_deadline, run_until,
};
use crate::native_bulk::{
    NativeBulkTarget, native_timeout_error, prepare_native_bulk_chunk, prepare_native_bulk_target,
};
use crate::operation_metrics::{OperationMetricsRegistry, OperationObserver};
use crate::timeout_config::PyTimeoutConfig;
use crate::transaction::{NativeBulkTransactionOwner, ReservedNativeBulkFailure, Transaction};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum NativeBulkSequencePhase {
    Constructed,
    Reserved,
    Active,
    Terminal,
}

struct NativeBulkProgress {
    phase: NativeBulkSequencePhase,
    chunk_size: usize,
    row_index_base: usize,
    total: u64,
    any_row_sent: bool,
    pending_rows: Option<usize>,
    failed: bool,
}

impl NativeBulkProgress {
    fn new(chunk_size: usize) -> Self {
        Self {
            phase: NativeBulkSequencePhase::Constructed,
            chunk_size,
            row_index_base: 0,
            total: 0,
            any_row_sent: false,
            pending_rows: None,
            failed: false,
        }
    }

    fn phase(&self) -> NativeBulkSequencePhase {
        self.phase
    }

    fn requires_abandonment(&self) -> bool {
        matches!(
            self.phase,
            NativeBulkSequencePhase::Reserved | NativeBulkSequencePhase::Active
        )
    }

    fn reserve(&mut self) -> PyResult<()> {
        if self.phase != NativeBulkSequencePhase::Constructed {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence may be reserved only once",
            ));
        }
        self.phase = NativeBulkSequencePhase::Reserved;
        Ok(())
    }

    fn activate(&mut self) -> PyResult<()> {
        if self.failed {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence failed and must be aborted",
            ));
        }
        match self.phase {
            NativeBulkSequencePhase::Reserved => {
                self.phase = NativeBulkSequencePhase::Active;
                Ok(())
            }
            NativeBulkSequencePhase::Active => Ok(()),
            NativeBulkSequencePhase::Constructed | NativeBulkSequencePhase::Terminal => Err(
                PyRuntimeError::new_err("Native bulk sequence activation requires one reservation"),
            ),
        }
    }

    fn prepare_push(&mut self, row_count: usize) -> PyResult<(usize, bool)> {
        if self.phase != NativeBulkSequencePhase::Active {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence is not active",
            ));
        }
        if self.failed {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence failed and must be aborted",
            ));
        }
        if self.pending_rows.is_some() {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence already has one in-flight chunk",
            ));
        }
        if row_count == 0 || row_count > self.chunk_size {
            return Err(PyValueError::new_err(
                "native bulk chunk must contain between 1 and chunk_size rows",
            ));
        }
        self.pending_rows = Some(row_count);
        Ok((self.row_index_base, self.any_row_sent))
    }

    fn complete_push(&mut self, row_count: usize, affected: u64) -> PyResult<()> {
        if self.pending_rows != Some(row_count) {
            self.any_row_sent = true;
            self.pending_rows = None;
            self.failed = true;
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence chunk completion did not match its in-flight chunk",
            ));
        }
        self.any_row_sent = true;
        self.pending_rows = None;
        let expected = u64::try_from(row_count).map_err(|_| {
            self.failed = true;
            PyValueError::new_err("native bulk chunk row count overflowed u64")
        })?;
        if affected != expected {
            self.failed = true;
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence affected count did not match its in-flight chunk",
            ));
        }

        let row_index_base = self.row_index_base.checked_add(row_count).ok_or_else(|| {
            self.failed = true;
            PyValueError::new_err("native bulk row offset overflowed usize")
        })?;
        let total = self.total.checked_add(affected).ok_or_else(|| {
            self.failed = true;
            PyValueError::new_err("native bulk cumulative count overflowed u64")
        })?;

        self.row_index_base = row_index_base;
        self.total = total;
        Ok(())
    }

    fn finish(&mut self) -> PyResult<u64> {
        if !matches!(
            self.phase,
            NativeBulkSequencePhase::Reserved | NativeBulkSequencePhase::Active
        ) || self.pending_rows.is_some()
            || self.failed
        {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence cannot finish from its current state",
            ));
        }
        self.phase = NativeBulkSequencePhase::Terminal;
        Ok(self.total)
    }

    fn fail_push(&mut self, any_row_sent: bool) {
        self.any_row_sent |= any_row_sent;
        self.pending_rows = None;
        self.failed = true;
    }

    fn terminate(&mut self) {
        self.pending_rows = None;
        self.phase = NativeBulkSequencePhase::Terminal;
    }

    fn any_row_sent(&self) -> bool {
        self.any_row_sent
    }
}

#[derive(Clone)]
struct SharedNativeBulkDeadline {
    inner: Arc<StdMutex<Option<Deadline>>>,
}

impl SharedNativeBulkDeadline {
    fn new(deadline: Option<Deadline>) -> Self {
        Self {
            inner: Arc::new(StdMutex::new(deadline)),
        }
    }

    fn lock(&self) -> StdMutexGuard<'_, Option<Deadline>> {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn get(&self) -> Option<Deadline> {
        *self.lock()
    }

    fn merge(&self, deadline: Option<Deadline>) {
        let mut current = self.lock();
        *current = earliest_deadline(*current, deadline);
    }

    fn remaining_seconds(&self) -> Option<f64> {
        self.get().map(|deadline| {
            deadline
                .at
                .saturating_duration_since(Instant::now())
                .as_secs_f64()
        })
    }

    fn elapsed(&self) -> PyResult<DeadlineElapsed> {
        self.get()
            .map(|deadline| DeadlineElapsed {
                timeout: deadline.timeout,
                phase: deadline.phase,
            })
            .ok_or_else(|| {
                PyRuntimeError::new_err("Native bulk sequence has no operation deadline to expire")
            })
    }
}

#[derive(Clone, Copy)]
enum NativeBulkAbortOutcome {
    Error,
    Cancelled,
}

impl NativeBulkAbortOutcome {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "error" => Ok(Self::Error),
            "cancelled" => Ok(Self::Cancelled),
            _ => Err(PyValueError::new_err(
                "native bulk abort outcome must be 'error' or 'cancelled'",
            )),
        }
    }
}

fn sequence_requires_database_cleanup(
    owner: NativeBulkTransactionOwner,
    phase: NativeBulkSequencePhase,
    database_reserved: bool,
) -> bool {
    if !database_reserved
        || matches!(
            phase,
            NativeBulkSequencePhase::Constructed | NativeBulkSequencePhase::Terminal
        )
    {
        return false;
    }
    match owner {
        NativeBulkTransactionOwner::Connection => phase == NativeBulkSequencePhase::Active,
        NativeBulkTransactionOwner::Caller => matches!(
            phase,
            NativeBulkSequencePhase::Reserved | NativeBulkSequencePhase::Active
        ),
    }
}

struct NativeBulkSequence {
    owner: NativeBulkTransactionOwner,
    transaction: Transaction,
    target: NativeBulkTarget,
    progress: NativeBulkProgress,
    started_at: Instant,
    deadline: SharedNativeBulkDeadline,
    metrics: Option<Arc<OperationMetricsRegistry>>,
    observer: Option<OperationObserver>,
    retained_error: Option<PyErr>,
    database_reserved: bool,
}

impl NativeBulkSequence {
    fn ensure_observer(&mut self) {
        if self.observer.is_none() {
            self.observer = Some(OperationObserver::start_at(
                self.metrics.clone(),
                OperationName::BulkInsert,
                self.started_at,
            ));
        }
    }

    fn retain_error(&mut self, error: PyErr) -> PyErr {
        let returned = Python::attach(|py| error.clone_ref(py));
        self.retained_error = Some(error);
        returned
    }

    async fn reserve(&mut self) -> PyResult<()> {
        self.progress.reserve()?;
        if self.owner == NativeBulkTransactionOwner::Connection {
            return Ok(());
        }

        let reservation = run_until(
            self.deadline.get(),
            self.transaction.reserve_bulk_producer(),
        )
        .await;
        match reservation {
            Ok(Ok(lifetime_deadline)) => {
                self.deadline.merge(lifetime_deadline);
                self.database_reserved = true;
                Ok(())
            }
            Ok(Err(error)) => {
                self.progress.terminate();
                Err(error)
            }
            Err(elapsed) => {
                self.progress.terminate();
                let error = native_timeout_error(elapsed, false, false);
                self.ensure_observer();
                self.observer
                    .take()
                    .expect("reserve timeout must own one observer")
                    .error(&error);
                Err(error)
            }
        }
    }

    async fn activate(&mut self) -> PyResult<()> {
        let already_active = self.progress.phase() == NativeBulkSequencePhase::Active;
        self.progress.activate()?;
        if already_active {
            return Ok(());
        }
        self.ensure_observer();
        if self.owner == NativeBulkTransactionOwner::Caller {
            let confirmation = run_until(
                self.deadline.get(),
                self.transaction.confirm_reserved_bulk(),
            )
            .await;
            return match confirmation {
                Ok(Ok(())) => Ok(()),
                Ok(Err(error)) => {
                    self.progress.fail_push(false);
                    let returned = self.retain_error(error);
                    Err(returned)
                }
                Err(elapsed) => {
                    let error = native_timeout_error(elapsed, false, false);
                    self.progress.fail_push(false);
                    let returned = self.retain_error(error);
                    Err(returned)
                }
            };
        }

        let activation = run_until(
            self.deadline.get(),
            self.transaction.activate_owned_bulk_lifecycle(),
        )
        .await;
        match activation {
            Ok(Ok(())) => {
                self.database_reserved = true;
                Ok(())
            }
            Ok(Err(error)) => {
                self.progress.fail_push(false);
                let returned = self.retain_error(error);
                Err(returned)
            }
            Err(elapsed) => {
                let error = native_timeout_error(elapsed, false, false);
                self.progress.fail_push(false);
                let returned = self.retain_error(error);
                Err(returned)
            }
        }
    }

    async fn push(&mut self, rows: Py<PyList>, row_count: usize) -> PyResult<u64> {
        let (row_index_base, any_prior_row_sent) = self.progress.prepare_push(row_count)?;
        let input = Python::attach(|py| {
            prepare_native_bulk_chunk(self.target.clone(), rows.bind(py), row_index_base)
        });
        let input = match input {
            Ok(input) => input,
            Err(error) => {
                self.progress.fail_push(any_prior_row_sent);
                let returned = self.retain_error(error);
                return Err(returned);
            }
        };

        match self
            .transaction
            .execute_reserved_native_bulk_chunk(
                self.owner,
                self.deadline.get(),
                input,
                any_prior_row_sent,
            )
            .await
        {
            Ok(affected) => {
                if let Err(error) = self.progress.complete_push(row_count, affected) {
                    let returned = self.retain_error(error);
                    return Err(returned);
                }
                Ok(affected)
            }
            Err(ReservedNativeBulkFailure {
                error,
                any_row_sent,
            }) => {
                self.progress.fail_push(any_row_sent);
                let returned = self.retain_error(error);
                Err(returned)
            }
        }
    }

    async fn finish(&mut self) -> PyResult<u64> {
        match self.progress.phase() {
            NativeBulkSequencePhase::Constructed => {
                return Err(PyRuntimeError::new_err(
                    "Native bulk sequence must be reserved before finish",
                ));
            }
            NativeBulkSequencePhase::Terminal => {
                return Err(PyRuntimeError::new_err(
                    "Native bulk sequence has already terminated",
                ));
            }
            NativeBulkSequencePhase::Reserved => {
                if self.owner == NativeBulkTransactionOwner::Caller
                    && let Err(error) = self
                        .transaction
                        .finish_reserved_bulk(self.owner, self.deadline.get())
                        .await
                {
                    self.progress.terminate();
                    return Err(error);
                }
                return self.progress.finish();
            }
            NativeBulkSequencePhase::Active => {}
        }

        let settlement = self
            .transaction
            .finish_reserved_bulk(self.owner, self.deadline.get())
            .await;
        match settlement {
            Ok(()) => {
                let total = self.progress.finish()?;
                self.observer
                    .take()
                    .expect("active sequence must own one observer")
                    .success();
                Ok(total)
            }
            Err(error) => {
                self.progress.terminate();
                self.observer
                    .take()
                    .expect("active sequence must own one observer")
                    .error(&error);
                Err(error)
            }
        }
    }

    async fn cleanup_database(&self) -> PyResult<()> {
        if !sequence_requires_database_cleanup(
            self.owner,
            self.progress.phase(),
            self.database_reserved,
        ) {
            return Ok(());
        }
        self.transaction
            .abort_reserved_bulk(self.owner, self.progress.any_row_sent())
            .await
    }

    fn finish_aborted_observer(&mut self, outcome: NativeBulkAbortOutcome) {
        let Some(observer) = self.observer.take() else {
            return;
        };
        if let Some(error) = self.retained_error.take() {
            observer.error(&error);
            return;
        }
        match outcome {
            NativeBulkAbortOutcome::Error => {
                observer.error(&PyRuntimeError::new_err(
                    "Native bulk producer failed before completion",
                ));
            }
            NativeBulkAbortOutcome::Cancelled => observer.cancel(),
        }
    }

    async fn abort(&mut self, outcome: NativeBulkAbortOutcome) -> PyResult<()> {
        if matches!(
            self.progress.phase(),
            NativeBulkSequencePhase::Constructed | NativeBulkSequencePhase::Terminal
        ) {
            return Ok(());
        }
        self.ensure_observer();
        let cleanup = self.cleanup_database().await;
        self.progress.terminate();
        self.finish_aborted_observer(outcome);
        cleanup
    }

    async fn expire(&mut self) -> PyResult<()> {
        if self.progress.phase() == NativeBulkSequencePhase::Terminal {
            return Err(PyRuntimeError::new_err(
                "Native bulk sequence has already terminated",
            ));
        }
        self.ensure_observer();
        let elapsed = self.deadline.elapsed()?;
        let timeout = native_timeout_error(elapsed, false, false);
        let cleanup = self.cleanup_database().await;
        self.progress.terminate();
        self.retained_error.take();
        self.observer
            .take()
            .expect("expired sequence must own one observer")
            .error(&timeout);

        match cleanup {
            Ok(()) => Err(timeout),
            Err(cleanup_error) => {
                Python::attach(|py| timeout.set_cause(py, Some(cleanup_error)));
                Err(timeout)
            }
        }
    }

    async fn abandon(&mut self) {
        if !self.progress.requires_abandonment() {
            return;
        }
        if sequence_requires_database_cleanup(
            self.owner,
            self.progress.phase(),
            self.database_reserved,
        ) {
            self.transaction
                .abandon_reserved_bulk(self.owner, self.progress.any_row_sent())
                .await;
        }
        self.progress.terminate();
        self.retained_error.take();
        if let Some(observer) = self.observer.take() {
            observer.cancel();
        }
    }
}

#[pyclass(name = "_NativeBulkSequence")]
pub(crate) struct PyNativeBulkSequence {
    inner: Arc<AsyncMutex<NativeBulkSequence>>,
    deadline: SharedNativeBulkDeadline,
}

impl PyNativeBulkSequence {
    pub(crate) fn new(
        owner: NativeBulkTransactionOwner,
        transaction: Transaction,
        table: String,
        columns: Vec<String>,
        chunk_size: usize,
        timeout_config: &PyTimeoutConfig,
        metrics: Option<Arc<OperationMetricsRegistry>>,
    ) -> PyResult<Self> {
        let target = prepare_native_bulk_target(table, columns, chunk_size)?;
        let started_at = Instant::now();
        let operation_deadline = timeout_config.operation_timeout.map(|timeout| Deadline {
            at: started_at + timeout,
            timeout,
            phase: TimeoutPhase::Operation,
        });
        let deadline = SharedNativeBulkDeadline::new(operation_deadline);
        Ok(Self {
            inner: Arc::new(AsyncMutex::new(NativeBulkSequence {
                owner,
                transaction,
                target,
                progress: NativeBulkProgress::new(chunk_size),
                started_at,
                deadline: deadline.clone(),
                metrics,
                observer: None,
                retained_error: None,
                database_reserved: false,
            })),
            deadline,
        })
    }

    fn concurrent_call_error() -> PyErr {
        PyRuntimeError::new_err("Native bulk sequence already has one in-flight call")
    }
}

#[pymethods]
impl PyNativeBulkSequence {
    pub fn reserve<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner
                .try_lock()
                .map_err(|_| Self::concurrent_call_error())?;
            sequence.reserve().await
        })
    }

    pub fn activate<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner
                .try_lock()
                .map_err(|_| Self::concurrent_call_error())?;
            sequence.activate().await
        })
    }

    pub fn push<'p>(&self, py: Python<'p>, rows: &Bound<'p, PyList>) -> PyResult<Bound<'p, PyAny>> {
        let row_count = rows.len();
        let rows = rows.clone().unbind();
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner
                .try_lock()
                .map_err(|_| Self::concurrent_call_error())?;
            sequence.push(rows, row_count).await
        })
    }

    pub fn finish<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner
                .try_lock()
                .map_err(|_| Self::concurrent_call_error())?;
            sequence.finish().await
        })
    }

    pub fn abort<'p>(&self, py: Python<'p>, outcome: &str) -> PyResult<Bound<'p, PyAny>> {
        let outcome = NativeBulkAbortOutcome::parse(outcome)?;
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            // Cleanup may race cancellation delivery from a preceding PyO3
            // awaitable. Wait for that future to release the sequence so the
            // observer and database state are terminal before Python re-raises.
            let mut sequence = inner.lock().await;
            sequence.abort(outcome).await
        })
    }

    pub fn expire<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner.lock().await;
            sequence.expire().await
        })
    }

    pub fn remaining_timeout(&self) -> Option<f64> {
        self.deadline.remaining_seconds()
    }
}

impl Drop for PyNativeBulkSequence {
    fn drop(&mut self) {
        let inner = Arc::clone(&self.inner);
        let _cleanup_task = pyo3_async_runtimes::tokio::get_runtime().spawn(async move {
            let mut sequence = inner.lock().await;
            sequence.abandon().await;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::{NativeBulkProgress, NativeBulkSequencePhase, sequence_requires_database_cleanup};
    use crate::transaction::NativeBulkTransactionOwner;
    use pyo3::Python;

    #[test]
    fn sequence_progress_is_bounded_and_preserves_global_indices() {
        Python::initialize();
        let mut progress = NativeBulkProgress::new(3);

        assert_eq!(progress.phase(), NativeBulkSequencePhase::Constructed);
        assert!(!progress.requires_abandonment());

        progress
            .reserve()
            .expect("one producer must reserve the sequence");
        assert_eq!(progress.phase(), NativeBulkSequencePhase::Reserved);
        assert!(progress.requires_abandonment());

        progress
            .activate()
            .expect("the first observed row must activate the sequence");
        assert_eq!(progress.phase(), NativeBulkSequencePhase::Active);

        assert_eq!(
            progress
                .prepare_push(3)
                .expect("one full chunk must be accepted"),
            (0, false),
        );
        progress
            .complete_push(3, 3)
            .expect("the complete TDS response must advance progress");
        assert_eq!(
            progress
                .prepare_push(2)
                .expect("the final partial chunk must be accepted"),
            (3, true),
        );
        progress
            .complete_push(2, 2)
            .expect("the final complete response must advance progress");

        assert_eq!(progress.finish().expect("one sequence must finish once"), 5,);
        assert_eq!(progress.phase(), NativeBulkSequencePhase::Terminal);
        assert!(!progress.requires_abandonment());
    }

    #[test]
    fn sequence_progress_rejects_unbounded_or_post_terminal_pushes() {
        Python::initialize();
        let mut progress = NativeBulkProgress::new(2);
        progress.reserve().expect("reservation must succeed");
        progress.activate().expect("activation must succeed");

        assert!(progress.prepare_push(0).is_err());
        assert!(progress.prepare_push(3).is_err());
        assert_eq!(progress.finish().expect("finish must succeed"), 0);
        assert!(progress.prepare_push(1).is_err());
        assert!(progress.finish().is_err());
    }

    #[test]
    fn sequence_activation_is_idempotent_only_while_the_sequence_remains_healthy() {
        Python::initialize();
        let mut progress = NativeBulkProgress::new(2);
        progress.reserve().expect("reservation must succeed");
        progress.activate().expect("first activation must succeed");
        progress
            .activate()
            .expect("repeating activation on the same healthy sequence must be idempotent");

        progress.fail_push(false);
        assert!(
            progress.activate().is_err(),
            "activation must not hide a failure that still requires cleanup",
        );
    }

    #[test]
    fn post_wire_accounting_failure_requires_cleanup_and_cannot_finish() {
        Python::initialize();
        let mut progress = NativeBulkProgress::new(2);
        progress.reserve().expect("reservation must succeed");
        progress.activate().expect("activation must succeed");
        progress
            .prepare_push(2)
            .expect("one bounded chunk must enter flight");

        let error = progress
            .complete_push(2, 1)
            .expect_err("an affected-count mismatch must fail closed");
        assert!(error.to_string().contains("affected count"));
        assert!(
            progress.any_row_sent(),
            "a complete TDS response means database work was attempted",
        );
        assert!(
            progress.prepare_push(1).is_err(),
            "a failed sequence must reject later chunks",
        );
        assert!(
            progress.finish().is_err(),
            "a failed sequence must require abort instead of settlement",
        );
    }

    #[test]
    fn sequence_progress_drop_gate_distinguishes_never_started_and_non_terminal() {
        Python::initialize();
        let constructed = NativeBulkProgress::new(1);
        assert!(!constructed.requires_abandonment());

        let mut reserved = NativeBulkProgress::new(1);
        reserved.reserve().expect("reservation must succeed");
        assert!(reserved.requires_abandonment());

        let mut activated = NativeBulkProgress::new(1);
        activated.reserve().expect("reservation must succeed");
        activated.activate().expect("activation must succeed");
        assert!(activated.requires_abandonment());
    }

    #[test]
    fn failed_connection_activation_does_not_invent_database_cleanup() {
        assert!(!sequence_requires_database_cleanup(
            NativeBulkTransactionOwner::Connection,
            NativeBulkSequencePhase::Active,
            false,
        ));
        assert!(sequence_requires_database_cleanup(
            NativeBulkTransactionOwner::Connection,
            NativeBulkSequencePhase::Active,
            true,
        ));
        assert!(sequence_requires_database_cleanup(
            NativeBulkTransactionOwner::Caller,
            NativeBulkSequencePhase::Reserved,
            true,
        ));
        assert!(!sequence_requires_database_cleanup(
            NativeBulkTransactionOwner::Caller,
            NativeBulkSequencePhase::Terminal,
            true,
        ));
    }
}
