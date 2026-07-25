use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_async_runtimes::tokio::future_into_py;
use std::sync::Arc;
use tiberius::{AuthMethod, Client, Config};
use tokio::net::TcpStream;
use tokio::sync::Mutex as AsyncMutex;
use tokio_util::compat::TokioAsyncReadCompatExt;

use crate::azure_auth::PyAzureCredential;
use crate::batch::{execute_batch_on_connection, parse_batch_items, query_batch_on_connection};
use crate::connection_config::config_from_ado_string;
use crate::helpers::{
    catch_driver_panic, execute_unparameterized_command, requires_direct_batch, wrap_query_stream,
};
use crate::parameter_conversion::{convert_parameters_to_fast, params_as_sql_refs};
use crate::ssl_config::PySslConfig;
use crate::types::{create_connection_error, create_sql_error};

/// Type for a single direct connection (not pooled)
type SingleConnectionType = Client<tokio_util::compat::Compat<TcpStream>>;

/// Authoritative transaction lifecycle stored in Rust.
///
/// In-flight states are written before awaiting the TDS command. If Python
/// cancels that future, the object remains fail-closed until `close()` drops
/// the physical connection instead of guessing the server-side outcome.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TransactionState {
    Idle,
    Beginning,
    Active,
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
            | Self::Committing
            | Self::RollingBack
            | Self::Failed
            | Self::Closing => Err(PyRuntimeError::new_err(
                "Transaction state is indeterminate; call close() before reuse",
            )),
        }
    }
}

/// The client and its transaction state share one mutex so validation, the
/// wire command, response consumption, and the final transition are atomic
/// with respect to every concurrent transaction operation.
struct TransactionSession {
    conn: Option<SingleConnectionType>,
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

/// Bundles the three cloned handles needed for async transaction operations.
struct TransactionHandles {
    session: Arc<AsyncMutex<TransactionSession>>,
    config: Arc<Config>,
    azure_credential: Option<PyAzureCredential>,
}

impl TransactionHandles {
    async fn ensure_connected(&self) -> PyResult<()> {
        Transaction::ensure_connected_inner(
            &self.session,
            &self.config,
            self.azure_credential.as_ref(),
        )
        .await
    }
}

/// A single dedicated connection (not pooled) for transaction support.
/// This holds one physical database connection that persists across queries,
/// allowing SQL Server transactions (BEGIN/COMMIT/ROLLBACK) to work correctly.
#[pyclass(name = "Transaction")]
pub struct Transaction {
    session: Arc<AsyncMutex<TransactionSession>>,
    config: Arc<Config>,
    _ssl_config: Option<PySslConfig>,
    azure_credential: Option<PyAzureCredential>,
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
        // Store the original server parameter for validation before it gets reassigned
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
            } else if azure_credential.is_some() {
                // Azure authentication will be set up dynamically during connection
                // No authentication is set on config here since we need to acquire tokens asynchronously
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

        // Validate authentication configuration when using individual parameters
        if server_param.is_some() && username.is_none() && azure_credential.is_none() {
            return Err(PyValueError::new_err(
                "When using individual connection parameters, either username/password or azure_credential must be provided",
            ));
        }

        Ok(Transaction {
            session: Arc::new(AsyncMutex::new(TransactionSession::default())),
            config: Arc::new(config),
            _ssl_config: ssl_config,
            azure_credential,
        })
    }

    /// Execute a SQL query that returns rows (SELECT statements)
    /// Returns rows as QueryStream
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
                let conn_ref = session
                    .conn
                    .as_mut()
                    .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;

                let tiberius_params = params_as_sql_refs(&fast_parameters);
                let operation = catch_driver_panic(async {
                    conn_ref
                        .query(&query, &tiberius_params)
                        .await
                        .map_err(|e| create_sql_error(e, "Query execution failed"))?
                        .into_first_result()
                        .await
                        .map_err(|e| create_sql_error(e, "Failed to get results"))
                })
                .await;
                match operation {
                    Ok(result) => result?,
                    Err(driver_panic) => {
                        session.conn.take();
                        session.state = TransactionState::Failed;
                        return Err(driver_panic);
                    }
                }
            };

            wrap_query_stream(execution_result)
        })
    }

    /// Execute a raw (non-prepared statement) SQL query
    /// Returns rows as QueryStream
    #[pyo3(signature = (query))]
    pub fn simple_query<'p>(&self, py: Python<'p>, query: String) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            handles.ensure_connected().await?;

            let execution_result = {
                let mut session = handles.session.lock().await;
                let conn_ref = session
                    .conn
                    .as_mut()
                    .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;

                let operation = catch_driver_panic(async {
                    conn_ref
                        .simple_query(&query)
                        .await
                        .map_err(|e| create_sql_error(e, "Query execution failed"))?
                        .into_first_result()
                        .await
                        .map_err(|e| create_sql_error(e, "Failed to get results"))
                })
                .await;
                match operation {
                    Ok(result) => result?,
                    Err(driver_panic) => {
                        session.conn.take();
                        session.state = TransactionState::Failed;
                        return Err(driver_panic);
                    }
                }
            };

            wrap_query_stream(execution_result)
        })
    }

    /// Execute a SQL command that doesn't return rows (INSERT/UPDATE/DELETE/DDL)
    /// Returns the number of affected rows
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
                let conn_ref = session
                    .conn
                    .as_mut()
                    .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;

                let operation = catch_driver_panic(async {
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
                .await;
                match operation {
                    Ok(result) => result?,
                    Err(driver_panic) => {
                        session.conn.take();
                        session.state = TransactionState::Failed;
                        return Err(driver_panic);
                    }
                }
            };

            Ok(affected)
        })
    }

    /// Execute multiple batch commands on the transaction connection.
    /// Does NOT wrap in automatic transaction - use begin/commit/rollback manually.
    /// Returns list of row counts affected by each command.
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
                let conn_ref = session
                    .conn
                    .as_mut()
                    .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;

                let operation =
                    catch_driver_panic(execute_batch_on_connection(conn_ref, batch_commands)).await;
                match operation {
                    Ok(result) => result?,
                    Err(driver_panic) => {
                        session.conn.take();
                        session.state = TransactionState::Failed;
                        return Err(driver_panic);
                    }
                }
            };

            Python::attach(|py| {
                let py_list = PyList::new(py, all_results)?;
                Ok(py_list.into_any().unbind())
            })
        })
    }

    /// Execute multiple batch queries on the transaction connection.
    /// Returns list of QueryStream objects, one per query.
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
                let conn_ref = session
                    .conn
                    .as_mut()
                    .ok_or_else(|| PyRuntimeError::new_err("Connection is not established"))?;

                let operation =
                    catch_driver_panic(query_batch_on_connection(conn_ref, batch_queries)).await;
                match operation {
                    Ok(result) => result?,
                    Err(driver_panic) => {
                        session.conn.take();
                        session.state = TransactionState::Failed;
                        return Err(driver_panic);
                    }
                }
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

    /// Begin a transaction
    pub fn begin<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            handles.ensure_connected().await?;
            Self::execute_transaction_command(&handles.session, TransactionCommand::Begin).await
        })
    }

    /// Commit the current transaction
    pub fn commit<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);

        future_into_py(py, async move {
            Self::execute_transaction_command(&session, TransactionCommand::Commit).await
        })
    }

    /// Rollback the current transaction
    pub fn rollback<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);

        future_into_py(py, async move {
            Self::execute_transaction_command(&session, TransactionCommand::Rollback).await
        })
    }

    /// Close the connection
    pub fn close<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let session = Arc::clone(&self.session);

        future_into_py(py, async move {
            let mut session = session.lock().await;
            let previous_state = session.state;
            let mut conn = session.conn.take();
            session.state = TransactionState::Closing;

            if previous_state == TransactionState::Active
                && let Some(conn_ref) = conn.as_mut()
                && let Ok(stream) = conn_ref
                    .simple_query("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
                    .await
            {
                let _ = stream.into_results().await;
            }

            // Dropping the physical connection guarantees server-side rollback even
            // if the best-effort command could not be acknowledged.
            drop(conn);
            session.state = TransactionState::Idle;
            Ok(())
        })
    }

    /// Check if connected
    pub fn is_connected(&self) -> bool {
        // Derive connectivity from the actual connection object rather than a stale flag.
        // If the lock is held (query in progress), the connection is active → true.
        // If we can peek and it's Some, connected. If None, not connected.
        match self.session.try_lock() {
            Ok(session) => session.conn.is_some(),
            Err(_) => true,
        }
    }
}

impl Transaction {
    /// Clone the three fields needed for async transaction operations into a single struct.
    fn clone_handles(&self) -> TransactionHandles {
        TransactionHandles {
            session: Arc::clone(&self.session),
            config: Arc::clone(&self.config),
            azure_credential: self.azure_credential.clone(),
        }
    }

    /// Execute a transaction control command (BEGIN/COMMIT/ROLLBACK).
    async fn execute_transaction_command(
        session: &Arc<AsyncMutex<TransactionSession>>,
        command: TransactionCommand,
    ) -> PyResult<()> {
        let mut session = session.lock().await;
        command.validate(session.state)?;
        session.state = command.in_flight_state();

        let operation = match session.conn.as_mut() {
            Some(conn_ref) => {
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

        let result = match operation {
            Ok(result) => result,
            Err(driver_panic) => Err(driver_panic),
        };

        match result {
            Ok(()) => {
                session.state = command.completed_state();
                Ok(())
            }
            Err(error) => {
                session.conn.take();
                session.state = TransactionState::Failed;
                Err(error)
            }
        }
    }

    /// Ensure connection is established. Initializes connection if needed.
    /// Returns error if connection fails.
    async fn ensure_connected_inner(
        session: &Arc<AsyncMutex<TransactionSession>>,
        config: &Arc<Config>,
        azure_credential: Option<&PyAzureCredential>,
    ) -> PyResult<()> {
        let mut session = session.lock().await;
        session.state.ensure_connection_usable()?;

        if session.conn.is_none() {
            let address = config.get_addr();
            let tcp_stream = TcpStream::connect(&address).await.map_err(|e| {
                create_connection_error(format!("Failed to connect to server {address}: {e}"))
            })?;

            // Disable Nagle algorithm — identical to pool connections in pool_manager.rs.
            // Without this, small TDS packets (common for parameterised queries) may be
            // buffered by the OS for up to 200 ms before transmission.
            tcp_stream.set_nodelay(true).map_err(|e| {
                create_connection_error(format!("Failed to set TCP_NODELAY: {}", e))
            })?;

            let compat_stream = tcp_stream.compat();

            // Configure authentication
            let mut auth_config = (**config).clone();
            if let Some(azure_cred) = azure_credential {
                let auth_method = azure_cred.to_auth_method().await?;
                auth_config.authentication(auth_method);
            }

            let new_conn: SingleConnectionType = Client::connect(auth_config, compat_stream)
                .await
                .map_err(|e| create_sql_error(e, "Failed to connect to database"))?;
            session.conn = Some(new_conn);
        }

        Ok(())
    }
}
