use crate::deadline::OperationName;
use crate::lifecycle_config::{ConnectionLifecycleState, PyLifecycleConfig};
use crate::pool_manager::ConnectionPool;
use crate::types::{
    LifecycleErrorMetadata, ShutdownTimeoutMetadata, create_lifecycle_error,
    create_shutdown_timeout_error,
};
use ahash::AHashMap as HashMap;
use futures_util::FutureExt;
use futures_util::future::join_all;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::future::Future;
use std::panic::AssertUnwindSafe;
use std::pin::Pin;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::Poll;
use std::time::Duration;
use tokio::sync::{Notify, RwLock, watch};
use tokio::time::Instant;

const STATE_OPEN: u8 = 0;
const STATE_CLOSING: u8 = 1;
const STATE_CLOSED: u8 = 2;

#[derive(Clone, Debug)]
pub(crate) struct ShutdownOutcome {
    pub(crate) had_resources: bool,
    pub(crate) generation: u64,
    pub(crate) forced: bool,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
    pub(crate) active_operations_at_timeout: usize,
    pub(crate) active_transactions_at_timeout: usize,
    pub(crate) force_completed: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct LifecycleFailure {
    pub(crate) operation: OperationName,
    pub(crate) state: ConnectionLifecycleState,
    pub(crate) generation: u64,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
    pub(crate) forced: bool,
}

impl LifecycleFailure {
    pub(crate) fn into_pyerr(self) -> PyErr {
        create_lifecycle_error(LifecycleErrorMetadata {
            operation: self.operation,
            state: self.state,
            generation: self.generation,
            retryable: self.retryable,
            connection_discarded: self.connection_discarded,
            outcome_unknown: self.outcome_unknown,
            forced: self.forced,
        })
        .unwrap_or_else(|metadata_error| metadata_error)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ForceRequested;

enum AdmissionError {
    Lifecycle(LifecycleFailure),
    Runtime(&'static str),
}

impl AdmissionError {
    fn into_pyerr(self) -> PyErr {
        match self {
            Self::Lifecycle(failure) => failure.into_pyerr(),
            Self::Runtime(message) => PyRuntimeError::new_err(message),
        }
    }
}

pub(crate) trait ForcedShutdownParticipant: Send + Sync {
    fn force_close(&self, generation: u64) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>>;
}

struct ParticipantEntry {
    generation: u64,
    participant: Arc<dyn ForcedShutdownParticipant>,
}

struct LifecycleInner {
    state: ConnectionLifecycleState,
    generation: u64,
    active_operations: usize,
    active_transactions: usize,
    next_participant_id: u64,
    participants: HashMap<u64, ParticipantEntry>,
    force_sender: watch::Sender<bool>,
    shutdown_sender: Option<watch::Sender<Option<ShutdownOutcome>>>,
}

pub(crate) struct ConnectionLifecycle {
    public_state: AtomicU8,
    inner: Mutex<LifecycleInner>,
    activity_changed: Notify,
}

pub(crate) struct OperationPermit {
    lifecycle: Arc<ConnectionLifecycle>,
    generation: u64,
    operation: OperationName,
    outcome_unknown: bool,
    force_receiver: watch::Receiver<bool>,
    released: bool,
}

pub(crate) struct TransactionPermit {
    lifecycle: Arc<ConnectionLifecycle>,
    generation: u64,
    participant_id: u64,
    force_receiver: watch::Receiver<bool>,
    released: bool,
}

impl ConnectionLifecycle {
    pub(crate) fn new() -> Arc<Self> {
        let (force_sender, _force_receiver) = watch::channel(false);
        Arc::new(Self {
            public_state: AtomicU8::new(STATE_OPEN),
            inner: Mutex::new(LifecycleInner {
                state: ConnectionLifecycleState::Open,
                generation: 0,
                active_operations: 0,
                active_transactions: 0,
                next_participant_id: 0,
                participants: HashMap::default(),
                force_sender,
                shutdown_sender: None,
            }),
            activity_changed: Notify::new(),
        })
    }

    fn lock_inner(&self) -> MutexGuard<'_, LifecycleInner> {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn publish_state(&self, state: ConnectionLifecycleState) {
        let encoded = match state {
            ConnectionLifecycleState::Open => STATE_OPEN,
            ConnectionLifecycleState::Closing => STATE_CLOSING,
            ConnectionLifecycleState::Closed => STATE_CLOSED,
        };
        self.public_state.store(encoded, Ordering::Release);
    }

    pub(crate) fn state(&self) -> ConnectionLifecycleState {
        match self.public_state.load(Ordering::Acquire) {
            STATE_OPEN => ConnectionLifecycleState::Open,
            STATE_CLOSING => ConnectionLifecycleState::Closing,
            STATE_CLOSED => ConnectionLifecycleState::Closed,
            _ => ConnectionLifecycleState::Closed,
        }
    }

    fn reopen_locked(&self, inner: &mut LifecycleInner) -> Result<(), AdmissionError> {
        let generation = inner
            .generation
            .checked_add(1)
            .ok_or(AdmissionError::Runtime(
                "connection lifecycle generation overflow",
            ))?;
        let (force_sender, _force_receiver) = watch::channel(false);
        inner.generation = generation;
        inner.state = ConnectionLifecycleState::Open;
        inner.active_operations = 0;
        inner.active_transactions = 0;
        inner.next_participant_id = 0;
        inner.participants.clear();
        inner.force_sender = force_sender;
        inner.shutdown_sender = None;
        self.publish_state(ConnectionLifecycleState::Open);
        Ok(())
    }

    fn admission_failure(
        operation: OperationName,
        state: ConnectionLifecycleState,
        generation: u64,
    ) -> LifecycleFailure {
        LifecycleFailure {
            operation,
            state,
            generation,
            retryable: true,
            connection_discarded: false,
            outcome_unknown: false,
            forced: false,
        }
    }

    pub(crate) fn admit_operation(
        self: &Arc<Self>,
        operation: OperationName,
        outcome_unknown: bool,
    ) -> PyResult<OperationPermit> {
        let mut inner = self.lock_inner();
        let result = (|| {
            match inner.state {
                ConnectionLifecycleState::Closing => {
                    return Err(AdmissionError::Lifecycle(Self::admission_failure(
                        operation,
                        inner.state,
                        inner.generation,
                    )));
                }
                ConnectionLifecycleState::Closed => {
                    self.reopen_locked(&mut inner)?;
                }
                ConnectionLifecycleState::Open => {}
            }

            let active_operations =
                inner
                    .active_operations
                    .checked_add(1)
                    .ok_or(AdmissionError::Runtime(
                        "connection lifecycle operation counter overflow",
                    ))?;
            inner.active_operations = active_operations;
            Ok(OperationPermit {
                lifecycle: Arc::clone(self),
                generation: inner.generation,
                operation,
                outcome_unknown,
                force_receiver: inner.force_sender.subscribe(),
                released: false,
            })
        })();
        drop(inner);
        result.map_err(AdmissionError::into_pyerr)
    }

    pub(crate) fn admit_transaction(
        self: &Arc<Self>,
        participant: Arc<dyn ForcedShutdownParticipant>,
    ) -> PyResult<TransactionPermit> {
        let mut inner = self.lock_inner();
        let result = (|| {
            match inner.state {
                ConnectionLifecycleState::Closing => {
                    return Err(AdmissionError::Lifecycle(Self::admission_failure(
                        OperationName::Begin,
                        inner.state,
                        inner.generation,
                    )));
                }
                ConnectionLifecycleState::Closed => {
                    self.reopen_locked(&mut inner)?;
                }
                ConnectionLifecycleState::Open => {}
            }

            let participant_id =
                inner
                    .next_participant_id
                    .checked_add(1)
                    .ok_or(AdmissionError::Runtime(
                        "connection lifecycle participant identifier overflow",
                    ))?;
            let active_transactions =
                inner
                    .active_transactions
                    .checked_add(1)
                    .ok_or(AdmissionError::Runtime(
                        "connection lifecycle transaction counter overflow",
                    ))?;
            inner.next_participant_id = participant_id;
            inner.active_transactions = active_transactions;
            let generation = inner.generation;
            inner.participants.insert(
                participant_id,
                ParticipantEntry {
                    generation,
                    participant,
                },
            );

            Ok(TransactionPermit {
                lifecycle: Arc::clone(self),
                generation,
                participant_id,
                force_receiver: inner.force_sender.subscribe(),
                released: false,
            })
        })();
        drop(inner);
        result.map_err(AdmissionError::into_pyerr)
    }

    pub(crate) fn counts(&self, generation: u64) -> Option<(usize, usize)> {
        let inner = self.lock_inner();
        (inner.generation == generation)
            .then_some((inner.active_operations, inner.active_transactions))
    }

    async fn wait_for_zero(self: &Arc<Self>, generation: u64, deadline: Instant) -> bool {
        loop {
            let notified = self.activity_changed.notified();
            match self.counts(generation) {
                Some((0, 0)) | None => return true,
                Some(_) => {}
            }
            if tokio::time::timeout_at(deadline, notified).await.is_err() {
                return matches!(self.counts(generation), Some((0, 0)) | None);
            }
        }
    }

    fn begin_force(&self, generation: u64) -> Vec<Arc<dyn ForcedShutdownParticipant>> {
        let inner = self.lock_inner();
        if inner.generation != generation {
            return Vec::new();
        }
        inner.force_sender.send_replace(true);
        inner
            .participants
            .values()
            .filter(|entry| entry.generation == generation)
            .map(|entry| Arc::clone(&entry.participant))
            .collect()
    }

    fn finish_closed(
        &self,
        generation: u64,
        outcome: ShutdownOutcome,
        sender: &watch::Sender<Option<ShutdownOutcome>>,
    ) {
        let mut inner = self.lock_inner();
        if inner.generation == generation {
            inner.state = ConnectionLifecycleState::Closed;
            self.publish_state(ConnectionLifecycleState::Closed);
            sender.send_replace(Some(outcome));
            inner.shutdown_sender = None;
            return;
        }
        drop(inner);
        sender.send_replace(Some(outcome));
    }

    async fn run_shutdown_supervisor(
        self: Arc<Self>,
        pool: Arc<RwLock<Option<ConnectionPool>>>,
        config: PyLifecycleConfig,
        generation: u64,
        sender: watch::Sender<Option<ShutdownOutcome>>,
        initial_operations: usize,
        initial_transactions: usize,
    ) {
        let graceful_deadline = Instant::now() + config.shutdown_timeout;
        let had_resources = if initial_operations > 0 || initial_transactions > 0 {
            true
        } else {
            pool.read().await.is_some()
        };
        let graceful = self.wait_for_zero(generation, graceful_deadline).await;

        let outcome = if graceful {
            *pool.write().await = None;
            ShutdownOutcome {
                had_resources,
                generation,
                forced: false,
                shutdown_timeout: config.shutdown_timeout,
                force_timeout: config.force_timeout,
                active_operations_at_timeout: 0,
                active_transactions_at_timeout: 0,
                force_completed: true,
            }
        } else {
            let (active_operations, active_transactions) =
                self.counts(generation).unwrap_or((0, 0));
            let participants = self.begin_force(generation);
            let force_deadline = Instant::now() + config.force_timeout;
            let participants_completed =
                run_force_participants(participants, generation, force_deadline).await;
            let drained = self.wait_for_zero(generation, force_deadline).await;
            *pool.write().await = None;
            ShutdownOutcome {
                had_resources,
                generation,
                forced: true,
                shutdown_timeout: config.shutdown_timeout,
                force_timeout: config.force_timeout,
                active_operations_at_timeout: active_operations,
                active_transactions_at_timeout: active_transactions,
                force_completed: participants_completed && drained,
            }
        };

        self.finish_closed(generation, outcome, &sender);
    }

    async fn emergency_close(
        self: Arc<Self>,
        pool: Arc<RwLock<Option<ConnectionPool>>>,
        config: PyLifecycleConfig,
        generation: u64,
        sender: watch::Sender<Option<ShutdownOutcome>>,
        initial_operations: usize,
        initial_transactions: usize,
    ) {
        let participants = self.begin_force(generation);
        let force_deadline = Instant::now() + config.force_timeout;
        let _ = run_force_participants(participants, generation, force_deadline).await;
        let _ = self.wait_for_zero(generation, force_deadline).await;
        *pool.write().await = None;
        self.finish_closed(
            generation,
            ShutdownOutcome {
                had_resources: true,
                generation,
                forced: true,
                shutdown_timeout: config.shutdown_timeout,
                force_timeout: config.force_timeout,
                active_operations_at_timeout: initial_operations,
                active_transactions_at_timeout: initial_transactions,
                force_completed: false,
            },
            &sender,
        );
    }

    pub(crate) async fn shutdown(
        self: Arc<Self>,
        pool: Arc<RwLock<Option<ConnectionPool>>>,
        config: PyLifecycleConfig,
    ) -> PyResult<bool> {
        let receiver_result = {
            let mut inner = self.lock_inner();
            match inner.state {
                ConnectionLifecycleState::Closed => Ok(None),
                ConnectionLifecycleState::Closing => inner
                    .shutdown_sender
                    .as_ref()
                    .map(|sender| Some(sender.subscribe()))
                    .ok_or("connection shutdown round has no result channel"),
                ConnectionLifecycleState::Open => {
                    let generation = inner.generation;
                    let initial_operations = inner.active_operations;
                    let initial_transactions = inner.active_transactions;
                    let (sender, receiver) = watch::channel(None);
                    inner.state = ConnectionLifecycleState::Closing;
                    inner.shutdown_sender = Some(sender.clone());
                    self.publish_state(ConnectionLifecycleState::Closing);

                    let lifecycle = Arc::clone(&self);
                    let emergency_lifecycle = Arc::clone(&self);
                    let supervisor_pool = Arc::clone(&pool);
                    let emergency_pool = Arc::clone(&pool);
                    let supervisor_config = config.clone();
                    let emergency_config = config.clone();
                    let emergency_sender = sender.clone();
                    let _supervisor = pyo3_async_runtimes::tokio::get_runtime().spawn(async move {
                        let result = AssertUnwindSafe(lifecycle.run_shutdown_supervisor(
                            supervisor_pool,
                            supervisor_config,
                            generation,
                            sender,
                            initial_operations,
                            initial_transactions,
                        ))
                        .catch_unwind()
                        .await;
                        if result.is_err() {
                            emergency_lifecycle
                                .emergency_close(
                                    emergency_pool,
                                    emergency_config,
                                    generation,
                                    emergency_sender,
                                    initial_operations,
                                    initial_transactions,
                                )
                                .await;
                        }
                    });
                    Ok(Some(receiver))
                }
            }
        };
        let Some(receiver) = receiver_result.map_err(PyRuntimeError::new_err)? else {
            return Ok(false);
        };

        let mut receiver = receiver;
        let outcome = loop {
            if let Some(outcome) = receiver.borrow().clone() {
                break outcome;
            }
            receiver.changed().await.map_err(|_| {
                PyRuntimeError::new_err("connection shutdown supervisor ended without a result")
            })?;
        };

        if outcome.forced {
            return Err(create_shutdown_timeout_error(ShutdownTimeoutMetadata {
                generation: outcome.generation,
                shutdown_timeout: outcome.shutdown_timeout,
                force_timeout: outcome.force_timeout,
                active_operations_at_timeout: outcome.active_operations_at_timeout,
                active_transactions_at_timeout: outcome.active_transactions_at_timeout,
                force_completed: outcome.force_completed,
            })?);
        }
        Ok(outcome.had_resources)
    }
}

impl OperationPermit {
    pub(crate) async fn run<T, F>(self, future: F) -> PyResult<T>
    where
        T: Send + 'static,
        F: Future<Output = PyResult<T>> + Send,
    {
        match run_force_aware(self.force_receiver.clone(), future).await {
            Ok(result) => result,
            Err(ForceRequested) => Err(LifecycleFailure {
                operation: self.operation,
                state: ConnectionLifecycleState::Closing,
                generation: self.generation,
                retryable: false,
                connection_discarded: true,
                outcome_unknown: self.outcome_unknown,
                forced: true,
            }
            .into_pyerr()),
        }
    }
}

impl Drop for OperationPermit {
    fn drop(&mut self) {
        if self.released {
            return;
        }
        self.released = true;
        let mut inner = self.lifecycle.lock_inner();
        if inner.generation == self.generation {
            inner.active_operations = inner.active_operations.saturating_sub(1);
            drop(inner);
            self.lifecycle.activity_changed.notify_one();
        }
    }
}

impl TransactionPermit {
    pub(crate) fn generation(&self) -> u64 {
        self.generation
    }

    pub(crate) fn authorize_data_operation(
        &self,
        operation: OperationName,
        outcome_unknown: bool,
    ) -> PyResult<()> {
        self.authorize(operation, outcome_unknown, false)
    }

    pub(crate) fn authorize_settlement_operation(
        &self,
        operation: OperationName,
        outcome_unknown: bool,
    ) -> PyResult<()> {
        self.authorize(operation, outcome_unknown, true)
    }

    fn authorize(
        &self,
        operation: OperationName,
        outcome_unknown: bool,
        settlement: bool,
    ) -> PyResult<()> {
        let inner = self.lifecycle.lock_inner();
        let failure = if inner.generation != self.generation {
            Some(self.forced_failure(operation, outcome_unknown))
        } else {
            match inner.state {
                ConnectionLifecycleState::Open => None,
                ConnectionLifecycleState::Closing if settlement => None,
                ConnectionLifecycleState::Closing => Some(ConnectionLifecycle::admission_failure(
                    operation,
                    inner.state,
                    inner.generation,
                )),
                ConnectionLifecycleState::Closed => {
                    Some(self.forced_failure(operation, outcome_unknown))
                }
            }
        };
        drop(inner);
        match failure {
            Some(failure) => Err(failure.into_pyerr()),
            None => Ok(()),
        }
    }

    pub(crate) fn force_receiver(&self) -> watch::Receiver<bool> {
        self.force_receiver.clone()
    }

    pub(crate) fn forced_failure(
        &self,
        operation: OperationName,
        outcome_unknown: bool,
    ) -> LifecycleFailure {
        LifecycleFailure {
            operation,
            state: ConnectionLifecycleState::Closing,
            generation: self.generation,
            retryable: false,
            connection_discarded: true,
            outcome_unknown,
            forced: true,
        }
    }
}

impl Drop for TransactionPermit {
    fn drop(&mut self) {
        if self.released {
            return;
        }
        self.released = true;
        let mut inner = self.lifecycle.lock_inner();
        if inner.generation == self.generation {
            inner.participants.remove(&self.participant_id);
            inner.active_transactions = inner.active_transactions.saturating_sub(1);
            drop(inner);
            self.lifecycle.activity_changed.notify_one();
        }
    }
}

async fn run_force_participants(
    participants: Vec<Arc<dyn ForcedShutdownParticipant>>,
    generation: u64,
    deadline: Instant,
) -> bool {
    let participant_futures = participants.into_iter().map(|participant| async move {
        AssertUnwindSafe(async move {
            participant.force_close(generation).await;
        })
        .catch_unwind()
        .await
        .is_ok()
    });
    match tokio::time::timeout_at(deadline, join_all(participant_futures)).await {
        Ok(results) => results.into_iter().all(std::convert::identity),
        Err(_) => false,
    }
}

pub(crate) async fn run_force_aware<T, F>(
    mut force_receiver: watch::Receiver<bool>,
    future: F,
) -> Result<T, ForceRequested>
where
    F: Future<Output = T>,
{
    let force = async move {
        loop {
            if *force_receiver.borrow() {
                return;
            }
            if force_receiver.changed().await.is_err() {
                return;
            }
        }
    };
    let mut force = Box::pin(force);
    let mut operation = Box::pin(future);

    std::future::poll_fn(|context| {
        if force.as_mut().poll(context).is_ready() {
            return Poll::Ready(Err(ForceRequested));
        }
        match operation.as_mut().poll(context) {
            Poll::Ready(value) => Poll::Ready(Ok(value)),
            Poll::Pending => Poll::Pending,
        }
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Weak;
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};

    struct NoopParticipant;

    impl ForcedShutdownParticipant for NoopParticipant {
        fn force_close(
            &self,
            _generation: u64,
        ) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>> {
            Box::pin(async {})
        }
    }

    struct ReleasingParticipant {
        calls: Arc<AtomicUsize>,
        permit: Weak<Mutex<Option<TransactionPermit>>>,
    }

    impl ForcedShutdownParticipant for ReleasingParticipant {
        fn force_close(
            &self,
            _generation: u64,
        ) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>> {
            let calls = Arc::clone(&self.calls);
            let permit = self.permit.clone();
            Box::pin(async move {
                calls.fetch_add(1, AtomicOrdering::SeqCst);
                if let Some(permit) = permit.upgrade() {
                    permit
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .take();
                }
            })
        }
    }

    struct SynchronouslyPanickingParticipant;

    impl ForcedShutdownParticipant for SynchronouslyPanickingParticipant {
        fn force_close(
            &self,
            _generation: u64,
        ) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>> {
            panic!("synchronous participant panic")
        }
    }

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .expect("lifecycle test runtime must build")
    }

    fn fast_config() -> PyLifecycleConfig {
        PyLifecycleConfig {
            shutdown_timeout: Duration::from_millis(10),
            force_timeout: Duration::from_millis(40),
        }
    }

    async fn wait_until_state(lifecycle: &ConnectionLifecycle, expected: ConnectionLifecycleState) {
        tokio::time::timeout(Duration::from_secs(1), async {
            while lifecycle.state() != expected {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("lifecycle state transition must be bounded");
    }

    fn force_completed(error: &PyErr) -> bool {
        Python::attach(|py| {
            error
                .value(py)
                .getattr("force_completed")
                .expect("shutdown timeout must expose force_completed")
                .extract()
                .expect("force_completed must be a bool")
        })
    }

    #[test]
    fn new_coordinator_is_open() {
        let lifecycle = ConnectionLifecycle::new();
        assert_eq!(lifecycle.state(), ConnectionLifecycleState::Open);
        assert_eq!(lifecycle.counts(0), Some((0, 0)));
    }

    #[test]
    fn closing_rejects_admission_and_dropped_permit_drains() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let permit = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Open must admit work");
            let pool = Arc::new(RwLock::new(None));
            let shutdown =
                tokio::spawn(Arc::clone(&lifecycle).shutdown(pool, PyLifecycleConfig::default()));
            tokio::task::yield_now().await;
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closing);
            assert!(
                lifecycle
                    .admit_operation(OperationName::Query, true)
                    .is_err()
            );
            drop(permit);
            assert!(
                shutdown
                    .await
                    .expect("shutdown task must not panic")
                    .expect("graceful shutdown must succeed")
            );
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);
        });
    }

    #[test]
    fn force_first_race_prefers_force() {
        runtime().block_on(async {
            let (sender, receiver) = watch::channel(false);
            sender.send_replace(true);
            assert_eq!(
                run_force_aware(receiver, async { 42 }).await,
                Err(ForceRequested)
            );
        });
    }

    #[test]
    fn transaction_permit_is_counted_once() {
        let lifecycle = ConnectionLifecycle::new();
        let permit = lifecycle
            .admit_transaction(Arc::new(NoopParticipant))
            .expect("Open must admit a transaction");
        assert_eq!(permit.generation(), 0);
        assert_eq!(lifecycle.counts(0), Some((0, 1)));
        drop(permit);
        assert_eq!(lifecycle.counts(0), Some((0, 0)));
    }

    #[test]
    fn cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let permit = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Open must admit work");
            let pool = Arc::new(RwLock::new(None));
            let first = tokio::spawn(
                Arc::clone(&lifecycle).shutdown(Arc::clone(&pool), PyLifecycleConfig::default()),
            );
            wait_until_state(&lifecycle, ConnectionLifecycleState::Closing).await;
            first.abort();
            let _ = first.await;

            let second = tokio::spawn(
                Arc::clone(&lifecycle).shutdown(Arc::clone(&pool), PyLifecycleConfig::default()),
            );
            let third =
                tokio::spawn(Arc::clone(&lifecycle).shutdown(pool, PyLifecycleConfig::default()));
            drop(permit);

            for waiter in [second, third] {
                assert!(
                    waiter
                        .await
                        .expect("shutdown waiter must not panic")
                        .expect("shared shutdown must remain graceful")
                );
            }
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);
        });
    }

    #[test]
    fn shutdown_budget_starts_before_waiting_for_pool_lock() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let permit = lifecycle
                .admit_operation(OperationName::Connect, false)
                .expect("Open must admit pool initialization");
            let mut force_receiver = permit.force_receiver.clone();
            let pool = Arc::new(RwLock::new(None));
            let pool_lock = pool.write().await;
            let shutdown =
                tokio::spawn(Arc::clone(&lifecycle).shutdown(Arc::clone(&pool), fast_config()));

            tokio::time::timeout(Duration::from_millis(100), force_receiver.changed())
                .await
                .expect("force must not wait behind pool initialization")
                .expect("force channel must remain open");
            assert!(*force_receiver.borrow());

            drop(permit);
            drop(pool_lock);
            let error = shutdown
                .await
                .expect("shutdown task must not panic")
                .expect_err("expired grace must remain visible");
            assert!(force_completed(&error));
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);
        });
    }

    #[test]
    fn stale_operation_permit_cannot_decrement_reopened_generation() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let stale = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Open must admit work");
            let pool = Arc::new(RwLock::new(None));
            Arc::clone(&lifecycle)
                .shutdown(pool, fast_config())
                .await
                .expect_err("a retained old permit must force shutdown");
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);

            let current = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Closed must reopen as a fresh generation");
            assert_eq!(lifecycle.counts(1), Some((1, 0)));
            drop(stale);
            assert_eq!(lifecycle.counts(1), Some((1, 0)));
            drop(current);
            assert_eq!(lifecycle.counts(1), Some((0, 0)));
        });
    }

    #[test]
    fn old_operation_reports_closing_even_after_new_generation_reopens() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let stale = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Open must admit work");
            Arc::clone(&lifecycle)
                .shutdown(Arc::new(RwLock::new(None)), fast_config())
                .await
                .expect_err("a retained old permit must force shutdown");
            let current = lifecycle
                .admit_operation(OperationName::Query, true)
                .expect("Closed must reopen as a fresh generation");

            let error = stale
                .run(async { Ok::<u8, PyErr>(1) })
                .await
                .expect_err("the old generation must observe its force signal");
            let state: String = Python::attach(|py| {
                error
                    .value(py)
                    .getattr("state")
                    .expect("lifecycle error must expose state")
                    .extract()
                    .expect("lifecycle state metadata must be a string")
            });
            assert_eq!(state, "Closing");
            assert_eq!(lifecycle.counts(1), Some((1, 0)));
            drop(current);
        });
    }

    #[test]
    fn participant_callback_runs_once_and_releases_drain_barrier() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let calls = Arc::new(AtomicUsize::new(0));
            let permit_holder = Arc::new(Mutex::new(None));
            let participant = Arc::new(ReleasingParticipant {
                calls: Arc::clone(&calls),
                permit: Arc::downgrade(&permit_holder),
            });
            let permit = lifecycle
                .admit_transaction(participant)
                .expect("Open must admit a transaction");
            *permit_holder
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(permit);

            let error = Arc::clone(&lifecycle)
                .shutdown(Arc::new(RwLock::new(None)), fast_config())
                .await
                .expect_err("grace expiry must remain visible");
            assert_eq!(calls.load(AtomicOrdering::SeqCst), 1);
            assert!(force_completed(&error));
            assert_eq!(lifecycle.counts(0), Some((0, 0)));
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);
        });
    }

    #[test]
    fn synchronous_participant_panic_does_not_skip_other_participants() {
        runtime().block_on(async {
            let calls = Arc::new(AtomicUsize::new(0));
            let permit_holder = Arc::new(Mutex::new(None));
            let participants: Vec<Arc<dyn ForcedShutdownParticipant>> = vec![
                Arc::new(SynchronouslyPanickingParticipant),
                Arc::new(ReleasingParticipant {
                    calls: Arc::clone(&calls),
                    permit: Arc::downgrade(&permit_holder),
                }),
            ];

            let completed = run_force_participants(
                participants,
                7,
                Instant::now() + Duration::from_millis(100),
            )
            .await;

            assert!(!completed);
            assert_eq!(calls.load(AtomicOrdering::SeqCst), 1);
        });
    }

    #[test]
    fn participant_panic_closes_generation_with_incomplete_force_metadata() {
        Python::initialize();
        runtime().block_on(async {
            let lifecycle = ConnectionLifecycle::new();
            let panic_permit = lifecycle
                .admit_transaction(Arc::new(SynchronouslyPanickingParticipant))
                .expect("Open must admit a transaction");
            let calls = Arc::new(AtomicUsize::new(0));
            let permit_holder = Arc::new(Mutex::new(None));
            let releasing_permit = lifecycle
                .admit_transaction(Arc::new(ReleasingParticipant {
                    calls: Arc::clone(&calls),
                    permit: Arc::downgrade(&permit_holder),
                }))
                .expect("Open must admit another transaction");
            *permit_holder
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(releasing_permit);

            let error = Arc::clone(&lifecycle)
                .shutdown(Arc::new(RwLock::new(None)), fast_config())
                .await
                .expect_err("grace expiry must remain visible");

            assert_eq!(calls.load(AtomicOrdering::SeqCst), 1);
            assert!(!force_completed(&error));
            assert_eq!(lifecycle.state(), ConnectionLifecycleState::Closed);
            drop(panic_permit);
        });
    }
}
