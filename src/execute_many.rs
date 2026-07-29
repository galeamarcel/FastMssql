use pyo3::exceptions::{PyOverflowError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyInt, PyList};
use smallvec::SmallVec;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use crate::helpers::{execute_unparameterized_command, requires_direct_batch};
use crate::parameter_conversion::{FastParameter, convert_parameters_to_fast, params_as_sql_refs};
use crate::pool_manager::{TiberiusClient, python_error_allows_connection_reuse};
use crate::py_parameters::Parameters;
use crate::types::{CommitOutcomeUnknown, create_sql_error};

pub(crate) const MAX_EXECUTE_MANY_CHUNK_SIZE: usize = 10_000;
const NO_ACTIVE_PARAMETER_SET: usize = usize::MAX;

pub(crate) fn parse_execute_many_atomic(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if !value.is_instance_of::<PyBool>() {
        return Err(PyTypeError::new_err("atomic must be exactly bool"));
    }
    value
        .extract::<bool>()
        .map_err(|_| PyTypeError::new_err("atomic must be exactly bool"))
}

pub(crate) fn parse_execute_many_chunk_size(value: &Bound<'_, PyAny>) -> PyResult<usize> {
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(PyTypeError::new_err(
            "chunk_size must be an integer between 1 and 10000",
        ));
    }
    let chunk_size = value
        .extract::<usize>()
        .map_err(|_| PyValueError::new_err("chunk_size must be an integer between 1 and 10000"))?;
    validate_execute_many_chunk_size(chunk_size)
}

pub(crate) fn validate_execute_many_chunk_size(chunk_size: usize) -> PyResult<usize> {
    if !(1..=MAX_EXECUTE_MANY_CHUNK_SIZE).contains(&chunk_size) {
        return Err(PyValueError::new_err(
            "chunk_size must be between 1 and 10,000",
        ));
    }
    Ok(chunk_size)
}

pub(crate) fn attach_execute_many_error_metadata(
    error: PyErr,
    parameter_set_index: usize,
    confirmed_committed_parameter_sets: usize,
    partial_commit_possible: bool,
) -> PyErr {
    Python::attach(|py| {
        let value = error.value(py);
        let _ = value.setattr("parameter_set_index", parameter_set_index);
        let _ = value.setattr(
            "confirmed_committed_parameter_sets",
            confirmed_committed_parameter_sets,
        );
        let _ = value.setattr("partial_commit_possible", partial_commit_possible);
    });
    error
}

pub(crate) fn set_execute_many_operation(error: &PyErr) {
    Python::attach(|py| {
        if error.is_instance_of::<CommitOutcomeUnknown>(py)
            || error.value(py).getattr("operation").is_ok()
        {
            let _ = error.value(py).setattr("operation", "execute_many");
        }
    });
}

#[derive(Debug, Default)]
pub(crate) struct ExecuteManyCounts {
    chunk_total: u64,
    total: u64,
}

impl ExecuteManyCounts {
    #[cfg(test)]
    fn with_totals(chunk_total: u64, total: u64) -> Self {
        Self { chunk_total, total }
    }

    pub(crate) fn begin_chunk(&mut self) {
        self.chunk_total = 0;
    }

    pub(crate) fn add_item(&mut self, affected: u64) -> PyResult<()> {
        let chunk_total = self.chunk_total.checked_add(affected).ok_or_else(|| {
            PyOverflowError::new_err("execute_many chunk affected-row count overflowed u64")
        })?;
        let total = self.total.checked_add(affected).ok_or_else(|| {
            PyOverflowError::new_err("execute_many total affected-row count overflowed u64")
        })?;
        self.chunk_total = chunk_total;
        self.total = total;
        Ok(())
    }

    pub(crate) fn chunk_total(&self) -> u64 {
        self.chunk_total
    }

    #[cfg(test)]
    pub(crate) fn total(&self) -> u64 {
        self.total
    }
}

pub(crate) fn invalid_parameter_set_type(index: usize) -> PyErr {
    PyTypeError::new_err(format!(
        "execute_many parameter set at index {index} must be a list or Parameters object"
    ))
}

pub(crate) struct PreparedExecuteManyChunk {
    parameter_sets: Vec<SmallVec<[FastParameter; 16]>>,
    start_index: usize,
}

impl PreparedExecuteManyChunk {
    pub(crate) fn len(&self) -> usize {
        self.parameter_sets.len()
    }

    pub(crate) fn start_index(&self) -> usize {
        self.start_index
    }
}

pub(crate) fn prepare_execute_many_chunk(
    parameter_sets: &Bound<'_, PyList>,
    start_index: usize,
    chunk_size: usize,
) -> PyResult<PreparedExecuteManyChunk> {
    validate_execute_many_chunk_size(chunk_size)?;
    let parameter_set_count = parameter_sets.len();
    if parameter_set_count == 0 || parameter_set_count > chunk_size {
        return Err(PyValueError::new_err(
            "execute_many chunk must contain between 1 and chunk_size parameter sets",
        ));
    }

    let _end_index = start_index
        .checked_add(parameter_set_count)
        .ok_or_else(|| {
            PyOverflowError::new_err("execute_many parameter-set index overflowed usize")
        })?;
    let mut prepared = Vec::with_capacity(parameter_set_count);
    for (offset, parameter_set) in parameter_sets.iter().enumerate() {
        let parameter_set_index = start_index + offset;
        if parameter_set.cast::<PyList>().is_err()
            && parameter_set.extract::<Py<Parameters>>().is_err()
        {
            return Err(attach_execute_many_error_metadata(
                invalid_parameter_set_type(parameter_set_index),
                parameter_set_index,
                0,
                false,
            ));
        }
        let converted = convert_parameters_to_fast(Some(&parameter_set), parameter_set.py())
            .map_err(|error| {
                attach_execute_many_error_metadata(error, parameter_set_index, 0, false)
            })?;
        prepared.push(converted);
    }
    Ok(PreparedExecuteManyChunk {
        parameter_sets: prepared,
        start_index,
    })
}

pub(crate) struct ExecuteManyWireProgress {
    active_parameter_set_index: AtomicUsize,
    any_statement_sent: AtomicBool,
    commit_outcome_unknown: AtomicBool,
    confirmed_committed_parameter_sets: AtomicUsize,
}

impl ExecuteManyWireProgress {
    pub(crate) fn new() -> Self {
        Self {
            active_parameter_set_index: AtomicUsize::new(NO_ACTIVE_PARAMETER_SET),
            any_statement_sent: AtomicBool::new(false),
            commit_outcome_unknown: AtomicBool::new(false),
            confirmed_committed_parameter_sets: AtomicUsize::new(0),
        }
    }

    pub(crate) fn begin_statement(&self, parameter_set_index: usize) {
        self.active_parameter_set_index
            .store(parameter_set_index, Ordering::Release);
        self.any_statement_sent.store(true, Ordering::Release);
    }

    pub(crate) fn begin_commit(&self, chunk_start_index: usize) {
        self.active_parameter_set_index
            .store(chunk_start_index, Ordering::Release);
        // Cancellation drops the Rust future before its post-await match can
        // run. Treat an in-flight COMMIT as unknown until an acknowledgement
        // or deterministic SQL rejection proves otherwise.
        self.commit_outcome_unknown.store(true, Ordering::Release);
    }

    pub(crate) fn mark_commit_unknown(&self) {
        self.commit_outcome_unknown.store(true, Ordering::Release);
    }

    pub(crate) fn mark_commit_known(&self) {
        self.commit_outcome_unknown.store(false, Ordering::Release);
    }

    pub(crate) fn confirm_committed_parameter_sets(&self, count: usize) {
        self.confirmed_committed_parameter_sets
            .store(count, Ordering::Release);
    }

    pub(crate) fn clear_active(&self) {
        self.active_parameter_set_index
            .store(NO_ACTIVE_PARAMETER_SET, Ordering::Release);
    }

    pub(crate) fn active_parameter_set_index(&self) -> Option<usize> {
        match self.active_parameter_set_index.load(Ordering::Acquire) {
            NO_ACTIVE_PARAMETER_SET => None,
            index => Some(index),
        }
    }

    pub(crate) fn any_statement_sent(&self) -> bool {
        self.any_statement_sent.load(Ordering::Acquire)
    }

    fn restore_any_statement_sent(&self, any_prior_statement_sent: bool) {
        self.any_statement_sent
            .store(any_prior_statement_sent, Ordering::Release);
    }

    pub(crate) fn commit_outcome_unknown(&self) -> bool {
        self.commit_outcome_unknown.load(Ordering::Acquire)
    }

    pub(crate) fn confirmed_committed_parameter_sets(&self) -> usize {
        self.confirmed_committed_parameter_sets
            .load(Ordering::Acquire)
    }
}

pub(crate) struct ExecuteManyChunkFailure {
    pub(crate) error: PyErr,
    pub(crate) parameter_set_index: usize,
    pub(crate) any_statement_sent: bool,
    pub(crate) protocol_reusable: bool,
}

impl ExecuteManyChunkFailure {
    fn new(
        error: PyErr,
        parameter_set_index: usize,
        any_statement_sent: bool,
        protocol_reusable: bool,
    ) -> Self {
        Self {
            error,
            parameter_set_index,
            any_statement_sent,
            protocol_reusable,
        }
    }
}

fn checked_statement_affected(rows_affected: &[u64]) -> PyResult<u64> {
    rows_affected.iter().try_fold(0_u64, |total, affected| {
        total.checked_add(*affected).ok_or_else(|| {
            PyOverflowError::new_err("execute_many statement affected-row count overflowed u64")
        })
    })
}

fn error_wire_sent(error: &PyErr) -> Option<bool> {
    Python::attach(|py| {
        error
            .value(py)
            .getattr("wire_sent")
            .and_then(|value| value.extract::<bool>())
            .ok()
    })
}

pub(crate) async fn execute_many_chunk_on_connection(
    connection: &mut TiberiusClient,
    sql: &str,
    chunk: &PreparedExecuteManyChunk,
    wire_progress: &ExecuteManyWireProgress,
) -> Result<u64, ExecuteManyChunkFailure> {
    let mut counts = ExecuteManyCounts::default();
    counts.begin_chunk();

    for (offset, parameters) in chunk.parameter_sets.iter().enumerate() {
        let parameter_set_index = chunk.start_index + offset;
        let any_prior_statement_sent = wire_progress.any_statement_sent();
        wire_progress.begin_statement(parameter_set_index);
        let affected = if parameters.is_empty() && requires_direct_batch(sql) {
            execute_unparameterized_command(
                connection,
                sql,
                "execute_many statement execution failed",
            )
            .await
        } else {
            let tiberius_params = params_as_sql_refs(parameters);
            connection
                .execute(sql, &tiberius_params)
                .await
                .map_err(|error| create_sql_error(error, "execute_many statement execution failed"))
                .and_then(|result| checked_statement_affected(result.rows_affected()))
        };

        match affected {
            Ok(affected) => {
                if let Err(error) = counts.add_item(affected) {
                    return Err(ExecuteManyChunkFailure::new(
                        error,
                        parameter_set_index,
                        true,
                        true,
                    ));
                }
            }
            Err(error) => {
                let current_statement_sent = error_wire_sent(&error).unwrap_or(true);
                if !current_statement_sent {
                    wire_progress.restore_any_statement_sent(any_prior_statement_sent);
                }
                let protocol_reusable = python_error_allows_connection_reuse(&error);
                return Err(ExecuteManyChunkFailure::new(
                    error,
                    parameter_set_index,
                    any_prior_statement_sent || current_statement_sent,
                    protocol_reusable,
                ));
            }
        }
    }

    Ok(counts.chunk_total())
}

#[cfg(test)]
mod tests {
    use super::{
        ExecuteManyCounts, attach_execute_many_error_metadata, checked_statement_affected,
        error_wire_sent, parse_execute_many_atomic, parse_execute_many_chunk_size,
        prepare_execute_many_chunk, validate_execute_many_chunk_size,
    };
    use pyo3::IntoPyObjectExt;
    use pyo3::exceptions::PyValueError;
    use pyo3::types::{PyAnyMethods, PyList, PyListMethods};
    use pyo3::{PyResult, Python};

    #[test]
    fn chunk_size_bounds_are_exact() {
        assert_eq!(validate_execute_many_chunk_size(1).unwrap(), 1);
        assert_eq!(validate_execute_many_chunk_size(10_000).unwrap(), 10_000);
        assert!(validate_execute_many_chunk_size(0).is_err());
        assert!(validate_execute_many_chunk_size(10_001).is_err());
    }

    #[test]
    fn python_validation_rejects_bool_coercion_and_non_integer_chunks() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            assert!(parse_execute_many_atomic(true.into_py_any(py)?.bind(py))?);
            for invalid in [
                0_i64.into_py_any(py)?,
                1_i64.into_py_any(py)?,
                "true".into_py_any(py)?,
            ] {
                assert!(parse_execute_many_atomic(invalid.bind(py)).is_err());
            }
            for invalid in [
                true.into_py_any(py)?,
                0_i64.into_py_any(py)?,
                (-1_i64).into_py_any(py)?,
                10_001_i64.into_py_any(py)?,
                1.5_f64.into_py_any(py)?,
            ] {
                assert!(parse_execute_many_chunk_size(invalid.bind(py)).is_err());
            }
            assert_eq!(
                parse_execute_many_chunk_size(1_000_i64.into_py_any(py)?.bind(py))?,
                1_000,
            );
            Ok(())
        })
    }

    #[test]
    fn chunk_conversion_reports_a_global_set_index_without_values() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let valid = PyList::new(py, [1_i64])?;
            let invalid = ("secret-value",).into_py_any(py)?;
            let chunk = PyList::empty(py);
            chunk.append(valid)?;
            chunk.append(invalid)?;
            let error = match prepare_execute_many_chunk(&chunk, 17, 2) {
                Ok(_) => panic!("tuple parameter sets must not be reinterpreted"),
                Err(error) => error,
            };

            let value = error.value(py);
            assert_eq!(
                value.getattr("parameter_set_index")?.extract::<usize>()?,
                18,
            );
            assert!(!value.to_string().contains("secret-value"));
            Ok(())
        })
    }

    #[test]
    fn affected_counts_never_wrap() {
        let mut counts = ExecuteManyCounts::default();
        counts.add_item(4).unwrap();
        counts.add_item(7).unwrap();
        assert_eq!(counts.chunk_total(), 11);
        assert_eq!(counts.total(), 11);
        assert!(checked_statement_affected(&[u64::MAX, 1]).is_err());

        let mut overflow = ExecuteManyCounts::with_totals(u64::MAX, u64::MAX);
        assert!(overflow.add_item(1).is_err());
    }

    #[test]
    fn each_parameter_set_keeps_the_existing_2098_parameter_limit() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let oversized_set = PyList::new(py, 0..2_099)?;
            let chunk = PyList::new(py, [oversized_set])?;
            let error = match prepare_execute_many_chunk(&chunk, 11, 1) {
                Ok(_) => panic!("2,099 user parameters must be rejected locally"),
                Err(error) => error,
            };

            assert!(
                error
                    .to_string()
                    .contains("Too many parameters: 2099 provided")
            );
            assert_eq!(
                error
                    .value(py)
                    .getattr("parameter_set_index")?
                    .extract::<usize>()?,
                11,
            );
            Ok(())
        })
    }

    #[test]
    fn error_metadata_is_global_and_does_not_rewrite_the_message() {
        Python::initialize();
        let error = PyValueError::new_err("parameter conversion failed");
        let error = attach_execute_many_error_metadata(error, 23, 20, true);

        Python::attach(|py| {
            let value = error.value(py);
            assert_eq!(value.to_string(), "parameter conversion failed");
            assert_eq!(
                value
                    .getattr("parameter_set_index")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                23,
            );
            assert_eq!(
                value
                    .getattr("confirmed_committed_parameter_sets")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                20,
            );
            assert!(
                value
                    .getattr("partial_commit_possible")
                    .unwrap()
                    .extract::<bool>()
                    .unwrap(),
            );
        });
    }

    #[test]
    fn explicit_pre_wire_metadata_is_respected_without_guessing() {
        Python::initialize();
        let error = PyValueError::new_err("local RPC encoding failed");
        Python::attach(|py| {
            error.value(py).setattr("wire_sent", false).unwrap();
        });
        assert_eq!(error_wire_sent(&error), Some(false));

        let unknown = PyValueError::new_err("driver did not classify the boundary");
        assert_eq!(error_wire_sent(&unknown), None);
    }
}
