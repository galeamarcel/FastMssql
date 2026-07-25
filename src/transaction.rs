use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_async_runtimes::tokio::future_into_py;
use std::ops::{Deref, DerefMut};
use std::sync::Arc;
use tiberius::{AuthMethod, Client, Config};
use tokio::net::TcpStream;
use tokio::sync::{Mutex as AsyncMutex, RwLock};
use tokio_util::compat::TokioAsyncReadCompatExt;

use crate::azure_auth::PyAzureCredential;
use crate::batch::{execute_batch_on_connection, parse_batch_items, query_batch_on_connection};
use crate::connection_config::config_from_ado_string;
use crate::helpers::{
    catch_driver_panic, execute_unparameterized_command, requires_direct_batch, wrap_query_stream,
};
use crate::parameter_conversion::{convert_parameters_to_fast, params_as_sql_refs};
use crate::pool_config::PyPoolConfig;
use crate::pool_manager::{
    ConnectionPool, OwnedPooledConnection, acquire_owned_connection,
    ensure_pool_initialized_with_auth,
};
use crate::ssl_config::PySslConfig;
use crate::types::{
    SqlError, create_commit_outcome_unknown, create_connection_error, create_sql_error,
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
}

impl Default for TransactionSession {
    fn default() -> Self {
        Self {
            conn: None,
            state: TransactionState::Idle,
        }
    }
}

#[derive(Clone)]
struct SharedPoolSource {
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    pool_config: PyPoolConfig,
}

#[derive(Clone, Copy)]
enum TransactionCommand {
    Begin,
    Commit,
    Rollback,
}

impl TransactionCommand {
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

struct TransactionHandles {
    session: Arc<AsyncMutex<TransactionSession>>,
    config: Arc<Config>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    pool_source: Option<SharedPoolSource>,
}

impl TransactionHandles {
    async fn ensure_connected(&self) -> PyResult<()> {
        Transaction::ensure_connected_inner(
            &self.session,
            &self.config,
            self.azure_credential.as_ref(),
            self.pool_source.as_ref(),
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
}

#[pymethods]
impl Transaction {
    #[new]
    #[pyo3(signature = (connection_string = None, ssl_config = None, azure_credential = None, server = None, database = None, username = None, password = None, application_intent = None, port = None, instance_name = None, application_name = None))]
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
        })
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
            handles.ensure_connected().await?;

            let execution_result = {
                let mut session = handles.session.lock().await;
                let previous_state = Self::begin_data_operation(&mut session)?;
                let operation = {
                    let conn_ref = session
                        .conn
                        .as_mut()
                        .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
                    let tiberius_params = params_as_sql_refs(&fast_parameters);
                    catch_driver_panic(async {
                        conn_ref
                            .query(&query, &tiberius_params)
                            .await
                            .map_err(|e| create_sql_error(e, "Query execution failed"))?
                            .into_first_result()
                            .await
                            .map_err(|e| create_sql_error(e, "Failed to get results"))
                    })
                    .await
                };
                Self::finish_data_operation(&mut session, previous_state, operation)?
            };

            wrap_query_stream(execution_result)
        })
    }

    /// Execute a raw (non-prepared) SQL query.
    #[pyo3(signature = (query))]
    pub fn simple_query<'p>(&self, py: Python<'p>, query: String) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            handles.ensure_connected().await?;

            let execution_result = {
                let mut session = handles.session.lock().await;
                let previous_state = Self::begin_data_operation(&mut session)?;
                let operation = {
                    let conn_ref = session
                        .conn
                        .as_mut()
                        .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
                    catch_driver_panic(async {
                        conn_ref
                            .simple_query(&query)
                            .await
                            .map_err(|e| create_sql_error(e, "Query execution failed"))?
                            .into_first_result()
                            .await
                            .map_err(|e| create_sql_error(e, "Failed to get results"))
                    })
                    .await
                };
                Self::finish_data_operation(&mut session, previous_state, operation)?
            };

            wrap_query_stream(execution_result)
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
            handles.ensure_connected().await?;

            let affected = {
                let mut session = handles.session.lock().await;
                let previous_state = Self::begin_data_operation(&mut session)?;
                let operation = {
                    let conn_ref = session
                        .conn
                        .as_mut()
                        .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
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
                                .map_err(|e| create_sql_error(e, "Command execution failed"))
                        }
                    })
                    .await
                };
                Self::finish_data_operation(&mut session, previous_state, operation)?
            };

            Ok(affected)
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
            handles.ensure_connected().await?;

            let all_results = {
                let mut session = handles.session.lock().await;
                let previous_state = Self::begin_data_operation(&mut session)?;
                let operation = {
                    let conn_ref = session
                        .conn
                        .as_mut()
                        .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
                    catch_driver_panic(execute_batch_on_connection(conn_ref, batch_commands)).await
                };
                Self::finish_data_operation(&mut session, previous_state, operation)?
            };

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
            handles.ensure_connected().await?;

            let all_results = {
                let mut session = handles.session.lock().await;
                let previous_state = Self::begin_data_operation(&mut session)?;
                let operation = {
                    let conn_ref = session
                        .conn
                        .as_mut()
                        .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
                    catch_driver_panic(query_batch_on_connection(conn_ref, batch_queries)).await
                };
                Self::finish_data_operation(&mut session, previous_state, operation)?
            };

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
            handles.ensure_connected().await?;
            Self::execute_transaction_command(&handles.session, TransactionCommand::Begin).await
        })
    }

    pub fn commit<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);
        future_into_py(py, async move {
            Self::execute_transaction_command(&session, TransactionCommand::Commit).await
        })
    }

    pub fn rollback<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);
        future_into_py(py, async move {
            Self::execute_transaction_command(&session, TransactionCommand::Rollback).await
        })
    }

    /// Release the direct socket or shared pool lease.
    pub fn close<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);

        future_into_py(py, async move {
            let mut session = session.lock().await;
            let previous_state = session.state;
            let mut conn = session.conn.take();
            session.state = TransactionState::Closing;

            if let Some(conn_ref) = conn.as_mut() {
                if previous_state == TransactionState::Active {
                    conn_ref.begin_operation();
                    let rollback = catch_driver_panic(async {
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
                    })
                    .await;

                    match rollback {
                        Ok(result @ Ok(())) => conn_ref.finish_operation(&result),
                        Ok(Err(_)) | Err(_) => conn_ref.mark_unusable(),
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
            session.state = TransactionState::Idle;
            Ok(())
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
        azure_credential: Option<Arc<PyAzureCredential>>,
    ) -> Self {
        Self {
            session: Arc::new(AsyncMutex::new(TransactionSession::default())),
            config,
            _ssl_config: None,
            azure_credential,
            pool_source: Some(SharedPoolSource { pool, pool_config }),
        }
    }

    fn clone_handles(&self) -> TransactionHandles {
        TransactionHandles {
            session: Arc::clone(&self.session),
            config: Arc::clone(&self.config),
            azure_credential: self.azure_credential.clone(),
            pool_source: self.pool_source.clone(),
        }
    }

    fn begin_data_operation(session: &mut TransactionSession) -> PyResult<TransactionState> {
        session.state.ensure_connection_usable()?;
        let previous_state = session.state;
        let connection = session
            .conn
            .as_mut()
            .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;
        connection.begin_operation();
        session.state = TransactionState::Executing;
        Ok(previous_state)
    }

    fn finish_data_operation<T>(
        session: &mut TransactionSession,
        previous_state: TransactionState,
        operation: Result<PyResult<T>, PyErr>,
    ) -> PyResult<T> {
        let (result, driver_panicked) = match operation {
            Ok(result) => (result, false),
            Err(driver_panic) => (Err(driver_panic), true),
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
            session.state = TransactionState::Failed;
        } else {
            session.state = previous_state;
        }

        result
    }

    async fn execute_transaction_command(
        session: &Arc<AsyncMutex<TransactionSession>>,
        command: TransactionCommand,
    ) -> PyResult<()> {
        let mut session = session.lock().await;
        command.validate(session.state)?;
        if session.conn.is_none() {
            return Err(PyRuntimeError::new_err("Connection is not established"));
        }
        session.state = command.in_flight_state();

        let operation = match session.conn.as_mut() {
            Some(conn_ref) => {
                conn_ref.begin_operation();
                catch_driver_panic(async {
                    conn_ref
                        .simple_query(command.sql())
                        .await
                        .map_err(|error| create_sql_error(error, command.error_context()))?
                        .into_results()
                        .await
                        .map_err(|error| create_sql_error(error, command.error_context()))?;
                    Ok(())
                })
                .await
            }
            None => Ok(Err(PyRuntimeError::new_err(
                "Connection is not established",
            ))),
        };

        let (result, driver_panicked) = match operation {
            Ok(result) => (result, false),
            Err(driver_panic) => (Err(driver_panic), true),
        };

        if let Some(connection) = session.conn.as_mut() {
            if driver_panicked {
                connection.mark_unusable();
            } else {
                connection.finish_operation(&result);
            }
        }

        match result {
            Ok(()) => {
                session.state = command.completed_state();
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
                session.state = TransactionState::Failed;
                let public_error = if matches!(command, TransactionCommand::Commit)
                    && !is_deterministic_commit_rejection(&error)
                {
                    create_commit_outcome_unknown(error)?
                } else {
                    error
                };
                Err(public_error)
            }
        }
    }

    async fn ensure_connected_inner(
        session: &Arc<AsyncMutex<TransactionSession>>,
        config: &Arc<Config>,
        azure_credential: Option<&Arc<PyAzureCredential>>,
        pool_source: Option<&SharedPoolSource>,
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
                azure_credential.cloned(),
            )
            .await?;
            let lease = acquire_owned_connection(&pool).await?;
            TransactionConnection::Pooled(lease)
        } else {
            let address = config.get_addr();
            let tcp_stream = TcpStream::connect(&address).await.map_err(|error| {
                create_connection_error(format!("Failed to connect to server {address}: {error}"))
            })?;
            tcp_stream.set_nodelay(true).map_err(|error| {
                create_connection_error(format!("Failed to set TCP_NODELAY: {error}"))
            })?;

            let mut auth_config = (**config).clone();
            if let Some(azure_credential) = azure_credential {
                let auth_method = azure_credential.to_auth_method().await?;
                auth_config.authentication(auth_method);
            }

            let direct = Client::connect(auth_config, tcp_stream.compat())
                .await
                .map_err(|error| create_sql_error(error, "Failed to connect to database"))?;
            TransactionConnection::Direct(direct)
        };

        connection.prepare_for_checkout();
        session.conn = Some(connection);
        Ok(())
    }
}
