use std::fmt::Write;

use crate::azure_auth::PyAzureCredential;
use crate::deadline::{DeadlineElapsed, OperationName, TimeoutPhase, deadline_from, run_until};
use crate::helpers::{
    catch_driver_panic, execute_unparameterized_command, requires_connection_retirement,
    requires_direct_batch,
};
use crate::lifecycle::ConnectionLifecycle;
use crate::operation_metrics::{OperationMetricsRegistry, observe_operation};
use crate::parameter_conversion::{
    FastParameter, FastParameterValue, MAX_USER_QUERY_PARAMETERS, TypedNull,
    convert_parameters_to_fast, params_as_sql_refs, python_to_single_fast_parameter,
};
use crate::pool_config::PyPoolConfig;
use crate::pool_manager::{
    ConnectionPool, PooledOperationGuard, connect_client_with_timeout,
    ensure_pool_initialized_with_auth, map_pool_checkout_error, timeout_error_or_metadata_failure,
};
use crate::timeout_config::PyTimeoutConfig;
use crate::types::{TimeoutErrorMetadata, create_sql_error};
use pyo3::exceptions::{PyOverflowError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTypeMethods};
use pyo3_async_runtimes::tokio::future_into_py;
use smallvec::SmallVec;
use std::sync::Arc;
use tiberius::Config;
use tokio::net::TcpStream;
use tokio::sync::RwLock;

type SqlClient = tiberius::Client<tokio_util::compat::Compat<TcpStream>>;

const MAX_BULK_PARAMETERS_PER_INSERT: usize = 2_000;
const MAX_ROWS_PER_VALUES_INSERT: usize = 1_000;

fn attach_batch_validation_context(error: PyErr, py: Python<'_>, batch_index: usize) -> PyErr {
    let value = error.value(py);
    let error_name = value
        .get_type()
        .name()
        .ok()
        .and_then(|name| name.extract::<String>().ok())
        .unwrap_or_else(|| "Exception".to_owned());
    let original_message = value
        .getattr("args")
        .ok()
        .and_then(|args| args.get_item(0).ok())
        .and_then(|message| message.extract::<String>().ok());

    if let Some(original_message) = original_message {
        let contextual_message = format!(
            "Batch item {batch_index} parameter validation failed: \
             {error_name}: {original_message}"
        );
        let _ = value.setattr("args", (contextual_message,));
    }
    let _ = value.setattr("batch_index", batch_index);

    error
}

fn bulk_rows_per_batch(column_count: usize) -> usize {
    debug_assert!(column_count > 0);
    (MAX_BULK_PARAMETERS_PER_INSERT / column_count).clamp(1, MAX_ROWS_PER_VALUES_INSERT)
}

fn attach_bulk_conversion_context(
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
            let _ = value.setattr("sql_type", "INFERRED");
        }
        if value.getattr("reason").is_err() {
            let _ = value.setattr("reason", "bulk_conversion_failed");
        }
        let _ = value.setattr("wire_sent", wire_sent);
        let _ = value.setattr("connection_discarded", wire_sent);
    });
    error
}

async fn consume_simple_command(
    connection: &mut SqlClient,
    command: &str,
    error_context: &'static str,
) -> PyResult<()> {
    connection
        .simple_query(command)
        .await
        .map_err(|error| create_sql_error(error, error_context))?
        .into_results()
        .await
        .map_err(|error| create_sql_error(error, error_context))?;
    Ok(())
}

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

fn attach_cleanup_cause(primary: PyErr, cleanup: PyErr) -> PyErr {
    Python::attach(|py| primary.set_cause(py, Some(cleanup)));
    primary
}

async fn rollback_after_failure(
    connection: &mut SqlClient,
    timeout_config: &PyTimeoutConfig,
    operation: OperationName,
    error_context: &'static str,
) -> PyResult<()> {
    let deadline = deadline_from(TimeoutPhase::Rollback, timeout_config.rollback_timeout);
    match run_until(
        deadline,
        catch_driver_panic(consume_simple_command(
            connection,
            "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION",
            error_context,
        )),
    )
    .await
    {
        Err(elapsed) => Err(operation_timeout_error(elapsed, operation, false)),
        Ok(Err(driver_panic)) => Err(driver_panic),
        Ok(Ok(result)) => result,
    }
}

/// Parses batch items (SQL queries with parameters) from a Python list.
pub fn parse_batch_items<'p>(
    items: &Bound<'p, PyList>,
    py: Python<'p>,
) -> PyResult<Vec<(String, SmallVec<[FastParameter; 16]>)>> {
    let mut batch_items = Vec::with_capacity(items.len());

    for (batch_index, item) in items.iter().enumerate() {
        let tuple = item.cast::<pyo3::types::PyTuple>().map_err(|_| {
            PyValueError::new_err("Each batch item must be a tuple of (sql, parameters)")
        })?;

        if tuple.len() != 2 {
            return Err(PyValueError::new_err(
                "Tuple must contain exactly 2 elements",
            ));
        }

        let sql: String = tuple.get_item(0)?.extract()?;
        let params_py = tuple.get_item(1)?;

        let fast_params = if params_py.is_none() {
            SmallVec::new()
        } else {
            convert_parameters_to_fast(Some(&params_py), py)
                .map_err(|error| attach_batch_validation_context(error, py, batch_index))?
        };

        if fast_params.len() > MAX_USER_QUERY_PARAMETERS {
            return Err(PyValueError::new_err(format!(
                "Batch item {} exceeds FastMssql user parameter limit: {} parameters provided, maximum is 2,098",
                batch_index,
                fast_params.len()
            )));
        }

        batch_items.push((sql, fast_params));
    }

    Ok(batch_items)
}

/// Internal helper: Execute batch commands on an existing connection without transaction management.
/// Used by both Connection (with automatic transaction) and Transaction (with manual control).
pub async fn execute_batch_on_connection(
    conn: &mut tiberius::Client<tokio_util::compat::Compat<tokio::net::TcpStream>>,
    batch_commands: Vec<(String, SmallVec<[FastParameter; 16]>)>,
) -> PyResult<Vec<u64>> {
    let mut all_results = Vec::with_capacity(batch_commands.len());

    for (sql, parameters) in batch_commands {
        // Fast path: skip SmallVec construction entirely for parameter-free statements
        // (common for DDL like CREATE TABLE inside a batch).
        let affected = if parameters.is_empty() && requires_direct_batch(&sql) {
            execute_unparameterized_command(conn, &sql, "Batch item failed").await?
        } else {
            let tiberius_params = params_as_sql_refs(&parameters);
            conn.execute(sql, &tiberius_params)
                .await
                .map_err(|e| create_sql_error(e, "Batch item failed"))?
                .rows_affected()
                .iter()
                .sum()
        };

        all_results.push(affected);
    }

    Ok(all_results)
}

/// Internal helper: Execute batch queries on an existing connection.
/// Used by both Connection and Transaction classes.
pub async fn query_batch_on_connection(
    conn: &mut tiberius::Client<tokio_util::compat::Compat<tokio::net::TcpStream>>,
    batch_queries: Vec<(String, SmallVec<[FastParameter; 16]>)>,
) -> PyResult<Vec<Vec<tiberius::Row>>> {
    let mut all_results = Vec::with_capacity(batch_queries.len());

    for (query, parameters) in batch_queries {
        // Fast path: skip SmallVec construction entirely for parameter-free queries.
        let stream = if parameters.is_empty() {
            conn.query(&query, &[])
                .await
                .map_err(|e| create_sql_error(e, "Batch query execution failed"))?
        } else {
            let tiberius_params = params_as_sql_refs(&parameters);
            conn.query(&query, &tiberius_params)
                .await
                .map_err(|e| create_sql_error(e, "Batch query execution failed"))?
        };

        let rows = stream
            .into_first_result()
            .await
            .map_err(|e| create_sql_error(e, "Failed to get batch results"))?;

        all_results.push(rows);
    }

    Ok(all_results)
}

pub fn execute_batch<'p>(
    config: Arc<Config>,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    operation_metrics: Option<Arc<OperationMetricsRegistry>>,
    py: Python<'p>,
    commands: &Bound<'p, PyList>,
) -> PyResult<Bound<'p, PyAny>> {
    let batch_commands = parse_batch_items(commands, py)?;

    future_into_py(py, async move {
        observe_operation(operation_metrics, OperationName::ExecuteBatch, async move {
            let permit = lifecycle.admit_operation(OperationName::ExecuteBatch, true)?;
            permit
                .run(async move {
                    // ── Safety: dedicated connection, not a pooled one ─────────────────────────
                    //
                    // execute_batch wraps all commands in a single BEGIN / COMMIT transaction.
                    // If the caller's coroutine is cancelled (e.g. asyncio.Task.cancel()) while
                    // the transaction is open, the Rust future is dropped.  With a *pooled*
                    // connection the guard would silently return the connection to the pool with
                    // an open BEGIN TRANSACTION, corrupting the state seen by the next caller.
                    //
                    // By using a *dedicated* TCP connection instead:
                    //   • If the future is dropped, the TCP socket is closed by the OS.
                    //   • SQL Server detects the broken connection and automatically rolls back.
                    //   • The shared pool is never touched, so no poisoning is possible.
                    //
                    // The cost (one extra TCP + TDS handshake per batch call) is acceptable
                    // because batch operations are inherently heavy and latency-tolerant.
                    // ───────────────────────────────────────────────────────────────────────────

                    let mut conn = connect_client_with_timeout(
                        &config,
                        azure_credential.as_ref(),
                        timeout_config.connect_timeout,
                        OperationName::ExecuteBatch,
                    )
                    .await?;

                    let deadline =
                        deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
                    let mut transaction_started = false;
                    let operation = run_until(
                        deadline,
                        catch_driver_panic(async {
                            consume_simple_command(
                                &mut conn,
                                "BEGIN TRANSACTION",
                                "Failed to start transaction",
                            )
                            .await?;
                            transaction_started = true;

                            let all_results =
                                execute_batch_on_connection(&mut conn, batch_commands).await?;

                            consume_simple_command(
                                &mut conn,
                                "COMMIT TRANSACTION",
                                "Failed to commit batch transaction",
                            )
                            .await?;
                            transaction_started = false;
                            Ok::<Vec<u64>, PyErr>(all_results)
                        }),
                    )
                    .await;

                    let all_results = match operation {
                        Err(elapsed) => {
                            return Err(operation_timeout_error(
                                elapsed,
                                OperationName::ExecuteBatch,
                                true,
                            ));
                        }
                        Ok(Err(driver_panic)) => return Err(driver_panic),
                        Ok(Ok(Ok(results))) => results,
                        Ok(Ok(Err(primary))) => {
                            if transaction_started
                                && let Err(cleanup) = rollback_after_failure(
                                    &mut conn,
                                    &timeout_config,
                                    OperationName::ExecuteBatch,
                                    "Failed to roll back batch transaction",
                                )
                                .await
                            {
                                return Err(attach_cleanup_cause(primary, cleanup));
                            }
                            return Err(primary);
                        }
                    };

                    // conn drops here — TCP connection closed cleanly.
                    // On future cancellation the OS closes the socket; SQL Server rolls back.

                    Python::attach(|py| {
                        let py_list = PyList::new(py, all_results)?;
                        Ok(py_list.into_any().unbind())
                    })
                })
                .await
        })
        .await
    })
}

#[allow(clippy::too_many_arguments)]
pub fn query_batch<'p>(
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    operation_metrics: Option<Arc<OperationMetricsRegistry>>,
    py: Python<'p>,
    queries: &Bound<'p, PyList>,
) -> PyResult<Bound<'p, PyAny>> {
    let batch_queries = parse_batch_items(queries, py)?;
    let retire_after_operation = batch_queries
        .iter()
        .any(|(sql, _)| requires_connection_retirement(sql));

    let pool = Arc::clone(&pool);
    let config = Arc::clone(&config);
    let pool_config = pool_config.clone();

    future_into_py(py, async move {
        observe_operation(operation_metrics, OperationName::QueryBatch, async move {
            let permit = lifecycle.admit_operation(OperationName::QueryBatch, true)?;
            permit
                .run(async move {
                    let pool_ref = ensure_pool_initialized_with_auth(
                        pool,
                        config,
                        &pool_config,
                        &timeout_config,
                        azure_credential,
                        OperationName::QueryBatch,
                    )
                    .await?;

                    let pooled = pool_ref.get().await.map_err(|error| {
                        map_pool_checkout_error(
                            error,
                            OperationName::QueryBatch,
                            timeout_config.acquire_timeout,
                        )
                    })?;
                    let mut conn = PooledOperationGuard::new(pooled);

                    let deadline =
                        deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
                    let operation = run_until(
                        deadline,
                        catch_driver_panic(query_batch_on_connection(&mut conn, batch_queries)),
                    )
                    .await;
                    let all_results = match operation {
                        Err(elapsed) => {
                            return Err(operation_timeout_error(
                                elapsed,
                                OperationName::QueryBatch,
                                true,
                            ));
                        }
                        Ok(Ok(result)) => {
                            conn.complete_with_result_and_retirement(
                                &result,
                                retire_after_operation,
                            );
                            result?
                        }
                        Ok(Err(driver_panic)) => return Err(driver_panic),
                    };

                    Python::attach(|py| -> PyResult<Py<PyAny>> {
                        let mut py_results = Vec::with_capacity(all_results.len());
                        for result in all_results {
                            let query_stream =
                                crate::types::PyQueryStream::from_tiberius_rows(result, py)?;
                            let py_result = Py::new(py, query_stream)?;
                            py_results.push(py_result.into_any());
                        }
                        let py_list = PyList::new(py, py_results)?;
                        Ok(py_list.into_any().unbind())
                    })
                })
                .await
        })
        .await
    })
}

/// Wraps a single SQL Server identifier part in square brackets and escapes `]` as `]]`.
///
/// Returns `Err` if `part` contains a null byte (`\x00`).  Null bytes are the only
/// character not neutralised by bracket-quoting: some driver layers and C-string APIs
/// treat `\x00` as a string terminator, which could silently truncate the identifier
/// and produce unintended SQL.  All other Unicode characters — including right-to-left
/// override codepoints (U+202E etc.) — are inert inside `[...]` and require no special
/// handling because SQL Server parses bracket-quoted names literally at the byte level.
fn quote_identifier_part(part: &str) -> PyResult<String> {
    if part.contains('\x00') {
        return Err(PyValueError::new_err(
            "Identifier contains a null byte (\\x00), which is not allowed in SQL Server identifiers",
        ));
    }
    let mut quoted = String::with_capacity(part.len() + 2);
    quoted.push('[');
    for ch in part.chars() {
        if ch == ']' {
            quoted.push(']'); // escape ] by doubling
        }
        quoted.push(ch);
    }
    quoted.push(']');
    Ok(quoted)
}

/// Quotes a (possibly multipart) SQL Server identifier, handling forms like:
///   table, schema.table, db.schema.table, db..table
///
/// Each dot-separated part is independently bracket-quoted so that:
/// - `dbo.users`     → `[dbo].[users]`
/// - `mydb..users`   → `[mydb]..[users]`  (empty middle part preserved as-is)
/// - `users`         → `[users]`
///
/// Returns `Err` (propagated from [`quote_identifier_part`]) if any identifier part
/// contains a null byte.
fn quote_identifier(name: &str) -> PyResult<String> {
    let parts: Vec<&str> = name.split('.').collect();
    let mut result = String::with_capacity(name.len() + parts.len() * 2);
    for (i, part) in parts.iter().enumerate() {
        if i > 0 {
            result.push('.');
        }
        if part.is_empty() {
            // preserve empty parts (e.g. the middle segment in db..table)
        } else {
            result.push_str(&quote_identifier_part(part)?);
        }
    }
    Ok(result)
}

/// Fix untyped (U8/tinyint) NULL placeholders in a flat row-major buffer.
///
/// When Python `None` is converted with `python_to_fast_parameter` it becomes
/// `Null(TypedNull::U8)` — a tinyint-typed NULL.  In a multi-row VALUES INSERT
/// SQL Server reconciles parameter types across the same column position in
/// every row, so a tinyint null alongside a nvarchar value causes a conversion
/// error.  This function scans each column, infers the correct type from the
/// first non-null sibling value, and patches every untyped NULL in that column.
fn fix_bulk_null_types(flat_data: &mut [FastParameter], col_count: usize) {
    if col_count == 0 || flat_data.is_empty() {
        return;
    }
    let row_count = flat_data.len() / col_count;

    for col in 0..col_count {
        // Infer the null type from the first non-null value in this column.
        let null_type = (0..row_count)
            .map(|row| &flat_data[row * col_count + col])
            .find_map(|parameter| match &parameter.value {
                FastParameterValue::String(_) => Some(TypedNull::String),
                FastParameterValue::U8(_) => Some(TypedNull::U8),
                FastParameterValue::I16(_) => Some(TypedNull::I16),
                FastParameterValue::I32(_) => Some(TypedNull::I32),
                FastParameterValue::I64(_) => Some(TypedNull::I64),
                FastParameterValue::F32(_) => Some(TypedNull::F32),
                FastParameterValue::F64(_) => Some(TypedNull::F64),
                FastParameterValue::Bool(_) => Some(TypedNull::Bit),
                FastParameterValue::Bytes(_) => Some(TypedNull::Binary),
                FastParameterValue::Xml(_) => Some(TypedNull::Xml),
                FastParameterValue::Numeric(_) => Some(TypedNull::Numeric),
                FastParameterValue::Date(_) => Some(TypedNull::Date),
                FastParameterValue::Time(_) => Some(TypedNull::Time),
                FastParameterValue::DateTime(_) => Some(TypedNull::DateTime),
                FastParameterValue::DateTimeOffset(_) => Some(TypedNull::DateTimeOffset),
                FastParameterValue::TdsDate(_) => Some(TypedNull::Date),
                FastParameterValue::TdsTime(_) => Some(TypedNull::Time),
                FastParameterValue::TdsDateTime(_) => Some(TypedNull::DateTime),
                FastParameterValue::TdsSmallDateTime(_) => Some(TypedNull::SmallDateTime),
                FastParameterValue::TdsDateTime2(_) => Some(TypedNull::DateTime2),
                FastParameterValue::TdsDateTimeOffset(_) => Some(TypedNull::DateTimeOffset),
                FastParameterValue::Uuid(_) => Some(TypedNull::Guid),
                FastParameterValue::Null(_) => None,
            })
            .unwrap_or(TypedNull::String); // all-null column → nvarchar null is safe

        // Patch every untyped Null in this column.
        for row in 0..row_count {
            let idx = row * col_count + col;
            let is_untyped_null = !flat_data[idx].has_explicit_sql_type()
                && matches!(
                    &flat_data[idx].value,
                    FastParameterValue::Null(TypedNull::U8)
                );
            if is_untyped_null {
                flat_data[idx].value = FastParameterValue::Null(null_type.clone());
            }
        }
    }
}

fn convert_bulk_chunk(
    data_rows: &Py<PyList>,
    start: usize,
    rows_per_batch: usize,
    col_count: usize,
    expected_row_count: usize,
) -> PyResult<Vec<FastParameter>> {
    Python::attach(|py| {
        let rows = data_rows.bind(py);
        if rows.len() != expected_row_count {
            return Err(PyValueError::new_err(
                "bulk_insert data_rows must not be resized while the operation is running",
            ));
        }
        if start >= expected_row_count {
            return Err(PyValueError::new_err(
                "bulk_insert chunk offset is outside the captured input",
            ));
        }

        let remaining = expected_row_count - start;
        let end = start + rows_per_batch.min(remaining);
        let mut chunk = Vec::with_capacity((end - start) * col_count);
        let wire_sent = start > 0;

        for row_index in start..end {
            let row = rows.get_item(row_index)?;
            let row = row.cast::<PyList>()?;
            if row.len() != col_count {
                return Err(PyValueError::new_err(format!(
                    "Row has {} values but {} columns specified",
                    row.len(),
                    col_count
                )));
            }
            for (column_index, value) in row.iter().enumerate() {
                let parameter_index = row_index
                    .checked_mul(col_count)
                    .and_then(|offset| offset.checked_add(column_index))
                    .ok_or_else(|| {
                        PyOverflowError::new_err(
                            "bulk_insert parameter diagnostic index overflowed usize",
                        )
                    })?;
                let parameter =
                    python_to_single_fast_parameter(&value, parameter_index).map_err(|error| {
                        attach_bulk_conversion_context(
                            error,
                            row_index,
                            column_index,
                            parameter_index,
                            wire_sent,
                        )
                    })?;
                chunk.push(parameter);
            }
        }

        fix_bulk_null_types(&mut chunk, col_count);
        Ok(chunk)
    })
}

#[allow(clippy::too_many_arguments)]
pub fn bulk_insert<'p>(
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    config: Arc<Config>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
    azure_credential: Option<Arc<PyAzureCredential>>,
    operation_metrics: Option<Arc<OperationMetricsRegistry>>,
    py: Python<'p>,
    table_name: String,
    columns: Vec<String>,
    data_rows: &Bound<'p, PyList>,
) -> PyResult<Bound<'p, PyAny>> {
    if columns.is_empty() {
        return Err(PyValueError::new_err(
            "At least one column must be specified",
        ));
    }

    let col_count = columns.len();

    // Respect both SQL Server limits: at most 1,000 row constructors in one
    // INSERT ... VALUES statement and a conservative 2,000 parameters.
    let rows_per_batch = bulk_rows_per_batch(col_count);
    let row_count = data_rows.len();

    // Validate and quote all identifiers before acquiring a lease or opening a
    // server-side transaction.
    let quoted_table = quote_identifier(&table_name)?;
    let columns_sql = columns
        .iter()
        .map(|column| quote_identifier(column))
        .collect::<PyResult<Vec<_>>>()?
        .join(", ");

    if row_count == 0 {
        return future_into_py(py, async move { Ok(0u64) });
    }

    // Keep the concrete Python input alive without duplicating its cells.
    // Conversion happens inside the returned awaitable, one SQL chunk at a
    // time, after the previous chunk has been fully consumed.
    let data_rows = data_rows.clone().unbind();

    future_into_py(py, async move {
        observe_operation(operation_metrics, OperationName::BulkInsert, async move {
            let permit = lifecycle.admit_operation(OperationName::BulkInsert, true)?;
            permit
                .run(async move {
                    let deadline =
                        deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
                    let mut start = 0usize;
                    let mut chunk = convert_bulk_chunk(
                        &data_rows,
                        start,
                        rows_per_batch,
                        col_count,
                        row_count,
                    )?;

                    let pool_ref = ensure_pool_initialized_with_auth(
                        pool,
                        config,
                        &pool_config,
                        &timeout_config,
                        azure_credential,
                        OperationName::BulkInsert,
                    )
                    .await?;

                    let pooled = pool_ref.get().await.map_err(|error| {
                        map_pool_checkout_error(
                            error,
                            OperationName::BulkInsert,
                            timeout_config.acquire_timeout,
                        )
                    })?;
                    let mut conn = PooledOperationGuard::new(pooled);

                    let mut transaction_started = false;
                    let operation = run_until(
                        deadline,
                        catch_driver_panic(async {
                            consume_simple_command(
                                &mut conn,
                                "BEGIN TRANSACTION",
                                "Failed to start bulk transaction",
                            )
                            .await?;
                            transaction_started = true;

                            let mut total_affected = 0u64;

                            loop {
                                let row_count_in_batch = chunk.len() / col_count;
                                let mut sql = String::with_capacity(
                                    100 + row_count_in_batch * (col_count * 5),
                                );
                                sql.push_str("INSERT INTO ");
                                sql.push_str(&quoted_table);
                                sql.push_str(" (");
                                sql.push_str(&columns_sql);
                                sql.push_str(") VALUES ");

                                for row in 0..row_count_in_batch {
                                    if row > 0 {
                                        sql.push(',');
                                    }
                                    sql.push('(');
                                    for column in 1..=col_count {
                                        if column > 1 {
                                            sql.push(',');
                                        }
                                        sql.push('@');
                                        sql.push('P');
                                        let parameter_number = (row * col_count) + column;
                                        let _ = write!(sql, "{}", parameter_number);
                                    }
                                    sql.push(')');
                                }

                                {
                                    let mut params: SmallVec<[&dyn tiberius::ToSql; 128]> =
                                        SmallVec::with_capacity(chunk.len());
                                    for parameter in &chunk {
                                        params.push(parameter as &dyn tiberius::ToSql);
                                    }

                                    let result =
                                        conn.execute(sql, &params).await.map_err(|error| {
                                            create_sql_error(error, "Batch execution failed")
                                        })?;
                                    for affected in result.rows_affected() {
                                        total_affected = total_affected
                                            .checked_add(*affected)
                                            .ok_or_else(|| {
                                                PyOverflowError::new_err(
                                                    "bulk_insert affected-row count overflowed u64",
                                                )
                                            })?;
                                    }
                                }

                                start += row_count_in_batch;
                                if start >= row_count {
                                    break;
                                }
                                // Rust evaluates an assignment's right-hand side before
                                // dropping its previous left-hand value. Drop explicitly so
                                // converted values from two chunks are never live together.
                                drop(chunk);
                                chunk = convert_bulk_chunk(
                                    &data_rows,
                                    start,
                                    rows_per_batch,
                                    col_count,
                                    row_count,
                                )?;
                            }

                            consume_simple_command(
                                &mut conn,
                                "COMMIT TRANSACTION",
                                "Failed to commit bulk transaction",
                            )
                            .await?;
                            transaction_started = false;
                            Ok::<u64, PyErr>(total_affected)
                        }),
                    )
                    .await;

                    let total_affected = match operation {
                        Err(elapsed) => {
                            return Err(operation_timeout_error(
                                elapsed,
                                OperationName::BulkInsert,
                                true,
                            ));
                        }
                        Ok(Err(driver_panic)) => return Err(driver_panic),
                        Ok(Ok(Ok(total))) => {
                            conn.complete();
                            total
                        }
                        Ok(Ok(Err(primary))) => {
                            conn.observe_error(&primary);
                            let result = if transaction_started {
                                rollback_after_failure(
                                    &mut conn,
                                    &timeout_config,
                                    OperationName::BulkInsert,
                                    "Failed to roll back bulk transaction",
                                )
                                .await
                            } else {
                                Ok(())
                            };
                            match result {
                                Ok(()) => {
                                    conn.complete();
                                    return Err(primary);
                                }
                                Err(cleanup) => {
                                    return Err(attach_cleanup_cause(primary, cleanup));
                                }
                            }
                        }
                    };

                    Python::attach(|py| {
                        let res = total_affected.into_pyobject(py)?;
                        Ok(res.into_any().unbind())
                    })
                })
                .await
        })
        .await
    })
}

#[cfg(test)]
mod tests {
    use super::{attach_bulk_conversion_context, bulk_rows_per_batch, fix_bulk_null_types};
    use crate::parameter_conversion::{
        FastParameterValue, TypedNull, python_to_single_fast_parameter,
    };
    use crate::py_parameters::Parameter;
    use crate::types::{ConversionError, create_parameter_conversion_error};
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use tiberius::ToSql;

    #[test]
    fn bulk_chunking_respects_row_constructor_and_parameter_limits() {
        assert_eq!(bulk_rows_per_batch(1), 1_000);
        assert_eq!(bulk_rows_per_batch(2), 1_000);
        assert_eq!(bulk_rows_per_batch(3), 666);
        assert_eq!(bulk_rows_per_batch(2_000), 1);
    }

    #[test]
    fn bulk_null_inference_preserves_explicit_tinyint_null_metadata() {
        Python::initialize();
        let mut flat_data = Python::attach(|py| {
            let descriptor = Parameter::new(
                py.None(),
                Some("TINYINT".to_owned()),
                "INPUT",
                None,
                None,
                None,
                None,
            )
            .expect("the test descriptor must be valid");
            let descriptor = Py::new(py, descriptor).expect("the test descriptor must allocate");
            let explicit = python_to_single_fast_parameter(descriptor.bind(py).as_any(), 0)
                .expect("the typed NULL must convert");
            let raw_integer = 42i64
                .into_pyobject(py)
                .expect("the raw integer must bind")
                .into_any();
            let inferred = python_to_single_fast_parameter(&raw_integer, 1)
                .expect("the raw integer must convert");
            vec![explicit, inferred]
        });

        fix_bulk_null_types(&mut flat_data, 1);

        assert!(matches!(
            flat_data[0].value,
            FastParameterValue::Null(TypedNull::U8)
        ));
        assert_eq!(
            flat_data[0]
                .sql_parameter_type()
                .expect("explicit SQL type metadata must remain present")
                .declaration(),
            "TINYINT"
        );
    }

    #[test]
    fn bulk_conversion_context_retains_error_identity_and_safe_metadata() {
        Python::initialize();
        let typed = create_parameter_conversion_error(
            3,
            "INT",
            "wrong_value_kind",
            "Python value has the wrong kind for the declared SQL type",
        );
        let original_pointer = Python::attach(|py| typed.value(py).as_ptr());
        let typed = attach_bulk_conversion_context(typed, 1, 1, 3, true);

        Python::attach(|py| {
            let value = typed.value(py);
            assert_eq!(value.as_ptr(), original_pointer);
            assert!(typed.is_instance_of::<ConversionError>(py));
            assert_eq!(
                value
                    .getattr("row_index")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                1
            );
            assert_eq!(
                value
                    .getattr("column_index")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                1
            );
            assert_eq!(
                value
                    .getattr("parameter_index")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                3
            );
            assert_eq!(
                value
                    .getattr("sql_type")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "INT"
            );
            assert_eq!(
                value
                    .getattr("reason")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "wrong_value_kind"
            );
            assert!(
                value
                    .getattr("wire_sent")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
            assert!(
                value
                    .getattr("connection_discarded")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
        });

        let raw = attach_bulk_conversion_context(
            PyValueError::new_err("safe raw conversion failure"),
            0,
            0,
            0,
            false,
        );
        Python::attach(|py| {
            let value = raw.value(py);
            assert!(raw.is_instance_of::<PyValueError>(py));
            assert_eq!(
                value
                    .getattr("sql_type")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "INFERRED"
            );
            assert_eq!(
                value
                    .getattr("reason")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "bulk_conversion_failed"
            );
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
        });
    }
}
