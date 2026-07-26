use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBool;
use std::time::Duration;

const DEFAULT_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_FORCE_TIMEOUT: Duration = Duration::from_secs(5);
const MIN_TIMEOUT_SECONDS: f64 = 1e-9;
const MAX_PORTABLE_TIMEOUT: Duration = Duration::from_secs(100 * 365 * 24 * 60 * 60);

#[derive(Clone, Copy)]
struct RequiredLifecycleSeconds(f64);

impl RequiredLifecycleSeconds {
    const fn finite(value: f64) -> Self {
        Self(value)
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for RequiredLifecycleSeconds {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if object.is_none() || object.is_instance_of::<PyBool>() {
            return Err(PyValueError::new_err(
                "lifecycle timeout values must be numbers greater than 0",
            ));
        }
        object.extract::<f64>().map(Self).map_err(|_| {
            PyValueError::new_err("lifecycle timeout values must be numbers greater than 0")
        })
    }
}

fn lifecycle_duration(name: &str, value: f64) -> PyResult<Duration> {
    if !value.is_finite() || value <= 0.0 {
        return Err(PyValueError::new_err(format!(
            "{name} must be a finite number greater than 0"
        )));
    }
    if value < MIN_TIMEOUT_SECONDS {
        return Err(PyValueError::new_err(format!(
            "{name} must be at least 0.000000001 seconds"
        )));
    }
    let duration = Duration::try_from_secs_f64(value)
        .map_err(|_| PyValueError::new_err(format!("{name} must fit in a Rust Duration")))?;
    if duration.is_zero() {
        return Err(PyValueError::new_err(format!(
            "{name} must be at least 0.000000001 seconds"
        )));
    }
    if duration > MAX_PORTABLE_TIMEOUT {
        return Err(PyValueError::new_err(format!(
            "{name} is too large; lifecycle timeout values must not exceed \
             3153600000 seconds (100 years)"
        )));
    }
    if tokio::time::Instant::now().checked_add(duration).is_none() {
        return Err(PyValueError::new_err(format!(
            "{name} is too large for this platform's monotonic clock"
        )));
    }
    Ok(duration)
}

/// Bounded policy for graceful connection shutdown and forced retirement.
#[pyclass(name = "LifecycleConfig", from_py_object)]
#[derive(Clone, Debug)]
pub struct PyLifecycleConfig {
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
}

impl Default for PyLifecycleConfig {
    fn default() -> Self {
        Self {
            shutdown_timeout: DEFAULT_SHUTDOWN_TIMEOUT,
            force_timeout: DEFAULT_FORCE_TIMEOUT,
        }
    }
}

#[pymethods]
impl PyLifecycleConfig {
    #[new]
    #[pyo3(
        signature = (
            shutdown_timeout_secs =
                RequiredLifecycleSeconds::finite(30.0),
            force_timeout_secs =
                RequiredLifecycleSeconds::finite(5.0)
        ),
        text_signature = "(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)"
    )]
    fn new(
        shutdown_timeout_secs: RequiredLifecycleSeconds,
        force_timeout_secs: RequiredLifecycleSeconds,
    ) -> PyResult<Self> {
        Ok(Self {
            shutdown_timeout: lifecycle_duration("shutdown_timeout_secs", shutdown_timeout_secs.0)?,
            force_timeout: lifecycle_duration("force_timeout_secs", force_timeout_secs.0)?,
        })
    }

    #[getter]
    fn shutdown_timeout_secs(&self) -> f64 {
        self.shutdown_timeout.as_secs_f64()
    }

    #[setter]
    fn set_shutdown_timeout_secs(&mut self, value: RequiredLifecycleSeconds) -> PyResult<()> {
        let validated = lifecycle_duration("shutdown_timeout_secs", value.0)?;
        self.shutdown_timeout = validated;
        Ok(())
    }

    #[getter]
    fn force_timeout_secs(&self) -> f64 {
        self.force_timeout.as_secs_f64()
    }

    #[setter]
    fn set_force_timeout_secs(&mut self, value: RequiredLifecycleSeconds) -> PyResult<()> {
        let validated = lifecycle_duration("force_timeout_secs", value.0)?;
        self.force_timeout = validated;
        Ok(())
    }

    fn __repr__(&self) -> String {
        format!(
            "LifecycleConfig(shutdown_timeout_secs={:?}, \
             force_timeout_secs={:?})",
            self.shutdown_timeout.as_secs_f64(),
            self.force_timeout.as_secs_f64(),
        )
    }
}

#[pyclass(name = "ConnectionLifecycleState", eq, eq_int, from_py_object)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionLifecycleState {
    Open,
    Closing,
    Closed,
}

#[pymethods]
impl ConnectionLifecycleState {
    #[classattr]
    const OPEN: Self = Self::Open;

    #[classattr]
    const CLOSING: Self = Self::Closing;

    #[classattr]
    const CLOSED: Self = Self::Closed;

    fn __str__(&self) -> &'static str {
        self.as_str()
    }

    fn __repr__(&self) -> String {
        format!("ConnectionLifecycleState.{}", self.as_str())
    }
}

impl ConnectionLifecycleState {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Open => "Open",
            Self::Closing => "Closing",
            Self::Closed => "Closed",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lifecycle_defaults_are_bounded() {
        let config = PyLifecycleConfig::default();
        assert_eq!(config.shutdown_timeout, Duration::from_secs(30));
        assert_eq!(config.force_timeout, Duration::from_secs(5));
    }

    #[test]
    fn lifecycle_duration_rejects_invalid_boundaries() {
        for value in [
            0.0,
            -1.0,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            5e-324,
            0.5e-9,
            1e19,
        ] {
            assert!(lifecycle_duration("test_timeout", value).is_err());
        }
        assert_eq!(
            lifecycle_duration("test_timeout", 1e-9).expect("one nanosecond must be accepted"),
            Duration::from_nanos(1)
        );
        assert_eq!(
            lifecycle_duration("test_timeout", MAX_PORTABLE_TIMEOUT.as_secs_f64())
                .expect("the portable ceiling must be accepted"),
            MAX_PORTABLE_TIMEOUT
        );
    }
}
