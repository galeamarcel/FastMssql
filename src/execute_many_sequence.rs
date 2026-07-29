use pyo3::exceptions::{PyOverflowError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyList, PyListMethods};
use pyo3_async_runtimes::tokio::future_into_py;
use std::sync::{Arc, Mutex as StdMutex, MutexGuard as StdMutexGuard};
use tokio::sync::Mutex as AsyncMutex;
use tokio::time::Instant;

use crate::deadline::{
    Deadline, DeadlineElapsed, OperationName, TimeoutPhase, earliest_deadline, run_until,
};
use crate::execute_many::{
    ExecuteManyWireProgress, PreparedExecuteManyChunk, attach_execute_many_error_metadata,
    prepare_execute_many_chunk, set_execute_many_operation,
};
use crate::helpers::requires_connection_retirement;
use crate::operation_metrics::{OperationMetricsRegistry, OperationObserver};
use crate::pool_manager::timeout_error_or_metadata_failure;
use crate::timeout_config::PyTimeoutConfig;
use crate::transaction::{ReservedExecuteManyFailure, Transaction};
use crate::types::TimeoutErrorMetadata;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ExecuteManyMode {
    ConnectionAtomic,
    ConnectionChunked,
    CallerTransaction,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ExecuteManySequencePhase {
    Constructed,
    Reserved,
    Active,
    Pushing,
    Finishing,
    Terminal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ExecuteManyAbortProgress {
    pub(crate) active_parameter_set_index: Option<usize>,
    pub(crate) confirmed_committed_parameter_sets: usize,
    pub(crate) partial_commit_possible: bool,
}

pub(crate) struct ExecuteManyProgress {
    mode: ExecuteManyMode,
    phase: ExecuteManySequencePhase,
    chunk_size: usize,
    next_parameter_set_index: usize,
    active_parameter_set_index: Option<usize>,
    pending_parameter_sets: Option<usize>,
    confirmed_committed_parameter_sets: usize,
    total: u64,
    any_statement_sent: bool,
    commit_outcome_unknown: bool,
    failed: bool,
}

impl ExecuteManyProgress {
    pub(crate) fn new(mode: ExecuteManyMode, chunk_size: usize) -> Self {
        Self {
            mode,
            phase: ExecuteManySequencePhase::Constructed,
            chunk_size,
            next_parameter_set_index: 0,
            active_parameter_set_index: None,
            pending_parameter_sets: None,
            confirmed_committed_parameter_sets: 0,
            total: 0,
            any_statement_sent: false,
            commit_outcome_unknown: false,
            failed: false,
        }
    }

    #[cfg(test)]
    fn with_index_for_test(
        mode: ExecuteManyMode,
        chunk_size: usize,
        next_parameter_set_index: usize,
    ) -> Self {
        Self {
            next_parameter_set_index,
            ..Self::new(mode, chunk_size)
        }
    }

    pub(crate) fn phase(&self) -> ExecuteManySequencePhase {
        self.phase
    }

    pub(crate) fn requires_abandonment(&self) -> bool {
        !matches!(
            self.phase,
            ExecuteManySequencePhase::Constructed | ExecuteManySequencePhase::Terminal
        )
    }

    pub(crate) fn reserve(&mut self) -> PyResult<()> {
        if self.phase != ExecuteManySequencePhase::Constructed {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence may be reserved only once",
            ));
        }
        self.phase = ExecuteManySequencePhase::Reserved;
        Ok(())
    }

    pub(crate) fn activate(&mut self) -> PyResult<()> {
        if self.failed {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence failed and must be aborted",
            ));
        }
        match self.phase {
            ExecuteManySequencePhase::Reserved => {
                self.phase = ExecuteManySequencePhase::Active;
                Ok(())
            }
            ExecuteManySequencePhase::Active => Ok(()),
            _ => Err(PyRuntimeError::new_err(
                "execute_many sequence activation requires one reservation",
            )),
        }
    }

    pub(crate) fn prepare_push(&mut self, parameter_set_count: usize) -> PyResult<usize> {
        if self.phase != ExecuteManySequencePhase::Active || self.failed {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence is not ready for a chunk",
            ));
        }
        if parameter_set_count == 0 || parameter_set_count > self.chunk_size {
            return Err(PyValueError::new_err(
                "execute_many chunk must contain between 1 and chunk_size parameter sets",
            ));
        }
        self.pending_parameter_sets = Some(parameter_set_count);
        self.active_parameter_set_index = Some(self.next_parameter_set_index);
        self.phase = ExecuteManySequencePhase::Pushing;
        Ok(self.next_parameter_set_index)
    }

    pub(crate) fn complete_push(
        &mut self,
        parameter_set_count: usize,
        affected: u64,
        commit_acknowledged: bool,
    ) -> PyResult<()> {
        if self.phase != ExecuteManySequencePhase::Pushing
            || self.pending_parameter_sets != Some(parameter_set_count)
        {
            self.failed = true;
            return Err(PyRuntimeError::new_err(
                "execute_many chunk completion did not match its in-flight chunk",
            ));
        }
        let next_parameter_set_index = self
            .next_parameter_set_index
            .checked_add(parameter_set_count)
            .ok_or_else(|| {
                self.failed = true;
                PyOverflowError::new_err("execute_many parameter-set index overflowed usize")
            })?;
        let total = self.total.checked_add(affected).ok_or_else(|| {
            self.failed = true;
            PyOverflowError::new_err("execute_many total affected-row count overflowed u64")
        })?;
        if self.mode == ExecuteManyMode::ConnectionChunked && commit_acknowledged {
            self.confirmed_committed_parameter_sets = next_parameter_set_index;
        }
        self.next_parameter_set_index = next_parameter_set_index;
        self.total = total;
        self.active_parameter_set_index = None;
        self.pending_parameter_sets = None;
        self.phase = ExecuteManySequencePhase::Active;
        Ok(())
    }

    pub(crate) fn note_local_failure(&mut self, parameter_set_index: usize) {
        self.active_parameter_set_index = Some(parameter_set_index);
        self.pending_parameter_sets = None;
        self.failed = true;
    }

    pub(crate) fn note_wire_failure(
        &mut self,
        parameter_set_index: usize,
        any_statement_sent: bool,
        commit_outcome_unknown: bool,
    ) {
        self.active_parameter_set_index = Some(parameter_set_index);
        self.any_statement_sent |= any_statement_sent;
        self.commit_outcome_unknown |= commit_outcome_unknown;
        self.pending_parameter_sets = None;
        self.failed = true;
    }

    pub(crate) fn terminate(&mut self) {
        self.pending_parameter_sets = None;
        self.phase = ExecuteManySequencePhase::Terminal;
    }

    pub(crate) fn begin_finish(&mut self) -> PyResult<()> {
        if !matches!(
            self.phase,
            ExecuteManySequencePhase::Reserved | ExecuteManySequencePhase::Active
        ) || self.failed
        {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence cannot finish from its current state",
            ));
        }
        self.phase = ExecuteManySequencePhase::Finishing;
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> PyResult<u64> {
        if !matches!(
            self.phase,
            ExecuteManySequencePhase::Reserved
                | ExecuteManySequencePhase::Active
                | ExecuteManySequencePhase::Finishing
        ) || self.failed
        {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence cannot finish from its current state",
            ));
        }
        self.phase = ExecuteManySequencePhase::Terminal;
        self.active_parameter_set_index = None;
        Ok(self.total)
    }

    #[cfg(test)]
    pub(crate) fn confirmed_committed_parameter_sets(&self) -> usize {
        self.confirmed_committed_parameter_sets
    }

    pub(crate) fn any_statement_sent(&self) -> bool {
        self.any_statement_sent
    }

    #[cfg(test)]
    pub(crate) fn abort_snapshot(&self) -> ExecuteManyAbortProgress {
        let partial_commit_possible = self.mode == ExecuteManyMode::ConnectionChunked
            && (self.confirmed_committed_parameter_sets > 0 || self.commit_outcome_unknown);
        ExecuteManyAbortProgress {
            active_parameter_set_index: self.active_parameter_set_index,
            confirmed_committed_parameter_sets: if self.mode == ExecuteManyMode::ConnectionChunked {
                self.confirmed_committed_parameter_sets
            } else {
                0
            },
            partial_commit_possible,
        }
    }

    pub(crate) fn abort_snapshot_with_wire(
        &self,
        wire: &ExecuteManyWireProgress,
    ) -> ExecuteManyAbortProgress {
        let confirmed = if self.mode == ExecuteManyMode::ConnectionChunked {
            self.confirmed_committed_parameter_sets
                .max(wire.confirmed_committed_parameter_sets())
        } else {
            0
        };
        let commit_unknown = self.commit_outcome_unknown || wire.commit_outcome_unknown();
        ExecuteManyAbortProgress {
            active_parameter_set_index: wire
                .active_parameter_set_index()
                .or(self.active_parameter_set_index),
            confirmed_committed_parameter_sets: confirmed,
            partial_commit_possible: self.mode == ExecuteManyMode::ConnectionChunked
                && (confirmed > 0 || commit_unknown),
        }
    }
}

#[derive(Clone)]
struct SharedExecuteManyDeadline {
    inner: Arc<StdMutex<Option<Deadline>>>,
}

impl SharedExecuteManyDeadline {
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
                PyRuntimeError::new_err("execute_many sequence has no operation deadline to expire")
            })
    }
}

#[derive(Clone, Copy)]
enum ExecuteManyAbortOutcome {
    Error,
    Cancelled,
}

impl ExecuteManyAbortOutcome {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "error" => Ok(Self::Error),
            "cancelled" => Ok(Self::Cancelled),
            _ => Err(PyValueError::new_err(
                "execute_many abort outcome must be 'error' or 'cancelled'",
            )),
        }
    }
}

struct ExecuteManySequence {
    mode: ExecuteManyMode,
    transaction: Transaction,
    sql: String,
    chunk_size: usize,
    progress: ExecuteManyProgress,
    wire_progress: ExecuteManyWireProgress,
    started_at: Instant,
    deadline: SharedExecuteManyDeadline,
    metrics: Option<Arc<OperationMetricsRegistry>>,
    observer: Option<OperationObserver>,
    retained_error: Option<PyErr>,
    database_reserved: bool,
    retire_after_operation: bool,
}

impl ExecuteManySequence {
    fn ensure_observer(&mut self) {
        if self.observer.is_none() {
            self.observer = Some(OperationObserver::start_at(
                self.metrics.clone(),
                OperationName::ExecuteMany,
                self.started_at,
            ));
        }
    }

    fn retain_error(&mut self, error: PyErr) -> PyErr {
        let returned = Python::attach(|py| error.clone_ref(py));
        self.retained_error = Some(error);
        returned
    }

    fn snapshot(&self) -> ExecuteManyAbortProgress {
        self.progress.abort_snapshot_with_wire(&self.wire_progress)
    }

    fn annotate_error(&self, error: PyErr, fallback_index: usize) -> PyErr {
        let snapshot = self.snapshot();
        let parameter_set_index = snapshot
            .active_parameter_set_index
            .unwrap_or(fallback_index);
        set_execute_many_operation(&error);
        attach_execute_many_error_metadata(
            error,
            parameter_set_index,
            snapshot.confirmed_committed_parameter_sets,
            snapshot.partial_commit_possible,
        )
    }

    fn error_parameter_set_index(error: &PyErr, fallback_index: usize) -> usize {
        Python::attach(|py| {
            error
                .value(py)
                .getattr("parameter_set_index")
                .and_then(|value| value.extract::<usize>())
                .unwrap_or(fallback_index)
        })
    }

    async fn reserve(&mut self) -> PyResult<()> {
        self.progress.reserve()?;
        if self.mode != ExecuteManyMode::CallerTransaction {
            return Ok(());
        }

        match run_until(
            self.deadline.get(),
            self.transaction.reserve_execute_many_producer(),
        )
        .await
        {
            Ok(Ok(lifetime_deadline)) => {
                self.deadline.merge(lifetime_deadline);
                self.database_reserved = true;
                Ok(())
            }
            Ok(Err(error)) => {
                self.progress.terminate();
                Err(self.annotate_error(error, 0))
            }
            Err(elapsed) => {
                self.progress.terminate();
                Err(self.annotate_error(execute_many_timeout_error(elapsed, false), 0))
            }
        }
    }

    async fn activate(&mut self) -> PyResult<()> {
        let already_active = self.progress.phase() == ExecuteManySequencePhase::Active;
        self.progress.activate()?;
        if already_active {
            return Ok(());
        }
        self.ensure_observer();

        let activation = if self.mode == ExecuteManyMode::CallerTransaction {
            run_until(
                self.deadline.get(),
                self.transaction.confirm_reserved_execute_many(),
            )
            .await
        } else {
            run_until(
                self.deadline.get(),
                self.transaction.activate_owned_execute_many_lifecycle(),
            )
            .await
        };

        match activation {
            Ok(Ok(())) => {
                self.database_reserved = true;
                Ok(())
            }
            Ok(Err(error)) => {
                self.progress.note_local_failure(0);
                let error = self.annotate_error(error, 0);
                let returned = self.retain_error(error);
                Err(returned)
            }
            Err(elapsed) => {
                let error = execute_many_timeout_error(elapsed, false);
                self.progress.note_local_failure(0);
                let error = self.annotate_error(error, 0);
                let returned = self.retain_error(error);
                Err(returned)
            }
        }
    }

    fn prepare_chunk(
        &mut self,
        parameter_sets: &Bound<'_, PyList>,
    ) -> PyResult<PreparedExecuteManyChunk> {
        let start_index = self.progress.prepare_push(parameter_sets.len())?;
        match prepare_execute_many_chunk(parameter_sets, start_index, self.chunk_size) {
            Ok(input) => Ok(input),
            Err(error) => {
                let parameter_set_index = Self::error_parameter_set_index(&error, start_index);
                self.progress.note_local_failure(parameter_set_index);
                let error = self.annotate_error(error, parameter_set_index);
                let returned = self.retain_error(error);
                Err(returned)
            }
        }
    }

    async fn push_prepared(&mut self, input: PreparedExecuteManyChunk) -> PyResult<u64> {
        let parameter_set_count = input.len();
        let chunk_start_index = input.start_index();
        match self
            .transaction
            .execute_reserved_execute_many_chunk(
                self.mode,
                self.deadline.get(),
                &self.sql,
                &input,
                &self.wire_progress,
            )
            .await
        {
            Ok(result) => {
                if let Err(error) = self.progress.complete_push(
                    parameter_set_count,
                    result.affected,
                    result.commit_acknowledged,
                ) {
                    self.progress.note_wire_failure(
                        chunk_start_index,
                        self.wire_progress.any_statement_sent(),
                        self.wire_progress.commit_outcome_unknown(),
                    );
                    let error = self.annotate_error(error, chunk_start_index);
                    let returned = self.retain_error(error);
                    return Err(returned);
                }
                Ok(result.affected)
            }
            Err(ReservedExecuteManyFailure {
                error,
                parameter_set_index,
                any_statement_sent,
            }) => {
                self.progress.note_wire_failure(
                    parameter_set_index,
                    any_statement_sent,
                    self.wire_progress.commit_outcome_unknown(),
                );
                let error = self.annotate_error(error, parameter_set_index);
                let returned = self.retain_error(error);
                Err(returned)
            }
        }
    }

    async fn finish(&mut self) -> PyResult<u64> {
        match self.progress.phase() {
            ExecuteManySequencePhase::Constructed => {
                return Err(PyRuntimeError::new_err(
                    "execute_many sequence must be reserved before finish",
                ));
            }
            ExecuteManySequencePhase::Terminal => {
                return Err(PyRuntimeError::new_err(
                    "execute_many sequence has already terminated",
                ));
            }
            ExecuteManySequencePhase::Reserved => {
                if self.mode == ExecuteManyMode::CallerTransaction
                    && let Err(error) = self
                        .transaction
                        .finish_reserved_execute_many(
                            self.mode,
                            self.deadline.get(),
                            &self.wire_progress,
                            self.retire_after_operation,
                        )
                        .await
                {
                    self.progress.terminate();
                    return Err(self.annotate_error(error, 0));
                }
                return self.progress.finish();
            }
            ExecuteManySequencePhase::Active => {}
            ExecuteManySequencePhase::Pushing | ExecuteManySequencePhase::Finishing => {
                return Err(PyRuntimeError::new_err(
                    "execute_many sequence cannot finish with work in flight",
                ));
            }
        }

        self.progress.begin_finish()?;
        let settlement = self
            .transaction
            .finish_reserved_execute_many(
                self.mode,
                self.deadline.get(),
                &self.wire_progress,
                self.retire_after_operation,
            )
            .await;
        match settlement {
            Ok(()) => {
                let total = self.progress.finish()?;
                self.observer
                    .take()
                    .expect("active execute_many sequence must own one observer")
                    .success();
                Ok(total)
            }
            Err(error) => {
                let fallback_index = self.wire_progress.active_parameter_set_index().unwrap_or(0);
                self.progress.note_wire_failure(
                    fallback_index,
                    self.wire_progress.any_statement_sent(),
                    self.wire_progress.commit_outcome_unknown(),
                );
                let error = self.annotate_error(error, fallback_index);
                let cleanup = self.cleanup_database().await;
                self.progress.terminate();
                self.observer
                    .take()
                    .expect("active execute_many sequence must own one observer")
                    .error(&error);
                if let Err(cleanup_error) = cleanup {
                    Python::attach(|py| error.set_cause(py, Some(cleanup_error)));
                }
                Err(error)
            }
        }
    }

    async fn cleanup_database(&self) -> PyResult<()> {
        if !self.database_reserved || !self.progress.requires_abandonment() {
            return Ok(());
        }
        self.transaction
            .abort_reserved_execute_many(
                self.mode,
                self.progress.any_statement_sent() || self.wire_progress.any_statement_sent(),
                self.retire_after_operation,
            )
            .await
    }

    fn finish_aborted_observer(&mut self, outcome: ExecuteManyAbortOutcome) {
        let Some(observer) = self.observer.take() else {
            return;
        };
        if let Some(error) = self.retained_error.take() {
            observer.error(&error);
            return;
        }
        match outcome {
            ExecuteManyAbortOutcome::Error => observer.error(&PyRuntimeError::new_err(
                "execute_many producer failed before completion",
            )),
            ExecuteManyAbortOutcome::Cancelled => observer.cancel(),
        }
    }

    async fn abort(
        &mut self,
        outcome: ExecuteManyAbortOutcome,
    ) -> PyResult<ExecuteManyAbortProgress> {
        if matches!(
            self.progress.phase(),
            ExecuteManySequencePhase::Constructed | ExecuteManySequencePhase::Terminal
        ) {
            return Ok(self.snapshot());
        }
        let snapshot = self.snapshot();
        let cleanup = self.cleanup_database().await;
        self.progress.terminate();
        self.finish_aborted_observer(outcome);
        cleanup.map(|_| snapshot)
    }

    async fn expire(&mut self) -> PyResult<()> {
        if self.progress.phase() == ExecuteManySequencePhase::Terminal {
            return Err(PyRuntimeError::new_err(
                "execute_many sequence has already terminated",
            ));
        }
        let elapsed = self.deadline.elapsed()?;
        let synchronized_security_retirement = self.retire_after_operation
            && (self.progress.any_statement_sent() || self.wire_progress.any_statement_sent());
        let timeout = self.annotate_error(
            // `expire()` is invoked only by the Python producer coordinator
            // between native calls. Any earlier TDS response is synchronized,
            // so terminal cleanup can roll back/release instead of claiming
            // retirement, except when the SQL security policy itself requires
            // that a used physical connection be discarded.
            execute_many_timeout_error(elapsed, synchronized_security_retirement),
            self.snapshot().active_parameter_set_index.unwrap_or(0),
        );
        let cleanup = self.cleanup_database().await;
        self.progress.terminate();
        self.retained_error.take();
        if let Some(observer) = self.observer.take() {
            observer.error(&timeout);
        }

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
        if self.database_reserved {
            self.transaction
                .abandon_reserved_execute_many(
                    self.mode,
                    self.progress.any_statement_sent() || self.wire_progress.any_statement_sent(),
                    self.retire_after_operation,
                )
                .await;
        }
        self.progress.terminate();
        self.retained_error.take();
        if let Some(observer) = self.observer.take() {
            observer.cancel();
        }
    }
}

fn execute_many_timeout_error(elapsed: DeadlineElapsed, connection_discarded: bool) -> PyErr {
    timeout_error_or_metadata_failure(
        elapsed,
        TimeoutErrorMetadata {
            operation: OperationName::ExecuteMany,
            retryable: false,
            connection_discarded,
            outcome_unknown: false,
        },
    )
}

fn progress_to_python(py: Python<'_>, progress: ExecuteManyAbortProgress) -> PyResult<Py<PyDict>> {
    let result = PyDict::new(py);
    result.set_item(
        "active_parameter_set_index",
        progress.active_parameter_set_index,
    )?;
    result.set_item(
        "confirmed_committed_parameter_sets",
        progress.confirmed_committed_parameter_sets,
    )?;
    result.set_item("partial_commit_possible", progress.partial_commit_possible)?;
    Ok(result.unbind())
}

fn validate_captured_list_length(current_len: usize, captured_len: usize) -> PyResult<()> {
    if current_len == captured_len {
        return Ok(());
    }
    Err(PyRuntimeError::new_err(
        "execute_many parameter_sets list was resized during execution",
    ))
}

#[pyclass(name = "_ExecuteManySequence")]
pub(crate) struct PyExecuteManySequence {
    inner: Arc<AsyncMutex<ExecuteManySequence>>,
    deadline: SharedExecuteManyDeadline,
}

impl PyExecuteManySequence {
    pub(crate) fn new(
        mode: ExecuteManyMode,
        transaction: Transaction,
        sql: String,
        chunk_size: usize,
        timeout_config: &PyTimeoutConfig,
        metrics: Option<Arc<OperationMetricsRegistry>>,
    ) -> PyResult<Self> {
        crate::execute_many::validate_execute_many_chunk_size(chunk_size)?;
        let started_at = Instant::now();
        let operation_deadline = timeout_config.operation_timeout.map(|timeout| Deadline {
            at: started_at + timeout,
            timeout,
            phase: TimeoutPhase::Operation,
        });
        let deadline = SharedExecuteManyDeadline::new(operation_deadline);
        let retire_after_operation = requires_connection_retirement(&sql);
        Ok(Self {
            inner: Arc::new(AsyncMutex::new(ExecuteManySequence {
                mode,
                transaction,
                sql,
                chunk_size,
                progress: ExecuteManyProgress::new(mode, chunk_size),
                wire_progress: ExecuteManyWireProgress::new(),
                started_at,
                deadline: deadline.clone(),
                metrics,
                observer: None,
                retained_error: None,
                database_reserved: false,
                retire_after_operation,
            })),
            deadline,
        })
    }

    fn concurrent_call_error() -> PyErr {
        PyRuntimeError::new_err("execute_many sequence already has one in-flight call")
    }

    async fn fail_with_cleanup(&self, error: PyErr) -> PyErr {
        let primary = Python::attach(|py| error.clone_ref(py));
        let cleanup = {
            let mut sequence = self.inner.lock().await;
            sequence.retained_error = Some(error);
            sequence.abort(ExecuteManyAbortOutcome::Error).await
        };
        if let Err(cleanup_error) = cleanup {
            Python::attach(|py| primary.set_cause(py, Some(cleanup_error)));
        }
        primary
    }

    pub(crate) async fn run_captured_list(
        &self,
        parameter_sets: Py<PyList>,
        captured_len: usize,
    ) -> PyResult<u64> {
        {
            let mut sequence = self.inner.lock().await;
            sequence.reserve().await?;
        }
        if captured_len == 0 {
            let mut sequence = self.inner.lock().await;
            return sequence.finish().await;
        }

        let mut start_index = 0usize;
        let mut activated = false;
        while start_index < captured_len {
            let chunk_size = {
                let sequence = self.inner.lock().await;
                sequence.chunk_size
            };
            let end_index = start_index
                .checked_add(chunk_size)
                .unwrap_or(captured_len)
                .min(captured_len);
            let prepared = Python::attach(|py| {
                let outer = parameter_sets.bind(py);
                validate_captured_list_length(outer.len(), captured_len)?;
                let chunk = outer.get_slice(start_index, end_index);
                prepare_execute_many_chunk(&chunk, start_index, end_index - start_index)
            });
            let prepared = match prepared {
                Ok(prepared) => prepared,
                Err(error) => {
                    let parameter_set_index =
                        ExecuteManySequence::error_parameter_set_index(&error, start_index);
                    let error = {
                        let sequence = self.inner.lock().await;
                        sequence.annotate_error(error, parameter_set_index)
                    };
                    return Err(self.fail_with_cleanup(error).await);
                }
            };

            let length_check = Python::attach(|py| {
                validate_captured_list_length(parameter_sets.bind(py).len(), captured_len)
            });
            if let Err(error) = length_check {
                let error = {
                    let sequence = self.inner.lock().await;
                    sequence.annotate_error(error, start_index)
                };
                return Err(self.fail_with_cleanup(error).await);
            }

            if !activated {
                let activation = {
                    let mut sequence = self.inner.lock().await;
                    sequence.activate().await
                };
                if let Err(error) = activation {
                    return Err(self.fail_with_cleanup(error).await);
                }
                activated = true;
            }

            let pushed = {
                let mut sequence = self.inner.lock().await;
                let prepared_start_index = prepared.start_index();
                match sequence.progress.prepare_push(prepared.len()) {
                    Ok(_) => sequence.push_prepared(prepared).await,
                    Err(error) => {
                        let error = sequence.annotate_error(error, prepared_start_index);
                        Err(sequence.retain_error(error))
                    }
                }
            };
            if let Err(error) = pushed {
                return Err(self.fail_with_cleanup(error).await);
            }
            start_index = end_index;
        }

        let length_check = Python::attach(|py| {
            validate_captured_list_length(parameter_sets.bind(py).len(), captured_len)
        });
        if let Err(error) = length_check {
            let error = {
                let sequence = self.inner.lock().await;
                sequence.annotate_error(error, start_index)
            };
            return Err(self.fail_with_cleanup(error).await);
        }

        let mut sequence = self.inner.lock().await;
        sequence.finish().await
    }
}

#[pymethods]
impl PyExecuteManySequence {
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

    pub fn push<'p>(
        &self,
        py: Python<'p>,
        parameter_sets: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let parameter_sets = parameter_sets.clone().unbind();
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner
                .try_lock()
                .map_err(|_| Self::concurrent_call_error())?;
            let input = Python::attach(|py| {
                let parameter_sets = parameter_sets.bind(py);
                sequence.prepare_chunk(parameter_sets)
            })?;
            sequence.push_prepared(input).await
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
        let outcome = ExecuteManyAbortOutcome::parse(outcome)?;
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let mut sequence = inner.lock().await;
            let progress = sequence.abort(outcome).await?;
            Python::attach(|py| progress_to_python(py, progress))
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

impl Drop for PyExecuteManySequence {
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
    use super::{
        ExecuteManyMode, ExecuteManyProgress, ExecuteManySequencePhase, execute_many_timeout_error,
        validate_captured_list_length,
    };
    use crate::deadline::{DeadlineElapsed, TimeoutPhase};
    use crate::execute_many::ExecuteManyWireProgress;
    use pyo3::Python;
    use pyo3::types::PyAnyMethods;
    use std::time::Duration;

    #[test]
    fn progress_preserves_global_indices_and_confirmed_commit_boundaries() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionChunked, 2);
        progress.reserve().unwrap();
        progress.activate().unwrap();

        assert_eq!(progress.prepare_push(2).unwrap(), 0);
        progress.complete_push(2, 3, true).unwrap();
        assert_eq!(progress.confirmed_committed_parameter_sets(), 2);

        assert_eq!(progress.prepare_push(1).unwrap(), 2);
        progress.complete_push(1, 4, true).unwrap();

        assert_eq!(progress.finish().unwrap(), 7);
        assert_eq!(progress.phase(), ExecuteManySequencePhase::Terminal);
        assert_eq!(progress.confirmed_committed_parameter_sets(), 3);
    }

    #[test]
    fn empty_reserved_sequence_finishes_without_activation() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionAtomic, 100);
        progress.reserve().unwrap();

        assert_eq!(progress.finish().unwrap(), 0);
        assert_eq!(progress.phase(), ExecuteManySequencePhase::Terminal);
    }

    #[test]
    fn captured_outer_list_resize_is_rejected_at_chunk_boundaries() {
        Python::initialize();
        assert!(validate_captured_list_length(4, 4).is_ok());
        let error = validate_captured_list_length(3, 4)
            .expect_err("shrinking the captured outer list must fail");
        assert!(error.to_string().contains("resized during execution"));
        assert!(
            validate_captured_list_length(5, 4).is_err(),
            "growing the captured outer list must fail",
        );
    }

    #[test]
    fn synchronized_producer_timeout_does_not_claim_connection_retirement() {
        Python::initialize();
        let error = execute_many_timeout_error(
            DeadlineElapsed {
                timeout: Duration::from_millis(250),
                phase: TimeoutPhase::Operation,
            },
            false,
        );

        Python::attach(|py| {
            let value = error.value(py);
            assert_eq!(
                value
                    .getattr("operation")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "execute_many",
            );
            assert!(
                !value
                    .getattr("connection_discarded")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap(),
            );
        });
    }

    #[test]
    fn atomic_and_caller_modes_never_report_confirmed_partial_commits() {
        Python::initialize();
        for mode in [
            ExecuteManyMode::ConnectionAtomic,
            ExecuteManyMode::CallerTransaction,
        ] {
            let mut progress = ExecuteManyProgress::new(mode, 2);
            progress.reserve().unwrap();
            progress.activate().unwrap();
            progress.prepare_push(1).unwrap();
            progress.complete_push(1, 1, false).unwrap();
            let snapshot = progress.abort_snapshot();
            assert_eq!(snapshot.confirmed_committed_parameter_sets, 0);
            assert!(!snapshot.partial_commit_possible);
        }
    }

    #[test]
    fn partial_mode_advances_only_after_commit_acknowledgement() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionChunked, 2);
        progress.reserve().unwrap();
        progress.activate().unwrap();
        progress.prepare_push(2).unwrap();

        let before_ack = progress.abort_snapshot();
        assert_eq!(before_ack.confirmed_committed_parameter_sets, 0);
        assert!(!before_ack.partial_commit_possible);

        progress.complete_push(2, 2, true).unwrap();
        let after_ack = progress.abort_snapshot();
        assert_eq!(after_ack.confirmed_committed_parameter_sets, 2);
        assert!(after_ack.partial_commit_possible);
    }

    #[test]
    fn in_flight_chunk_commit_is_unknown_until_acknowledged() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionChunked, 2);
        progress.reserve().unwrap();
        progress.activate().unwrap();
        let wire = ExecuteManyWireProgress::new();

        wire.begin_commit(0);
        let in_flight = progress.abort_snapshot_with_wire(&wire);
        assert_eq!(in_flight.active_parameter_set_index, Some(0));
        assert_eq!(in_flight.confirmed_committed_parameter_sets, 0);
        assert!(in_flight.partial_commit_possible);

        wire.mark_commit_known();
        let rejected = progress.abort_snapshot_with_wire(&wire);
        assert!(!rejected.partial_commit_possible);
    }

    #[test]
    fn sequence_rejects_unbounded_push_and_checked_index_overflow() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionAtomic, 2);
        progress.reserve().unwrap();
        progress.activate().unwrap();
        assert!(progress.prepare_push(0).is_err());
        assert!(progress.prepare_push(3).is_err());

        let mut overflow = ExecuteManyProgress::with_index_for_test(
            ExecuteManyMode::ConnectionAtomic,
            2,
            usize::MAX,
        );
        overflow.reserve().unwrap();
        overflow.activate().unwrap();
        overflow.prepare_push(1).unwrap();
        assert!(overflow.complete_push(1, 1, false).is_err());
    }

    #[test]
    fn producer_failure_keeps_a_partial_buffer_off_the_wire() {
        Python::initialize();
        let mut progress = ExecuteManyProgress::new(ExecuteManyMode::ConnectionAtomic, 3);
        progress.reserve().unwrap();
        progress.activate().unwrap();
        progress.note_local_failure(1);

        let snapshot = progress.abort_snapshot();
        assert_eq!(snapshot.active_parameter_set_index, Some(1));
        assert!(!progress.any_statement_sent());
    }
}
