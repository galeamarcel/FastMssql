use crate::azure_auth::PyAzureCredential;
use crate::deadline::{DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until};
use crate::pool_config::PyPoolConfig;
use crate::timeout_config::PyTimeoutConfig;
use crate::types::{
    SqlError, TimeoutErrorMetadata, create_connection_error, create_operation_timeout_error,
    create_sql_error,
};
use bb8::Pool;
use pyo3::prelude::*;
use std::fmt;
use std::sync::Arc;
use std::time::Duration;
use tiberius::Config;
use tokio::sync::RwLock;
use tokio_util::compat::TokioAsyncWriteCompatExt;

// ──────────────────────────────────────────────────────────────────────────────
// Custom connection manager
// ──────────────────────────────────────────────────────────────────────────────

type TiberiusClient = tiberius::Client<tokio_util::compat::Compat<tokio::net::TcpStream>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ConnectionDisposition {
    /// A newly authenticated session on which no application operation ran yet.
    Clean,
    /// The TDS stream is synchronized, but application-visible session state may
    /// have changed and must be reset before cross-lease reuse.
    NeedsReset,
    /// The transport or TDS stream is not safe for another operation.
    Broken,
}

impl ConnectionDisposition {
    fn after_sql_server_severity(severity: u8) -> Self {
        match severity {
            // SQL Server severities 20-25 are fatal system errors. Lower
            // severities are application/server errors that leave the TDS
            // stream synchronized, though the session still needs reset.
            0..=19 => Self::NeedsReset,
            20.. => Self::Broken,
        }
    }

    fn after_python_error(error: &PyErr) -> Self {
        Python::attach(|py| {
            if error.is_instance_of::<SqlError>(py) {
                let severity = error
                    .value(py)
                    .getattr("severity")
                    .and_then(|value| value.extract::<u8>());

                severity
                    .map(Self::after_sql_server_severity)
                    .unwrap_or(Self::Broken)
            } else {
                // I/O, TLS, protocol, conversion, runtime, or an unclassified
                // internal error is conservatively unsafe to reuse.
                Self::Broken
            }
        })
    }
}

pub(crate) fn python_error_allows_connection_reuse(error: &PyErr) -> bool {
    ConnectionDisposition::after_python_error(error) != ConnectionDisposition::Broken
}

pub struct ManagedConnection {
    client: TiberiusClient,
    disposition: ConnectionDisposition,
}

impl ManagedConnection {
    fn new(client: TiberiusClient) -> Self {
        Self {
            client,
            disposition: ConnectionDisposition::Clean,
        }
    }

    pub(crate) fn mark_unusable(&mut self) {
        self.disposition = ConnectionDisposition::Broken;
    }

    pub(crate) fn mark_clean(&mut self) {
        self.disposition = ConnectionDisposition::Clean;
    }

    pub(crate) fn mark_needs_reset(&mut self) {
        if self.disposition != ConnectionDisposition::Broken {
            self.disposition = ConnectionDisposition::NeedsReset;
        }
    }

    pub(crate) fn prepare_for_checkout(&mut self) {
        if self.disposition == ConnectionDisposition::NeedsReset {
            self.client.reset_connection_on_next_request();
            self.mark_clean();
        }
    }

    /// Marks a request as unsafe before its first await.
    ///
    /// If the owning Rust future is cancelled, no completion callback runs and
    /// bb8 will retire this connection rather than returning a partial TDS
    /// response to another borrower.
    pub(crate) fn begin_operation(&mut self) {
        self.disposition = ConnectionDisposition::Broken;
    }

    /// Records a fully consumed successful response.
    pub(crate) fn finish_operation_success(&mut self) {
        self.disposition = ConnectionDisposition::NeedsReset;
    }

    pub(crate) fn apply_operation_error(&mut self, error: &PyErr) {
        self.disposition = if python_error_allows_connection_reuse(error) {
            ConnectionDisposition::NeedsReset
        } else {
            ConnectionDisposition::Broken
        };
    }

    pub(crate) fn is_reusable(&self) -> bool {
        self.disposition != ConnectionDisposition::Broken
    }
}

impl std::ops::Deref for ManagedConnection {
    type Target = TiberiusClient;

    fn deref(&self) -> &Self::Target {
        &self.client
    }
}

impl std::ops::DerefMut for ManagedConnection {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.client
    }
}

/// Error type for `AzureConnectionManager`.
#[derive(Debug)]
pub enum PoolConnectionError {
    Io {
        source: std::io::Error,
        address: Option<String>,
    },
    Tiberius(tiberius::error::Error),
    Auth(String),
    Timeout {
        timeout: Duration,
    },
}

impl fmt::Display for PoolConnectionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PoolConnectionError::Io {
                source,
                address: Some(address),
            } => {
                write!(f, "I/O error connecting to {address}: {source}")
            }
            PoolConnectionError::Io {
                source,
                address: None,
            } => write!(f, "I/O error: {source}"),
            PoolConnectionError::Tiberius(e) => write!(f, "SQL error: {e}"),
            PoolConnectionError::Auth(e) => write!(f, "Auth error: {e}"),
            PoolConnectionError::Timeout { timeout } => write!(
                f,
                "physical connection timed out after {} seconds",
                timeout.as_secs_f64()
            ),
        }
    }
}

impl std::error::Error for PoolConnectionError {}

impl From<std::io::Error> for PoolConnectionError {
    fn from(source: std::io::Error) -> Self {
        PoolConnectionError::Io {
            source,
            address: None,
        }
    }
}

impl From<tiberius::error::Error> for PoolConnectionError {
    fn from(e: tiberius::error::Error) -> Self {
        PoolConnectionError::Tiberius(e)
    }
}

/// Convert a [`PoolConnectionError`] into a typed Python exception,
/// preserving the structured context of the underlying [`tiberius::error::Error`]
/// (SQL error code/state, TLS details, routing info, etc.) rather than
/// collapsing everything into an opaque string via `Display`.
impl From<PoolConnectionError> for pyo3::PyErr {
    fn from(e: PoolConnectionError) -> Self {
        match e {
            PoolConnectionError::Tiberius(terr) => create_sql_error(terr, "Connection error"),
            PoolConnectionError::Io {
                source,
                address: Some(address),
            } => create_connection_error(format!("I/O error connecting to {address}: {source}")),
            PoolConnectionError::Io {
                source,
                address: None,
            } => create_connection_error(format!("I/O error: {source}")),
            PoolConnectionError::Auth(msg) => {
                create_connection_error(format!("Authentication error: {msg}"))
            }
            PoolConnectionError::Timeout { timeout } => timeout_error_or_metadata_failure(
                DeadlineElapsed {
                    timeout,
                    phase: TimeoutPhase::Connect,
                },
                TimeoutErrorMetadata {
                    operation: OperationName::Connect,
                    retryable: true,
                    connection_discarded: false,
                    outcome_unknown: false,
                },
            ),
        }
    }
}

pub(crate) fn timeout_error_or_metadata_failure(
    elapsed: DeadlineElapsed,
    metadata: TimeoutErrorMetadata,
) -> PyErr {
    match create_operation_timeout_error(elapsed, metadata) {
        Ok(timeout) => timeout,
        Err(metadata_failure) => metadata_failure,
    }
}

fn map_physical_connection_error(error: PoolConnectionError, operation: OperationName) -> PyErr {
    match error {
        PoolConnectionError::Timeout { timeout } => timeout_error_or_metadata_failure(
            DeadlineElapsed {
                timeout,
                phase: TimeoutPhase::Connect,
            },
            TimeoutErrorMetadata {
                operation,
                retryable: true,
                connection_discarded: false,
                outcome_unknown: false,
            },
        ),
        other => other.into(),
    }
}

async fn connect_client_inner(
    base_config: &Config,
    azure_credential: Option<&Arc<PyAzureCredential>>,
) -> Result<TiberiusClient, PoolConnectionError> {
    let mut config = base_config.clone();

    if let Some(credential) = azure_credential {
        let auth_method = credential
            .to_auth_method()
            .await
            .map_err(|error| PoolConnectionError::Auth(error.to_string()))?;
        config.authentication(auth_method);
    }

    let address = config.get_addr();
    let tcp = tokio::net::TcpStream::connect(&address)
        .await
        .map_err(|source| PoolConnectionError::Io {
            source,
            address: Some(address),
        })?;
    tcp.set_nodelay(true)?;

    match tiberius::Client::connect(config.clone(), tcp.compat_write()).await {
        Ok(client) => Ok(client),
        Err(tiberius::error::Error::Routing { host, port }) => {
            config.host(&host);
            config.port(port);
            let address = config.get_addr();
            let tcp = tokio::net::TcpStream::connect(&address)
                .await
                .map_err(|source| PoolConnectionError::Io {
                    source,
                    address: Some(address),
                })?;
            tcp.set_nodelay(true)?;
            tiberius::Client::connect(config, tcp.compat_write())
                .await
                .map_err(Into::into)
        }
        Err(error) => Err(error.into()),
    }
}

async fn connect_client_bounded(
    base_config: &Config,
    azure_credential: Option<&Arc<PyAzureCredential>>,
    timeout: Option<Duration>,
) -> Result<TiberiusClient, PoolConnectionError> {
    let deadline = deadline_from(TimeoutPhase::Connect, timeout);
    match run_until(
        deadline,
        connect_client_inner(base_config, azure_credential),
    )
    .await
    {
        Ok(result) => result,
        Err(elapsed) => Err(PoolConnectionError::Timeout {
            timeout: elapsed.timeout,
        }),
    }
}

pub(crate) async fn connect_client_with_timeout(
    config: &Config,
    azure_credential: Option<&Arc<PyAzureCredential>>,
    connect_timeout: Option<Duration>,
    operation: OperationName,
) -> PyResult<TiberiusClient> {
    connect_client_bounded(config, azure_credential, connect_timeout)
        .await
        .map_err(|error| map_physical_connection_error(error, operation))
}

/// A `bb8::ManageConnection` implementation that calls `to_auth_method()` on every
/// new physical connection.
///
/// For Azure credentials (`azure_credential = Some(…)`) this ensures the token cache
/// is consulted — and the token refreshed if it has expired — each time `bb8` opens a
/// connection (on pool warm-up, `max_lifetime` rotation, idle-timeout eviction, or
/// reconnect after error).  This fixes the bug where a static token baked into
/// `bb8_tiberius::ConnectionManager`'s config would silently go stale after ~1 hour.
///
/// For SQL Server / Windows auth (`azure_credential = None`) the base config already
/// carries the credentials and the manager behaves identically to `bb8_tiberius`.
pub struct AzureConnectionManager {
    /// Base config — host, port, database, SSL.  Auth is NOT set here for Azure paths;
    /// it is applied dynamically in `connect()`.
    base_config: Config,
    /// Azure credential, or `None` for non-Azure auth.
    azure_credential: Option<Arc<PyAzureCredential>>,
    /// One budget covering credential refresh, TCP, TLS, login, and routing.
    connect_timeout: Option<Duration>,
}

impl AzureConnectionManager {
    pub fn new(
        base_config: Config,
        azure_credential: Option<Arc<PyAzureCredential>>,
        connect_timeout: Option<Duration>,
    ) -> Self {
        Self {
            base_config,
            azure_credential,
            connect_timeout,
        }
    }
}

impl bb8::ManageConnection for AzureConnectionManager {
    type Connection = ManagedConnection;
    type Error = PoolConnectionError;

    async fn connect(&self) -> Result<Self::Connection, Self::Error> {
        let client = connect_client_bounded(
            &self.base_config,
            self.azure_credential.as_ref(),
            self.connect_timeout,
        )
        .await?;
        Ok(ManagedConnection::new(client))
    }

    async fn is_valid(&self, conn: &mut Self::Connection) -> Result<(), Self::Error> {
        // A checkout validation query is itself the next application request,
        // so it must carry RESETCONNECTION before inspecting the connection.
        conn.prepare_for_checkout();
        // Cancellation during validation must not return a partially consumed
        // reset/health response to the pool.
        conn.mark_unusable();
        conn.simple_query("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION; SELECT 1")
            .await?
            .into_results()
            .await?;
        conn.mark_clean();
        Ok(())
    }

    /// Returns whether a cancelled pooled operation made this client unsafe to
    /// reuse.
    ///
    /// bb8 calls this synchronously whenever a checkout is returned. Normal
    /// liveness checks remain in [`is_valid`](AzureConnectionManager::is_valid);
    /// this flag handles the distinct case where Python cancellation dropped a
    /// Rust future before Tiberius finished consuming the server response.
    fn has_broken(&self, conn: &mut Self::Connection) -> bool {
        !conn.is_reusable()
    }
}

pub(crate) struct PooledOperationGuard<'a> {
    connection: bb8::PooledConnection<'a, AzureConnectionManager>,
    completed: bool,
}

impl<'a> PooledOperationGuard<'a> {
    pub(crate) fn new(mut connection: bb8::PooledConnection<'a, AzureConnectionManager>) -> Self {
        connection.prepare_for_checkout();
        Self {
            connection,
            completed: false,
        }
    }

    pub(crate) fn complete(&mut self) {
        self.connection.mark_needs_reset();
        self.completed = true;
    }

    pub(crate) fn complete_with_result_and_retirement<T>(
        &mut self,
        result: &PyResult<T>,
        retire_after_operation: bool,
    ) {
        if retire_after_operation {
            // EXECUTE AS can take effect before a later statement raises a
            // nonfatal SQL error. Never return that physical security context
            // to the pool, regardless of the operation's final result.
            self.connection.mark_unusable();
            self.completed = true;
            return;
        }

        match result {
            Ok(_) => self.connection.mark_needs_reset(),
            Err(error) => self.connection.apply_operation_error(error),
        }
        self.completed = true;
    }

    pub(crate) fn observe_error(&mut self, error: &PyErr) {
        self.connection.apply_operation_error(error);
    }
}

impl std::ops::Deref for PooledOperationGuard<'_> {
    type Target = TiberiusClient;

    fn deref(&self) -> &Self::Target {
        &self.connection.client
    }
}

impl std::ops::DerefMut for PooledOperationGuard<'_> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.connection.client
    }
}

impl Drop for PooledOperationGuard<'_> {
    fn drop(&mut self) {
        if !self.completed {
            self.connection.mark_unusable();
        }
    }
}

pub type ConnectionPool = Pool<AzureConnectionManager>;
pub(crate) type OwnedPooledConnection = bb8::PooledConnection<'static, AzureConnectionManager>;

pub(crate) fn map_pool_checkout_error(
    error: bb8::RunError<PoolConnectionError>,
    operation: OperationName,
    acquire_timeout: Duration,
) -> PyErr {
    match error {
        bb8::RunError::TimedOut => timeout_error_or_metadata_failure(
            DeadlineElapsed {
                timeout: acquire_timeout,
                phase: TimeoutPhase::Acquire,
            },
            TimeoutErrorMetadata {
                operation,
                retryable: true,
                connection_discarded: false,
                outcome_unknown: false,
            },
        ),
        bb8::RunError::User(error) => map_physical_connection_error(error, operation),
    }
}

pub(crate) async fn acquire_owned_connection(
    pool: &ConnectionPool,
    operation: OperationName,
    acquire_timeout: Duration,
) -> PyResult<OwnedPooledConnection> {
    pool.get_owned()
        .await
        .map_err(|error| map_pool_checkout_error(error, operation, acquire_timeout))
}

// ──────────────────────────────────────────────────────────────────────────────
// Pool helpers
// ──────────────────────────────────────────────────────────────────────────────

pub async fn establish_pool(
    base_config: &Config,
    azure_credential: Option<Arc<PyAzureCredential>>,
    pool_config: &PyPoolConfig,
    timeout_config: &PyTimeoutConfig,
    operation: OperationName,
) -> PyResult<ConnectionPool> {
    let manager = AzureConnectionManager::new(
        base_config.clone(),
        azure_credential,
        timeout_config.connect_timeout,
    );
    let mut builder = Pool::builder()
        .max_size(pool_config.max_size)
        .connection_timeout(timeout_config.acquire_timeout);

    if let Some(min) = pool_config.min_idle {
        builder = builder.min_idle(Some(min));
    }
    if let Some(lt) = pool_config.max_lifetime {
        builder = builder.max_lifetime(Some(lt));
    }
    if let Some(to) = pool_config.idle_timeout {
        builder = builder.idle_timeout(Some(to));
    }
    if let Some(test) = pool_config.test_on_check_out {
        builder = builder.test_on_check_out(test);
    }
    if let Some(retry) = pool_config.retry_connection {
        builder = builder.retry_connection(retry);
    }

    let pool = builder
        .build(manager)
        .await
        .map_err(|error| map_physical_connection_error(error, operation))?;

    // Warmup pool if min_idle is configured to eliminate cold-start latency.
    if let Some(min_idle) = pool_config.min_idle {
        warmup_pool(&pool, min_idle, timeout_config, operation).await?;
    }

    Ok(pool)
}

pub async fn ensure_pool_initialized_with_auth(
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: &PyPoolConfig,
    timeout_config: &PyTimeoutConfig,
    azure_credential: Option<Arc<PyAzureCredential>>,
    operation: OperationName,
) -> PyResult<ConnectionPool> {
    {
        let read_guard = pool.read().await;
        if let Some(existing_pool) = read_guard.as_ref() {
            return Ok(existing_pool.clone());
        }
    }

    let mut write_guard = pool.write().await;

    if let Some(existing_pool) = write_guard.as_ref() {
        return Ok(existing_pool.clone());
    }

    // Pass the base config and credential to establish_pool.
    // AzureConnectionManager will call to_auth_method() on every new connection,
    // so tokens are always fresh regardless of when bb8 decides to open them.
    let new_pool = establish_pool(
        &config,
        azure_credential,
        pool_config,
        timeout_config,
        operation,
    )
    .await?;
    *write_guard = Some(new_pool.clone());
    Ok(new_pool)
}

/// Warms up the connection pool by pre-establishing `target_connections` connections.
/// This eliminates cold-start latency on first queries.
///
/// All tasks run concurrently via a [`tokio::task::JoinSet`].  The total budget is
/// `connection_timeout × target_connections` (capped at 120 s).  If the deadline
/// expires, all outstanding tasks are cancelled via [`JoinSet::shutdown`] and an
/// error is returned. The first connection failure retains its typed timeout/TLS/
/// protocol classification; task-join failures are aggregated separately.
pub async fn warmup_pool(
    pool: &ConnectionPool,
    target_connections: u32,
    timeout_config: &PyTimeoutConfig,
    operation: OperationName,
) -> PyResult<()> {
    use tokio::task::JoinSet;

    // Total warmup budget: per-connection timeout × number of connections, capped at
    // 2 minutes.  bb8 will enforce connection_timeout per task when calling
    // pool.get(); this outer deadline is a safety net to guarantee that
    // warmup_pool() always returns even if bb8's own timeout is misconfigured or
    // bypassed.
    let warmup_budget = timeout_config
        .acquire_timeout
        .saturating_mul(target_connections.max(1))
        .min(std::time::Duration::from_secs(120));

    let mut set: JoinSet<Result<(), bb8::RunError<PoolConnectionError>>> = JoinSet::new();

    for _ in 0..target_connections {
        let pool_clone = pool.clone();
        // Each task acquires one connection (exercising the full connect path) then
        // immediately drops the guard, returning it to the pool.
        set.spawn(async move { pool_clone.get().await.map(|_conn| ()) });
    }

    let deadline = tokio::time::Instant::now() + warmup_budget;
    let mut errors: Vec<bb8::RunError<PoolConnectionError>> = Vec::new();
    let mut join_errors: Vec<String> = Vec::new();

    loop {
        match tokio::time::timeout_at(deadline, set.join_next()).await {
            // Task completed successfully.
            Ok(Some(Ok(Ok(())))) => {}
            // Task returned a bb8/connection error – collect it and continue.
            Ok(Some(Ok(Err(error)))) => errors.push(error),
            // Task panicked or was cancelled – record the join error and continue.
            Ok(Some(Err(join_err))) => join_errors.push(format!("task panicked: {join_err}")),
            // All tasks finished.
            Ok(None) => break,
            // Overall deadline exceeded – abort every outstanding task.
            Err(_elapsed) => {
                let outstanding = set.len();
                set.shutdown().await;
                return Err(create_connection_error(format!(
                    "Connection pool warmup timed out after {}s ({outstanding} task(s) cancelled)",
                    warmup_budget.as_secs(),
                )));
            }
        }
    }

    if let Some(error) = errors.into_iter().next() {
        return Err(map_pool_checkout_error(
            error,
            operation,
            timeout_config.acquire_timeout,
        ));
    }

    if !join_errors.is_empty() {
        return Err(create_connection_error(format!(
            "Connection pool warmup encountered {} error(s): {}",
            join_errors.len(),
            join_errors.join("; "),
        )));
    }

    Ok(())
}

#[cfg(test)]
mod connection_disposition_tests {
    use super::ConnectionDisposition;

    #[test]
    fn nonfatal_sql_server_errors_need_reset_but_remain_synchronized() {
        for severity in [0, 10, 16, 19] {
            assert_eq!(
                ConnectionDisposition::after_sql_server_severity(severity),
                ConnectionDisposition::NeedsReset
            );
        }
    }

    #[test]
    fn fatal_sql_server_errors_are_broken() {
        for severity in [20, 21, 24, 25, u8::MAX] {
            assert_eq!(
                ConnectionDisposition::after_sql_server_severity(severity),
                ConnectionDisposition::Broken
            );
        }
    }
}
