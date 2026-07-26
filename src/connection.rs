use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_async_runtimes::tokio::future_into_py;
use std::sync::Arc;
use tiberius::{AuthMethod, Config, Row};
use tokio::sync::RwLock;

use crate::azure_auth::PyAzureCredential;
use crate::batch::{bulk_insert, execute_batch, query_batch};
use crate::connection_config::config_from_ado_string;
use crate::deadline::{DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until};
use crate::helpers::{
    catch_driver_panic, execute_unparameterized_command, requires_connection_retirement,
    requires_direct_batch, wrap_query_stream,
};
use crate::lifecycle::ConnectionLifecycle;
use crate::lifecycle_config::{ConnectionLifecycleState, PyLifecycleConfig};
use crate::parameter_conversion::{FastParameter, convert_parameters_to_fast, params_as_sql_refs};
use crate::pool_config::PyPoolConfig;
use crate::pool_manager::{
    ConnectionPool, PooledOperationGuard, ensure_pool_initialized_with_auth,
    map_pool_checkout_error, timeout_error_or_metadata_failure,
};
use crate::ssl_config::PySslConfig;
use crate::timeout_config::PyTimeoutConfig;
use crate::transaction::Transaction;
use crate::types::{TimeoutErrorMetadata, create_sql_error};

const READINESS_QUERY: &str = "SELECT 1";

struct ConnectionHandles {
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    azure_credential: Option<Arc<PyAzureCredential>>,
}

fn reconcile_checkout_counts(
    get_started: u64,
    get_direct: u64,
    get_waited: u64,
    get_timed_out: u64,
) -> (u64, u64) {
    let completed = get_direct
        .saturating_add(get_waited)
        .saturating_add(get_timed_out);
    let reconciled_started = get_started.max(completed);
    (reconciled_started, reconciled_started - completed)
}

#[derive(Debug, Default)]
struct PoolStatisticsSnapshot {
    connected: bool,
    connections: u32,
    idle_connections: u32,
    get_started: u64,
    get_direct: u64,
    get_waited: u64,
    get_timed_out: u64,
    pending_gets: u64,
    get_wait_time_seconds: f64,
    connections_created: u64,
    connections_closed_broken: u64,
    connections_closed_invalid: u64,
    connections_closed_max_lifetime: u64,
    connections_closed_idle_timeout: u64,
}

impl PoolStatisticsSnapshot {
    fn from_state(state: bb8::State) -> Self {
        let statistics = state.statistics;
        let (get_started, pending_gets) = reconcile_checkout_counts(
            statistics.get_started,
            statistics.get_direct,
            statistics.get_waited,
            statistics.get_timed_out,
        );
        Self {
            connected: true,
            connections: state.connections,
            idle_connections: state.idle_connections,
            get_started,
            get_direct: statistics.get_direct,
            get_waited: statistics.get_waited,
            get_timed_out: statistics.get_timed_out,
            pending_gets,
            get_wait_time_seconds: statistics.get_wait_time.as_secs_f64(),
            connections_created: statistics.connections_created,
            connections_closed_broken: statistics.connections_closed_broken,
            connections_closed_invalid: statistics.connections_closed_invalid,
            connections_closed_max_lifetime: statistics.connections_closed_max_lifetime,
            connections_closed_idle_timeout: statistics.connections_closed_idle_timeout,
        }
    }
}

impl ConnectionHandles {
    fn ensure_connected(
        &self,
        operation: OperationName,
    ) -> impl std::future::Future<Output = PyResult<ConnectionPool>> + '_ {
        ensure_pool_initialized_with_auth(
            self.pool.clone(),
            self.config.clone(),
            &self.pool_config,
            &self.timeout_config,
            self.azure_credential.clone(),
            operation,
        )
    }
}

#[pyclass(name = "Connection")]
pub struct PyConnection {
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    lifecycle_config: PyLifecycleConfig,
    _ssl_config: Option<PySslConfig>,
    azure_credential: Option<Arc<PyAzureCredential>>,
}

impl PyConnection {
    fn operation_timeout_error(
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

    fn clone_handles(&self) -> ConnectionHandles {
        ConnectionHandles {
            pool: Arc::clone(&self.pool),
            config: Arc::clone(&self.config),
            pool_config: self.pool_config.clone(),
            timeout_config: self.timeout_config.clone(),
            lifecycle: Arc::clone(&self.lifecycle),
            azure_credential: self.azure_credential.clone(),
        }
    }

    async fn get_pool_connection<'a>(
        pool: &'a ConnectionPool,
        timeouts: &PyTimeoutConfig,
        operation: OperationName,
    ) -> PyResult<PooledOperationGuard<'a>> {
        let connection = pool
            .get()
            .await
            .map_err(|error| map_pool_checkout_error(error, operation, timeouts.acquire_timeout))?;
        Ok(PooledOperationGuard::new(connection))
    }

    async fn validate_pool_readiness(
        pool: &ConnectionPool,
        timeouts: &PyTimeoutConfig,
        operation_name: OperationName,
    ) -> PyResult<()> {
        let mut connection = Self::get_pool_connection(pool, timeouts, operation_name).await?;
        let deadline = deadline_from(TimeoutPhase::Operation, timeouts.operation_timeout);
        let operation = run_until(
            deadline,
            catch_driver_panic(async {
                connection
                    .simple_query(READINESS_QUERY)
                    .await
                    .map_err(|error| create_sql_error(error, "Connection readiness query failed"))?
                    .into_results()
                    .await
                    .map_err(|error| {
                        create_sql_error(error, "Failed to consume connection readiness response")
                    })?;
                Ok::<(), PyErr>(())
            }),
        )
        .await;

        match operation {
            Err(elapsed) => Err(Self::operation_timeout_error(
                elapsed,
                operation_name,
                false,
            )),
            Ok(Ok(result)) => {
                connection.complete_with_result_and_retirement(&result, false);
                result
            }
            Ok(Err(driver_panic)) => Err(driver_panic),
        }
    }

    #[inline]
    async fn execute_query_async_gil_free(
        pool: &ConnectionPool,
        timeouts: &PyTimeoutConfig,
        operation_name: OperationName,
        query: &str,
        parameters: &[FastParameter],
    ) -> PyResult<Vec<Row>> {
        let retire_after_operation = requires_connection_retirement(query);
        let mut conn = Self::get_pool_connection(pool, timeouts, operation_name).await?;
        let tiberius_params = params_as_sql_refs(parameters);
        let deadline = deadline_from(TimeoutPhase::Operation, timeouts.operation_timeout);

        let operation = run_until(
            deadline,
            catch_driver_panic(async {
                let stream = conn
                    .query(query, &tiberius_params)
                    .await
                    .map_err(|e| create_sql_error(e, "Query execution failed"))?;

                stream
                    .into_first_result()
                    .await
                    .map_err(|e| create_sql_error(e, "Failed to get results"))
            }),
        )
        .await;

        match operation {
            Err(elapsed) => Err(Self::operation_timeout_error(elapsed, operation_name, true)),
            Ok(Ok(result)) => {
                conn.complete_with_result_and_retirement(&result, retire_after_operation);
                result
            }
            Ok(Err(driver_panic)) => Err(driver_panic),
        }
    }

    #[inline]
    async fn execute_simple_query_async_gil_free(
        pool: &ConnectionPool,
        timeouts: &PyTimeoutConfig,
        operation_name: OperationName,
        query: &str,
    ) -> PyResult<Vec<Row>> {
        let retire_after_operation = requires_connection_retirement(query);
        let mut conn = Self::get_pool_connection(pool, timeouts, operation_name).await?;
        let deadline = deadline_from(TimeoutPhase::Operation, timeouts.operation_timeout);

        let operation = run_until(
            deadline,
            catch_driver_panic(async {
                let stream = conn
                    .simple_query(query)
                    .await
                    .map_err(|e| create_sql_error(e, "Query execution failed"))?;

                stream
                    .into_first_result()
                    .await
                    .map_err(|e| create_sql_error(e, "Failed to get results"))
            }),
        )
        .await;

        match operation {
            Err(elapsed) => Err(Self::operation_timeout_error(elapsed, operation_name, true)),
            Ok(Ok(result)) => {
                conn.complete_with_result_and_retirement(&result, retire_after_operation);
                result
            }
            Ok(Err(driver_panic)) => Err(driver_panic),
        }
    }

    #[inline]
    async fn execute_command_async_gil_free(
        pool: &ConnectionPool,
        timeouts: &PyTimeoutConfig,
        operation_name: OperationName,
        query: &str,
        parameters: &[FastParameter],
    ) -> PyResult<u64> {
        let retire_after_operation = requires_connection_retirement(query);
        let mut conn = Self::get_pool_connection(pool, timeouts, operation_name).await?;
        let deadline = deadline_from(TimeoutPhase::Operation, timeouts.operation_timeout);
        let operation = run_until(
            deadline,
            catch_driver_panic(async {
                if parameters.is_empty() && requires_direct_batch(query) {
                    execute_unparameterized_command(&mut conn, query, "Command execution failed")
                        .await
                } else {
                    let tiberius_params = params_as_sql_refs(parameters);
                    conn.execute(query, &tiberius_params)
                        .await
                        .map(|result| result.rows_affected().iter().sum())
                        .map_err(|e| create_sql_error(e, "Command execution failed"))
                }
            }),
        )
        .await;

        match operation {
            Err(elapsed) => Err(Self::operation_timeout_error(elapsed, operation_name, true)),
            Ok(Ok(result)) => {
                conn.complete_with_result_and_retirement(&result, retire_after_operation);
                result
            }
            Ok(Err(driver_panic)) => Err(driver_panic),
        }
    }
}

#[pymethods]
impl PyConnection {
    #[new]
    #[pyo3(signature = (connection_string = None, pool_config = None, ssl_config = None, azure_credential = None, server = None, database = None, username = None, password = None, application_intent = None, port = None, instance_name = None, application_name = None, timeout_config = None, lifecycle_config = None))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        connection_string: Option<String>,
        pool_config: Option<PyPoolConfig>,
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
        lifecycle_config: Option<PyLifecycleConfig>,
    ) -> PyResult<Self> {
        let config = if let Some(conn_str) = connection_string {
            config_from_ado_string(&conn_str, ssl_config.as_ref())?
        } else if let Some(ref srv) = server {
            let mut config = Config::new();
            config.host(srv);
            if let Some(db) = database {
                config.database(&db);
            }
            if let Some(ref user) = username {
                if azure_credential.is_some() {
                    return Err(PyValueError::new_err(
                        "Cannot use both username/password and azure_credential.",
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
                            "Invalid application_intent '{}'",
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

        if server.is_some() && username.is_none() && azure_credential.is_none() {
            return Err(PyValueError::new_err(
                "Either username/password or azure_credential must be provided",
            ));
        }

        let original_pool_config = pool_config.unwrap_or_default();
        let effective_timeout = match timeout_config {
            Some(timeout_config) => timeout_config,
            None => PyTimeoutConfig::from_pool_compatibility(&original_pool_config)?,
        };
        let effective_pool_config = effective_timeout.align_pool_config(&original_pool_config);

        Ok(PyConnection {
            pool: Arc::new(RwLock::new(None)),
            config: Arc::new(config),
            pool_config: effective_pool_config,
            timeout_config: effective_timeout,
            lifecycle: ConnectionLifecycle::new(),
            lifecycle_config: lifecycle_config.unwrap_or_default(),
            _ssl_config: ssl_config,
            azure_credential: azure_credential.map(Arc::new),
        })
    }

    #[pyo3(signature = (query, parameters=None))]
    pub fn query<'p>(
        &self,
        py: Python<'p>,
        query: String,
        parameters: Option<&Bound<PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let fast_parameters = convert_parameters_to_fast(parameters, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::Query, true)?;
            permit
                .run(async move {
                    let pool_ref = handles.ensure_connected(OperationName::Query).await?;
                    let execution_result = Self::execute_query_async_gil_free(
                        &pool_ref,
                        &handles.timeout_config,
                        OperationName::Query,
                        &query,
                        &fast_parameters,
                    )
                    .await?;
                    wrap_query_stream(execution_result)
                })
                .await
        })
    }

    #[pyo3(signature = (query))]
    pub fn simple_query<'p>(&self, py: Python<'p>, query: String) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::SimpleQuery, true)?;
            permit
                .run(async move {
                    let pool_ref = handles.ensure_connected(OperationName::SimpleQuery).await?;
                    let execution_result = Self::execute_simple_query_async_gil_free(
                        &pool_ref,
                        &handles.timeout_config,
                        OperationName::SimpleQuery,
                        &query,
                    )
                    .await?;
                    wrap_query_stream(execution_result)
                })
                .await
        })
    }

    #[pyo3(signature = (query, parameters=None))]
    pub fn execute<'p>(
        &self,
        py: Python<'p>,
        query: String,
        parameters: Option<&Bound<PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let fast_parameters = convert_parameters_to_fast(parameters, py)?;
        let handles = self.clone_handles();

        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::Execute, true)?;
            permit
                .run(async move {
                    let pool_ref = handles.ensure_connected(OperationName::Execute).await?;
                    Self::execute_command_async_gil_free(
                        &pool_ref,
                        &handles.timeout_config,
                        OperationName::Execute,
                        &query,
                        &fast_parameters,
                    )
                    .await
                })
                .await
        })
    }

    /// Return whether this object currently owns a pool handle.
    ///
    /// This method performs no network I/O and does not prove SQL Server
    /// readiness. Use `ping()` for a live readiness check.
    pub fn is_connected<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let pool = self.pool.clone();
        future_into_py(py, async move {
            let connected = pool.read().await.is_some();
            Ok(connected)
        })
    }

    pub fn pool_stats<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let pool = self.pool.clone();
        let max_size = self.pool_config.max_size;
        let min_idle = self.pool_config.min_idle;

        future_into_py(py, async move {
            let snapshot = {
                let pool_guard = pool.read().await;
                if let Some(pool_ref) = pool_guard.as_ref() {
                    PoolStatisticsSnapshot::from_state(pool_ref.state())
                } else {
                    PoolStatisticsSnapshot::default()
                }
            };

            Python::try_attach(|py| {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("connected", snapshot.connected)?;
                dict.set_item("connections", snapshot.connections)?;
                dict.set_item("idle_connections", snapshot.idle_connections)?;
                dict.set_item(
                    "active_connections",
                    snapshot
                        .connections
                        .saturating_sub(snapshot.idle_connections),
                )?;
                dict.set_item("max_size", max_size)?;
                dict.set_item("min_idle", min_idle)?;
                dict.set_item("get_started", snapshot.get_started)?;
                dict.set_item("get_direct", snapshot.get_direct)?;
                dict.set_item("get_waited", snapshot.get_waited)?;
                dict.set_item("get_timed_out", snapshot.get_timed_out)?;
                dict.set_item("pending_gets", snapshot.pending_gets)?;
                dict.set_item("get_wait_time_seconds", snapshot.get_wait_time_seconds)?;
                dict.set_item("connections_created", snapshot.connections_created)?;
                dict.set_item(
                    "connections_closed_broken",
                    snapshot.connections_closed_broken,
                )?;
                dict.set_item(
                    "connections_closed_invalid",
                    snapshot.connections_closed_invalid,
                )?;
                dict.set_item(
                    "connections_closed_max_lifetime",
                    snapshot.connections_closed_max_lifetime,
                )?;
                dict.set_item(
                    "connections_closed_idle_timeout",
                    snapshot.connections_closed_idle_timeout,
                )?;
                Ok(dict.unbind())
            })
            .ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err("Failed to attach Python runtime thread")
            })?
        })
    }

    /// Create a transaction that reserves one lease from this connection's
    /// shared pool until COMMIT, ROLLBACK, or close.
    pub fn transaction(&self) -> Transaction {
        Transaction::from_pool(
            Arc::clone(&self.pool),
            Arc::clone(&self.config),
            self.pool_config.clone(),
            self.timeout_config.clone(),
            Arc::clone(&self.lifecycle),
            self.azure_credential.clone(),
        )
    }

    #[getter]
    pub fn timeout_config(&self) -> PyTimeoutConfig {
        self.timeout_config.clone()
    }

    #[getter]
    pub fn lifecycle_config(&self) -> PyLifecycleConfig {
        self.lifecycle_config.clone()
    }

    #[getter]
    pub fn lifecycle_state(&self) -> ConnectionLifecycleState {
        self.lifecycle.state()
    }

    pub fn __aenter__<'p>(slf: Bound<'p, Self>, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let handles = slf.borrow().clone_handles();
        let slf_clone = slf.clone().unbind();

        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::Connect, false)?;
            permit
                .run(async move {
                    let pool = handles.ensure_connected(OperationName::Connect).await?;
                    Self::validate_pool_readiness(
                        &pool,
                        &handles.timeout_config,
                        OperationName::Connect,
                    )
                    .await?;
                    Python::try_attach(|py| Ok(slf_clone.clone_ref(py))).ok_or_else(|| {
                        pyo3::exceptions::PyRuntimeError::new_err(
                            "Failed to attach Python runtime thread",
                        )
                    })?
                })
                .await
        })
    }

    pub fn __aexit__<'p>(
        &self,
        py: Python<'p>,
        _exc_type: Option<Bound<PyAny>>,
        _exc_value: Option<Bound<PyAny>>,
        _traceback: Option<Bound<PyAny>>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let pool = Arc::clone(&self.pool);
        let lifecycle = Arc::clone(&self.lifecycle);
        let lifecycle_config = self.lifecycle_config.clone();
        future_into_py(py, async move {
            lifecycle.shutdown(pool, lifecycle_config).await?;
            Ok(())
        })
    }

    /// Initialize the shared pool and, by default, verify SQL Server readiness.
    ///
    /// Set `validate=False` only for intentional lazy pool allocation. A lazy
    /// pool is not proof that SQL Server is reachable.
    #[pyo3(signature = (validate = true))]
    pub fn connect<'p>(&self, py: Python<'p>, validate: bool) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::Connect, false)?;
            permit
                .run(async move {
                    let pool = handles.ensure_connected(OperationName::Connect).await?;
                    if validate {
                        Self::validate_pool_readiness(
                            &pool,
                            &handles.timeout_config,
                            OperationName::Connect,
                        )
                        .await?;
                    }
                    Ok(true)
                })
                .await
        })
    }

    /// Execute a complete `SELECT 1` round-trip through the shared pool.
    ///
    /// Returns `True` on success and raises a typed FastMssql exception on
    /// checkout, authentication, TLS, SQL, protocol, I/O, panic, or timeout
    /// failure.
    pub fn ping<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        future_into_py(py, async move {
            let permit = handles
                .lifecycle
                .admit_operation(OperationName::Ping, false)?;
            permit
                .run(async move {
                    let pool = handles.ensure_connected(OperationName::Ping).await?;
                    Self::validate_pool_readiness(
                        &pool,
                        &handles.timeout_config,
                        OperationName::Ping,
                    )
                    .await?;
                    Ok(true)
                })
                .await
        })
    }

    pub fn disconnect<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let pool = Arc::clone(&self.pool);
        let lifecycle = Arc::clone(&self.lifecycle);
        let lifecycle_config = self.lifecycle_config.clone();
        future_into_py(py, async move {
            lifecycle.shutdown(pool, lifecycle_config).await
        })
    }

    #[pyo3(signature = (queries))]
    pub fn query_batch<'p>(
        &self,
        py: Python<'p>,
        queries: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        query_batch(
            handles.pool,
            handles.config,
            handles.pool_config,
            handles.timeout_config,
            handles.lifecycle,
            handles.azure_credential,
            py,
            queries,
        )
    }

    pub fn bulk_insert<'p>(
        &self,
        py: Python<'p>,
        table_name: String,
        columns: Vec<String>,
        data_rows: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        bulk_insert(
            handles.pool,
            handles.config,
            handles.pool_config,
            handles.timeout_config,
            handles.lifecycle,
            handles.azure_credential,
            py,
            table_name,
            columns,
            data_rows,
        )
    }

    pub fn execute_batch<'p>(
        &self,
        py: Python<'p>,
        commands: &Bound<'p, PyList>,
    ) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        execute_batch(
            handles.config,
            handles.timeout_config,
            handles.lifecycle,
            handles.azure_credential,
            py,
            commands,
        )
    }
}

#[cfg(test)]
mod pool_observability_tests {
    use super::reconcile_checkout_counts;

    #[test]
    fn checkout_counts_preserve_normal_pending_work() {
        assert_eq!(reconcile_checkout_counts(10, 4, 3, 1), (10, 2));
    }

    #[test]
    fn checkout_counts_reconcile_transient_relaxed_load_order() {
        assert_eq!(reconcile_checkout_counts(2, 2, 1, 0), (3, 0));
    }

    #[test]
    fn checkout_counts_do_not_overflow_completed_sum() {
        assert_eq!(
            reconcile_checkout_counts(u64::MAX, u64::MAX, 1, 1),
            (u64::MAX, 0)
        );
    }
}
