use crate::pool_config::PyPoolConfig;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBool;
use std::time::Duration;

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Copy)]
struct OptionalSeconds(Option<f64>);

impl OptionalSeconds {
    const fn finite(value: f64) -> Self {
        Self(Some(value))
    }
}

#[derive(Clone, Copy)]
struct RequiredSeconds(f64);

impl RequiredSeconds {
    const fn finite(value: f64) -> Self {
        Self(value)
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for OptionalSeconds {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if object.is_instance_of::<PyBool>() {
            return Err(PyValueError::new_err(
                "timeout values do not accept booleans",
            ));
        }
        if object.is_none() {
            return Ok(Self(None));
        }
        object
            .extract::<f64>()
            .map(|value| Self(Some(value)))
            .map_err(|_| PyValueError::new_err("timeout values must be numbers or None"))
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for RequiredSeconds {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if object.is_none() || object.is_instance_of::<PyBool>() {
            return Err(PyValueError::new_err(
                "acquire_timeout_secs must be a number greater than 0",
            ));
        }
        object.extract::<f64>().map(Self).map_err(|_| {
            PyValueError::new_err("acquire_timeout_secs must be a number greater than 0")
        })
    }
}

fn positive_finite_seconds(name: &str, value: f64) -> PyResult<Duration> {
    if !value.is_finite() || value <= 0.0 {
        return Err(PyValueError::new_err(format!(
            "{name} must be a finite number greater than 0"
        )));
    }
    Duration::try_from_secs_f64(value)
        .map_err(|_| PyValueError::new_err(format!("{name} must fit in a Rust Duration")))
}

fn optional_duration(name: &str, value: OptionalSeconds) -> PyResult<Option<Duration>> {
    value
        .0
        .map(|seconds| positive_finite_seconds(name, seconds))
        .transpose()
}

fn render_optional(value: Option<Duration>) -> String {
    value
        .map(|duration| format!("{:?}", duration.as_secs_f64()))
        .unwrap_or_else(|| "None".to_string())
}

/// Timeout policy for physical connection setup, pool acquisition, SQL
/// operations, transaction lifetime, and rollback cleanup.
#[pyclass(name = "TimeoutConfig", from_py_object)]
#[derive(Clone, Debug)]
pub struct PyTimeoutConfig {
    pub(crate) connect_timeout: Option<Duration>,
    pub(crate) acquire_timeout: Duration,
    pub(crate) operation_timeout: Option<Duration>,
    pub(crate) transaction_timeout: Option<Duration>,
    pub(crate) rollback_timeout: Option<Duration>,
}

impl PyTimeoutConfig {
    pub(crate) fn explicit_default() -> Self {
        Self {
            connect_timeout: Some(DEFAULT_TIMEOUT),
            acquire_timeout: DEFAULT_TIMEOUT,
            operation_timeout: None,
            transaction_timeout: None,
            rollback_timeout: Some(DEFAULT_TIMEOUT),
        }
    }

    pub(crate) fn from_pool_compatibility(pool: &PyPoolConfig) -> Self {
        let legacy = pool.connection_timeout.unwrap_or(DEFAULT_TIMEOUT);
        Self {
            connect_timeout: Some(legacy),
            acquire_timeout: legacy,
            operation_timeout: None,
            transaction_timeout: None,
            rollback_timeout: Some(DEFAULT_TIMEOUT),
        }
    }

    pub(crate) fn align_pool_config(&self, pool: &PyPoolConfig) -> PyPoolConfig {
        let mut aligned = pool.clone();
        aligned.connection_timeout = Some(self.acquire_timeout);
        aligned
    }
}

impl Default for PyTimeoutConfig {
    fn default() -> Self {
        Self::explicit_default()
    }
}

#[pymethods]
impl PyTimeoutConfig {
    #[new]
    #[pyo3(
        signature = (
            connect_timeout_secs = OptionalSeconds::finite(30.0),
            acquire_timeout_secs = RequiredSeconds::finite(30.0),
            operation_timeout_secs = OptionalSeconds(None),
            transaction_timeout_secs = OptionalSeconds(None),
            rollback_timeout_secs = OptionalSeconds::finite(30.0)
        ),
        text_signature = "(connect_timeout_secs=30.0, acquire_timeout_secs=30.0, operation_timeout_secs=None, transaction_timeout_secs=None, rollback_timeout_secs=30.0)"
    )]
    fn new(
        connect_timeout_secs: OptionalSeconds,
        acquire_timeout_secs: RequiredSeconds,
        operation_timeout_secs: OptionalSeconds,
        transaction_timeout_secs: OptionalSeconds,
        rollback_timeout_secs: OptionalSeconds,
    ) -> PyResult<Self> {
        Ok(Self {
            connect_timeout: optional_duration("connect_timeout_secs", connect_timeout_secs)?,
            acquire_timeout: positive_finite_seconds(
                "acquire_timeout_secs",
                acquire_timeout_secs.0,
            )?,
            operation_timeout: optional_duration("operation_timeout_secs", operation_timeout_secs)?,
            transaction_timeout: optional_duration(
                "transaction_timeout_secs",
                transaction_timeout_secs,
            )?,
            rollback_timeout: optional_duration("rollback_timeout_secs", rollback_timeout_secs)?,
        })
    }

    #[getter]
    fn connect_timeout_secs(&self) -> Option<f64> {
        self.connect_timeout.map(|value| value.as_secs_f64())
    }

    #[setter]
    fn set_connect_timeout_secs(&mut self, value: OptionalSeconds) -> PyResult<()> {
        self.connect_timeout = optional_duration("connect_timeout_secs", value)?;
        Ok(())
    }

    #[getter]
    fn acquire_timeout_secs(&self) -> f64 {
        self.acquire_timeout.as_secs_f64()
    }

    #[setter]
    fn set_acquire_timeout_secs(&mut self, value: RequiredSeconds) -> PyResult<()> {
        self.acquire_timeout = positive_finite_seconds("acquire_timeout_secs", value.0)?;
        Ok(())
    }

    #[getter]
    fn operation_timeout_secs(&self) -> Option<f64> {
        self.operation_timeout.map(|value| value.as_secs_f64())
    }

    #[setter]
    fn set_operation_timeout_secs(&mut self, value: OptionalSeconds) -> PyResult<()> {
        self.operation_timeout = optional_duration("operation_timeout_secs", value)?;
        Ok(())
    }

    #[getter]
    fn transaction_timeout_secs(&self) -> Option<f64> {
        self.transaction_timeout.map(|value| value.as_secs_f64())
    }

    #[setter]
    fn set_transaction_timeout_secs(&mut self, value: OptionalSeconds) -> PyResult<()> {
        self.transaction_timeout = optional_duration("transaction_timeout_secs", value)?;
        Ok(())
    }

    #[getter]
    fn rollback_timeout_secs(&self) -> Option<f64> {
        self.rollback_timeout.map(|value| value.as_secs_f64())
    }

    #[setter]
    fn set_rollback_timeout_secs(&mut self, value: OptionalSeconds) -> PyResult<()> {
        self.rollback_timeout = optional_duration("rollback_timeout_secs", value)?;
        Ok(())
    }

    fn __repr__(&self) -> String {
        format!(
            "TimeoutConfig(connect_timeout_secs={}, acquire_timeout_secs={:?}, operation_timeout_secs={}, transaction_timeout_secs={}, rollback_timeout_secs={})",
            render_optional(self.connect_timeout),
            self.acquire_timeout.as_secs_f64(),
            render_optional(self.operation_timeout),
            render_optional(self.transaction_timeout),
            render_optional(self.rollback_timeout),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compatibility_uses_legacy_pool_timeout() {
        let pool = PyPoolConfig {
            connection_timeout: Some(Duration::from_secs(2)),
            ..PyPoolConfig::default()
        };

        let timeout = PyTimeoutConfig::from_pool_compatibility(&pool);

        assert_eq!(timeout.connect_timeout, Some(Duration::from_secs(2)));
        assert_eq!(timeout.acquire_timeout, Duration::from_secs(2));
        assert_eq!(timeout.operation_timeout, None);
        assert_eq!(timeout.transaction_timeout, None);
        assert_eq!(timeout.rollback_timeout, Some(DEFAULT_TIMEOUT));
    }

    #[test]
    fn explicit_acquire_timeout_aligns_internal_pool() {
        let pool = PyPoolConfig::default();
        let mut timeout = PyTimeoutConfig::explicit_default();
        timeout.acquire_timeout = Duration::from_millis(125);

        let aligned = timeout.align_pool_config(&pool);

        assert_eq!(aligned.connection_timeout, Some(Duration::from_millis(125)));
        assert_eq!(pool.connection_timeout, Some(DEFAULT_TIMEOUT));
    }
}
