use crate::azure_auth::PyAzureCredential;
use crate::deadline::{DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until};
use crate::pool_config::PyPoolConfig;
use crate::timeout_config::PyTimeoutConfig;
use crate::types::{
    ConversionError, SqlError, TimeoutErrorMetadata, create_connection_error,
    create_operation_timeout_error, create_sql_error,
};
use bb8::Pool;
use pyo3::prelude::*;
use std::fmt;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tiberius::Config;
use tiberius::SqlBrowser;
use tokio::net::TcpStream;
use tokio::sync::{RwLock, watch};
use tokio_util::compat::TokioAsyncWriteCompatExt;

// ──────────────────────────────────────────────────────────────────────────────
// Custom connection manager
// ──────────────────────────────────────────────────────────────────────────────

pub(crate) type TiberiusClient =
    tiberius::Client<tokio_util::compat::Compat<tokio::net::TcpStream>>;

#[derive(Clone, Debug, Eq, PartialEq)]
enum InitialTarget {
    Direct(String),
    SqlBrowser,
}

fn classify_initial_target(config: &Config) -> InitialTarget {
    if config.has_instance_name() && !config.has_explicit_port() {
        InitialTarget::SqlBrowser
    } else {
        InitialTarget::Direct(config.get_addr())
    }
}

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
            } else if error.is_instance_of::<ConversionError>(py)
                && error
                    .value(py)
                    .getattr("wire_sent")
                    .and_then(|value| value.extract::<bool>())
                    .is_ok_and(|wire_sent| !wire_sent)
            {
                Self::NeedsReset
            } else {
                // I/O, TLS, protocol, conversion, runtime, or an unclassified
                // internal error is conservatively unsafe to reuse.
                Self::Broken
            }
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CheckoutAction {
    Ready,
    Reset,
    Validate { reset: bool },
    Reject,
}

fn checkout_action(
    disposition: ConnectionDisposition,
    validate_on_checkout: bool,
) -> CheckoutAction {
    match (disposition, validate_on_checkout) {
        (ConnectionDisposition::Clean, false) => CheckoutAction::Ready,
        (ConnectionDisposition::NeedsReset, false) => CheckoutAction::Reset,
        (ConnectionDisposition::Clean, true) => CheckoutAction::Validate { reset: false },
        (ConnectionDisposition::NeedsReset, true) => CheckoutAction::Validate { reset: true },
        (ConnectionDisposition::Broken, _) => CheckoutAction::Reject,
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
    BackgroundConnect {
        wave_id: u64,
        source: Box<PoolConnectionError>,
    },
    Discovery(tiberius::error::Error),
    Tiberius(tiberius::error::Error),
    Auth(String),
    Timeout {
        timeout: Duration,
    },
    Unusable,
}

impl Clone for PoolConnectionError {
    fn clone(&self) -> Self {
        match self {
            Self::Io { source, address } => Self::Io {
                // std::io::Error is not Clone. This copy is used only to move a
                // terminal physical-connect failure from bb8's background
                // creator to the waiting checkout while retaining kind/detail.
                source: std::io::Error::new(source.kind(), source.to_string()),
                address: address.clone(),
            },
            Self::BackgroundConnect { wave_id, source } => Self::BackgroundConnect {
                wave_id: *wave_id,
                source: Box::new((**source).clone()),
            },
            Self::Discovery(error) => Self::Discovery(error.clone()),
            Self::Tiberius(error) => Self::Tiberius(error.clone()),
            Self::Auth(message) => Self::Auth(message.clone()),
            Self::Timeout { timeout } => Self::Timeout { timeout: *timeout },
            Self::Unusable => Self::Unusable,
        }
    }
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
            PoolConnectionError::BackgroundConnect { source, .. } => source.fmt(f),
            PoolConnectionError::Discovery(error) => {
                write!(f, "SQL Browser discovery failed: {error}")
            }
            PoolConnectionError::Tiberius(e) => write!(f, "SQL error: {e}"),
            PoolConnectionError::Auth(e) => write!(f, "Auth error: {e}"),
            PoolConnectionError::Timeout { timeout } => write!(
                f,
                "physical connection timed out after {} seconds",
                timeout.as_secs_f64()
            ),
            PoolConnectionError::Unusable => {
                write!(f, "physical connection is not safe for checkout")
            }
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

fn discovery_error_with_metadata(source: tiberius::error::Error) -> PyErr {
    let message = format!("SQL Browser discovery failed: {source}");
    let error = create_connection_error(message.clone());
    let metadata = Python::attach(|py| {
        let value = error.value(py);
        value.setattr("message", message.as_str())?;
        value.setattr("stage", "sql_browser_discovery")?;
        value.setattr("retryable", true)?;
        value.setattr("connection_discarded", false)?;
        value.setattr("outcome_unknown", false)?;
        Ok::<(), PyErr>(())
    });

    match metadata {
        Ok(()) => error,
        Err(metadata_failure) => {
            Python::attach(|py| {
                metadata_failure.set_cause(py, Some(error));
            });
            metadata_failure
        }
    }
}

/// Convert a [`PoolConnectionError`] into a typed Python exception,
/// preserving the structured context of the underlying [`tiberius::error::Error`]
/// (SQL error code/state, TLS details, routing info, etc.) rather than
/// collapsing everything into an opaque string via `Display`.
impl From<PoolConnectionError> for pyo3::PyErr {
    fn from(e: PoolConnectionError) -> Self {
        match e {
            PoolConnectionError::BackgroundConnect { source, .. } => (*source).into(),
            PoolConnectionError::Discovery(source) => discovery_error_with_metadata(source),
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
            PoolConnectionError::Unusable => {
                create_connection_error("Physical connection is not safe for checkout")
            }
        }
    }
}

async fn open_direct_stream(address: String) -> Result<TcpStream, PoolConnectionError> {
    let tcp = TcpStream::connect(&address)
        .await
        .map_err(|source| PoolConnectionError::Io {
            source,
            address: Some(address.clone()),
        })?;
    tcp.set_nodelay(true)
        .map_err(|source| PoolConnectionError::Io {
            source,
            address: Some(address),
        })?;
    Ok(tcp)
}

async fn open_initial_stream(config: &Config) -> Result<TcpStream, PoolConnectionError> {
    match classify_initial_target(config) {
        InitialTarget::Direct(address) => open_direct_stream(address).await,
        InitialTarget::SqlBrowser => {
            let tcp = <TcpStream as SqlBrowser>::connect_named(config)
                .await
                .map_err(PoolConnectionError::Discovery)?;
            tcp.set_nodelay(true)
                .map_err(|source| PoolConnectionError::Discovery(source.into()))?;
            Ok(tcp)
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
        PoolConnectionError::BackgroundConnect { source, .. } => {
            map_physical_connection_error(*source, operation)
        }
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

    let tcp = open_initial_stream(&config).await?;

    match tiberius::Client::connect(config.clone(), tcp.compat_write()).await {
        Ok(client) => Ok(client),
        Err(tiberius::error::Error::Routing { host, port }) => {
            config.host(&host);
            config.port(port);
            let tcp = open_direct_stream(config.get_addr()).await?;
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
    /// Whether checkout performs the optional SQL Server health probe.
    validate_on_checkout: bool,
    /// Tracks terminal background connection attempts so bb8 can preserve
    /// their typed error when retries are explicitly disabled.
    connection_attempts: Arc<ConnectionAttemptTracker>,
}

impl AzureConnectionManager {
    fn new(
        base_config: Config,
        azure_credential: Option<Arc<PyAzureCredential>>,
        connect_timeout: Option<Duration>,
        validate_on_checkout: bool,
        connection_attempts: Arc<ConnectionAttemptTracker>,
    ) -> Self {
        Self {
            base_config,
            azure_credential,
            connect_timeout,
            validate_on_checkout,
            connection_attempts,
        }
    }
}

impl bb8::ManageConnection for AzureConnectionManager {
    type Connection = ManagedConnection;
    type Error = PoolConnectionError;

    async fn connect(&self) -> Result<Self::Connection, Self::Error> {
        let attempt = self.connection_attempts.begin();
        let wave_id = attempt.wave_id();
        let result = connect_client_bounded(
            &self.base_config,
            self.azure_credential.as_ref(),
            self.connect_timeout,
        )
        .await;
        attempt.finish(&result);
        let client = result.map_err(|source| PoolConnectionError::BackgroundConnect {
            wave_id,
            source: Box::new(source),
        })?;
        Ok(ManagedConnection::new(client))
    }

    async fn is_valid(&self, conn: &mut Self::Connection) -> Result<(), Self::Error> {
        match checkout_action(conn.disposition, self.validate_on_checkout) {
            CheckoutAction::Ready => Ok(()),
            CheckoutAction::Reset => {
                // Cancellation during the private reset must retire the
                // partially reset physical session.
                conn.mark_unusable();
                conn.client.reset_connection().await?;
                conn.mark_clean();
                Ok(())
            }
            CheckoutAction::Validate { reset } => {
                if reset {
                    conn.client.reset_connection_on_next_request();
                }
                // The health response must reach terminal completion before
                // the lease can be exposed to application SQL.
                conn.mark_unusable();
                conn.client
                    .simple_query("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION; SELECT 1")
                    .await?
                    .into_results()
                    .await?;
                conn.mark_clean();
                Ok(())
            }
            CheckoutAction::Reject => Err(PoolConnectionError::Unusable),
        }
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

#[derive(Debug, Default)]
struct ConnectionAttemptState {
    wave_id: u64,
    in_flight: usize,
    pending_failure_sinks: usize,
    saw_success: bool,
    last_failure: Option<PoolConnectionError>,
}

#[derive(Debug)]
struct ConnectionAttemptTracker {
    state: Mutex<ConnectionAttemptState>,
    terminal_failure_wave: watch::Sender<u64>,
}

impl Default for ConnectionAttemptTracker {
    fn default() -> Self {
        let (terminal_failure_wave, _) = watch::channel(0);
        Self {
            state: Mutex::new(ConnectionAttemptState::default()),
            terminal_failure_wave,
        }
    }
}

impl ConnectionAttemptTracker {
    fn begin(self: &Arc<Self>) -> ConnectionAttemptGuard {
        let wave_id = {
            let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
            if state.in_flight == 0 && state.pending_failure_sinks == 0 {
                state.wave_id = state.wave_id.wrapping_add(1);
                state.saw_success = false;
                state.last_failure = None;
            }
            state.in_flight += 1;
            state.wave_id
        };
        ConnectionAttemptGuard {
            tracker: Arc::clone(self),
            wave_id,
            finished: false,
        }
    }

    fn complete(&self, outcome: Option<Result<(), PoolConnectionError>>) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        debug_assert!(state.in_flight > 0);
        state.in_flight = state.in_flight.saturating_sub(1);
        match outcome {
            Some(Ok(())) => {
                state.saw_success = true;
                state.last_failure = None;
            }
            Some(Err(error)) => {
                state.pending_failure_sinks += 1;
                if !state.saw_success {
                    state.last_failure = Some(error);
                }
            }
            None => {}
        }
    }

    fn publish_failure(&self, wave_id: u64, error: PoolConnectionError) {
        let terminal_wave = {
            let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
            if state.wave_id != wave_id || state.pending_failure_sinks == 0 {
                return;
            }

            state.pending_failure_sinks -= 1;
            if !state.saw_success {
                state.last_failure = Some(error);
            }
            if state.in_flight == 0
                && state.pending_failure_sinks == 0
                && !state.saw_success
                && state.last_failure.is_some()
            {
                Some(wave_id)
            } else {
                None
            }
        };

        if let Some(wave_id) = terminal_wave {
            // bb8 invokes the error sink only after releasing the failed
            // connection approval. A waiter can therefore safely start a new
            // physical attempt after observing this wave.
            self.terminal_failure_wave.send_replace(wave_id);
        }
    }

    fn active_wave(&self) -> Option<u64> {
        let state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if state.in_flight > 0 || state.pending_failure_sinks > 0 {
            Some(state.wave_id)
        } else {
            None
        }
    }

    fn subscribe(&self) -> watch::Receiver<u64> {
        self.terminal_failure_wave.subscribe()
    }

    fn failure_for_wave(&self, wave_id: u64) -> Option<PoolConnectionError> {
        let state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if state.wave_id == wave_id
            && state.in_flight == 0
            && state.pending_failure_sinks == 0
            && !state.saw_success
        {
            state.last_failure.clone()
        } else {
            None
        }
    }
}

#[derive(Clone, Debug)]
struct ConnectionAttemptErrorSink {
    tracker: Arc<ConnectionAttemptTracker>,
}

impl ConnectionAttemptErrorSink {
    fn new(tracker: Arc<ConnectionAttemptTracker>) -> Self {
        Self { tracker }
    }
}

impl bb8::ErrorSink<PoolConnectionError> for ConnectionAttemptErrorSink {
    fn sink(&self, error: PoolConnectionError) {
        if let PoolConnectionError::BackgroundConnect { wave_id, source } = error {
            self.tracker.publish_failure(wave_id, *source);
        }
    }

    fn boxed_clone(&self) -> Box<dyn bb8::ErrorSink<PoolConnectionError>> {
        Box::new(self.clone())
    }
}

struct ConnectionAttemptGuard {
    tracker: Arc<ConnectionAttemptTracker>,
    wave_id: u64,
    finished: bool,
}

impl ConnectionAttemptGuard {
    fn wave_id(&self) -> u64 {
        self.wave_id
    }

    fn finish<T>(mut self, result: &Result<T, PoolConnectionError>) {
        let outcome = match result {
            Ok(_) => Ok(()),
            Err(error) => Err(error.clone()),
        };
        self.tracker.complete(Some(outcome));
        self.finished = true;
    }
}

impl Drop for ConnectionAttemptGuard {
    fn drop(&mut self) {
        if !self.finished {
            self.tracker.complete(None);
        }
    }
}

pub(crate) struct PooledOperationGuard<'a> {
    connection: bb8::PooledConnection<'a, AzureConnectionManager>,
    completed: bool,
}

impl<'a> PooledOperationGuard<'a> {
    pub(crate) fn new(connection: bb8::PooledConnection<'a, AzureConnectionManager>) -> Self {
        Self {
            connection,
            completed: false,
        }
    }

    pub(crate) fn begin_operation(&mut self) {
        self.connection.begin_operation();
    }

    pub(crate) fn complete_success(&mut self, retire_after_operation: bool) {
        if retire_after_operation {
            self.connection.mark_unusable();
        } else {
            self.connection.finish_operation_success();
        }
        self.completed = true;
    }

    pub(crate) fn complete_error(&mut self, error: &PyErr, retire_after_operation: bool) {
        if retire_after_operation {
            self.connection.mark_unusable();
        } else {
            self.connection.apply_operation_error(error);
        }
        self.completed = true;
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
        match result {
            Ok(_) => self.complete_success(retire_after_operation),
            Err(error) => {
                // EXECUTE AS can take effect before a later statement raises a
                // nonfatal SQL error. Never return that physical security
                // context to the pool, regardless of the final result.
                self.complete_error(error, retire_after_operation);
            }
        }
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

#[derive(Clone)]
pub struct ConnectionPool {
    inner: Pool<AzureConnectionManager>,
    connection_attempts: Arc<ConnectionAttemptTracker>,
    retry_connection: bool,
}

fn consume_inherited_failure(inherited_wave: &mut Option<u64>, failed_wave: u64) -> bool {
    if *inherited_wave == Some(failed_wave) {
        *inherited_wave = None;
        true
    } else {
        false
    }
}

impl ConnectionPool {
    fn new(
        inner: Pool<AzureConnectionManager>,
        connection_attempts: Arc<ConnectionAttemptTracker>,
        retry_connection: bool,
    ) -> Self {
        Self {
            inner,
            connection_attempts,
            retry_connection,
        }
    }

    fn terminal_connection_failure(&self, wave_id: u64) -> Option<PoolConnectionError> {
        if self.inner.state().connections == 0 {
            self.connection_attempts.failure_for_wave(wave_id)
        } else {
            None
        }
    }

    pub fn state(&self) -> bb8::State {
        self.inner.state()
    }

    pub async fn get(
        &self,
    ) -> Result<bb8::PooledConnection<'_, AzureConnectionManager>, bb8::RunError<PoolConnectionError>>
    {
        if self.retry_connection {
            return self.inner.get().await;
        }

        let mut inherited_wave = self.connection_attempts.active_wave();
        loop {
            let mut failures = self.connection_attempts.subscribe();
            tokio::select! {
                result = self.inner.get() => return result,
                changed = failures.changed() => {
                    if changed.is_err() {
                        return self.inner.get().await;
                    }
                    let failed_wave = *failures.borrow_and_update();
                    if consume_inherited_failure(&mut inherited_wave, failed_wave) {
                        // This waiter arrived while an older caller's physical
                        // attempt was still settling. Give this logical
                        // acquisition one fresh wave instead of replaying the
                        // older caller's terminal failure.
                        continue;
                    }
                    if let Some(error) = self.terminal_connection_failure(failed_wave) {
                        return Err(bb8::RunError::User(error));
                    }
                }
            }
        }
    }

    pub async fn get_owned(
        &self,
    ) -> Result<
        bb8::PooledConnection<'static, AzureConnectionManager>,
        bb8::RunError<PoolConnectionError>,
    > {
        if self.retry_connection {
            return self.inner.get_owned().await;
        }

        let mut inherited_wave = self.connection_attempts.active_wave();
        loop {
            let mut failures = self.connection_attempts.subscribe();
            tokio::select! {
                result = self.inner.get_owned() => return result,
                changed = failures.changed() => {
                    if changed.is_err() {
                        return self.inner.get_owned().await;
                    }
                    let failed_wave = *failures.borrow_and_update();
                    if consume_inherited_failure(&mut inherited_wave, failed_wave) {
                        continue;
                    }
                    if let Some(error) = self.terminal_connection_failure(failed_wave) {
                        return Err(bb8::RunError::User(error));
                    }
                }
            }
        }
    }
}

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

pub(crate) async fn acquire_owned_operation_guard(
    pool: &ConnectionPool,
    operation: OperationName,
    acquire_timeout: Duration,
) -> PyResult<PooledOperationGuard<'static>> {
    let connection = acquire_owned_connection(pool, operation, acquire_timeout).await?;
    let mut guard = PooledOperationGuard::new(connection);
    guard.begin_operation();
    Ok(guard)
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
    let validate_on_checkout = pool_config.test_on_check_out.unwrap_or(true);
    let retry_connection = pool_config.retry_connection.unwrap_or(true);
    let connection_attempts = Arc::new(ConnectionAttemptTracker::default());
    let manager = AzureConnectionManager::new(
        base_config.clone(),
        azure_credential,
        timeout_config.connect_timeout,
        validate_on_checkout,
        Arc::clone(&connection_attempts),
    );
    let mut builder = Pool::builder()
        .max_size(pool_config.max_size)
        .connection_timeout(timeout_config.acquire_timeout)
        .error_sink(Box::new(ConnectionAttemptErrorSink::new(Arc::clone(
            &connection_attempts,
        ))))
        // Mandatory cross-lease reset is implemented in the manager hook.
        // This hook must remain active even when the optional health probe is
        // disabled through PoolConfig.
        .test_on_check_out(true);

    if let Some(min) = pool_config.min_idle {
        builder = builder.min_idle(Some(min));
    }
    if let Some(lt) = pool_config.max_lifetime {
        builder = builder.max_lifetime(Some(lt));
    }
    if let Some(to) = pool_config.idle_timeout {
        builder = builder.idle_timeout(Some(to));
    }
    if let Some(retry) = pool_config.retry_connection {
        builder = builder.retry_connection(retry);
    }

    let pool = builder
        .build(manager)
        .await
        .map_err(|error| map_physical_connection_error(error, operation))?;
    let pool = ConnectionPool::new(pool, connection_attempts, retry_connection);

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
    use super::{
        CheckoutAction, ConnectionAttemptErrorSink, ConnectionAttemptTracker,
        ConnectionDisposition, InitialTarget, PoolConnectionError, checkout_action,
        classify_initial_target, consume_inherited_failure, map_physical_connection_error,
        python_error_allows_connection_reuse,
    };
    use crate::deadline::OperationName;
    use crate::types::{ConversionError, create_parameter_conversion_error};
    use pyo3::Python;
    use pyo3::types::PyAnyMethods;
    use std::sync::Arc;
    use std::time::Duration;
    use tiberius::Config;

    #[test]
    fn initial_target_matrix_uses_browser_only_without_explicit_port() {
        let mut direct_default = Config::new();
        direct_default.host("db-host");
        assert_eq!(
            classify_initial_target(&direct_default),
            InitialTarget::Direct("db-host:1433".to_owned())
        );

        let mut named = Config::new();
        named.host("db-host");
        named.instance_name("SQLEXPRESS");
        assert_eq!(classify_initial_target(&named), InitialTarget::SqlBrowser);

        let mut direct_port = Config::new();
        direct_port.host("db-host");
        direct_port.port(51433);
        assert_eq!(
            classify_initial_target(&direct_port),
            InitialTarget::Direct("db-host:51433".to_owned())
        );

        let mut named_with_port = Config::new();
        named_with_port.host("db-host");
        named_with_port.instance_name("SQLEXPRESS");
        named_with_port.port(51433);
        assert_eq!(
            classify_initial_target(&named_with_port),
            InitialTarget::Direct("db-host:51433".to_owned())
        );
    }

    #[test]
    fn checkout_validation_error_cannot_complete_a_physical_connect_wave() {
        let tracker = Arc::new(ConnectionAttemptTracker::default());
        let attempt = tracker.begin();
        let connect_failure: Result<(), PoolConnectionError> = Err(PoolConnectionError::Io {
            source: std::io::Error::new(
                std::io::ErrorKind::ConnectionRefused,
                "physical connect refused",
            ),
            address: Some("127.0.0.1:51433".to_owned()),
        });
        attempt.finish(&connect_failure);

        let failure_wave = tracker.subscribe();
        let sink = ConnectionAttemptErrorSink::new(Arc::clone(&tracker));
        bb8::ErrorSink::sink(&sink, PoolConnectionError::Unusable);

        assert!(
            !failure_wave.has_changed().unwrap(),
            "an is_valid failure must not publish a background connect wave"
        );
        assert!(
            tracker.active_wave().is_some(),
            "the pending physical connect failure must remain attributable"
        );
    }

    #[test]
    fn tagged_background_failure_publishes_its_underlying_error() {
        let tracker = Arc::new(ConnectionAttemptTracker::default());
        let attempt = tracker.begin();
        let wave_id = tracker.active_wave().expect("active connect wave");
        let connect_failure: Result<(), PoolConnectionError> = Err(PoolConnectionError::Io {
            source: std::io::Error::new(
                std::io::ErrorKind::ConnectionRefused,
                "physical connect refused",
            ),
            address: Some("127.0.0.1:51433".to_owned()),
        });
        attempt.finish(&connect_failure);

        let failure_wave = tracker.subscribe();
        let sink = ConnectionAttemptErrorSink::new(Arc::clone(&tracker));
        bb8::ErrorSink::sink(
            &sink,
            PoolConnectionError::BackgroundConnect {
                wave_id,
                source: Box::new(PoolConnectionError::Io {
                    source: std::io::Error::new(
                        std::io::ErrorKind::ConnectionRefused,
                        "physical connect refused",
                    ),
                    address: Some("127.0.0.1:51433".to_owned()),
                }),
            },
        );

        assert!(failure_wave.has_changed().unwrap());
        match tracker
            .failure_for_wave(wave_id)
            .expect("terminal physical connect failure")
        {
            PoolConnectionError::Io { source, address } => {
                assert_eq!(source.kind(), std::io::ErrorKind::ConnectionRefused);
                assert_eq!(address.as_deref(), Some("127.0.0.1:51433"));
            }
            other => panic!("expected the underlying I/O error, got {other:?}"),
        }
    }

    #[test]
    fn tagged_failure_cannot_consume_another_connect_wave() {
        let tracker = Arc::new(ConnectionAttemptTracker::default());
        let attempt = tracker.begin();
        let wave_id = tracker.active_wave().expect("active connect wave");
        let connect_failure: Result<(), PoolConnectionError> = Err(PoolConnectionError::Io {
            source: std::io::Error::new(
                std::io::ErrorKind::ConnectionRefused,
                "physical connect refused",
            ),
            address: Some("127.0.0.1:51433".to_owned()),
        });
        attempt.finish(&connect_failure);

        let failure_wave = tracker.subscribe();
        let sink = ConnectionAttemptErrorSink::new(Arc::clone(&tracker));
        bb8::ErrorSink::sink(
            &sink,
            PoolConnectionError::BackgroundConnect {
                wave_id: wave_id.wrapping_add(1),
                source: Box::new(PoolConnectionError::Unusable),
            },
        );

        assert!(
            !failure_wave.has_changed().unwrap(),
            "a mismatched tag must not publish the active connect wave"
        );
        assert_eq!(tracker.active_wave(), Some(wave_id));
    }

    #[test]
    fn background_connect_timeout_preserves_the_triggering_operation() {
        Python::initialize();
        let error = map_physical_connection_error(
            PoolConnectionError::BackgroundConnect {
                wave_id: 1,
                source: Box::new(PoolConnectionError::Timeout {
                    timeout: Duration::from_millis(50),
                }),
            },
            OperationName::Query,
        );

        Python::attach(|py| {
            let value = error.value(py);
            assert_eq!(
                value
                    .getattr("operation")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "query"
            );
            assert_eq!(
                value.getattr("phase").unwrap().extract::<String>().unwrap(),
                "connect"
            );
        });
    }

    #[test]
    fn inherited_connect_failure_is_ignored_exactly_once() {
        let mut inherited_wave = Some(7);
        assert!(consume_inherited_failure(&mut inherited_wave, 7));
        assert_eq!(inherited_wave, None);
        assert!(!consume_inherited_failure(&mut inherited_wave, 7));

        let mut newer_wave = Some(8);
        assert!(!consume_inherited_failure(&mut newer_wave, 7));
        assert_eq!(newer_wave, Some(8));
    }

    #[test]
    fn checkout_action_matrix_keeps_reset_mandatory_and_health_optional() {
        assert_eq!(
            checkout_action(ConnectionDisposition::Clean, false),
            CheckoutAction::Ready
        );
        assert_eq!(
            checkout_action(ConnectionDisposition::NeedsReset, false),
            CheckoutAction::Reset
        );
        assert_eq!(
            checkout_action(ConnectionDisposition::Clean, true),
            CheckoutAction::Validate { reset: false }
        );
        assert_eq!(
            checkout_action(ConnectionDisposition::NeedsReset, true),
            CheckoutAction::Validate { reset: true }
        );
        for validate_on_checkout in [false, true] {
            assert_eq!(
                checkout_action(ConnectionDisposition::Broken, validate_on_checkout),
                CheckoutAction::Reject
            );
        }
    }

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

    #[test]
    fn structured_pre_wire_parameter_errors_preserve_connection_reuse() {
        Python::initialize();
        let error = create_parameter_conversion_error(
            0,
            "VARCHAR(3)",
            "length_overflow",
            "SQL parameter value is incompatible with its declared type",
        );

        assert!(python_error_allows_connection_reuse(&error));
        Python::attach(|py| {
            let value = error.value(py);
            assert!(
                !value
                    .getattr("wire_sent")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
            assert!(
                !value
                    .getattr("connection_discarded")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
            assert!(
                !value
                    .getattr("outcome_unknown")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
        });

        let generic = ConversionError::new_err("unclassified conversion failure");
        assert!(!python_error_allows_connection_reuse(&generic));
    }
}
