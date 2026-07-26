use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBoolMethods};

#[derive(Clone, Copy)]
struct ExactBool(bool);

impl ExactBool {
    const FALSE: Self = Self(false);
}

impl<'a, 'py> FromPyObject<'a, 'py> for ExactBool {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        object
            .cast::<PyBool>()
            .map(|value| Self(value.is_true()))
            .map_err(|_| PyTypeError::new_err("enabled must be a bool"))
    }
}

#[pyclass(name = "OperationMetricsConfig", from_py_object)]
#[derive(Clone, Debug, Default)]
pub struct PyOperationMetricsConfig {
    pub(crate) enabled: bool,
}

#[pymethods]
impl PyOperationMetricsConfig {
    #[new]
    #[pyo3(
        signature = (enabled = ExactBool::FALSE),
        text_signature = "(enabled=False)"
    )]
    fn new(enabled: ExactBool) -> Self {
        Self { enabled: enabled.0 }
    }

    #[getter]
    fn enabled(&self) -> bool {
        self.enabled
    }

    #[setter]
    fn set_enabled(&mut self, enabled: ExactBool) {
        self.enabled = enabled.0;
    }

    fn __repr__(&self) -> String {
        let enabled = if self.enabled { "True" } else { "False" };
        format!("OperationMetricsConfig(enabled={enabled})")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::exceptions::PyTypeError;
    use std::ffi::CString;

    #[test]
    fn operation_metrics_config_defaults_disabled() {
        let config = PyOperationMetricsConfig::default();
        assert!(!config.enabled);
    }

    #[test]
    fn operation_metrics_config_rejects_numpy_named_truthy_objects() {
        Python::initialize();
        Python::attach(|py| {
            let fake_numpy_bool = py
                .eval(
                    &CString::new(
                        "type('bool_', (), {'__module__': 'numpy', \
                         '__bool__': lambda self: True})()",
                    )
                    .unwrap(),
                    None,
                    None,
                )
                .unwrap();
            let error = py
                .get_type::<PyOperationMetricsConfig>()
                .call1((fake_numpy_bool,))
                .unwrap_err();
            assert!(error.is_instance_of::<PyTypeError>(py));
        });
    }
}
