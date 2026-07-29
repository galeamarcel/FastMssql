use std::sync::Arc;

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyInt, PyList};
use pyo3_async_runtimes::tokio::future_into_py;
use tiberius::error::Error as TiberiusError;
use tiberius::{Config, SqlParameterType, TokenRow, validate_bulk_insert_columns};
use tokio::sync::RwLock;

use crate::azure_auth::PyAzureCredential;
use crate::deadline::{
    Deadline, DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until,
};
use crate::helpers::catch_driver_panic;
use crate::lifecycle::ConnectionLifecycle;
use crate::operation_metrics::{OperationMetricsRegistry, observe_operation};
use crate::parameter_conversion::python_to_target_column_data;
use crate::pool_config::PyPoolConfig;
use crate::pool_manager::{
    ConnectionPool, PooledOperationGuard, TiberiusClient, acquire_owned_operation_guard,
    ensure_pool_initialized_with_auth, timeout_error_or_metadata_failure,
};
use crate::sql_parameter_type::{ParameterTypeMetadata, parse_sql_parameter_type};
use crate::timeout_config::PyTimeoutConfig;
use crate::transaction::is_deterministic_commit_rejection;
use crate::types::{
    TimeoutErrorMetadata, create_commit_outcome_unknown, create_parameter_conversion_error,
    create_protocol_error, create_sql_error,
};

#[cfg(test)]
const DEFAULT_CHUNK_SIZE: usize = 1_000;
const MAX_CHUNK_SIZE: usize = 10_000;
const CONTROL_BEGIN: &str = "BEGIN TRANSACTION";
const CONTROL_COMMIT: &str = "COMMIT TRANSACTION";
const CONTROL_ROLLBACK: &str = "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION";

pub(crate) fn parse_native_chunk_size(value: &Bound<'_, PyAny>) -> PyResult<usize> {
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(PyTypeError::new_err(
            "chunk_size must be an integer between 1 and 10000",
        ));
    }

    let chunk_size = value
        .extract::<usize>()
        .map_err(|_| PyValueError::new_err("chunk_size must be an integer between 1 and 10000"))?;
    if !(1..=MAX_CHUNK_SIZE).contains(&chunk_size) {
        return Err(PyValueError::new_err(
            "chunk_size must be an integer between 1 and 10000",
        ));
    }
    Ok(chunk_size)
}

#[derive(Clone)]
pub(crate) struct NativeBulkTarget {
    table: String,
    columns: Vec<String>,
    column_count: usize,
    chunk_size: usize,
}

pub(crate) struct PreparedNativeBulk {
    target: NativeBulkTarget,
    rows: Py<PyList>,
    source_row_count: usize,
    row_start: usize,
    row_count: usize,
    row_index_base: usize,
}

impl PreparedNativeBulk {
    pub(crate) fn is_empty(&self) -> bool {
        self.row_count == 0
    }

    fn chunk_window(&self, start: usize) -> PyResult<Self> {
        let remaining = self.row_count.checked_sub(start).ok_or_else(|| {
            PyValueError::new_err("native bulk chunk offset exceeded the input row count")
        })?;
        let row_count = remaining.min(self.target.chunk_size);
        let row_start = self
            .row_start
            .checked_add(start)
            .ok_or_else(|| PyValueError::new_err("native bulk chunk offset overflowed usize"))?;
        let row_index_base = checked_native_bulk_row_index(self.row_index_base, start)?;
        validate_native_bulk_diagnostic_capacity(
            row_count,
            row_index_base,
            self.target.column_count,
        )?;

        Ok(Self {
            target: self.target.clone(),
            rows: Python::attach(|py| self.rows.clone_ref(py)),
            source_row_count: self.source_row_count,
            row_start,
            row_count,
            row_index_base,
        })
    }
}

fn native_bulk_diagnostic_overflow() -> PyErr {
    PyValueError::new_err("native bulk diagnostic index overflowed usize")
}

fn checked_native_bulk_row_index(row_index_base: usize, local_row_index: usize) -> PyResult<usize> {
    row_index_base
        .checked_add(local_row_index)
        .ok_or_else(native_bulk_diagnostic_overflow)
}

fn checked_native_bulk_parameter_index(
    row_index: usize,
    column_count: usize,
    column_index: usize,
) -> PyResult<usize> {
    row_index
        .checked_mul(column_count)
        .and_then(|offset| offset.checked_add(column_index))
        .ok_or_else(native_bulk_diagnostic_overflow)
}

fn validate_native_bulk_diagnostic_capacity(
    row_count: usize,
    row_index_base: usize,
    column_count: usize,
) -> PyResult<()> {
    if row_count == 0 {
        return Ok(());
    }

    let last_row_index = checked_native_bulk_row_index(row_index_base, row_count - 1)?;
    if column_count > 0 {
        checked_native_bulk_parameter_index(last_row_index, column_count, column_count - 1)?;
    }
    Ok(())
}

pub(crate) fn prepare_native_bulk_target(
    table: String,
    columns: Vec<String>,
    chunk_size: usize,
) -> PyResult<NativeBulkTarget> {
    if !(1..=MAX_CHUNK_SIZE).contains(&chunk_size) {
        return Err(PyValueError::new_err(
            "chunk_size must be an integer between 1 and 10000",
        ));
    }

    let column_refs = columns.iter().map(String::as_str).collect::<Vec<_>>();
    validate_bulk_insert_columns(&table, &column_refs).map_err(|_| {
        native_conversion_error(
            0,
            "identifier_validation_failed",
            "Native bulk identifier validation failed",
        )
    })?;

    Ok(NativeBulkTarget {
        table,
        column_count: columns.len(),
        columns,
        chunk_size,
    })
}

pub(crate) fn prepare_native_bulk_chunk(
    target: NativeBulkTarget,
    rows: &Bound<'_, PyList>,
    row_index_base: usize,
) -> PyResult<PreparedNativeBulk> {
    let row_count = rows.len();
    validate_native_bulk_diagnostic_capacity(row_count, row_index_base, target.column_count)?;

    Ok(PreparedNativeBulk {
        target,
        rows: rows.clone().unbind(),
        source_row_count: row_count,
        row_start: 0,
        row_count,
        row_index_base,
    })
}

pub(crate) fn prepare_native_bulk(
    table: String,
    columns: Vec<String>,
    rows: &Bound<'_, PyList>,
    chunk_size: usize,
) -> PyResult<PreparedNativeBulk> {
    let target = prepare_native_bulk_target(table, columns, chunk_size)?;
    prepare_native_bulk_chunk(target, rows, 0)
}

pub(crate) struct NativeBulkFailure {
    pub(crate) error: PyErr,
    pub(crate) any_row_sent: bool,
    pub(crate) protocol_reusable: bool,
}

impl NativeBulkFailure {
    fn new(error: PyErr, any_row_sent: bool, protocol_reusable: bool) -> Self {
        set_native_error_metadata(&error, any_row_sent, !protocol_reusable, false);
        Self {
            error,
            any_row_sent,
            protocol_reusable,
        }
    }
}

fn native_conversion_error(
    parameter_index: usize,
    reason: &'static str,
    message: &'static str,
) -> PyErr {
    create_parameter_conversion_error(parameter_index, "NATIVE_BULK", reason, message)
}

fn set_native_error_metadata(
    error: &PyErr,
    wire_sent: bool,
    connection_discarded: bool,
    outcome_unknown: bool,
) {
    Python::attach(|py| {
        let value = error.value(py);
        if value.getattr("retryable").is_err() {
            let _ = value.setattr("retryable", false);
        }
        let _ = value.setattr("wire_sent", wire_sent);
        let _ = value.setattr("connection_discarded", connection_discarded);
        let _ = value.setattr("outcome_unknown", outcome_unknown);
    });
}

pub(crate) fn set_native_connection_discarded(error: &PyErr, discarded: bool) {
    Python::attach(|py| {
        let _ = error.value(py).setattr("connection_discarded", discarded);
    });
}

fn attach_cell_context(
    error: PyErr,
    row_index: usize,
    column_index: usize,
    parameter_index: usize,
    wire_sent: bool,
) -> PyErr {
    Python::attach(|py| {
        let value = error.value(py);
        let _ = value.setattr("row_index", row_index);
        let _ = value.setattr("column_index", column_index);
        let _ = value.setattr("parameter_index", parameter_index);
        if value.getattr("sql_type").is_err() {
            let _ = value.setattr("sql_type", "NATIVE_BULK");
        }
        if value.getattr("reason").is_err() {
            let _ = value.setattr("reason", "bulk_conversion_failed");
        }
    });
    set_native_error_metadata(&error, wire_sent, false, false);
    error
}

fn attach_cleanup_cause(primary: PyErr, cleanup: PyErr) -> PyErr {
    Python::attach(|py| primary.set_cause(py, Some(cleanup)));
    primary
}

fn tiberius_start_error_allows_reuse(error: &TiberiusError) -> bool {
    matches!(error, TiberiusError::BulkInput(_))
        || matches!(error, TiberiusError::Server(server) if server.class() <= 19)
}

fn tiberius_wire_error_allows_reuse(error: &TiberiusError) -> bool {
    matches!(error, TiberiusError::Server(server) if server.class() <= 19)
}

fn map_bulk_start_error(error: TiberiusError, any_row_sent: bool) -> NativeBulkFailure {
    let protocol_reusable = tiberius_start_error_allows_reuse(&error);
    let error = match error {
        TiberiusError::BulkInput(_) => native_conversion_error(
            0,
            "unsupported_target",
            "Native bulk target metadata is unsupported",
        ),
        TiberiusError::Protocol(message) if message.starts_with("unsupported column type: ") => {
            native_conversion_error(
                0,
                "unsupported_target",
                "Native bulk target type is unsupported",
            )
        }
        other => create_sql_error(other, "Native bulk request setup failed"),
    };
    NativeBulkFailure::new(error, any_row_sent, protocol_reusable)
}

fn map_bulk_wire_error(error: TiberiusError, any_row_sent: bool) -> NativeBulkFailure {
    // `BulkLoadRequest::send()` encodes into the live request buffer before
    // returning a local BulkInput error. That buffer can contain a partial row,
    // so it cannot be finalized or followed by cleanup SQL safely.
    let protocol_reusable = tiberius_wire_error_allows_reuse(&error);
    let error = match error {
        TiberiusError::BulkInput(_) => {
            native_conversion_error(0, "bulk_encoding_failed", "Native bulk row encoding failed")
        }
        other => create_sql_error(other, "Native bulk request failed"),
    };
    NativeBulkFailure::new(error, any_row_sent, protocol_reusable)
}

fn validate_row_list<'py>(
    row: &Bound<'py, PyAny>,
    row_index: usize,
    column_count: usize,
    wire_sent: bool,
) -> PyResult<Bound<'py, PyList>> {
    let first_parameter_index = checked_native_bulk_parameter_index(row_index, column_count, 0)?;
    let row = row.cast::<PyList>().map_err(|_| {
        attach_cell_context(
            native_conversion_error(
                first_parameter_index,
                "row_must_be_list",
                "Each native bulk row must be a list",
            ),
            row_index,
            0,
            first_parameter_index,
            wire_sent,
        )
    })?;
    if row.len() != column_count {
        return Err(attach_cell_context(
            native_conversion_error(
                first_parameter_index,
                "row_width_mismatch",
                "Native bulk row width does not match the target column count",
            ),
            row_index,
            0,
            first_parameter_index,
            wire_sent,
        ));
    }
    Ok(row.clone())
}

fn validate_top_level_length(input: &PreparedNativeBulk) -> PyResult<()> {
    Python::attach(|py| {
        if input.rows.bind(py).len() != input.source_row_count {
            return Err(PyValueError::new_err(
                "native_bulk_insert rows must not be resized while the operation is running",
            ));
        }
        Ok(())
    })
}

fn convert_chunk(
    input: &PreparedNativeBulk,
    start: usize,
    targets: &[SqlParameterType],
    any_row_sent: bool,
) -> PyResult<Vec<TokenRow<'static>>> {
    Python::attach(|py| {
        let rows = input.rows.bind(py);
        if rows.len() != input.source_row_count {
            return Err(PyValueError::new_err(
                "native_bulk_insert rows must not be resized while the operation is running",
            ));
        }

        let end = start
            .checked_add(input.target.chunk_size)
            .ok_or_else(|| PyValueError::new_err("native bulk chunk offset overflowed usize"))?
            .min(input.row_count);
        let mut converted = Vec::with_capacity(end - start);

        for local_row_index in start..end {
            let source_row_index =
                input
                    .row_start
                    .checked_add(local_row_index)
                    .ok_or_else(|| {
                        PyValueError::new_err("native bulk chunk offset overflowed usize")
                    })?;
            let row_index = checked_native_bulk_row_index(input.row_index_base, local_row_index)?;
            let row = rows.get_item(source_row_index)?;
            let row = validate_row_list(&row, row_index, input.target.column_count, any_row_sent)?;
            let mut token_row = TokenRow::with_capacity(input.target.column_count);

            for (column_index, target) in targets.iter().enumerate() {
                let parameter_index = checked_native_bulk_parameter_index(
                    row_index,
                    input.target.column_count,
                    column_index,
                )?;
                let value = row.get_item(column_index)?;
                let converted_cell = python_to_target_column_data(&value, target, parameter_index)
                    .map_err(|error| {
                        attach_cell_context(
                            error,
                            row_index,
                            column_index,
                            parameter_index,
                            any_row_sent,
                        )
                    })?;
                token_row.push(converted_cell);
            }
            converted.push(token_row);
        }

        Ok(converted)
    })
}

async fn finalize_after_local_failure<S>(
    request: tiberius::BulkLoadRequest<'_, S>,
    primary: PyErr,
    any_row_sent: bool,
) -> NativeBulkFailure
where
    S: futures_util::io::AsyncRead + futures_util::io::AsyncWrite + Unpin + Send,
{
    match request.finalize().await {
        Ok(result) => {
            if result.total() == 0 {
                NativeBulkFailure::new(primary, any_row_sent, true)
            } else {
                let cleanup = create_protocol_error(
                    "Native bulk empty finalization returned an unexpected row count",
                );
                NativeBulkFailure::new(attach_cleanup_cause(primary, cleanup), any_row_sent, false)
            }
        }
        Err(error) => {
            let cleanup = create_sql_error(error, "Native bulk cleanup failed");
            NativeBulkFailure::new(attach_cleanup_cause(primary, cleanup), any_row_sent, false)
        }
    }
}

fn validate_native_bulk_affected_count(
    expected_row_count: usize,
    affected: u64,
    any_row_sent: bool,
) -> Result<u64, NativeBulkFailure> {
    let expected = u64::try_from(expected_row_count).map_err(|_| {
        NativeBulkFailure::new(
            create_protocol_error("Native bulk chunk row count overflowed u64"),
            any_row_sent,
            true,
        )
    })?;
    if affected != expected {
        return Err(NativeBulkFailure::new(
            create_protocol_error(
                "Native bulk response row count did not match the submitted chunk",
            ),
            any_row_sent,
            true,
        ));
    }
    Ok(affected)
}

pub(crate) async fn run_native_bulk_chunk(
    client: &mut TiberiusClient,
    input: &PreparedNativeBulk,
    any_prior_row_sent: bool,
) -> Result<u64, NativeBulkFailure> {
    if input.is_empty() {
        return Ok(0);
    }
    if input.row_count > input.target.chunk_size {
        return Err(NativeBulkFailure::new(
            PyValueError::new_err("native bulk chunk exceeded the configured chunk_size"),
            any_prior_row_sent,
            true,
        ));
    }

    validate_top_level_length(input)
        .map_err(|error| NativeBulkFailure::new(error, any_prior_row_sent, true))?;

    let column_refs = input
        .target
        .columns
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    let mut request = client
        .bulk_insert_columns(&input.target.table, &column_refs)
        .await
        .map_err(|error| map_bulk_start_error(error, any_prior_row_sent))?;

    let declarations = match request.column_declarations() {
        Ok(declarations) => declarations,
        Err(error) => {
            let primary = map_bulk_start_error(error, any_prior_row_sent).error;
            return Err(finalize_after_local_failure(request, primary, any_prior_row_sent).await);
        }
    };

    let mut targets = Vec::with_capacity(declarations.len());
    for (column_index, declaration) in declarations.iter().enumerate() {
        match parse_sql_parameter_type(
            declaration,
            ParameterTypeMetadata {
                precision: None,
                scale: None,
                length: None,
            },
        ) {
            Ok(target) => targets.push(target),
            Err(_) => {
                let primary = native_conversion_error(
                    column_index,
                    "unsupported_target",
                    "Native bulk target type is unsupported",
                );
                return Err(
                    finalize_after_local_failure(request, primary, any_prior_row_sent).await,
                );
            }
        }
    }

    let converted = match convert_chunk(input, 0, &targets, any_prior_row_sent) {
        Ok(converted) => converted,
        Err(primary) => {
            return Err(finalize_after_local_failure(request, primary, any_prior_row_sent).await);
        }
    };
    let chunk_row_count = converted.len();
    let mut any_row_sent = any_prior_row_sent;

    for row in converted {
        any_row_sent = true;
        if let Err(error) = request.send(row).await {
            return Err(map_bulk_wire_error(error, any_row_sent));
        }
    }

    let affected = request
        .finalize()
        .await
        .map_err(|error| map_bulk_wire_error(error, any_row_sent))?
        .total();
    let affected = validate_native_bulk_affected_count(chunk_row_count, affected, any_row_sent)?;
    validate_top_level_length(input)
        .map_err(|error| NativeBulkFailure::new(error, any_row_sent, true))?;
    Ok(affected)
}

pub(crate) async fn run_native_bulk_chunks(
    client: &mut TiberiusClient,
    input: &PreparedNativeBulk,
) -> Result<u64, NativeBulkFailure> {
    let mut start = 0usize;
    let mut total = 0u64;
    let mut any_row_sent = false;

    while start < input.row_count {
        let chunk = input
            .chunk_window(start)
            .map_err(|error| NativeBulkFailure::new(error, any_row_sent, true))?;
        let chunk_row_count = chunk.row_count;
        let affected = run_native_bulk_chunk(client, &chunk, any_row_sent).await?;
        any_row_sent |= chunk_row_count > 0;
        total = total.checked_add(affected).ok_or_else(|| {
            NativeBulkFailure::new(
                create_protocol_error("Native bulk cumulative row count overflowed u64"),
                any_row_sent,
                true,
            )
        })?;
        start = start.checked_add(chunk_row_count).ok_or_else(|| {
            NativeBulkFailure::new(
                create_protocol_error("Native bulk chunk offset overflowed usize"),
                any_row_sent,
                true,
            )
        })?;
    }

    Ok(total)
}

async fn consume_control_command(
    connection: &mut TiberiusClient,
    command: &'static str,
    context: &'static str,
) -> PyResult<()> {
    connection
        .simple_query(command)
        .await
        .map_err(|error| create_sql_error(error, context))?
        .into_results()
        .await
        .map_err(|error| create_sql_error(error, context))?;
    Ok(())
}

pub(crate) fn native_timeout_error(
    elapsed: DeadlineElapsed,
    connection_discarded: bool,
    outcome_unknown: bool,
) -> PyErr {
    timeout_error_or_metadata_failure(
        elapsed,
        TimeoutErrorMetadata {
            operation: OperationName::BulkInsert,
            retryable: false,
            connection_discarded,
            outcome_unknown,
        },
    )
}

async fn rollback_connection_failure(
    connection: &mut PooledOperationGuard<'_>,
    deadline: Option<Deadline>,
    mut failure: NativeBulkFailure,
) -> PyResult<u64> {
    if !failure.protocol_reusable {
        set_native_connection_discarded(&failure.error, true);
        return Err(failure.error);
    }

    let rollback = run_until(
        deadline,
        catch_driver_panic(consume_control_command(
            connection,
            CONTROL_ROLLBACK,
            "Failed to roll back native bulk transaction",
        )),
    )
    .await;

    match rollback {
        Ok(Ok(Ok(()))) => {
            connection.complete_success(false);
            set_native_connection_discarded(&failure.error, false);
            Err(failure.error)
        }
        Err(elapsed) => {
            let cleanup = native_timeout_error(elapsed, true, false);
            set_native_connection_discarded(&failure.error, true);
            failure.error = attach_cleanup_cause(failure.error, cleanup);
            Err(failure.error)
        }
        Ok(Err(driver_panic)) => {
            set_native_connection_discarded(&failure.error, true);
            failure.error = attach_cleanup_cause(failure.error, driver_panic);
            Err(failure.error)
        }
        Ok(Ok(Err(cleanup))) => {
            set_native_connection_discarded(&failure.error, true);
            failure.error = attach_cleanup_cause(failure.error, cleanup);
            Err(failure.error)
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn execute_connection_native_bulk(
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    azure_credential: Option<Arc<PyAzureCredential>>,
    input: PreparedNativeBulk,
) -> PyResult<u64> {
    let deadline = deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
    let pool = match run_until(
        deadline,
        ensure_pool_initialized_with_auth(
            pool,
            config,
            &pool_config,
            &timeout_config,
            azure_credential,
            OperationName::BulkInsert,
        ),
    )
    .await
    {
        Ok(result) => result?,
        Err(elapsed) => return Err(native_timeout_error(elapsed, false, false)),
    };
    let mut connection = match run_until(
        deadline,
        acquire_owned_operation_guard(
            &pool,
            OperationName::BulkInsert,
            timeout_config.acquire_timeout,
        ),
    )
    .await
    {
        Ok(result) => result?,
        Err(elapsed) => return Err(native_timeout_error(elapsed, false, false)),
    };

    let begin = run_until(
        deadline,
        catch_driver_panic(consume_control_command(
            &mut connection,
            CONTROL_BEGIN,
            "Failed to start native bulk transaction",
        )),
    )
    .await;
    match begin {
        Err(elapsed) => return Err(native_timeout_error(elapsed, true, false)),
        Ok(Err(driver_panic)) => return Err(driver_panic),
        Ok(Ok(Err(error))) => {
            connection.complete_error(&error, false);
            return Err(error);
        }
        Ok(Ok(Ok(()))) => {}
    }

    let bulk = run_until(
        deadline,
        catch_driver_panic(run_native_bulk_chunks(&mut connection, &input)),
    )
    .await;
    let total = match bulk {
        Err(elapsed) => return Err(native_timeout_error(elapsed, true, false)),
        Ok(Err(driver_panic)) => return Err(driver_panic),
        Ok(Ok(Err(failure))) => {
            return rollback_connection_failure(&mut connection, deadline, failure).await;
        }
        Ok(Ok(Ok(total))) => total,
    };

    let commit = run_until(
        deadline,
        catch_driver_panic(consume_control_command(
            &mut connection,
            CONTROL_COMMIT,
            "Failed to commit native bulk transaction",
        )),
    )
    .await;
    match commit {
        Ok(Ok(Ok(()))) => {
            connection.complete_success(false);
            Ok(total)
        }
        Err(elapsed) => {
            create_commit_outcome_unknown(native_timeout_error(elapsed, true, true)).and_then(Err)
        }
        Ok(Err(driver_panic)) => create_commit_outcome_unknown(driver_panic).and_then(Err),
        Ok(Ok(Err(error))) if is_deterministic_commit_rejection(&error) => Err(error),
        Ok(Ok(Err(error))) => create_commit_outcome_unknown(error).and_then(Err),
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn connection_native_bulk_insert<'p>(
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    operation_metrics: Option<Arc<OperationMetricsRegistry>>,
    py: Python<'p>,
    input: PreparedNativeBulk,
) -> PyResult<Bound<'p, PyAny>> {
    if input.is_empty() {
        return future_into_py(py, async move { Ok(0u64) });
    }

    future_into_py(py, async move {
        observe_operation(operation_metrics, OperationName::BulkInsert, async move {
            let permit = lifecycle.admit_operation(OperationName::BulkInsert, true)?;
            permit
                .run(execute_connection_native_bulk(
                    pool,
                    config,
                    pool_config,
                    timeout_config,
                    azure_credential,
                    input,
                ))
                .await
        })
        .await
    })
}

#[cfg(test)]
mod tests {
    use super::{
        DEFAULT_CHUNK_SIZE, NativeBulkTarget, convert_chunk, parse_native_chunk_size,
        prepare_native_bulk, prepare_native_bulk_chunk, prepare_native_bulk_target,
        tiberius_start_error_allows_reuse, tiberius_wire_error_allows_reuse,
        validate_native_bulk_affected_count,
    };
    use pyo3::IntoPyObjectExt;
    use pyo3::prelude::*;
    use pyo3::types::PyList;
    use std::ffi::CString;
    use tiberius::SqlParameterType;
    use tiberius::error::Error as TiberiusError;

    fn python_rows<'py>(py: Python<'py>, expression: &str) -> PyResult<Bound<'py, PyList>> {
        let expression = CString::new(expression).expect("fixed test expression has no NUL");
        py.eval(&expression, None, None)?
            .cast_into::<PyList>()
            .map_err(Into::into)
    }

    #[test]
    fn native_chunk_size_rejects_bool_and_out_of_range_values() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            for invalid in [
                true.into_py_any(py)?,
                0_i64.into_py_any(py)?,
                (-1_i64).into_py_any(py)?,
                10_001_i64.into_py_any(py)?,
                1.5_f64.into_py_any(py)?,
            ] {
                assert!(parse_native_chunk_size(invalid.bind(py)).is_err());
            }
            assert_eq!(
                parse_native_chunk_size(1_000_i64.into_py_any(py)?.bind(py))?,
                DEFAULT_CHUNK_SIZE
            );
            Ok(())
        })
    }

    #[test]
    fn bulk_input_is_reusable_only_before_the_live_wire_request() {
        Python::initialize();
        let error = TiberiusError::BulkInput("local row encoding failed".into());

        assert!(tiberius_start_error_allows_reuse(&error));
        assert!(!tiberius_wire_error_allows_reuse(&error));
    }

    #[test]
    fn native_bulk_target_validation_is_not_repeated_for_chunks() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            assert!(
                prepare_native_bulk_target(
                    "[dbo].[already_quoted_target]".to_owned(),
                    vec!["id".to_owned()],
                    1,
                )
                .is_err()
            );

            let forged_target = NativeBulkTarget {
                table: "[dbo].[already_quoted_target]".to_owned(),
                columns: vec!["id".to_owned()],
                column_count: 1,
                chunk_size: 1,
            };
            let rows = python_rows(py, "[[1]]")?;
            let input = prepare_native_bulk_chunk(forged_target, &rows, 0)?;

            assert_eq!(input.row_count, 1);
            assert_eq!(input.row_index_base, 0);
            Ok(())
        })
    }

    #[test]
    fn native_bulk_chunk_uses_checked_global_diagnostic_indices() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let target = prepare_native_bulk_target(
                "dbo.native_bulk_global_index".to_owned(),
                vec!["left_value".to_owned(), "right_value".to_owned()],
                2,
            )?;
            let rows = python_rows(py, "[[1, 2], [3, 'do-not-leak']]")?;
            let input = prepare_native_bulk_chunk(target, &rows, 5)?;
            let error = match convert_chunk(
                &input,
                0,
                &[SqlParameterType::int(), SqlParameterType::int()],
                false,
            ) {
                Ok(_) => panic!("the second row must fail integer conversion"),
                Err(error) => error,
            };

            assert_eq!(error.value(py).getattr("row_index")?.extract::<usize>()?, 6);
            assert_eq!(
                error
                    .value(py)
                    .getattr("column_index")?
                    .extract::<usize>()?,
                1
            );
            assert_eq!(
                error
                    .value(py)
                    .getattr("parameter_index")?
                    .extract::<usize>()?,
                13
            );
            assert!(!error.to_string().contains("do-not-leak"));
            Ok(())
        })
    }

    #[test]
    fn native_bulk_chunk_rejects_global_index_overflow_before_execution() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let target = prepare_native_bulk_target(
                "dbo.native_bulk_overflow".to_owned(),
                vec!["left_value".to_owned(), "right_value".to_owned()],
                2,
            )?;
            let two_rows = python_rows(py, "[[1, 2], [3, 4]]")?;
            let row_overflow =
                match prepare_native_bulk_chunk(target.clone(), &two_rows, usize::MAX) {
                    Ok(_) => panic!("row_index_base plus local index must be checked"),
                    Err(error) => error,
                };
            assert!(
                row_overflow
                    .to_string()
                    .contains("diagnostic index overflowed usize")
            );

            let one_row = python_rows(py, "[[1, 2]]")?;
            let parameter_overflow = match prepare_native_bulk_chunk(target, &one_row, usize::MAX) {
                Ok(_) => panic!("global parameter index must be checked"),
                Err(error) => error,
            };
            assert!(
                parameter_overflow
                    .to_string()
                    .contains("diagnostic index overflowed usize")
            );
            Ok(())
        })
    }

    #[test]
    fn native_bulk_list_adapter_uses_global_row_base_zero() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let rows = python_rows(py, "[[1], [2]]")?;
            let input = prepare_native_bulk(
                "dbo.native_bulk_list_adapter".to_owned(),
                vec!["id".to_owned()],
                &rows,
                1,
            )?;

            assert_eq!(input.row_index_base, 0);
            assert_eq!(input.row_count, 2);
            Ok(())
        })
    }

    #[test]
    fn native_bulk_chunk_requires_the_exact_affected_count() {
        Python::initialize();
        let exact = match validate_native_bulk_affected_count(3, 3, true) {
            Ok(affected) => affected,
            Err(_) => panic!("the exact count must pass"),
        };
        assert_eq!(exact, 3);

        let failure = validate_native_bulk_affected_count(3, 2, true)
            .expect_err("a short server count must fail");
        assert!(failure.any_row_sent);
        assert!(
            failure
                .error
                .to_string()
                .contains("did not match the submitted chunk")
        );
    }
}
