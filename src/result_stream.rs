use crate::deadline::{DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until};
use crate::helpers::catch_driver_panic;
use crate::lifecycle::{ForceRequested, OperationPermit, run_force_aware};
use crate::operation_metrics::OperationObserver;
use crate::parameter_conversion::{FastParameter, params_as_sql_refs};
use crate::pool_manager::{
    PooledOperationGuard, TiberiusClient, python_error_allows_connection_reuse,
    timeout_error_or_metadata_failure,
};
use crate::procedure::{ProcedureCall, ProcedureResponseState, RawProcedureSummary};
use crate::result_types::{
    ColumnMetadataData, DoneResultData, OutputParameterData, PyColumnMetadata, PyResultSummary,
    ResultSummaryData, SqlMessageData,
};
use crate::transaction::TransactionResponseLease;
use crate::type_mapping::column_data_to_python;
use crate::types::{
    ColumnInfo, PyFastRow, ResultReceiveCancelled, TimeoutErrorMetadata, create_protocol_error,
    create_sql_error,
};
use futures_util::TryStreamExt;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyRuntimeError, PyStopAsyncIteration, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyTuple};
use pyo3_async_runtimes::tokio::future_into_py;
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Mutex as StdMutex, MutexGuard as StdMutexGuard};
use std::time::Duration;
use tiberius::{ResponseEvent, ResponseMetadata, ResponseStream, Row};
use tokio::sync::{Mutex, mpsc, watch};

const MAX_RESULT_SETS: usize = 1_024;
const MAX_DONE_RECORDS: usize = 4_096;
const MAX_INFO_MESSAGES: usize = 1_024;
const RECEIVE_WAITING: u8 = 0;
const RECEIVE_CONSUMED: u8 = 1;
const RECEIVE_CANCEL_REQUESTED: u8 = 2;

#[derive(Clone, Copy)]
pub(crate) struct BufferSize(usize);

impl BufferSize {
    pub(crate) const DEFAULT: Self = Self(64);

    pub(crate) const fn get(self) -> usize {
        self.0
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for BufferSize {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if object.is_instance_of::<PyBool>() {
            return Err(PyTypeError::new_err("buffer_size must be a plain integer"));
        }
        let value = object
            .extract::<usize>()
            .map_err(|_| PyTypeError::new_err("buffer_size must be a plain integer"))?;
        if !(1..=1_024).contains(&value) {
            return Err(PyValueError::new_err(
                "buffer_size must be between 1 and 1024",
            ));
        }
        Ok(Self(value))
    }
}

pub(crate) enum ResultRequest {
    Query {
        sql: String,
        parameters: Vec<FastParameter>,
    },
    Batch {
        sql: String,
    },
    Procedure(ProcedureCall),
}

struct ResultSetMetadata {
    index: usize,
    columns: Arc<Vec<ColumnMetadataData>>,
    column_info: Arc<ColumnInfo>,
}

enum ConversionEvent {
    Metadata(ResultSetMetadata),
    Row(Row),
    RawSummary(RawSummaryData),
}

struct RawSummaryData {
    summary: ResultSummaryData,
    procedure: Option<RawProcedureSummary>,
}

struct ResponseEventEnvelope {
    sequence: u64,
    event: ConversionEvent,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ConsumerAckKind {
    Converted,
    Discarded,
    ConversionFailed,
}

struct ConsumerAck {
    sequence: u64,
    kind: ConsumerAckKind,
    error: Option<Arc<PyErr>>,
}

#[derive(Clone)]
enum TerminalRelease {
    Pending,
    ReleasedSuccess,
    ReleasedFailure(Arc<PyErr>),
    ReleasedRetired,
}

enum ProducerFailure {
    Cancelled,
    Failed(PyErr),
}

enum ProducerCompletion {
    Success,
    Retired,
    Failed(PyErr),
    Cancelled,
}

struct ProducerChannels {
    events: mpsc::Sender<ResponseEventEnvelope>,
    acknowledgements: mpsc::Receiver<ConsumerAck>,
    cancellation: watch::Receiver<bool>,
    buffer_size: usize,
    next_sequence: u64,
    outstanding: HashSet<u64>,
}

impl ProducerChannels {
    fn cancelled(&self) -> bool {
        *self.cancellation.borrow()
    }

    fn accept_ack(&mut self, ack: ConsumerAck) -> Result<(), ProducerFailure> {
        if !self.outstanding.remove(&ack.sequence) {
            return Err(ProducerFailure::Failed(create_protocol_error(
                "result stream received an invalid consumer acknowledgement",
            )));
        }
        if ack.kind == ConsumerAckKind::ConversionFailed {
            let error = ack.error.map_or_else(
                || create_protocol_error("result stream consumer conversion failed"),
                |error| Python::attach(|py| error.clone_ref(py)),
            );
            return Err(ProducerFailure::Failed(error));
        }
        Ok(())
    }

    async fn receive_ack(&mut self) -> Result<(), ProducerFailure> {
        match self.acknowledgements.try_recv() {
            Ok(ack) => return self.accept_ack(ack),
            Err(mpsc::error::TryRecvError::Disconnected) => {
                return Err(ProducerFailure::Cancelled);
            }
            Err(mpsc::error::TryRecvError::Empty) => {}
        }
        if self.cancelled() {
            return Err(ProducerFailure::Cancelled);
        }
        let ack = tokio::select! {
            biased;
            ack = self.acknowledgements.recv() => {
                ack.ok_or(ProducerFailure::Cancelled)?
            }
            changed = self.cancellation.changed() => {
                if changed.is_err() || self.cancelled() {
                    return Err(ProducerFailure::Cancelled);
                }
                return Err(ProducerFailure::Cancelled);
            }
            _ = self.events.closed() => {
                return Err(ProducerFailure::Cancelled);
            }
        };
        self.accept_ack(ack)
    }

    async fn wait_for_credit(&mut self) -> Result<(), ProducerFailure> {
        while self.outstanding.len() >= self.buffer_size {
            self.receive_ack().await?;
        }
        Ok(())
    }

    async fn send(&mut self, event: ConversionEvent) -> Result<(), ProducerFailure> {
        self.wait_for_credit().await?;
        let sequence = self.next_sequence;
        self.next_sequence = self.next_sequence.checked_add(1).ok_or_else(|| {
            ProducerFailure::Failed(create_protocol_error(
                "result stream event sequence overflow",
            ))
        })?;
        let envelope = ResponseEventEnvelope { sequence, event };
        tokio::select! {
            biased;
            changed = self.cancellation.changed() => {
                if changed.is_err() || self.cancelled() {
                    return Err(ProducerFailure::Cancelled);
                }
                return Err(ProducerFailure::Cancelled);
            }
            _ = self.events.closed() => {
                return Err(ProducerFailure::Cancelled);
            }
            sent = self.events.send(envelope) => {
                sent.map_err(|_| ProducerFailure::Cancelled)?;
            }
        }
        self.outstanding.insert(sequence);
        Ok(())
    }

    async fn wait_for_all_acks(&mut self) -> Result<(), ProducerFailure> {
        while !self.outstanding.is_empty() {
            self.receive_ack().await?;
        }
        if self.cancelled() {
            Err(ProducerFailure::Cancelled)
        } else {
            Ok(())
        }
    }
}

fn metadata_event(metadata: ResponseMetadata) -> ResultSetMetadata {
    let column_info = ColumnInfo::from_response_columns(metadata.columns());
    let columns = metadata
        .columns()
        .iter()
        .enumerate()
        .map(|(ordinal, column)| ColumnMetadataData::from_response(ordinal, column))
        .collect();
    ResultSetMetadata {
        index: metadata.result_index(),
        columns: Arc::new(columns),
        column_info,
    }
}

async fn drain_response(
    mut response: ResponseStream<'_>,
    channels: &mut ProducerChannels,
    mut procedure: Option<ProcedureResponseState>,
) -> Result<(), ProducerFailure> {
    let mut summary = ResultSummaryData::default();

    loop {
        let event = tokio::select! {
            biased;
            changed = channels.cancellation.changed() => {
                if changed.is_err() || channels.cancelled() {
                    return Err(ProducerFailure::Cancelled);
                }
                return Err(ProducerFailure::Cancelled);
            }
            _ = channels.events.closed() => {
                return Err(ProducerFailure::Cancelled);
            }
            event = response.try_next() => event,
        };

        let event = match event {
            Ok(Some(event)) => event,
            Ok(None) => break,
            Err(error) => {
                channels.wait_for_all_acks().await?;
                return Err(ProducerFailure::Failed(create_sql_error(
                    error,
                    "Result stream execution failed",
                )));
            }
        };

        match event {
            ResponseEvent::Metadata(metadata) => {
                summary.result_set_count =
                    summary.result_set_count.checked_add(1).ok_or_else(|| {
                        ProducerFailure::Failed(create_protocol_error("response_limit_exceeded"))
                    })?;
                if summary.result_set_count > MAX_RESULT_SETS {
                    return Err(ProducerFailure::Failed(create_protocol_error(
                        "response_limit_exceeded",
                    )));
                }
                channels
                    .send(ConversionEvent::Metadata(metadata_event(metadata)))
                    .await?;
            }
            ResponseEvent::Row(row) => {
                channels.send(ConversionEvent::Row(row)).await?;
            }
            ResponseEvent::Done(done) => {
                if summary.done.len() >= MAX_DONE_RECORDS {
                    return Err(ProducerFailure::Failed(create_protocol_error(
                        "response_limit_exceeded",
                    )));
                }
                summary.done.push(DoneResultData::from(done));
            }
            ResponseEvent::Info(info) => {
                if summary.messages.len() >= MAX_INFO_MESSAGES {
                    return Err(ProducerFailure::Failed(create_protocol_error(
                        "response_limit_exceeded",
                    )));
                }
                summary.messages.push(SqlMessageData::from(info));
            }
            ResponseEvent::ReturnStatus(status) => {
                if procedure.is_some() && summary.return_status.is_some() {
                    return Err(ProducerFailure::Failed(create_protocol_error(
                        "stored procedure returned duplicate status tokens",
                    )));
                }
                summary.return_status = Some(status);
            }
            ResponseEvent::ReturnValue(value) => {
                if let Some(procedure) = procedure.as_mut() {
                    procedure
                        .record_return_value(value)
                        .map_err(ProducerFailure::Failed)?;
                }
            }
        }
    }

    let procedure = procedure
        .map(ProcedureResponseState::finish)
        .transpose()
        .map_err(ProducerFailure::Failed)?;
    channels
        .send(ConversionEvent::RawSummary(RawSummaryData {
            summary,
            procedure,
        }))
        .await?;
    channels.wait_for_all_acks().await
}

async fn execute_request(
    client: &mut TiberiusClient,
    request: ResultRequest,
    channels: &mut ProducerChannels,
) -> Result<(), ProducerFailure> {
    match request {
        ResultRequest::Query { sql, parameters } => {
            let parameters = params_as_sql_refs(&parameters);
            let response = client
                .response_query(sql, &parameters)
                .await
                .map_err(|error| {
                    ProducerFailure::Failed(create_sql_error(error, "Result stream startup failed"))
                })?;
            drain_response(response, channels, None).await
        }
        ResultRequest::Batch { sql } => {
            let response = client.response_batch(sql).await.map_err(|error| {
                ProducerFailure::Failed(create_sql_error(error, "Result batch startup failed"))
            })?;
            drain_response(response, channels, None).await
        }
        ResultRequest::Procedure(call) => {
            let (procedure, arguments, output_slots, return_slot) = call.into_parts();
            let response = client
                .response_rpc(procedure, arguments)
                .await
                .map_err(|error| {
                    ProducerFailure::Failed(create_sql_error(
                        error,
                        "Stored procedure startup failed",
                    ))
                })?;
            drain_response(
                response,
                channels,
                Some(ProcedureResponseState::new(output_slots, return_slot)),
            )
            .await
        }
    }
}

fn operation_timeout_error(elapsed: DeadlineElapsed, operation: OperationName) -> PyErr {
    timeout_error_or_metadata_failure(
        elapsed,
        TimeoutErrorMetadata {
            operation,
            retryable: false,
            connection_discarded: true,
            outcome_unknown: true,
        },
    )
}

async fn producer_body(
    mut guard: PooledOperationGuard<'static>,
    request: ResultRequest,
    operation: OperationName,
    operation_timeout: Option<Duration>,
    retire_after_operation: bool,
    mut channels: ProducerChannels,
) -> ProducerCompletion {
    let deadline = deadline_from(TimeoutPhase::Operation, operation_timeout);
    let result = run_until(
        deadline,
        execute_request(&mut guard, request, &mut channels),
    )
    .await;

    let completion = match result {
        Err(elapsed) => ProducerCompletion::Failed(operation_timeout_error(elapsed, operation)),
        Ok(Ok(())) => {
            guard.complete_success(retire_after_operation);
            if retire_after_operation {
                ProducerCompletion::Retired
            } else {
                ProducerCompletion::Success
            }
        }
        Ok(Err(ProducerFailure::Cancelled)) => ProducerCompletion::Cancelled,
        Ok(Err(ProducerFailure::Failed(error))) => {
            let connection_discarded =
                retire_after_operation || !python_error_allows_connection_reuse(&error);
            let error = stream_error_with_metadata(error, operation, connection_discarded);
            guard.complete_error(&error, retire_after_operation);
            ProducerCompletion::Failed(error)
        }
    };

    drop(channels);
    drop(guard);
    completion
}

async fn transaction_producer_body(
    mut lease: TransactionResponseLease,
    request: ResultRequest,
    operation: OperationName,
    mut channels: ProducerChannels,
) -> ProducerCompletion {
    let deadline = lease.deadline();
    let force_receiver = lease.force_receiver();
    let operation_result = async {
        let client = match lease.client_mut() {
            Ok(client) => client,
            Err(error) => return Ok(Err(ProducerFailure::Failed(error))),
        };
        run_until(deadline, execute_request(client, request, &mut channels)).await
    };
    let result = match force_receiver {
        Some(receiver) => run_force_aware(receiver, operation_result).await,
        None => Ok(operation_result.await),
    };

    let completion = match result {
        Err(ForceRequested) => ProducerCompletion::Failed(lease.fail_forced()),
        Ok(Err(elapsed)) => {
            lease.fail();
            ProducerCompletion::Failed(operation_timeout_error(elapsed, operation))
        }
        Ok(Ok(Ok(()))) => {
            if lease.complete_success() {
                ProducerCompletion::Retired
            } else {
                ProducerCompletion::Success
            }
        }
        Ok(Ok(Err(ProducerFailure::Cancelled))) => {
            lease.fail();
            ProducerCompletion::Cancelled
        }
        Ok(Ok(Err(ProducerFailure::Failed(error)))) => {
            let connection_discarded = lease.complete_error(&error);
            let error = stream_error_with_metadata(error, operation, connection_discarded);
            ProducerCompletion::Failed(error)
        }
    };

    drop(channels);
    drop(lease);
    completion
}

struct ProducerSupervisor {
    permit: OperationPermit,
    observer: OperationObserver,
    guard: PooledOperationGuard<'static>,
    request: ResultRequest,
    operation: OperationName,
    operation_timeout: Option<Duration>,
    retire_after_operation: bool,
    channels: ProducerChannels,
    terminal: watch::Sender<TerminalRelease>,
}

struct TransactionProducerSupervisor {
    observer: OperationObserver,
    lease: TransactionResponseLease,
    request: ResultRequest,
    operation: OperationName,
    channels: ProducerChannels,
    terminal: watch::Sender<TerminalRelease>,
}

fn publish_completion(
    observer: OperationObserver,
    terminal: watch::Sender<TerminalRelease>,
    completion: ProducerCompletion,
) {
    let terminal_state = match completion {
        ProducerCompletion::Success => {
            observer.success();
            TerminalRelease::ReleasedSuccess
        }
        ProducerCompletion::Retired => {
            observer.success();
            TerminalRelease::ReleasedRetired
        }
        ProducerCompletion::Failed(error) => {
            observer.error(&error);
            TerminalRelease::ReleasedFailure(Arc::new(error))
        }
        ProducerCompletion::Cancelled => {
            observer.cancel();
            TerminalRelease::ReleasedRetired
        }
    };
    terminal.send_replace(terminal_state);
}

fn panic_completion(error: PyErr, operation: OperationName) -> ProducerCompletion {
    ProducerCompletion::Failed(stream_error_with_metadata(error, operation, true))
}

async fn supervise_producer(supervisor: ProducerSupervisor) {
    let ProducerSupervisor {
        permit,
        observer,
        guard,
        request,
        operation,
        operation_timeout,
        retire_after_operation,
        channels,
        terminal,
    } = supervisor;
    let completion = match catch_driver_panic(permit.run(async move {
        Ok(producer_body(
            guard,
            request,
            operation,
            operation_timeout,
            retire_after_operation,
            channels,
        )
        .await)
    }))
    .await
    {
        Ok(Ok(completion)) => completion,
        Ok(Err(error)) => ProducerCompletion::Failed(error),
        Err(error) => panic_completion(error, operation),
    };
    publish_completion(observer, terminal, completion);
}

async fn supervise_transaction_producer(supervisor: TransactionProducerSupervisor) {
    let TransactionProducerSupervisor {
        observer,
        lease,
        request,
        operation,
        channels,
        terminal,
    } = supervisor;
    let completion = match catch_driver_panic(transaction_producer_body(
        lease, request, operation, channels,
    ))
    .await
    {
        Ok(completion) => completion,
        Err(error) => panic_completion(error, operation),
    };
    publish_completion(observer, terminal, completion);
}

struct ActiveSet {
    index: usize,
    closed: Arc<AtomicBool>,
}

struct ConsumerState {
    receiver: mpsc::Receiver<ResponseEventEnvelope>,
    pending_metadata: Option<ResponseEventEnvelope>,
    active_set: Option<ActiveSet>,
}

#[derive(Default)]
struct PublishedState {
    summary: Option<Arc<ResultSummaryData>>,
    failure: Option<Arc<PyErr>>,
}

struct SharedConsumer {
    state: Mutex<ConsumerState>,
    acknowledgements: mpsc::Sender<ConsumerAck>,
    cancellation: watch::Sender<bool>,
    terminal: watch::Receiver<TerminalRelease>,
    operation: OperationName,
    busy: AtomicBool,
    closed: AtomicBool,
    complete: AtomicBool,
    aborted: AtomicBool,
    published: StdMutex<PublishedState>,
}

impl SharedConsumer {
    fn published(&self) -> StdMutexGuard<'_, PublishedState> {
        self.published
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn clone_failure(&self) -> Option<PyErr> {
        self.published()
            .failure
            .as_ref()
            .map(|error| Python::attach(|py| error.clone_ref(py)))
    }

    fn record_failure(&self, error: PyErr) -> Arc<PyErr> {
        let mut published = self.published();
        if let Some(existing) = published.failure.as_ref() {
            return Arc::clone(existing);
        }
        let error = Arc::new(error);
        published.failure = Some(Arc::clone(&error));
        error
    }

    fn publish_summary(&self, summary: ResultSummaryData) {
        let mut published = self.published();
        if published.summary.is_none() {
            published.summary = Some(Arc::new(summary));
        }
    }

    fn summary(&self) -> Option<Arc<ResultSummaryData>> {
        self.published().summary.as_ref().map(Arc::clone)
    }

    fn cancel(&self) {
        self.aborted.store(true, Ordering::Release);
        let _ = self.cancellation.send(true);
    }

    fn acknowledge(
        &self,
        sequence: u64,
        kind: ConsumerAckKind,
        error: Option<Arc<PyErr>>,
    ) -> PyResult<()> {
        let acknowledgement = ConsumerAck {
            sequence,
            kind,
            error,
        };
        match self.acknowledgements.try_send(acknowledgement) {
            Ok(()) | Err(mpsc::error::TrySendError::Closed(_)) => Ok(()),
            Err(mpsc::error::TrySendError::Full(_)) => {
                let error = create_protocol_error(
                    "result stream acknowledgement capacity invariant failed",
                );
                self.record_failure(Python::attach(|py| error.clone_ref(py)));
                self.cancel();
                Err(error)
            }
        }
    }
}

struct ConsumerPermit {
    shared: Arc<SharedConsumer>,
}

impl ConsumerPermit {
    fn acquire(shared: &Arc<SharedConsumer>) -> PyResult<Self> {
        shared
            .busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| PyRuntimeError::new_err("result stream already has an active consumer"))?;
        Ok(Self {
            shared: Arc::clone(shared),
        })
    }
}

impl Drop for ConsumerPermit {
    fn drop(&mut self) {
        self.shared.busy.store(false, Ordering::Release);
    }
}

struct ReceiveCancellation {
    shared: Arc<SharedConsumer>,
    phase: AtomicU8,
    sender: watch::Sender<bool>,
}

impl ReceiveCancellation {
    fn new(shared: Arc<SharedConsumer>) -> (Arc<Self>, watch::Receiver<bool>) {
        let (sender, receiver) = watch::channel(false);
        (
            Arc::new(Self {
                shared,
                phase: AtomicU8::new(RECEIVE_WAITING),
                sender,
            }),
            receiver,
        )
    }

    fn request(&self) {
        let previous = self.phase.swap(RECEIVE_CANCEL_REQUESTED, Ordering::AcqRel);
        if previous == RECEIVE_CONSUMED {
            self.shared.cancel();
        }
        let _ = self.sender.send(true);
    }

    fn mark_consumed(&self) -> bool {
        self.phase
            .compare_exchange(
                RECEIVE_WAITING,
                RECEIVE_CONSUMED,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_ok()
    }

    fn requested(&self) -> bool {
        self.phase.load(Ordering::Acquire) == RECEIVE_CANCEL_REQUESTED
    }
}

#[pyclass]
struct PyReceiveCancellation {
    control: Arc<ReceiveCancellation>,
    armed: AtomicBool,
}

#[pymethods]
impl PyReceiveCancellation {
    fn cancel(&self) {
        self.control.request();
    }

    fn disarm(&self) {
        self.armed.store(false, Ordering::Release);
    }
}

impl Drop for PyReceiveCancellation {
    fn drop(&mut self) {
        if self.armed.swap(false, Ordering::AcqRel) {
            self.control.request();
        }
    }
}

fn wrap_receive_awaitable<'py>(
    py: Python<'py>,
    awaitable: Bound<'py, PyAny>,
    control: Arc<ReceiveCancellation>,
) -> PyResult<Bound<'py, PyAny>> {
    let cancellation = Py::new(
        py,
        PyReceiveCancellation {
            control: Arc::clone(&control),
            armed: AtomicBool::new(true),
        },
    )?;
    let package = py.import("fastmssql")?;
    let helper = package.getattr("_await_result_stream_receive")?;
    let observer = package.getattr("_observe_result_stream_receive")?;
    if let Err(error) = awaitable.call_method1("add_done_callback", (observer,)) {
        control.request();
        return Err(error);
    }
    match helper.call1((awaitable, cancellation)) {
        Ok(wrapped) => Ok(wrapped),
        Err(error) => {
            control.request();
            Err(error)
        }
    }
}

fn receive_cancelled_error() -> PyErr {
    ResultReceiveCancelled::new_err("result stream receive was cancelled")
}

async fn wait_for_receive_cancellation(receiver: &mut watch::Receiver<bool>) {
    loop {
        if *receiver.borrow_and_update() {
            return;
        }
        if receiver.changed().await.is_err() {
            std::future::pending::<()>().await;
        }
    }
}

async fn lock_consumer_state<'a>(
    shared: &'a Arc<SharedConsumer>,
    cancellation: &mut watch::Receiver<bool>,
) -> PyResult<tokio::sync::MutexGuard<'a, ConsumerState>> {
    tokio::select! {
        biased;
        _ = wait_for_receive_cancellation(cancellation) => Err(receive_cancelled_error()),
        state = shared.state.lock() => Ok(state),
    }
}

fn local_closed_error() -> PyErr {
    PyRuntimeError::new_err("result stream was closed before normal completion")
}

async fn receive_envelope(state: &mut ConsumerState) -> Option<ResponseEventEnvelope> {
    match state.pending_metadata.take() {
        Some(envelope) => Some(envelope),
        None => state.receiver.recv().await,
    }
}

async fn receive_envelope_or_cancel(
    state: &mut ConsumerState,
    control: &Arc<ReceiveCancellation>,
    cancellation: &mut watch::Receiver<bool>,
) -> PyResult<Option<ResponseEventEnvelope>> {
    let envelope = tokio::select! {
        biased;
        _ = wait_for_receive_cancellation(cancellation) => {
            return Err(receive_cancelled_error());
        }
        envelope = receive_envelope(state) => envelope,
    };
    if let Some(envelope) = envelope {
        if control.mark_consumed() {
            Ok(Some(envelope))
        } else {
            state.pending_metadata = Some(envelope);
            Err(receive_cancelled_error())
        }
    } else {
        Ok(None)
    }
}

fn close_active_set(state: &mut ConsumerState, index: usize) {
    if let Some(active) = state.active_set.take() {
        active.closed.store(true, Ordering::Release);
        debug_assert_eq!(active.index, index);
    }
}

async fn await_terminal(shared: &Arc<SharedConsumer>, allow_aborted: bool) -> PyResult<()> {
    let mut terminal = shared.terminal.clone();
    loop {
        let current = terminal.borrow().clone();
        match current {
            TerminalRelease::Pending => {}
            TerminalRelease::ReleasedSuccess | TerminalRelease::ReleasedRetired => {
                shared.closed.store(true, Ordering::Release);
                if shared.aborted.load(Ordering::Acquire) {
                    if allow_aborted {
                        return Ok(());
                    }
                    return Err(local_closed_error());
                }
                if shared.summary().is_none() {
                    return Err(create_protocol_error(
                        "result stream released without a terminal summary",
                    ));
                }
                shared.complete.store(true, Ordering::Release);
                return Ok(());
            }
            TerminalRelease::ReleasedFailure(error) => {
                let cloned = Python::attach(|py| error.clone_ref(py));
                let stored = shared.record_failure(Python::attach(|py| cloned.clone_ref(py)));
                shared.closed.store(true, Ordering::Release);
                return Err(Python::attach(|py| stored.clone_ref(py)));
            }
        }

        terminal.changed().await.map_err(|_| {
            create_protocol_error("result stream terminal supervisor ended unexpectedly")
        })?;
    }
}

async fn await_terminal_or_cancel(
    shared: &Arc<SharedConsumer>,
    allow_aborted: bool,
    cancellation: &mut watch::Receiver<bool>,
) -> PyResult<()> {
    tokio::select! {
        biased;
        _ = wait_for_receive_cancellation(cancellation) => Err(receive_cancelled_error()),
        result = await_terminal(shared, allow_aborted) => result,
    }
}

fn ensure_usable(shared: &Arc<SharedConsumer>) -> PyResult<()> {
    if let Some(error) = shared.clone_failure() {
        return Err(error);
    }
    if shared.aborted.load(Ordering::Acquire) {
        return Err(local_closed_error());
    }
    Ok(())
}

fn summary_object(shared: &Arc<SharedConsumer>) -> PyResult<Py<PyResultSummary>> {
    if !shared.complete.load(Ordering::Acquire) {
        return Err(PyRuntimeError::new_err(
            "result summary is unavailable before normal completion",
        ));
    }
    let summary = shared.summary().ok_or_else(|| {
        create_protocol_error("result stream completed without a terminal summary")
    })?;
    Python::attach(|py| Py::new(py, PyResultSummary::from_data(summary)))
}

fn attach_stream_error_metadata(
    error: &PyErr,
    operation: OperationName,
    connection_discarded: bool,
) -> PyResult<()> {
    Python::attach(|py| {
        let value = error.value(py);
        value.setattr("operation", operation.as_str())?;
        value.setattr("phase", TimeoutPhase::Operation.as_str())?;
        value.setattr("retryable", false)?;
        value.setattr("wire_sent", true)?;
        value.setattr("connection_discarded", connection_discarded)?;
        value.setattr("outcome_unknown", false)?;
        Ok(())
    })
}

fn stream_error_with_metadata(
    error: PyErr,
    operation: OperationName,
    connection_discarded: bool,
) -> PyErr {
    match attach_stream_error_metadata(&error, operation, connection_discarded) {
        Ok(()) => error,
        Err(metadata_failure) => {
            Python::attach(|py| {
                metadata_failure.set_cause(py, Some(error));
            });
            metadata_failure
        }
    }
}

fn conversion_failed(shared: &Arc<SharedConsumer>, sequence: u64, error: PyErr) -> PyErr {
    let error = stream_error_with_metadata(error, shared.operation, true);
    let stored = shared.record_failure(Python::attach(|py| error.clone_ref(py)));
    let _ = shared.acknowledge(sequence, ConsumerAckKind::ConversionFailed, Some(stored));
    shared.cancel();
    error
}

fn convert_raw_summary(mut raw: RawSummaryData, py: Python<'_>) -> PyResult<ResultSummaryData> {
    let Some(procedure) = raw.procedure else {
        return Ok(raw.summary);
    };

    raw.summary.output_parameters = procedure
        .output_values
        .into_iter()
        .map(|output| {
            column_data_to_python(&output.value, output.column_type, py).map(|value| {
                OutputParameterData {
                    key: output.key,
                    value,
                }
            })
        })
        .collect::<PyResult<Vec<_>>>()?;

    if let Some(key) = procedure.return_key {
        let status = raw.summary.return_status.ok_or_else(|| {
            create_protocol_error("stored procedure response omitted its return status")
        })?;
        raw.summary.output_parameters.push(OutputParameterData {
            key,
            value: status.into_py_any(py)?,
        });
    }

    Ok(raw.summary)
}

fn publish_raw_summary(
    shared: &Arc<SharedConsumer>,
    sequence: u64,
    raw: RawSummaryData,
) -> PyResult<()> {
    let summary = match Python::attach(|py| convert_raw_summary(raw, py)) {
        Ok(summary) => summary,
        Err(error) => return Err(conversion_failed(shared, sequence, error)),
    };
    shared.publish_summary(summary);
    shared.acknowledge(sequence, ConsumerAckKind::Converted, None)
}

async fn outer_next(
    shared: Arc<SharedConsumer>,
    control: Arc<ReceiveCancellation>,
    mut cancellation: watch::Receiver<bool>,
) -> PyResult<Py<PyResultSet>> {
    let _permit = ConsumerPermit::acquire(&shared)?;
    if control.requested() {
        return Err(receive_cancelled_error());
    }
    ensure_usable(&shared)?;
    if shared.complete.load(Ordering::Acquire) {
        return Err(PyStopAsyncIteration::new_err(""));
    }

    let mut state = lock_consumer_state(&shared, &mut cancellation).await?;
    if let Some(active) = state.active_set.as_ref() {
        if !active.closed.load(Ordering::Acquire) {
            return Err(PyRuntimeError::new_err(
                "the active result set must be consumed or closed",
            ));
        }
        state.active_set = None;
    }
    if shared.summary().is_some() {
        drop(state);
        await_terminal_or_cancel(&shared, false, &mut cancellation).await?;
        return Err(PyStopAsyncIteration::new_err(""));
    }

    let envelope = match receive_envelope_or_cancel(&mut state, &control, &mut cancellation).await?
    {
        Some(envelope) => envelope,
        None => {
            drop(state);
            await_terminal_or_cancel(&shared, false, &mut cancellation).await?;
            return Err(PyStopAsyncIteration::new_err(""));
        }
    };
    let ResponseEventEnvelope { sequence, event } = envelope;
    match event {
        ConversionEvent::Metadata(metadata) => {
            let closed = Arc::new(AtomicBool::new(false));
            let result_set = PyResultSet {
                shared: Arc::clone(&shared),
                index: metadata.index,
                columns: metadata.columns,
                column_info: metadata.column_info,
                closed: Arc::clone(&closed),
            };
            let result_set = match Python::attach(|py| Py::new(py, result_set)) {
                Ok(result_set) => result_set,
                Err(error) => {
                    return Err(conversion_failed(&shared, sequence, error));
                }
            };
            state.active_set = Some(ActiveSet {
                index: metadata.index,
                closed,
            });
            if let Err(error) = shared.acknowledge(sequence, ConsumerAckKind::Converted, None) {
                close_active_set(&mut state, metadata.index);
                return Err(error);
            }
            Ok(result_set)
        }
        ConversionEvent::Row(_) => {
            let error =
                create_protocol_error("result stream received a row outside an active result set");
            Err(conversion_failed(&shared, sequence, error))
        }
        ConversionEvent::RawSummary(summary) => {
            publish_raw_summary(&shared, sequence, summary)?;
            drop(state);
            await_terminal(&shared, false).await?;
            Err(PyStopAsyncIteration::new_err(""))
        }
    }
}

async fn result_set_next(
    shared: Arc<SharedConsumer>,
    index: usize,
    column_info: Arc<ColumnInfo>,
    closed: Arc<AtomicBool>,
    control: Arc<ReceiveCancellation>,
    mut cancellation: watch::Receiver<bool>,
) -> PyResult<Py<PyFastRow>> {
    if closed.load(Ordering::Acquire) {
        return Err(PyStopAsyncIteration::new_err(""));
    }
    let _permit = ConsumerPermit::acquire(&shared)?;
    if control.requested() {
        return Err(receive_cancelled_error());
    }
    ensure_usable(&shared)?;
    let mut state = lock_consumer_state(&shared, &mut cancellation).await?;
    if state.active_set.as_ref().map(|active| active.index) != Some(index) {
        closed.store(true, Ordering::Release);
        return Err(PyStopAsyncIteration::new_err(""));
    }

    let envelope = match receive_envelope_or_cancel(&mut state, &control, &mut cancellation).await?
    {
        Some(envelope) => envelope,
        None => {
            close_active_set(&mut state, index);
            closed.store(true, Ordering::Release);
            drop(state);
            await_terminal_or_cancel(&shared, false, &mut cancellation).await?;
            return Err(PyStopAsyncIteration::new_err(""));
        }
    };
    let ResponseEventEnvelope { sequence, event } = envelope;
    match event {
        ConversionEvent::Row(row) => {
            if row.result_index() != index {
                let error =
                    create_protocol_error("result stream row index did not match active metadata");
                return Err(conversion_failed(&shared, sequence, error));
            }
            let converted = Python::attach(|py| {
                PyFastRow::from_tiberius_row(row, py, Arc::clone(&column_info))
                    .and_then(|row| Py::new(py, row))
            });
            let converted = match converted {
                Ok(converted) => converted,
                Err(error) => {
                    return Err(conversion_failed(&shared, sequence, error));
                }
            };
            shared.acknowledge(sequence, ConsumerAckKind::Converted, None)?;
            Ok(converted)
        }
        event @ ConversionEvent::Metadata(_) => {
            state.pending_metadata = Some(ResponseEventEnvelope { sequence, event });
            close_active_set(&mut state, index);
            closed.store(true, Ordering::Release);
            Err(PyStopAsyncIteration::new_err(""))
        }
        ConversionEvent::RawSummary(summary) => {
            publish_raw_summary(&shared, sequence, summary)?;
            close_active_set(&mut state, index);
            closed.store(true, Ordering::Release);
            drop(state);
            await_terminal_or_cancel(&shared, false, &mut cancellation).await?;
            Err(PyStopAsyncIteration::new_err(""))
        }
    }
}

#[cfg(test)]
mod producer_channel_tests {
    use super::*;

    #[test]
    fn supervised_protocol_panics_are_fail_closed_metadata() {
        Python::initialize();
        let completion = panic_completion(
            create_protocol_error("simulated result decoder panic"),
            OperationName::Query,
        );
        let ProducerCompletion::Failed(error) = completion else {
            panic!("driver panic must become a terminal stream failure");
        };

        Python::attach(|py| {
            let value = error.value(py);
            assert_eq!(
                value
                    .getattr("operation")
                    .and_then(|item| item.extract::<String>())
                    .expect("operation metadata"),
                "query",
            );
            assert_eq!(
                value
                    .getattr("phase")
                    .and_then(|item| item.extract::<String>())
                    .expect("phase metadata"),
                "operation",
            );
            assert!(
                !value
                    .getattr("retryable")
                    .and_then(|item| item.extract::<bool>())
                    .expect("retryable metadata")
            );
            assert!(
                value
                    .getattr("wire_sent")
                    .and_then(|item| item.extract::<bool>())
                    .expect("wire metadata")
            );
            assert!(
                value
                    .getattr("connection_discarded")
                    .and_then(|item| item.extract::<bool>())
                    .expect("discard metadata")
            );
            assert!(
                !value
                    .getattr("outcome_unknown")
                    .and_then(|item| item.extract::<bool>())
                    .expect("outcome metadata")
            );
        });
    }

    #[tokio::test]
    async fn queued_conversion_failure_wins_over_later_cancellation() {
        Python::initialize();
        let (event_sender, _event_receiver) = mpsc::channel::<ResponseEventEnvelope>(1);
        let (ack_sender, ack_receiver) = mpsc::channel::<ConsumerAck>(1);
        let (cancel_sender, cancel_receiver) = watch::channel(false);
        let mut outstanding = HashSet::new();
        outstanding.insert(7);
        let mut channels = ProducerChannels {
            events: event_sender,
            acknowledgements: ack_receiver,
            cancellation: cancel_receiver,
            buffer_size: 1,
            next_sequence: 8,
            outstanding,
        };

        ack_sender
            .send(ConsumerAck {
                sequence: 7,
                kind: ConsumerAckKind::ConversionFailed,
                error: None,
            })
            .await
            .expect("ack receiver remains live");
        cancel_sender
            .send(true)
            .expect("cancel receiver remains live");

        assert!(matches!(
            channels.receive_ack().await,
            Err(ProducerFailure::Failed(_))
        ));
        assert!(channels.outstanding.is_empty());
    }

    #[tokio::test]
    async fn cancellation_after_final_successful_ack_stays_fail_closed() {
        let (event_sender, _event_receiver) = mpsc::channel::<ResponseEventEnvelope>(1);
        let (ack_sender, ack_receiver) = mpsc::channel::<ConsumerAck>(1);
        let (cancel_sender, cancel_receiver) = watch::channel(false);
        let mut outstanding = HashSet::new();
        outstanding.insert(11);
        let mut channels = ProducerChannels {
            events: event_sender,
            acknowledgements: ack_receiver,
            cancellation: cancel_receiver,
            buffer_size: 1,
            next_sequence: 12,
            outstanding,
        };

        ack_sender
            .send(ConsumerAck {
                sequence: 11,
                kind: ConsumerAckKind::Converted,
                error: None,
            })
            .await
            .expect("ack receiver remains live");
        cancel_sender
            .send(true)
            .expect("cancel receiver remains live");

        assert!(matches!(
            channels.wait_for_all_acks().await,
            Err(ProducerFailure::Cancelled)
        ));
        assert!(channels.outstanding.is_empty());
    }

    #[tokio::test]
    async fn close_after_consumer_failure_waits_for_terminal_release() {
        Python::initialize();
        let (_event_sender, event_receiver) = mpsc::channel::<ResponseEventEnvelope>(1);
        let (ack_sender, _ack_receiver) = mpsc::channel::<ConsumerAck>(1);
        let (cancel_sender, _cancel_receiver) = watch::channel(false);
        let (terminal_sender, terminal_receiver) = watch::channel(TerminalRelease::Pending);
        let shared = Arc::new(SharedConsumer {
            state: Mutex::new(ConsumerState {
                receiver: event_receiver,
                pending_metadata: None,
                active_set: None,
            }),
            acknowledgements: ack_sender,
            cancellation: cancel_sender,
            terminal: terminal_receiver,
            operation: OperationName::Query,
            busy: AtomicBool::new(false),
            closed: AtomicBool::new(false),
            complete: AtomicBool::new(false),
            aborted: AtomicBool::new(false),
            published: StdMutex::new(PublishedState::default()),
        });
        let stored = shared.record_failure(PyRuntimeError::new_err(
            "result stream consumer conversion failed",
        ));
        shared.cancel();

        let close = close_stream(Arc::clone(&shared));
        tokio::pin!(close);
        tokio::select! {
            biased;
            result = &mut close => {
                panic!("close returned before terminal release: {result:?}");
            }
            _ = tokio::task::yield_now() => {}
        }

        terminal_sender.send_replace(TerminalRelease::ReleasedFailure(stored));
        let error = close
            .await
            .expect_err("stored consumer failure must remain terminal");
        Python::attach(|py| {
            assert!(error.is_instance_of::<PyRuntimeError>(py));
        });
        assert!(shared.closed.load(Ordering::Acquire));
    }
}

async fn close_result_set(
    shared: Arc<SharedConsumer>,
    index: usize,
    closed: Arc<AtomicBool>,
) -> PyResult<()> {
    if closed.load(Ordering::Acquire) {
        return Ok(());
    }
    let _permit = ConsumerPermit::acquire(&shared)?;
    ensure_usable(&shared)?;
    let mut state = shared.state.lock().await;
    if state.active_set.as_ref().map(|active| active.index) != Some(index) {
        closed.store(true, Ordering::Release);
        return Ok(());
    }

    loop {
        let envelope = match receive_envelope(&mut state).await {
            Some(envelope) => envelope,
            None => {
                close_active_set(&mut state, index);
                closed.store(true, Ordering::Release);
                drop(state);
                await_terminal(&shared, false).await?;
                return Ok(());
            }
        };
        let ResponseEventEnvelope { sequence, event } = envelope;
        match event {
            ConversionEvent::Row(_) => {
                shared.acknowledge(sequence, ConsumerAckKind::Discarded, None)?;
            }
            event @ ConversionEvent::Metadata(_) => {
                state.pending_metadata = Some(ResponseEventEnvelope { sequence, event });
                close_active_set(&mut state, index);
                closed.store(true, Ordering::Release);
                return Ok(());
            }
            ConversionEvent::RawSummary(summary) => {
                publish_raw_summary(&shared, sequence, summary)?;
                close_active_set(&mut state, index);
                closed.store(true, Ordering::Release);
                drop(state);
                await_terminal(&shared, false).await?;
                return Ok(());
            }
        }
    }
}

async fn finish_stream(shared: Arc<SharedConsumer>) -> PyResult<Py<PyResultSummary>> {
    let _permit = ConsumerPermit::acquire(&shared)?;
    if shared.complete.load(Ordering::Acquire) {
        return summary_object(&shared);
    }
    ensure_usable(&shared)?;
    let mut state = shared.state.lock().await;
    if let Some(active) = state.active_set.take() {
        active.closed.store(true, Ordering::Release);
    }

    loop {
        let envelope = match receive_envelope(&mut state).await {
            Some(envelope) => envelope,
            None => {
                drop(state);
                await_terminal(&shared, false).await?;
                return summary_object(&shared);
            }
        };
        let sequence = envelope.sequence;
        match envelope.event {
            ConversionEvent::Metadata(_) | ConversionEvent::Row(_) => {
                shared.acknowledge(sequence, ConsumerAckKind::Discarded, None)?;
            }
            ConversionEvent::RawSummary(summary) => {
                publish_raw_summary(&shared, sequence, summary)?;
                drop(state);
                await_terminal(&shared, false).await?;
                return summary_object(&shared);
            }
        }
    }
}

async fn close_stream(shared: Arc<SharedConsumer>) -> PyResult<()> {
    let _permit = ConsumerPermit::acquire(&shared)?;
    if shared.complete.load(Ordering::Acquire) {
        return Ok(());
    }
    if shared.clone_failure().is_some() {
        return await_terminal(&shared, false).await;
    }
    if shared.summary().is_some() {
        return await_terminal(&shared, false).await;
    }
    shared.closed.store(true, Ordering::Release);
    shared.cancel();
    {
        let mut state = shared.state.lock().await;
        if let Some(active) = state.active_set.take() {
            active.closed.store(true, Ordering::Release);
        }
        state.pending_metadata = None;
        state.receiver.close();
        while state.receiver.try_recv().is_ok() {}
    }
    await_terminal(&shared, true).await
}

#[pyclass(name = "ResultStream")]
pub struct PyResultStream {
    shared: Arc<SharedConsumer>,
}

impl PyResultStream {
    fn initialize(
        operation: OperationName,
        buffer_size: usize,
    ) -> (Self, ProducerChannels, watch::Sender<TerminalRelease>) {
        let (event_sender, event_receiver) = mpsc::channel::<ResponseEventEnvelope>(buffer_size);
        let (ack_sender, ack_receiver) = mpsc::channel::<ConsumerAck>(buffer_size);
        let (cancel_sender, cancel_receiver) = watch::channel(false);
        let (terminal_sender, terminal_receiver) = watch::channel(TerminalRelease::Pending);
        let channels = ProducerChannels {
            events: event_sender,
            acknowledgements: ack_receiver,
            cancellation: cancel_receiver,
            buffer_size,
            next_sequence: 0,
            outstanding: HashSet::with_capacity(buffer_size),
        };
        let shared = Arc::new(SharedConsumer {
            state: Mutex::new(ConsumerState {
                receiver: event_receiver,
                pending_metadata: None,
                active_set: None,
            }),
            acknowledgements: ack_sender,
            cancellation: cancel_sender,
            terminal: terminal_receiver,
            operation,
            busy: AtomicBool::new(false),
            closed: AtomicBool::new(false),
            complete: AtomicBool::new(false),
            aborted: AtomicBool::new(false),
            published: StdMutex::new(PublishedState::default()),
        });
        (Self { shared }, channels, terminal_sender)
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn spawn(
        guard: PooledOperationGuard<'static>,
        permit: OperationPermit,
        observer: OperationObserver,
        request: ResultRequest,
        operation: OperationName,
        operation_timeout: Option<Duration>,
        retire_after_operation: bool,
        buffer_size: usize,
    ) -> Self {
        let (stream, channels, terminal) = Self::initialize(operation, buffer_size);

        let _supervisor = pyo3_async_runtimes::tokio::get_runtime().spawn(supervise_producer(
            ProducerSupervisor {
                permit,
                observer,
                guard,
                request,
                operation,
                operation_timeout,
                retire_after_operation,
                channels,
                terminal,
            },
        ));
        stream
    }

    pub(crate) fn spawn_transaction(
        lease: TransactionResponseLease,
        observer: OperationObserver,
        request: ResultRequest,
        operation: OperationName,
        buffer_size: usize,
    ) -> Self {
        let (stream, channels, terminal) = Self::initialize(operation, buffer_size);
        let _supervisor = pyo3_async_runtimes::tokio::get_runtime().spawn(
            supervise_transaction_producer(TransactionProducerSupervisor {
                observer,
                lease,
                request,
                operation,
                channels,
                terminal,
            }),
        );
        stream
    }
}

#[pymethods]
impl PyResultStream {
    fn __aiter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    fn __anext__<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        let (control, cancellation) = ReceiveCancellation::new(Arc::clone(&shared));
        let future_control = Arc::clone(&control);
        let awaitable = future_into_py(py, async move {
            outer_next(shared, future_control, cancellation).await
        })?;
        wrap_receive_awaitable(py, awaitable, control)
    }

    fn __aenter__<'p>(slf: Py<Self>, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        future_into_py(py, async move { Ok(slf) })
    }

    fn __aexit__<'p>(
        &self,
        py: Python<'p>,
        _exc_type: Option<Py<PyAny>>,
        _exc_value: Option<Py<PyAny>>,
        _traceback: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        future_into_py(py, async move {
            if shared.complete.load(Ordering::Acquire) {
                Ok(())
            } else {
                close_stream(shared).await
            }
        })
    }

    fn aclose<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        future_into_py(py, async move { close_stream(shared).await })
    }

    fn finish<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        future_into_py(py, async move { finish_stream(shared).await })
    }

    #[getter]
    fn closed(&self) -> bool {
        self.shared.closed.load(Ordering::Acquire)
    }

    #[getter]
    fn complete(&self) -> bool {
        self.shared.complete.load(Ordering::Acquire)
    }

    #[getter]
    fn summary(&self) -> PyResult<Py<PyResultSummary>> {
        summary_object(&self.shared)
    }

    fn __repr__(&self) -> String {
        format!(
            "ResultStream(closed={}, complete={})",
            self.closed(),
            self.complete(),
        )
    }
}

impl Drop for PyResultStream {
    fn drop(&mut self) {
        if !self.shared.closed.load(Ordering::Acquire) {
            self.shared.cancel();
        }
    }
}

#[pyclass(name = "ResultSet")]
pub struct PyResultSet {
    shared: Arc<SharedConsumer>,
    index: usize,
    columns: Arc<Vec<ColumnMetadataData>>,
    column_info: Arc<ColumnInfo>,
    closed: Arc<AtomicBool>,
}

#[pymethods]
impl PyResultSet {
    fn __aiter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    fn __anext__<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        let column_info = Arc::clone(&self.column_info);
        let closed = Arc::clone(&self.closed);
        let index = self.index;
        let (control, cancellation) = ReceiveCancellation::new(Arc::clone(&shared));
        let future_control = Arc::clone(&control);
        let awaitable = future_into_py(py, async move {
            result_set_next(
                shared,
                index,
                column_info,
                closed,
                future_control,
                cancellation,
            )
            .await
        })?;
        wrap_receive_awaitable(py, awaitable, control)
    }

    fn aclose<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let shared = Arc::clone(&self.shared);
        let closed = Arc::clone(&self.closed);
        let index = self.index;
        future_into_py(
            py,
            async move { close_result_set(shared, index, closed).await },
        )
    }

    #[getter]
    fn index(&self) -> usize {
        self.index
    }

    #[getter]
    fn columns(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let values = self
            .columns
            .iter()
            .cloned()
            .map(|column| Py::new(py, PyColumnMetadata::from_data(column)))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyTuple::new(py, values)?.unbind())
    }

    #[getter]
    fn column_names(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        Ok(PyTuple::new(py, self.columns.iter().map(|column| column.name.as_str()))?.unbind())
    }

    #[getter]
    fn closed(&self) -> bool {
        self.closed.load(Ordering::Acquire)
    }

    fn __repr__(&self) -> String {
        format!(
            "ResultSet(index={}, column_count={}, closed={})",
            self.index,
            self.columns.len(),
            self.closed(),
        )
    }
}

impl Drop for PyResultSet {
    fn drop(&mut self) {
        if !self.closed.swap(true, Ordering::AcqRel) && !self.shared.closed.load(Ordering::Acquire)
        {
            self.shared.cancel();
        }
    }
}
