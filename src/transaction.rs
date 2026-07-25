use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_async_runtimes::tokio::future_into_py;
use std::ops::{Deref, DerefMut};
use std::sync::Arc;
use tiberius::{AuthMethod, Client, Config};
use tokio::net::TcpStream;
use tokio::sync::{Mutex as AsyncMutex, RwLock};

use crate::azure_auth::PyAzureCredential;
use crate::batch::{execute_batch_on_connection, parse_batch_items, query_batch_on_connection};
use crate::connection_config::config_from_ado_string;
use crate::deadline::{
    Deadline, DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, earliest_deadline,
    run_until,
};
use crate::helpers::{
    catch_driver_panic, execute_unparameterized_command, requires_direct_batch, wrap_query_stream,
};
use crate::parameter_conversion::{convert_parameters_to_fast, params_as_sql_refs};
use crate::pool_config::PyPoolConfig;
use crate::pool_manager::{
    ConnectionPool, OwnedPooledConnection, acquire_owned_connection, connect_client_with_timeout,
    ensure_pool_initialized_with_auth, timeout_error_or_metadata_failure,
};
use crate::ssl_config::PySslConfig;
use crate::timeout_config::PyTimeoutConfig;
use crate::types::{
    SqlError, TimeoutErrorMetadata, create_commit_outcome_unknown, create_sql_error,
};

type SingleConnectionType = Client<tokio_util::compat::Compat<TcpStream>>;

/// A transaction can use the legacy direct socket or an owned bb8 lease.
///
/// `PooledConnection<'static, _>` owns an internal pool handle, so it can live
/// for the whole SQL transaction while remaining counted against
/// `pool.max_size`.
enum TransactionConnection {
    Direct(SingleConnectionType),
    Pooled(OwnedPooledConnection),
}

impl TransactionConnection {
    fn is_pooled(&self) -> bool {
        matches!(self, Self::Pooled(_))
    }

    fn prepare_for_checkout(&mut self) {
        if let Self::Pooled(connection) = self {
            connection.prepare_for_checkout();
        }
    }

    /// Mark the TDS stream unsafe before the request's first await. If Python
    /// cancels the future, no completion path can accidentally restore it.
    fn begin_operation(&mut self) {
        if let Self::Pooled(connection) = self {
            connection.begin_operation();
        }
    }

    fn finish_operation<T>(&mut self, result: &PyResult<T>) {
        if let Self::Pooled(connection) = self {
            match result {
                Ok(_) => connection.finish_operation_success(),
                Err(error) => connection.apply_operation_error(error),
            }
        }
    }

    fn mark_unusable(&mut self) {
        if let Self::Pooled(connection) = self {
            connection.mark_unusable();
        }
    }

    fn is_reusable(&self) -> bool {
        match self {
            Self::Direct(_) => true,
            Self::Pooled(connection) => connection.is_reusable(),
        }
    }
}

impl Deref for TransactionConnection {
    type Target = SingleConnectionType;

    fn deref(&self) -> &Self::Target {
        match self {
            Self::Direct(connection) => connection,
            Self::Pooled(connection) => connection,
        }
    }
}

impl DerefMut for TransactionConnection {
    fn deref_mut(&mut self) -> &mut Self::Target {
        match self {
            Self::Direct(connection) => connection,
            Self::Pooled(connection) => connection,
        }
    }
}

/// Authoritative transaction lifecycle stored in Rust.
///
/// In-flight states are written before awaiting TDS. If Python cancels that
/// future, the object remains fail-closed until `close()` drops or retires the
/// physical connection instead of guessing the server-side outcome.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TransactionState {
    Idle,
    Beginning,
    Active,
    Executing,
    Committing,
    Committed,
    RollingBack,
    RolledBack,
    Failed,
    Closing,
}

impl TransactionState {
    fn is_in_flight(self) -> bool {
        matches!(
            self,
            Self::Beginning | Self::Executing | Self::Committing | Self::RollingBack
        )
    }

    fn ensure_connection_usable(self) -> PyResult<()> {
        match self {
            Self::Idle | Self::Active | Self::Committed | Self::RolledBack => Ok(()),
            Self::Beginning
            | Self::Executing
            | Self::Committing
            | Self::RollingBack
            | Self::Failed
            | Self::Closing => Err(PyRuntimeError::new_err(
                "Transaction state is indeterminate; call close() before reuse",
            )),
        }
    }
}

/// The connection and transaction state share one mutex so validation, wire
/// I/O, response consumption, and the final transition are atomic with respect
/// to every concurrent operation on this object.
struct TransactionSession {
    conn: Option<TransactionConnection>,
    state: TransactionState,
    operation_epoch: u64,
    lifetime_deadline: Option<Deadline>,
}

impl Default for TransactionSession {
    fn default() -> Self {
        Self {
            conn: None,
            state: TransactionState::Idle,
            operation_epoch: 0,
            lifetime_deadline: None,
        }
    }
}

impl TransactionSession {
    fn transition_to(&mut self, state: TransactionState) {
        self.state = state;
        if matches!(
            state,
            TransactionState::Idle
                | TransactionState::Committed
                | TransactionState::RolledBack
                | TransactionState::Failed
                | TransactionState::Closing
        ) {
            self.lifetime_deadline = None;
        }
    }

    fn enter_in_flight(&mut self, state: TransactionState) -> u64 {
        debug_assert!(state.is_in_flight());
        self.operation_epoch = self.operation_epoch.wrapping_add(1);
        self.transition_to(state);
        self.operation_epoch
    }

    fn retire_cancelled_operation(&mut self, epoch: u64) {
        if self.operation_epoch != epoch || !self.state.is_in_flight() {
            return;
        }

        if let Some(connection) = self.conn.as_mut() {
            connection.mark_unusable();
        }
        self.conn.take();
        self.transition_to(TransactionState::Failed);
    }

    fn retire_expired_lifetime(&mut self) -> Option<DeadlineElapsed> {
        let lifetime = self.lifetime_deadline?;
        if lifetime.at > tokio::time::Instant::now() {
            return None;
        }

        if let Some(connection) = self.conn.as_mut() {
            connection.mark_unusable();
        }
        self.conn.take();
        self.transition_to(TransactionState::Failed);
        Some(DeadlineElapsed {
            timeout: lifetime.timeout,
            phase: TimeoutPhase::Transaction,
        })
    }
}

/// Retires a transaction connection if Python drops an in-flight Rust future.
///
/// Cleanup is epoch-checked so a delayed task cannot close a later operation
/// after the transaction object has been explicitly closed and reused.
struct TransactionCancellationGuard {
    session: Arc<AsyncMutex<TransactionSession>>,
    armed_epoch: Option<u64>,
}

impl TransactionCancellationGuard {
    fn new(session: Arc<AsyncMutex<TransactionSession>>) -> Self {
        Self {
            session,
            armed_epoch: None,
        }
    }

    fn arm(&mut self, epoch: u64) {
        self.armed_epoch = Some(epoch);
    }

    fn disarm(&mut self) {
        self.armed_epoch = None;
    }
}

impl Drop for TransactionCancellationGuard {
    fn drop(&mut self) {
        let Some(epoch) = self.armed_epoch.take() else {
            return;
        };

        if let Ok(mut session) = self.session.try_lock() {
            session.retire_cancelled_operation(epoch);
            return;
        }

        let session = Arc::clone(&self.session);
        let _cleanup_task = pyo3_async_runtimes::tokio::get_runtime().spawn(async move {
            let mut session = session.lock().await;
            session.retire_cancelled_operation(epoch);
        });
    }
}

#[derive(Clone)]
struct SharedPoolSource {
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
}

#[derive(Clone, Copy)]
enum TransactionCommand {
    Begin,
    Commit,
    Rollback,
}

impl TransactionCommand {
    fn operation_name(self) -> OperationName {
        match self {
            Self::Begin => OperationName::Begin,
            Self::Commit => OperationName::Commit,
            Self::Rollback => OperationName::Rollback,
        }
    }

    fn sql(self) -> &'static str {
        match self {
            Self::Begin => "BEGIN TRANSACTION",
            Self::Commit => "COMMIT TRANSACTION",
            Self::Rollback => "ROLLBACK TRANSACTION",
        }
    }

    fn error_context(self) -> &'static str {
        match self {
            Self::Begin => "Failed to begin transaction",
            Self::Commit => "Failed to commit transaction",
            Self::Rollback => "Failed to rollback transaction",
        }
    }

    fn in_flight_state(self) -> TransactionState {
        match self {
            Self::Begin => TransactionState::Beginning,
            Self::Commit => TransactionState::Committing,
            Self::Rollback => TransactionState::RollingBack,
        }
    }

    fn completed_state(self) -> TransactionState {
        match self {
            Self::Begin => TransactionState::Active,
            Self::Commit => TransactionState::Committed,
            Self::Rollback => TransactionState::RolledBack,
        }
    }

    fn releases_pool_lease(self) -> bool {
        matches!(self, Self::Commit | Self::Rollback)
    }

    fn validate(self, state: TransactionState) -> PyResult<()> {
        match (self, state) {
            (
                Self::Begin,
                TransactionState::Idle | TransactionState::Committed | TransactionState::RolledBack,
            )
            | (Self::Commit | Self::Rollback, TransactionState::Active) => Ok(()),
            (Self::Begin, TransactionState::Active) => {
                Err(PyRuntimeError::new_err("Transaction has already begun"))
            }
            (Self::Commit | Self::Rollback, TransactionState::Idle) => {
                Err(PyRuntimeError::new_err("Transaction has not begun"))
            }
            (Self::Commit | Self::Rollback, TransactionState::Committed) => Err(
                PyRuntimeError::new_err("Transaction has already been committed"),
            ),
            (Self::Commit | Self::Rollback, TransactionState::RolledBack) => Err(
                PyRuntimeError::new_err("Transaction has already been rolled back"),
            ),
            (
                _,
                TransactionState::Beginning
                | TransactionState::Executing
                | TransactionState::Committing
                | TransactionState::RollingBack
                | TransactionState::Failed
                | TransactionState::Closing,
            ) => Err(PyRuntimeError::new_err(
                "Transaction state is indeterminate; call close() before reuse",
            )),
        }
    }
}

fn command_deadline(
    command: TransactionCommand,
    timeout_config: &PyTimeoutConfig,
    lifetime_deadline: Option<Deadline>,
) -> Option<Deadline> {
    match command {
        TransactionCommand::Begin => {
            deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout)
        }
        TransactionCommand::Commit => earliest_deadline(
            deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout),
            lifetime_deadline,
        ),
        TransactionCommand::Rollback => {
            deadline_from(TimeoutPhase::Rollback, timeout_config.rollback_timeout)
        }
    }
}

fn is_deterministic_commit_rejection(error: &PyErr) -> bool {
    Python::attach(|py| {
        if !error.is_instance_of::<SqlError>(py) {
            return false;
        }
        error
            .value(py)
            .getattr("severity")
            .and_then(|value| value.extract::<u8>())
            .is_ok_and(|severity| severity <= 19)
    })
}

fn transaction_timeout_error(
    elapsed: DeadlineElapsed,
    operation: OperationName,
    outcome_unknown: bool,
) -> PyErr {
    timeout_error_or_metadata_failure(
        elapsed,
        TimeoutErrorMetadata {
            operation,
            retryable: false,
            connection_discarded: true,
            outcome_unknown,
        },
    )
}

struct TransactionHandles {
    session: Arc<AsyncMutex<TransactionSession>>,
    config: Arc<Config>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    pool_source: Option<SharedPoolSource>,
    timeout_config: PyTimeoutConfig,
}

impl TransactionHandles {
    async fn ensure_connected(&self, operation: OperationName) -> PyResult<()> {
        Transaction::ensure_connected_inner(
            &self.session,
            &self.config,
            self.azure_credential.as_ref(),
            self.pool_source.as_ref(),
            &self.timeout_config,
            operation,
        )
        .await
    }
}

/// A SQL Server transaction session.
///
/// `Connection.transaction()` constructs a transaction backed by an owned
/// lease from that connection's shared pool. The public constructor remains a
/// compatibility path backed by one direct socket.
#[pyclass(name = "Transaction")]
pub struct Transaction {
    session: Arc<AsyncMutex<TransactionSession>>,
    config: Arc<Config>,
    _ssl_config: Option<PySslConfig>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    pool_source: Option<SharedPoolSource>,
    timeout_config: PyTimeoutConfig,
}

#[pymethods]
impl Transaction {
    #[new]
    #[pyo3(signature = (connection_string = None, ssl_config = None, azure_credential = None, server = None, database = None, username = None, password = None, application_intent = None, port = None, instance_name = None, application_name = None, timeout_config = None))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        connection_string: Option<String>,
        ssl_config: Option<PySslConfig>,
        azure_credential: Option<PyAzureCredential>,
        server: Option<String>,
        database: Option<String>,
        username: Option<String>,
        password: Option<String>,
        application_intent: Option<String>,
        port: Option<u16>,
        instance_name: Option<String>,
        application_name: Option<String>,
        timeout_config: Option<PyTimeoutConfig>,
    ) -> PyResult<Self> {
        let server_param = server.clone();

        let config = if let Some(conn_str) = connection_string {
            config_from_ado_string(&conn_str, ssl_config.as_ref())?
        } else if let Some(srv) = server {
            let mut config = Config::new();
            config.host(&srv);
            if let Some(db) = database {
                config.database(&db);
            }
            if let Some(ref user) = username {
                if azure_credential.is_some() {
                    return Err(PyValueError::new_err(
                        "Cannot use both username/password and azure_credential. Choose one authentication method.",
                    ));
                }
                let pwd = password.ok_or_else(|| {
                    PyValueError::new_err("password is required when username is provided")
                })?;
                config.authentication(AuthMethod::sql_server(user, &pwd));
            }
            if let Some(p) = port {
                config.port(p);
            }
            if let Some(itn) = instance_name {
                config.instance_name(itn);
            }
            if let Some(apn) = application_name {
                config.application_name(apn);
            }
            if let Some(intent) = application_intent {
                match intent.to_lowercase().trim() {
                    "readonly" | "read_only" => config.readonly(true),
                    "readwrite" | "read_write" | "" => config.readonly(false),
                    invalid => {
                        return Err(PyValueError::new_err(format!(
                            "Invalid application_intent '{}'. Valid values: 'readonly', 'read_only', 'readwrite', 'read_write', or empty string",
                            invalid
                        )));
                    }
                }
            }
            if let Some(ref ssl_cfg) = ssl_config {
                ssl_cfg.apply_to_config(&mut config);
            }
            config
        } else {
            return Err(PyValueError::new_err(
                "Either connection_string or server must be provided",
            ));
        };

        if server_param.is_some() && username.is_none() && azure_credential.is_none() {
            return Err(PyValueError::new_err(
                "When using individual connection parameters, either username/password or azure_credential must be provided",
            ));
        }

        Ok(Self {
            session: Arc::new(AsyncMutex::new(TransactionSession::default())),
            config: Arc::new(config),
            _ssl_config: ssl_config,
            azure_credential: azure_credential.map(Arc::new),
            pool_source: None,
            timeout_config: timeout_config.unwrap_or_else(PyTimeoutConfig::explicit_default),
        })
    }

    #[getter]
    pub fn timeout_config(&self) -> PyTimeoutConfig {
        self.timeout_config.clone()
    }

    /// Execute a SQL query that returns rows.
    #[pyo3(signature = (query, parameters=None))]
    pub fn query<'p>(
        &self,
        py: Python<'p>,
        query: String,
        parameters: Option<&Bound<'p, PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let fast_parameters = convert_parameters_to_fast(parameters, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let mut cancellation_guard =
                TransactionCancellationGuard::new(Arc::clone(&handles.session));
            handles.ensure_connected(OperationName::Query).await?;

            let execution_result = {
                let mut session = handles.session.lock().await;
                let (previous_state, epoch, deadline) = Self::begin_data_operation(
                    &mut session,
                    OperationName::Query,
                    &handles.timeout_config,
                )?;
                cancellation_guard.arm(epoch);
                let operation = match session.conn.as_mut() {
                    Some(conn_ref) => {
                        let tiberius_params = params_as_sql_refs(&fast_parameters);
                        run_until(
                            deadline,
                            catch_driver_panic(async {
                                conn_ref
                                    .query(&query, &tiberius_params)
                                    .await
                                    .map_err(|e| create_sql_error(e, "Query execution failed"))?
                                    .into_first_result()
                                    .await
                                    .map_err(|e| create_sql_error(e, "Failed to get results"))
                            }),
                        )
                        .await
                    }
                    None => Ok(Ok(Err(PyRuntimeError::new_err(
                        "Connection is not established",
                    )))),
                };
                Self::finish_data_operation(
                    &mut session,
                    previous_state,
                    OperationName::Query,
                    operation,
                )
            };
            cancellation_guard.disarm();

            wrap_query_stream(execution_result?)
        })
    }

    /// Execute a raw (non-prepared) SQL query.
    #[pyo3(signature = (query))]
    pub fn simple_query<'p>(&self, py: Python<'p>, query: String) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let mut cancellation_guard =
                TransactionCancellationGuard::new(Arc::clone(&handles.session));
            handles.ensure_connected(OperationName::SimpleQuery).await?;

            let execution_result = {
                let mut session = handles.session.lock().await;
                let (previous_state, epoch, deadline) = Self::begin_data_operation(
                    &mut session,
                    OperationName::SimpleQuery,
                    &handles.timeout_config,
                )?;
                cancellation_guard.arm(epoch);
                let operation = match session.conn.as_mut() {
                    Some(conn_ref) => {
                        run_until(
                            deadline,
                            catch_driver_panic(async {
                                conn_ref
                                    .simple_query(&query)
                                    .await
                                    .map_err(|e| create_sql_error(e, "Query execution failed"))?
                                    .into_first_result()
                                    .await
                                    .map_err(|e| create_sql_error(e, "Failed to get results"))
                            }),
                        )
                        .await
                    }
                    None => Ok(Ok(Err(PyRuntimeError::new_err(
                        "Connection is not established",
                    )))),
                };
                Self::finish_data_operation(
                    &mut session,
                    previous_state,
                    OperationName::SimpleQuery,
                    operation,
                )
            };
            cancellation_guard.disarm();

            wrap_query_stream(execution_result?)
        })
    }

    /// Execute an INSERT/UPDATE/DELETE/DDL command.
    #[pyo3(signature = (command, parameters=None))]
    pub fn execute<'p>(
        &self,
        py: Python<'p>,
        command: String,
        parameters: Option<&Bound<'p, PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let fast_parameters = convert_parameters_to_fast(parameters, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let mut cancellation_guard =
                TransactionCancellationGuard::new(Arc::clone(&handles.session));
            handles.ensure_connected(OperationName::Execute).await?;

            let affected = {
                let mut session = handles.session.lock().await;
                let (previous_state, epoch, deadline) = Self::begin_data_operation(
                    &mut session,
                    OperationName::Execute,
                    &handles.timeout_config,
                )?;
                cancellation_guard.arm(epoch);
                let operation = match session.conn.as_mut() {
                    Some(conn_ref) => {
                        run_until(
                            deadline,
                            catch_driver_panic(async {
                                if fast_parameters.is_empty() && requires_direct_batch(&command) {
                                    execute_unparameterized_command(
                                        conn_ref,
                                        &command,
                                        "Command execution failed",
                                    )
                                    .await
                                } else {
                                    let tiberius_params = params_as_sql_refs(&fast_parameters);
                                    conn_ref
                                        .execute(&command, &tiberius_params)
                                        .await
                                        .map(|result| result.total())
                                        .map_err(|e| {
                                            create_sql_error(e, "Command execution failed")
                                        })
                                }
                            }),
                        )
                        .await
                    }
                    None => Ok(Ok(Err(PyRuntimeError::new_err(
                        "Connection is not established",
                    )))),
                };
                Self::finish_data_operation(
                    &mut session,
                    previous_state,
                    OperationName::Execute,
                    operation,
                )
            };
            cancellation_guard.disarm();

            affected
        })
    }

    /// Execute multiple commands in sequence on the transaction lease.
    #[pyo3(signature = (commands))]
    pub fn execute_batch<'p>(
        &self,
        py: Python<'p>,
        commands: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let batch_commands = parse_batch_items(commands, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let mut cancellation_guard =
                TransactionCancellationGuard::new(Arc::clone(&handles.session));
            handles
                .ensure_connected(OperationName::ExecuteBatch)
                .await?;

            let all_results = {
                let mut session = handles.session.lock().await;
                let (previous_state, epoch, deadline) = Self::begin_data_operation(
                    &mut session,
                    OperationName::ExecuteBatch,
                    &handles.timeout_config,
                )?;
                cancellation_guard.arm(epoch);
                let operation = match session.conn.as_mut() {
                    Some(conn_ref) => {
                        run_until(
                            deadline,
                            catch_driver_panic(execute_batch_on_connection(
                                conn_ref,
                                batch_commands,
                            )),
                        )
                        .await
                    }
                    None => Ok(Ok(Err(PyRuntimeError::new_err(
                        "Connection is not established",
                    )))),
                };
                Self::finish_data_operation(
                    &mut session,
                    previous_state,
                    OperationName::ExecuteBatch,
                    operation,
                )
            };
            cancellation_guard.disarm();
            let all_results = all_results?;

            Python::attach(|py| {
                let py_list = PyList::new(py, all_results)?;
                Ok(py_list.into_any().unbind())
            })
        })
    }

    /// Execute multiple queries in sequence on the transaction lease.
    #[pyo3(signature = (queries))]
    pub fn query_batch<'p>(
        &self,
        py: Python<'p>,
        queries: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let batch_queries = parse_batch_items(queries, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let mut cancellation_guard =
                TransactionCancellationGuard::new(Arc::clone(&handles.session));
            handles.ensure_connected(OperationName::QueryBatch).await?;

            let all_results = {
                let mut session = handles.session.lock().await;
                let (previous_state, epoch, deadline) = Self::begin_data_operation(
                    &mut session,
                    OperationName::QueryBatch,
                    &handles.timeout_config,
                )?;
                cancellation_guard.arm(epoch);
                let operation = match session.conn.as_mut() {
                    Some(conn_ref) => {
                        run_until(
                            deadline,
                            catch_driver_panic(query_batch_on_connection(conn_ref, batch_queries)),
                        )
                        .await
                    }
                    None => Ok(Ok(Err(PyRuntimeError::new_err(
                        "Connection is not established",
                    )))),
                };
                Self::finish_data_operation(
                    &mut session,
                    previous_state,
                    OperationName::QueryBatch,
                    operation,
                )
            };
            cancellation_guard.disarm();
            let all_results = all_results?;

            Python::attach(|py| -> PyResult<Py<PyAny>> {
                let mut py_results = Vec::with_capacity(all_results.len());
                for result in all_results {
                    let py_result = wrap_query_stream(result)?;
                    py_results.push(py_result.into_any());
                }
                let py_list = PyList::new(py, py_results)?;
                Ok(py_list.into_any().unbind())
            })
        })
    }

    pub fn begin<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            handles.ensure_connected(OperationName::Begin).await?;
            Self::execute_transaction_command(
                &handles.session,
                TransactionCommand::Begin,
                &handles.timeout_config,
            )
            .await
        })
    }

    pub fn commit<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);
        let timeout_config = self.timeout_config.clone();
        future_into_py(py, async move {
            Self::execute_transaction_command(&session, TransactionCommand::Commit, &timeout_config)
                .await
        })
    }

    pub fn rollback<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);
        let timeout_config = self.timeout_config.clone();
        future_into_py(py, async move {
            Self::execute_transaction_command(
                &session,
                TransactionCommand::Rollback,
                &timeout_config,
            )
            .await
        })
    }

    /// Release the direct socket or shared pool lease.
    pub fn close<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);
        let timeout_config = self.timeout_config.clone();

        future_into_py(py, async move {
            let mut session = session.lock().await;
            let previous_state = session.state;
            let mut conn = session.conn.take();
            session.transition_to(TransactionState::Closing);
            let mut close_result: PyResult<()> = Ok(());

            if let Some(conn_ref) = conn.as_mut() {
                if previous_state == TransactionState::Active {
                    conn_ref.begin_operation();
                    let deadline =
                        deadline_from(TimeoutPhase::Rollback, timeout_config.rollback_timeout);
                    let rollback = run_until(
                        deadline,
                        catch_driver_panic(async {
                            conn_ref
                                .simple_query("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
                                .await
                                .map_err(|error| {
                                    create_sql_error(error, "Failed to close transaction")
                                })?
                                .into_results()
                                .await
                                .map_err(|error| {
                                    create_sql_error(error, "Failed to close transaction")
                                })?;
                            Ok(())
                        }),
                    )
                    .await;

                    match rollback {
                        Err(elapsed) => {
                            conn_ref.mark_unusable();
                            close_result = Err(transaction_timeout_error(
                                elapsed,
                                OperationName::Close,
                                false,
                            ));
                        }
                        Ok(Err(driver_panic)) => {
                            conn_ref.mark_unusable();
                            close_result = Err(driver_panic);
                        }
                        Ok(Ok(result @ Ok(()))) => {
                            conn_ref.finish_operation(&result);
                        }
                        Ok(Ok(Err(error))) => {
                            conn_ref.mark_unusable();
                            close_result = Err(error);
                        }
                    }
                } else if matches!(
                    previous_state,
                    TransactionState::Idle
                        | TransactionState::Committed
                        | TransactionState::RolledBack
                ) {
                    let clean_result: PyResult<()> = Ok(());
                    conn_ref.finish_operation(&clean_result);
                } else {
                    conn_ref.mark_unusable();
                }
            }

            // Dropping a direct socket guarantees server-side rollback. Dropping
            // a pooled lease returns only a fully consumed response; an
            // in-flight/cancelled lease is marked Broken and bb8 retires it.
            drop(conn);
            session.transition_to(TransactionState::Idle);
            close_result
        })
    }

    pub fn is_connected(&self) -> bool {
        match self.session.try_lock() {
            Ok(session) => session.conn.is_some(),
            Err(_) => true,
        }
    }
}

impl Transaction {
    pub(crate) fn from_pool(
        pool: Arc<RwLock<Option<ConnectionPool>>>,
        config: Arc<Config>,
        pool_config: PyPoolConfig,
        timeout_config: PyTimeoutConfig,
        azure_credential: Option<Arc<PyAzureCredential>>,
    ) -> Self {
        Self {
            session: Arc::new(AsyncMutex::new(TransactionSession::default())),
            config,
            _ssl_config: None,
            azure_credential,
            pool_source: Some(SharedPoolSource {
                pool,
                pool_config,
                timeout_config: timeout_config.clone(),
            }),
            timeout_config,
        }
    }

    fn clone_handles(&self) -> TransactionHandles {
        TransactionHandles {
            session: Arc::clone(&self.session),
            config: Arc::clone(&self.config),
            azure_credential: self.azure_credential.clone(),
            pool_source: self.pool_source.clone(),
            timeout_config: self.timeout_config.clone(),
        }
    }

    fn begin_data_operation(
        session: &mut TransactionSession,
        operation: OperationName,
        timeout_config: &PyTimeoutConfig,
    ) -> PyResult<(TransactionState, u64, Option<Deadline>)> {
        session.state.ensure_connection_usable()?;

        if let Some(elapsed) = session.retire_expired_lifetime() {
            return Err(transaction_timeout_error(elapsed, operation, false));
        }

        let previous_state = session.state;
        let active_deadline = earliest_deadline(
            deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout),
            session.lifetime_deadline,
        );
        let connection = session
            .conn
            .as_mut()
            .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
        connection.begin_operation();
        let epoch = session.enter_in_flight(TransactionState::Executing);
        Ok((previous_state, epoch, active_deadline))
    }

    fn finish_data_operation<T>(
        session: &mut TransactionSession,
        previous_state: TransactionState,
        operation_name: OperationName,
        operation: Result<Result<PyResult<T>, PyErr>, DeadlineElapsed>,
    ) -> PyResult<T> {
        let (result, driver_panicked) = match operation {
            Err(elapsed) => {
                if let Some(connection) = session.conn.as_mut() {
                    connection.mark_unusable();
                }
                session.conn.take();
                session.transition_to(TransactionState::Failed);
                return Err(transaction_timeout_error(elapsed, operation_name, true));
            }
            Ok(Ok(result)) => (result, false),
            Ok(Err(driver_panic)) => (Err(driver_panic), true),
        };

        let connection_broken = match session.conn.as_mut() {
            Some(connection) => {
                if driver_panicked {
                    connection.mark_unusable();
                } else {
                    connection.finish_operation(&result);
                }
                driver_panicked || !connection.is_reusable()
            }
            None => true,
        };

        if connection_broken {
            session.conn.take();
            session.transition_to(TransactionState::Failed);
        } else {
            session.transition_to(previous_state);
        }

        result
    }

    async fn execute_transaction_command(
        session: &Arc<AsyncMutex<TransactionSession>>,
        command: TransactionCommand,
        timeout_config: &PyTimeoutConfig,
    ) -> PyResult<()> {
        let mut cancellation_guard = TransactionCancellationGuard::new(Arc::clone(session));
        let mut session = session.lock().await;
        command.validate(session.state)?;
        if session.conn.is_none() {
            return Err(PyRuntimeError::new_err("Connection is not established"));
        }

        if matches!(command, TransactionCommand::Commit)
            && let Some(elapsed) = session.retire_expired_lifetime()
        {
            return Err(transaction_timeout_error(
                elapsed,
                OperationName::Commit,
                false,
            ));
        }

        let deadline = command_deadline(command, timeout_config, session.lifetime_deadline);
        if let Some(connection) = session.conn.as_mut() {
            connection.begin_operation();
        }
        let epoch = session.enter_in_flight(command.in_flight_state());
        cancellation_guard.arm(epoch);

        let operation = match session.conn.as_mut() {
            Some(conn_ref) => {
                run_until(
                    deadline,
                    catch_driver_panic(async {
                        conn_ref
                            .simple_query(command.sql())
                            .await
                            .map_err(|error| create_sql_error(error, command.error_context()))?
                            .into_results()
                            .await
                            .map_err(|error| create_sql_error(error, command.error_context()))?;
                        Ok(())
                    }),
                )
                .await
            }
            None => Ok(Ok(Err(PyRuntimeError::new_err(
                "Connection is not established",
            )))),
        };

        let command_result = match operation {
            Err(elapsed) => {
                if let Some(connection) = session.conn.as_mut() {
                    connection.mark_unusable();
                }
                session.conn.take();
                session.transition_to(TransactionState::Failed);
                let timeout = transaction_timeout_error(
                    elapsed,
                    command.operation_name(),
                    matches!(command, TransactionCommand::Commit),
                );
                if matches!(command, TransactionCommand::Commit) {
                    create_commit_outcome_unknown(timeout).and_then(Err)
                } else {
                    Err(timeout)
                }
            }
            Ok(Err(driver_panic)) => {
                if let Some(connection) = session.conn.as_mut() {
                    connection.mark_unusable();
                }
                session.conn.take();
                session.transition_to(TransactionState::Failed);
                if matches!(command, TransactionCommand::Commit) {
                    create_commit_outcome_unknown(driver_panic).and_then(Err)
                } else {
                    Err(driver_panic)
                }
            }
            Ok(Ok(result)) => {
                if let Some(connection) = session.conn.as_mut() {
                    connection.finish_operation(&result);
                }

                match result {
                    Ok(()) => {
                        session.transition_to(command.completed_state());
                        if matches!(command, TransactionCommand::Begin) {
                            session.lifetime_deadline = deadline_from(
                                TimeoutPhase::Transaction,
                                timeout_config.transaction_timeout,
                            );
                        }
                        let release_pool_lease = command.releases_pool_lease()
                            && session
                                .conn
                                .as_ref()
                                .is_some_and(TransactionConnection::is_pooled);
                        if release_pool_lease {
                            session.conn.take();
                        }
                        Ok(())
                    }
                    Err(error) => {
                        session.conn.take();
                        session.transition_to(TransactionState::Failed);
                        if matches!(command, TransactionCommand::Commit)
                            && !is_deterministic_commit_rejection(&error)
                        {
                            create_commit_outcome_unknown(error).and_then(Err)
                        } else {
                            Err(error)
                        }
                    }
                }
            }
        };
        cancellation_guard.disarm();
        command_result
    }

    async fn ensure_connected_inner(
        session: &Arc<AsyncMutex<TransactionSession>>,
        config: &Arc<Config>,
        azure_credential: Option<&Arc<PyAzureCredential>>,
        pool_source: Option<&SharedPoolSource>,
        timeout_config: &PyTimeoutConfig,
        operation: OperationName,
    ) -> PyResult<()> {
        let mut session = session.lock().await;
        session.state.ensure_connection_usable()?;

        if session.conn.is_some() {
            return Ok(());
        }

        let mut connection = if let Some(source) = pool_source {
            let pool = ensure_pool_initialized_with_auth(
                Arc::clone(&source.pool),
                Arc::clone(config),
                &source.pool_config,
                &source.timeout_config,
                azure_credential.cloned(),
                operation,
            )
            .await?;
            let lease =
                acquire_owned_connection(&pool, operation, source.timeout_config.acquire_timeout)
                    .await?;
            TransactionConnection::Pooled(lease)
        } else {
            let direct = connect_client_with_timeout(
                config,
                azure_credential,
                timeout_config.connect_timeout,
                operation,
            )
            .await?;
            TransactionConnection::Direct(direct)
        };

        connection.prepare_for_checkout();
        session.conn = Some(connection);
        Ok(())
    }
}

#[cfg(test)]
mod cancellation_retirement_tests {
    use super::{TransactionCommand, TransactionSession, TransactionState, command_deadline};
    use crate::deadline::{Deadline, TimeoutPhase};
    use crate::timeout_config::PyTimeoutConfig;
    use std::time::Duration;
    use tokio::time::Instant;

    #[test]
    fn matching_in_flight_epoch_becomes_failed() {
        let mut session = TransactionSession::default();
        let epoch = session.enter_in_flight(TransactionState::Executing);

        session.retire_cancelled_operation(epoch);

        assert_eq!(session.state, TransactionState::Failed);
        assert!(session.conn.is_none());
    }

    #[test]
    fn stale_epoch_cannot_retire_a_newer_operation() {
        let mut session = TransactionSession::default();
        let stale_epoch = session.enter_in_flight(TransactionState::Executing);
        let current_epoch = session.enter_in_flight(TransactionState::Committing);

        session.retire_cancelled_operation(stale_epoch);

        assert_eq!(session.operation_epoch, current_epoch);
        assert_eq!(session.state, TransactionState::Committing);
    }

    #[test]
    fn matching_epoch_does_not_change_a_terminal_state() {
        let mut session = TransactionSession::default();
        let epoch = session.enter_in_flight(TransactionState::Committing);
        session.transition_to(TransactionState::Committed);

        session.retire_cancelled_operation(epoch);

        assert_eq!(session.state, TransactionState::Committed);
    }

    #[test]
    fn repeated_retirement_is_idempotent() {
        let mut session = TransactionSession::default();
        let epoch = session.enter_in_flight(TransactionState::RollingBack);

        session.retire_cancelled_operation(epoch);
        session.retire_cancelled_operation(epoch);

        assert_eq!(session.state, TransactionState::Failed);
        assert!(session.conn.is_none());
    }

    #[test]
    fn expired_lifetime_retires_only_the_current_session_state() {
        let mut session = TransactionSession {
            state: TransactionState::Active,
            operation_epoch: 41,
            lifetime_deadline: Some(Deadline {
                at: Instant::now() - Duration::from_millis(1),
                timeout: Duration::from_millis(250),
                phase: TimeoutPhase::Transaction,
            }),
            ..TransactionSession::default()
        };

        let elapsed = session
            .retire_expired_lifetime()
            .expect("the lifetime must be expired");

        assert_eq!(elapsed.phase, TimeoutPhase::Transaction);
        assert_eq!(elapsed.timeout, Duration::from_millis(250));
        assert_eq!(session.operation_epoch, 41);
        assert_eq!(session.state, TransactionState::Failed);
        assert!(session.conn.is_none());
        assert!(session.lifetime_deadline.is_none());
    }

    #[test]
    fn rollback_deadline_is_independent_of_transaction_lifetime() {
        let mut timeout_config = PyTimeoutConfig::explicit_default();
        timeout_config.operation_timeout = Some(Duration::from_millis(10));
        timeout_config.rollback_timeout = Some(Duration::from_secs(3));
        let lifetime = Deadline {
            at: Instant::now() + Duration::from_millis(1),
            timeout: Duration::from_millis(250),
            phase: TimeoutPhase::Transaction,
        };

        let selected = command_deadline(
            TransactionCommand::Rollback,
            &timeout_config,
            Some(lifetime),
        )
        .expect("rollback must have its own deadline");

        assert_eq!(selected.phase, TimeoutPhase::Rollback);
        assert_eq!(selected.timeout, Duration::from_secs(3));
    }

    #[test]
    fn every_terminal_state_clears_the_lifetime_deadline() {
        for state in [
            TransactionState::Idle,
            TransactionState::Committed,
            TransactionState::RolledBack,
            TransactionState::Failed,
            TransactionState::Closing,
        ] {
            let mut session = TransactionSession {
                lifetime_deadline: Some(Deadline {
                    at: Instant::now() + Duration::from_secs(1),
                    timeout: Duration::from_secs(1),
                    phase: TimeoutPhase::Transaction,
                }),
                ..TransactionSession::default()
            };

            session.transition_to(state);

            assert_eq!(session.state, state);
            assert!(session.lifetime_deadline.is_none());
        }
    }
}
