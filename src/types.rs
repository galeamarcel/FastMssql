use crate::deadline::{DeadlineElapsed, OperationName};
use crate::lifecycle_config::ConnectionLifecycleState;
use crate::type_mapping;
use ahash::AHashMap as HashMap;
use pyo3::exceptions::{PyException, PyRuntimeError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::{create_exception, exceptions::PyValueError};
use std::sync::Arc;
use std::time::Duration;
use tiberius::{ColumnType, Row, error::Error as TError};

create_exception!(crate::fastmssql, SqlError, PyException);
create_exception!(crate::fastmssql, SqlConnectionError, PyException);
create_exception!(crate::fastmssql, OperationTimeoutError, SqlConnectionError);
create_exception!(
    crate::fastmssql,
    ConnectionLifecycleError,
    SqlConnectionError
);
create_exception!(
    crate::fastmssql,
    ShutdownTimeoutError,
    ConnectionLifecycleError
);
create_exception!(crate::fastmssql, CommitOutcomeUnknown, PyException);
create_exception!(crate::fastmssql, TlsError, PyException);
create_exception!(crate::fastmssql, ProtocolError, PyException);
create_exception!(crate::fastmssql, ConversionError, PyException);

const UNKNOWN_COMMIT_MESSAGE: &str =
    "COMMIT completion was not confirmed; the transaction outcome is unknown";

pub(crate) struct TimeoutErrorMetadata {
    pub(crate) operation: OperationName,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct LifecycleErrorMetadata {
    pub(crate) operation: OperationName,
    pub(crate) state: ConnectionLifecycleState,
    pub(crate) generation: u64,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
    pub(crate) forced: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ShutdownTimeoutMetadata {
    pub(crate) generation: u64,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
    pub(crate) active_operations_at_timeout: usize,
    pub(crate) active_transactions_at_timeout: usize,
    pub(crate) force_completed: bool,
}

pub(crate) fn create_lifecycle_error(metadata: LifecycleErrorMetadata) -> PyResult<PyErr> {
    let message = if metadata.forced {
        format!(
            "{} was interrupted by forced connection shutdown in generation {}",
            metadata.operation.as_str(),
            metadata.generation
        )
    } else {
        format!(
            "{} was rejected while connection generation {} was {}",
            metadata.operation.as_str(),
            metadata.generation,
            metadata.state.as_str()
        )
    };
    Python::attach(|py| {
        let error = ConnectionLifecycleError::new_err(message.clone());
        let value = error.value(py);
        value.setattr("message", message)?;
        value.setattr("operation", metadata.operation.as_str())?;
        value.setattr("phase", "shutdown")?;
        value.setattr("state", metadata.state.as_str())?;
        value.setattr("generation", metadata.generation)?;
        value.setattr("retryable", metadata.retryable)?;
        value.setattr("connection_discarded", metadata.connection_discarded)?;
        value.setattr("outcome_unknown", metadata.outcome_unknown)?;
        value.setattr("forced", metadata.forced)?;
        Ok(error)
    })
}

pub(crate) fn create_shutdown_timeout_error(metadata: ShutdownTimeoutMetadata) -> PyResult<PyErr> {
    let shutdown_seconds = metadata.shutdown_timeout.as_secs_f64();
    let force_seconds = metadata.force_timeout.as_secs_f64();
    let message = format!(
        "disconnect exceeded the {:.6} second graceful shutdown budget; \
         forced retirement completed={}",
        shutdown_seconds, metadata.force_completed
    );
    Python::attach(|py| {
        let error = ShutdownTimeoutError::new_err(message.clone());
        let value = error.value(py);
        value.setattr("message", message)?;
        value.setattr("operation", OperationName::Disconnect.as_str())?;
        value.setattr("phase", "shutdown")?;
        value.setattr("state", ConnectionLifecycleState::Closed.as_str())?;
        value.setattr("generation", metadata.generation)?;
        value.setattr("retryable", false)?;
        value.setattr("connection_discarded", true)?;
        value.setattr("outcome_unknown", false)?;
        value.setattr("forced", true)?;
        value.setattr("shutdown_timeout_seconds", shutdown_seconds)?;
        value.setattr("force_timeout_seconds", force_seconds)?;
        value.setattr(
            "active_operations_at_timeout",
            metadata.active_operations_at_timeout,
        )?;
        value.setattr(
            "active_transactions_at_timeout",
            metadata.active_transactions_at_timeout,
        )?;
        value.setattr("force_completed", metadata.force_completed)?;
        Ok(error)
    })
}

pub(crate) fn create_operation_timeout_error(
    elapsed: DeadlineElapsed,
    metadata: TimeoutErrorMetadata,
) -> PyResult<PyErr> {
    let phase = elapsed.phase.as_str();
    let seconds = elapsed.timeout.as_secs_f64();
    let message = format!(
        "{} timed out in {} phase after {:.6} seconds",
        metadata.operation.as_str(),
        phase,
        seconds
    );
    Python::attach(|py| {
        let error = OperationTimeoutError::new_err(message.clone());
        let value = error.value(py);
        value.setattr("message", message)?;
        value.setattr("operation", metadata.operation.as_str())?;
        value.setattr("phase", phase)?;
        value.setattr("timeout_seconds", seconds)?;
        value.setattr("retryable", metadata.retryable)?;
        value.setattr("connection_discarded", metadata.connection_discarded)?;
        value.setattr("outcome_unknown", metadata.outcome_unknown)?;
        Ok(error)
    })
}

pub(crate) fn create_commit_outcome_unknown(cause: PyErr) -> PyResult<PyErr> {
    Python::attach(|py| {
        let error = CommitOutcomeUnknown::new_err(UNKNOWN_COMMIT_MESSAGE);
        {
            let value = error.value(py);
            value.setattr("message", UNKNOWN_COMMIT_MESSAGE)?;
            value.setattr("operation", "commit")?;
            value.setattr("retryable", false)?;
            value.setattr("connection_discarded", true)?;
        }
        error.set_cause(py, Some(cause));
        Ok(error)
    })
}

fn is_tls_io_failure(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("certificate")
        || lower.contains("tls")
        || lower.contains("ssl")
        || lower.contains("handshake")
        || lower.contains("invalid peer")
        || lower.contains("unknown issuer")
}

pub fn create_sql_error(err: TError, base: &'static str) -> PyErr {
    match err {
        TError::Server(s) => {
            let code = s.code();
            let message = s.message().to_string();
            let state = s.state();
            let severity = s.class();
            Python::attach(|py| {
                let exc = SqlError::new_err(message.clone());
                {
                    let value = exc.value(py);
                    let _ = value.setattr("code", code);
                    let _ = value.setattr("message", message.as_str());
                    let _ = value.setattr("state", state);
                    let _ = value.setattr("class", severity);
                    let _ = value.setattr("severity", severity);
                }
                exc
            })
        }
        TError::Io { kind: _, message } if is_tls_io_failure(&message) => {
            create_tls_error(format!("{base}: {message}"), message)
        }
        TError::Io { kind: _, message } => Python::attach(|py| {
            let exc = SqlConnectionError::new_err(format!("{base}: {message}"));
            let _ = exc.value(py).setattr("message", message.as_str());
            exc
        }),
        TError::Tls(message) => create_tls_error(format!("{base}: {message}"), message),
        TError::Routing { host, port } => {
            let message = format!("server redirected to {host}:{port}");
            Python::attach(|py| {
                let exc = SqlConnectionError::new_err(format!("{base}: {message}"));
                {
                    let value = exc.value(py);
                    let _ = value.setattr("message", message.as_str());
                    let _ = value.setattr("host", host.as_str());
                    let _ = value.setattr("port", port);
                }
                exc
            })
        }
        TError::Protocol(msg) => {
            let message = msg.into_owned();
            Python::attach(|py| {
                let exc = ProtocolError::new_err(format!("{base}: {message}"));
                let _ = exc.value(py).setattr("message", message.as_str());
                exc
            })
        }
        TError::Encoding(msg) => {
            let message = format!("encoding error: {msg}");
            Python::attach(|py| {
                let exc = ConversionError::new_err(format!("{base}: {message}"));
                let _ = exc.value(py).setattr("message", message.as_str());
                exc
            })
        }
        TError::Conversion(msg) => {
            let message = msg.into_owned();
            Python::attach(|py| {
                let exc = ConversionError::new_err(format!("{base}: {message}"));
                let _ = exc.value(py).setattr("message", message.as_str());
                exc
            })
        }
        TError::ParameterConversion {
            parameter_index,
            sql_type,
            reason,
            message,
        } => create_parameter_conversion_error(parameter_index, &sql_type, &reason, &message),
        _ => PyRuntimeError::new_err(format!("{base}: {err}")),
    }
}

/// Creates a `SqlConnectionError` with the `.message` attribute set to the provided message.
pub fn create_connection_error(message: impl Into<String>) -> PyErr {
    let message = message.into();
    Python::attach(|py| {
        let exc = SqlConnectionError::new_err(message.clone());
        let _ = exc.value(py).setattr("message", message.as_str());
        exc
    })
}

fn create_tls_error(rendered_message: impl Into<String>, detail: impl Into<String>) -> PyErr {
    let rendered_message = rendered_message.into();
    let detail = detail.into();
    Python::attach(|py| {
        let exc = TlsError::new_err(rendered_message);
        let _ = exc.value(py).setattr("message", detail.as_str());
        exc
    })
}

pub fn create_protocol_error(message: impl Into<String>) -> PyErr {
    let message = message.into();
    Python::attach(|py| {
        let exc = ProtocolError::new_err(message.clone());
        let _ = exc.value(py).setattr("message", message.as_str());
        exc
    })
}

pub(crate) fn create_parameter_conversion_error(
    parameter_index: usize,
    sql_type: &str,
    reason: &str,
    message: &str,
) -> PyErr {
    Python::attach(|py| {
        let error = ConversionError::new_err(message.to_owned());
        {
            let value = error.value(py);
            let _ = value.setattr("message", message);
            let _ = value.setattr("parameter_index", parameter_index);
            let _ = value.setattr("sql_type", sql_type);
            let _ = value.setattr("reason", reason);
            let _ = value.setattr("retryable", false);
            let _ = value.setattr("wire_sent", false);
            let _ = value.setattr("connection_discarded", false);
            let _ = value.setattr("outcome_unknown", false);
        }
        error
    })
}

#[cfg(test)]
mod error_classification_tests {
    use super::{ConversionError, create_sql_error, is_tls_io_failure};
    use pyo3::Python;
    use pyo3::types::PyAnyMethods;
    use tiberius::error::Error as TError;

    #[test]
    fn certificate_and_handshake_io_messages_are_tls_failures() {
        assert!(is_tls_io_failure(
            "invalid peer certificate: Other(UnsupportedCertVersion)"
        ));
        assert!(is_tls_io_failure("TLS handshake failed"));
        assert!(is_tls_io_failure("unknown issuer"));
        assert!(!is_tls_io_failure("Connection refused (os error 61)"));
        assert!(!is_tls_io_failure("connection reset by peer"));
    }

    #[test]
    fn typed_driver_conversion_metadata_reaches_python_unchanged() {
        Python::initialize();
        let error = create_sql_error(
            TError::ParameterConversion {
                parameter_index: 7,
                sql_type: "VARCHAR(4)".to_owned(),
                reason: "encoding_error".to_owned(),
                message: "SQL parameter value cannot use the active code page".to_owned(),
            },
            "Query execution failed",
        );

        Python::attach(|py| {
            assert!(error.is_instance_of::<ConversionError>(py));
            let value = error.value(py);
            assert_eq!(
                value
                    .getattr("parameter_index")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                7
            );
            assert_eq!(
                value
                    .getattr("sql_type")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "VARCHAR(4)"
            );
            assert_eq!(
                value
                    .getattr("reason")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "encoding_error"
            );
            assert!(
                !value
                    .getattr("retryable")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
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
            assert!(
                !value
                    .getattr("outcome_unknown")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap()
            );
        });
    }
}

/// Memory-optimized to share column metadata across all rows in a result set.
/// Holds shared column information for a result set to reduce memory usage.
/// This is shared across all `PyFastRow` instances in a result set.
#[derive(Debug)]
pub struct ColumnInfo {
    /// Ordered list of column names
    pub names: Vec<String>,
    /// Map from column name to its index for fast lookups
    pub map: HashMap<String, usize>,
    /// Cached column types (one per column) to avoid repeated lookups during value conversion
    pub column_types: Vec<ColumnType>,
}

/// Memory-optimized to share column metadata across all rows in a result set.
#[pyclass(name = "FastRow", from_py_object)]
pub struct PyFastRow {
    // Row values stored in column order for cache-friendly access
    values: Vec<Py<PyAny>>,
    // Shared pointer to column metadata for the entire result set
    column_info: Arc<ColumnInfo>,
}

impl Clone for PyFastRow {
    fn clone(&self) -> Self {
        Python::attach(|py| PyFastRow {
            values: self.values.iter().map(|v| v.clone_ref(py)).collect(),
            column_info: Arc::clone(&self.column_info),
        })
    }
}

impl PyFastRow {
    /// Create a new PyFastRow from a Tiberius row and shared column info
    pub fn from_tiberius_row(row: Row, py: Python, column_info: Arc<ColumnInfo>) -> PyResult<Self> {
        // Pre-allocate vector with exact capacity and cache num_columns to avoid repeated lookups
        let num_columns = column_info.names.len();
        let mut values = Vec::with_capacity(num_columns);

        // Eagerly convert all values in column order using cached column types
        for i in 0..num_columns {
            let col_type = column_info
                .column_types
                .get(i)
                .copied()
                .ok_or_else(|| PyValueError::new_err("Column type not found"))?;
            let value = Self::extract_value_direct(&row, i, col_type, py)?;
            values.push(value);
        }

        Ok(PyFastRow {
            values,
            column_info,
        })
    }

    /// Convert value directly from Tiberius to Python using centralized type mapping
    /// Uses cached column type to avoid repeated lookups
    #[inline]
    fn extract_value_direct(
        row: &Row,
        index: usize,
        col_type: ColumnType,
        py: Python,
    ) -> PyResult<Py<PyAny>> {
        type_mapping::sql_to_python(row, index, col_type, py)
    }
}

#[pymethods]
impl PyFastRow {
    /// Ultra-fast column access using shared column map and direct Vec indexing
    pub fn __getitem__(&self, py: Python, key: Bound<PyAny>) -> PyResult<Py<PyAny>> {
        // Try string extraction first (most common case)
        if let Ok(name) = key.extract::<&str>() {
            // Access by name: O(1) hash lookup + O(1) Vec access
            if let Some(&index) = self.column_info.map.get(name) {
                Ok(self.values[index].clone_ref(py))
            } else {
                Err(PyValueError::new_err(format!(
                    "Column '{}' not found",
                    name
                )))
            }
        } else if let Ok(index) = key.extract::<isize>() {
            // Access by index: Direct O(1) Vec access - extremely fast!
            // Normalise negative indices the same way Python sequences do.
            let len = self.values.len() as isize;
            let actual = if index < 0 { len + index } else { index };
            if actual < 0 || actual >= len {
                Err(pyo3::exceptions::PyIndexError::new_err(
                    "Column index out of range",
                ))
            } else {
                Ok(self.values[actual as usize].clone_ref(py))
            }
        } else {
            Err(PyValueError::new_err("Key must be string or integer"))
        }
    }

    /// Get all column names from shared column info - returns slice to avoid cloning
    pub fn columns(&self) -> &[String] {
        &self.column_info.names
    }

    /// Get number of columns
    pub fn __len__(&self) -> usize {
        self.column_info.names.len()
    }

    /// Get a specific column value by name
    pub fn get(&self, py: Python, column: &str) -> PyResult<Py<PyAny>> {
        self.__getitem__(py, column.into_pyobject(py)?.into_any())
    }

    /// Get a value by column index
    pub fn get_by_index(&self, py: Python, index: usize) -> PyResult<Py<PyAny>> {
        self.__getitem__(py, index.into_pyobject(py)?.into_any())
    }

    /// Get all values as a list - optimized to minimize cloning
    pub fn values(&self, py: Python) -> PyResult<Py<pyo3::types::PyList>> {
        Ok(pyo3::types::PyList::new(py, &self.values)?.into())
    }

    /// Convert to dictionary - optimized with zip iterator
    pub fn to_dict(&self, py: Python) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);

        for (name, value) in self.column_info.names.iter().zip(self.values.iter()) {
            dict.set_item(name, value)?;
        }

        Ok(dict.into())
    }

    /// String representation
    pub fn __str__(&self) -> String {
        format!("FastRow with {} columns", self.column_info.names.len())
    }

    /// Detailed representation
    pub fn __repr__(&self) -> String {
        format!("FastRow(columns={:?})", self.column_info.names)
    }
}

/// Helper to build column info from the first row
/// Caches both column names and types for efficient value conversion
fn build_column_info(first_row: &Row) -> Arc<ColumnInfo> {
    let mut names = Vec::with_capacity(first_row.columns().len());
    let mut column_types = Vec::with_capacity(first_row.columns().len());
    let mut map = HashMap::with_capacity(first_row.columns().len());

    for col in first_row.columns().iter() {
        let name = col.name().to_string();
        names.push(name);
        column_types.push(col.column_type());
    }

    // Build map after names are finalized to avoid clone
    for (i, name) in names.iter().enumerate() {
        map.insert(name.clone(), i);
    }

    Arc::new(ColumnInfo {
        names,
        map,
        column_types,
    })
}

/// A streaming wrapper around a Tiberius QueryStream
/// Implements async iteration to fetch rows one at a time
/// Lazy conversion: stores raw rows, converts to Python on-demand, caches for reset()
#[pyclass(name = "QueryStream")]
pub struct PyQueryStream {
    // Store raw Tiberius rows in Option (Row doesn't impl Clone, so we take() on first access)
    tiberius_rows: Vec<Option<Row>>,
    // Cache of converted rows (parallel to tiberius_rows, None = not yet converted)
    converted_cache: Vec<Option<PyFastRow>>,
    column_info: Option<Arc<ColumnInfo>>,
    position: usize,
    is_complete: bool,
}

#[pymethods]
impl PyQueryStream {
    /// Return self for synchronous iteration protocol (for row in result:)
    pub fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Get the next row in synchronous iteration
    /// Returns the next FastRow, or raises StopIteration when complete
    pub fn __next__(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.position < self.tiberius_rows.len() {
            let fast_row = self.get_or_convert_row(py, self.position)?;
            self.position += 1;
            Py::new(py, fast_row).map(|p| p.into_any())
        } else {
            // All rows have been iterated
            self.is_complete = true;
            Err(pyo3::exceptions::PyStopIteration::new_err(""))
        }
    }

    /// Get a row by index or a slice of rows
    /// Supports negative indexing and slicing: result[0], result[-1], result[5:10]
    pub fn __getitem__(&mut self, py: Python<'_>, key: Bound<PyAny>) -> PyResult<Py<PyAny>> {
        // Handle slice
        if let Ok(slice) = key.cast::<pyo3::types::PySlice>() {
            let indices = slice.indices(self.tiberius_rows.len() as isize)?;
            let start = indices.start as usize;
            let stop = indices.stop as usize;
            let step = indices.step;

            if step != 1 {
                return Err(PyValueError::new_err("Slice step must be 1"));
            }

            // Handle empty slice - return empty list immediately
            if start >= stop || self.tiberius_rows.is_empty() {
                return Ok(pyo3::types::PyList::empty(py).into());
            }

            let mut row_list = Vec::with_capacity(stop - start);
            for i in start..stop {
                let fast_row = self.get_or_convert_row(py, i)?;
                row_list.push(Py::new(py, fast_row)?.into_any());
            }

            let py_list = pyo3::types::PyList::new(py, row_list)?;
            return Ok(py_list.into());
        }

        // Handle single index
        if let Ok(index) = key.extract::<isize>() {
            let len = self.tiberius_rows.len() as isize;
            let actual_index = if index < 0 {
                if index.abs() > len {
                    return Err(pyo3::exceptions::PyIndexError::new_err(
                        "Index out of range",
                    ));
                }
                (len + index) as usize
            } else {
                index as usize
            };

            if actual_index >= self.tiberius_rows.len() {
                return Err(pyo3::exceptions::PyIndexError::new_err(
                    "Index out of range",
                ));
            }

            let fast_row = self.get_or_convert_row(py, actual_index)?;

            return Py::new(py, fast_row).map(|p| p.into_any());
        }

        Err(PyValueError::new_err("Index must be an integer or slice"))
    }

    /// Load all remaining rows at once
    /// Returns a list of PyFastRow objects
    pub fn all(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let remaining_count = self.tiberius_rows.len() - self.position;
        let mut row_list = Vec::with_capacity(remaining_count);

        if remaining_count == 0 {
            return Ok(pyo3::types::PyList::empty(py).into());
        }

        for i in self.position..self.tiberius_rows.len() {
            let fast_row = self.get_or_convert_row(py, i)?;
            let py_row = Py::new(py, fast_row)?;
            row_list.push(py_row.into_any());
        }

        self.position = self.tiberius_rows.len();
        let py_list = pyo3::types::PyList::new(py, row_list)?;
        Ok(py_list.into())
    }

    /// Get the next N rows as a batch
    pub fn fetch(&mut self, py: Python<'_>, n: usize) -> PyResult<Py<PyAny>> {
        let end = std::cmp::min(self.position + n, self.tiberius_rows.len());
        let batch_size = end - self.position;
        let mut row_list = Vec::with_capacity(batch_size);

        if batch_size == 0 {
            return Ok(pyo3::types::PyList::empty(py).into());
        }

        for i in self.position..end {
            let fast_row = self.get_or_convert_row(py, i)?;
            let py_row = Py::new(py, fast_row)?;
            row_list.push(py_row.into_any());
        }

        self.position = end;
        let py_list = pyo3::types::PyList::new(py, row_list)?;
        Ok(py_list.into())
    }

    /// Get column names
    pub fn columns(&self) -> PyResult<Vec<String>> {
        match &self.column_info {
            Some(info) => Ok(info.names.clone()),
            None => Err(PyValueError::new_err("No column information available")),
        }
    }

    /// Reset iteration to the beginning
    pub fn reset(&mut self) {
        self.position = 0;
    }

    /// Get current position in the stream
    pub fn position(&self) -> usize {
        self.position
    }

    /// Get total number of rows
    pub fn len(&self) -> usize {
        self.tiberius_rows.len()
    }

    /// Support for Python's len() builtin
    pub fn __len__(&self) -> usize {
        self.tiberius_rows.len()
    }

    /// Check if stream is empty
    pub fn is_empty(&self) -> bool {
        self.tiberius_rows.is_empty()
    }

    /// Backwards compatibility: check if stream has rows
    pub fn has_rows(&self) -> bool {
        !self.tiberius_rows.is_empty()
    }

    /// Backwards compatibility: get all rows at once (returns to beginning)
    pub fn rows(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        // Reset to beginning and return all rows
        self.position = 0;
        self.all(py)
    }

    /// Backwards compatibility: fetch one row
    pub fn fetchone(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyFastRow>>> {
        if self.position < self.tiberius_rows.len() {
            let fast_row = self.get_or_convert_row(py, self.position)?;
            self.position += 1;
            Ok(Some(Py::new(py, fast_row)?))
        } else {
            Ok(None)
        }
    }

    /// Backwards compatibility: fetch many rows
    pub fn fetchmany(&mut self, py: Python<'_>, n: usize) -> PyResult<Py<PyAny>> {
        self.fetch(py, n)
    }

    /// Backwards compatibility: fetch all rows
    pub fn fetchall(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.all(py)
    }
}

impl PyQueryStream {
    /// Private helper: check cache → convert from tiberius row → cache result
    fn get_or_convert_row(&mut self, py: Python<'_>, index: usize) -> PyResult<PyFastRow> {
        if let Some(cached) = &self.converted_cache[index] {
            Ok(cached.clone())
        } else {
            let row = self.tiberius_rows[index]
                .take()
                .ok_or_else(|| PyValueError::new_err("Row already consumed"))?;
            let column_info = self
                .column_info
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("No column info"))?;
            let fast_row = PyFastRow::from_tiberius_row(row, py, Arc::clone(column_info))?;
            self.converted_cache[index] = Some(fast_row.clone());
            Ok(fast_row)
        }
    }

    /// Create a new QueryStream from Tiberius rows
    /// LAZY: stores raw rows, NO Python conversion (minimal GIL hold)
    /// Rows converted on-demand during iteration and cached for reset()
    pub fn from_tiberius_rows(tiberius_rows: Vec<tiberius::Row>, _py: Python) -> PyResult<Self> {
        if tiberius_rows.is_empty() {
            return Ok(PyQueryStream {
                tiberius_rows: Vec::new(),
                converted_cache: Vec::new(),
                column_info: None,
                position: 0,
                is_complete: false,
            });
        }

        let first_row = &tiberius_rows[0];
        let column_info = build_column_info(first_row);

        let row_count = tiberius_rows.len();

        let converted_cache: Vec<Option<PyFastRow>> = vec![None; row_count];

        let wrapped_rows: Vec<Option<Row>> = tiberius_rows.into_iter().map(Some).collect();

        Ok(PyQueryStream {
            tiberius_rows: wrapped_rows,
            converted_cache,
            column_info: Some(column_info),
            position: 0,
            is_complete: false,
        })
    }
}
