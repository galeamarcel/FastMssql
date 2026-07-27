use crate::sql_parameter_type::{ParameterTypeMetadata, parse_sql_parameter_type};
use crate::type_mapping;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};
use tiberius::{SqlParameterType, TypeLength};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ParameterDirection {
    Input,
    Output,
    InputOutput,
    ReturnValue,
}

impl ParameterDirection {
    fn parse(value: &str) -> PyResult<Self> {
        match value.trim().to_ascii_uppercase().as_str() {
            "INPUT" => Ok(Self::Input),
            "OUTPUT" => Ok(Self::Output),
            "INPUT_OUTPUT" => Ok(Self::InputOutput),
            "RETURN_VALUE" => Ok(Self::ReturnValue),
            _ => Err(PyValueError::new_err("Invalid parameter direction")),
        }
    }

    pub(crate) fn canonical(self) -> &'static str {
        match self {
            Self::Input => "INPUT",
            Self::Output => "OUTPUT",
            Self::InputOutput => "INPUT_OUTPUT",
            Self::ReturnValue => "RETURN_VALUE",
        }
    }
}

#[pyclass]
pub struct Parameter {
    pub(crate) value: Py<PyAny>,
    pub(crate) sql_type: Option<SqlParameterType>,
    pub(crate) direction: ParameterDirection,
    pub(crate) expanded: bool,
}

impl Parameter {
    fn from_raw_value(value: Py<PyAny>) -> PyResult<Self> {
        Self::new(value, None, "INPUT", None, None, None, None)
    }
}

#[pymethods]
impl Parameter {
    #[new]
    #[pyo3(signature = (
        value,
        sql_type=None,
        *,
        direction="INPUT",
        precision=None,
        scale=None,
        length=None,
        expanded=None
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        value: Py<PyAny>,
        sql_type: Option<String>,
        direction: &str,
        precision: Option<&Bound<PyAny>>,
        scale: Option<&Bound<PyAny>>,
        length: Option<&Bound<PyAny>>,
        expanded: Option<bool>,
    ) -> PyResult<Self> {
        let precision = parse_u8_metadata(precision, "precision")?;
        let scale = parse_u8_metadata(scale, "scale")?;
        let length = parse_length_metadata(length)?;
        let metadata = ParameterTypeMetadata {
            precision,
            scale,
            length,
        };
        let sql_type = match sql_type {
            Some(declaration) => Some(
                parse_sql_parameter_type(&declaration, metadata)
                    .map_err(|_| PyValueError::new_err("Invalid SQL parameter declaration"))?,
            ),
            None if metadata.precision.is_some()
                || metadata.scale.is_some()
                || metadata.length.is_some() =>
            {
                return Err(PyValueError::new_err(
                    "SQL parameter metadata requires sql_type",
                ));
            }
            None => None,
        };
        let direction = ParameterDirection::parse(direction)?;
        let detected_expansion =
            Python::attach(|py| type_mapping::is_expandable_iterable(value.bind(py)))?;
        let expanded = match expanded {
            Some(requested) if requested != detected_expansion => {
                return Err(PyValueError::new_err(
                    "Parameter cannot expand according to the requested override",
                ));
            }
            Some(requested) => requested,
            None => detected_expansion,
        };

        Ok(Self {
            value,
            sql_type,
            direction,
            expanded,
        })
    }

    #[getter]
    fn value(&self, py: Python) -> Py<PyAny> {
        self.value.clone_ref(py)
    }

    #[getter]
    fn sql_type(&self) -> Option<String> {
        self.sql_type.as_ref().map(SqlParameterType::declaration)
    }

    #[getter]
    fn direction(&self) -> &'static str {
        self.direction.canonical()
    }

    #[getter]
    fn precision(&self) -> Option<u8> {
        self.sql_type.as_ref().and_then(SqlParameterType::precision)
    }

    #[getter]
    fn scale(&self) -> Option<u8> {
        self.sql_type.as_ref().and_then(SqlParameterType::scale)
    }

    #[getter]
    fn length(&self, py: Python) -> PyResult<Py<PyAny>> {
        match self.sql_type.as_ref().and_then(SqlParameterType::length) {
            Some(TypeLength::Limited(length)) => length.into_py_any(py),
            Some(TypeLength::Max) => "MAX".into_py_any(py),
            None => Ok(py.None()),
        }
    }

    #[getter]
    fn expanded(&self) -> bool {
        self.expanded
    }

    #[getter]
    fn is_expanded(&self) -> bool {
        self.expanded
    }

    fn __repr__(&self) -> String {
        let sql_type = self.sql_type.as_ref().map_or_else(
            || "None".to_owned(),
            |value| format!("'{}'", value.declaration()),
        );
        let expanded = if self.expanded { "True" } else { "False" };

        format!(
            "Parameter(sql_type={sql_type}, direction='{}', \
             expanded={expanded}, value=<redacted>)",
            self.direction.canonical()
        )
    }
}

fn parse_u8_metadata(value: Option<&Bound<PyAny>>, field: &str) -> PyResult<Option<u8>> {
    let Some(value) = value else {
        return Ok(None);
    };

    if value.is_instance_of::<PyBool>() || value.cast::<PyInt>().is_err() {
        return Err(PyValueError::new_err(format!(
            "Parameter {field} must be a plain unsigned integer"
        )));
    }

    value.extract::<u8>().map(Some).map_err(|_| {
        PyValueError::new_err(format!(
            "Parameter {field} must be a plain unsigned integer"
        ))
    })
}

fn parse_length_metadata(value: Option<&Bound<PyAny>>) -> PyResult<Option<TypeLength>> {
    let Some(value) = value else {
        return Ok(None);
    };

    if let Ok(text) = value.cast::<PyString>() {
        return if text
            .to_str()
            .is_ok_and(|text| text.trim().eq_ignore_ascii_case("MAX"))
        {
            Ok(Some(TypeLength::Max))
        } else {
            Err(PyValueError::new_err(
                "Parameter length must be a positive integer or MAX",
            ))
        };
    }

    if value.is_instance_of::<PyBool>() || value.cast::<PyInt>().is_err() {
        return Err(PyValueError::new_err(
            "Parameter length must be a positive integer or MAX",
        ));
    }
    let length = value
        .extract::<u16>()
        .map_err(|_| PyValueError::new_err("Parameter length must be a positive integer or MAX"))?;

    Ok(Some(TypeLength::Limited(length)))
}

#[pyclass]
pub struct Parameters {
    pub(crate) positional: Vec<Py<Parameter>>,
    pub(crate) named: Py<PyDict>,
}

#[pymethods]
impl Parameters {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    pub fn new(
        py: Python,
        args: &Bound<PyTuple>,
        kwargs: Option<&Bound<PyDict>>,
    ) -> PyResult<Self> {
        let mut positional = Vec::new();
        let named = PyDict::new(py);

        for arg in args.iter() {
            if let Ok(existing_param) = arg.extract::<Py<Parameter>>() {
                positional.push(existing_param);
            } else {
                positional.push(Py::new(py, Parameter::from_raw_value(arg.unbind())?)?);
            }
        }

        if let Some(kwargs_dict) = kwargs {
            for (key, value) in kwargs_dict.iter() {
                if let Ok(existing_param) = value.extract::<Py<Parameter>>() {
                    named.set_item(key, existing_param)?;
                } else {
                    named.set_item(
                        key,
                        Py::new(py, Parameter::from_raw_value(value.unbind())?)?,
                    )?;
                }
            }
        }

        Ok(Self {
            positional,
            named: named.into(),
        })
    }

    #[pyo3(signature = (
        value,
        sql_type=None,
        *,
        direction="INPUT",
        precision=None,
        scale=None,
        length=None,
        expanded=None
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn add(
        mut slf: PyRefMut<Self>,
        py: Python,
        value: Py<PyAny>,
        sql_type: Option<String>,
        direction: &str,
        precision: Option<&Bound<PyAny>>,
        scale: Option<&Bound<PyAny>>,
        length: Option<&Bound<PyAny>>,
        expanded: Option<bool>,
    ) -> PyResult<Py<Parameters>> {
        let param = Parameter::new(
            value, sql_type, direction, precision, scale, length, expanded,
        )?;
        slf.positional.push(Py::new(py, param)?);
        Ok(slf.into())
    }

    #[pyo3(signature = (
        key,
        value,
        sql_type=None,
        *,
        direction="INPUT",
        precision=None,
        scale=None,
        length=None,
        expanded=None
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn set(
        slf: PyRefMut<Self>,
        py: Python,
        key: String,
        value: Py<PyAny>,
        sql_type: Option<String>,
        direction: &str,
        precision: Option<&Bound<PyAny>>,
        scale: Option<&Bound<PyAny>>,
        length: Option<&Bound<PyAny>>,
        expanded: Option<bool>,
    ) -> PyResult<Py<Parameters>> {
        let param = Parameter::new(
            value, sql_type, direction, precision, scale, length, expanded,
        )?;
        slf.named.bind(py).set_item(key, Py::new(py, param)?)?;
        Ok(slf.into())
    }

    pub fn to_list(&self, py: Python) -> PyResult<Py<PyList>> {
        let named_len = self.named.bind(py).len();
        if named_len > 0 {
            let names = self
                .named
                .bind(py)
                .keys()
                .iter()
                .filter_map(|key| key.extract::<String>().ok())
                .collect::<Vec<_>>();
            return Err(PyValueError::new_err(format!(
                "Named parameters are not supported by the SQL Server wire protocol. \
                 Use positional parameters (Parameters(value1, value2, ...)) instead. \
                 Found {named_len} named parameter(s): {names:?}"
            )));
        }
        let values = self
            .positional
            .iter()
            .map(|parameter| parameter.borrow(py).value.clone_ref(py));
        Ok(PyList::new(py, values)?.into())
    }

    fn __len__(&self, py: Python) -> usize {
        self.positional.len() + self.named.bind(py).len()
    }

    fn __repr__(&self, py: Python) -> String {
        let positional_len = self.positional.len();
        let named_len = self.named.bind(py).len();

        match (positional_len, named_len) {
            (0, 0) => "Parameters()".to_owned(),
            (positional, 0) => format!("Parameters(positional={positional})"),
            (0, named) => format!("Parameters(named={named})"),
            (positional, named) => {
                format!("Parameters(positional={positional}, named={named})")
            }
        }
    }

    #[getter]
    fn positional(&self, py: Python) -> Vec<Py<Parameter>> {
        self.positional
            .iter()
            .map(|parameter| parameter.clone_ref(py))
            .collect()
    }

    #[getter]
    fn named(&self, py: Python) -> PyResult<Py<PyDict>> {
        let copy = PyDict::new(py);
        for (key, value) in self.named.bind(py).iter() {
            copy.set_item(key, value)?;
        }
        Ok(copy.into())
    }
}
