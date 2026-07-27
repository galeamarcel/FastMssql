use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};
use std::sync::Arc;
use tiberius::{ResponseColumn, ResponseDone, ResponseDoneKind, ResponseInfo, ResponseLength};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum ColumnLength {
    Limited(usize),
    Max,
}

#[derive(Clone, Debug)]
pub(crate) struct ColumnMetadataData {
    pub(crate) ordinal: usize,
    pub(crate) name: String,
    pub(crate) type_name: String,
    pub(crate) nullable: Option<bool>,
    pub(crate) precision: Option<u8>,
    pub(crate) scale: Option<u8>,
    pub(crate) length: Option<ColumnLength>,
}

impl ColumnMetadataData {
    pub(crate) fn from_response(ordinal: usize, column: &ResponseColumn) -> Self {
        Self {
            ordinal,
            name: column.name().to_owned(),
            type_name: column.type_name().to_owned(),
            nullable: column.nullable(),
            precision: column.precision(),
            scale: column.scale(),
            length: column.length().map(|length| match length {
                ResponseLength::Limited(value) => ColumnLength::Limited(value),
                ResponseLength::Max => ColumnLength::Max,
            }),
        }
    }
}

#[pyclass(name = "ColumnMetadata", frozen, from_py_object)]
#[derive(Clone)]
pub struct PyColumnMetadata {
    data: ColumnMetadataData,
}

impl PyColumnMetadata {
    pub(crate) fn from_data(data: ColumnMetadataData) -> Self {
        Self { data }
    }
}

#[pymethods]
impl PyColumnMetadata {
    #[getter]
    fn ordinal(&self) -> usize {
        self.data.ordinal
    }

    #[getter]
    fn name(&self) -> &str {
        &self.data.name
    }

    #[getter]
    fn type_name(&self) -> &str {
        &self.data.type_name
    }

    #[getter]
    fn nullable(&self) -> Option<bool> {
        self.data.nullable
    }

    #[getter]
    fn precision(&self) -> Option<u8> {
        self.data.precision
    }

    #[getter]
    fn scale(&self) -> Option<u8> {
        self.data.scale
    }

    #[getter]
    fn length(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self.data.length.as_ref() {
            Some(ColumnLength::Limited(value)) => Ok(value.into_pyobject(py)?.into_any().unbind()),
            Some(ColumnLength::Max) => Ok("MAX".into_pyobject(py)?.into_any().unbind()),
            None => Ok(py.None()),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ColumnMetadata(ordinal={}, type_name={:?})",
            self.data.ordinal, self.data.type_name
        )
    }
}

#[derive(Clone, Debug)]
pub(crate) struct DoneResultData {
    pub(crate) kind: &'static str,
    pub(crate) rows_affected: Option<u64>,
    pub(crate) more_results: bool,
    pub(crate) in_transaction: bool,
    pub(crate) attention_acknowledged: bool,
}

impl From<ResponseDone> for DoneResultData {
    fn from(done: ResponseDone) -> Self {
        let kind = match done.kind() {
            ResponseDoneKind::Done => "DONE",
            ResponseDoneKind::DoneProc => "DONEPROC",
            ResponseDoneKind::DoneInProc => "DONEINPROC",
        };
        Self {
            kind,
            rows_affected: done.rows_affected(),
            more_results: done.more_results(),
            in_transaction: done.in_transaction(),
            attention_acknowledged: done.attention_acknowledged(),
        }
    }
}

#[pyclass(name = "DoneResult", frozen, from_py_object)]
#[derive(Clone)]
pub struct PyDoneResult {
    data: DoneResultData,
}

impl PyDoneResult {
    fn from_data(data: DoneResultData) -> Self {
        Self { data }
    }
}

#[pymethods]
impl PyDoneResult {
    #[getter]
    fn kind(&self) -> &'static str {
        self.data.kind
    }

    #[getter]
    fn rows_affected(&self) -> Option<u64> {
        self.data.rows_affected
    }

    #[getter]
    fn more_results(&self) -> bool {
        self.data.more_results
    }

    #[getter]
    fn in_transaction(&self) -> bool {
        self.data.in_transaction
    }

    #[getter]
    fn attention_acknowledged(&self) -> bool {
        self.data.attention_acknowledged
    }

    fn __repr__(&self) -> String {
        format!(
            "DoneResult(kind={:?}, rows_affected={:?})",
            self.data.kind, self.data.rows_affected
        )
    }
}

#[derive(Clone, Debug)]
pub(crate) struct SqlMessageData {
    pub(crate) number: u32,
    pub(crate) state: u8,
    pub(crate) severity: u8,
    pub(crate) message: String,
    pub(crate) server: String,
    pub(crate) procedure: String,
    pub(crate) line: u32,
}

impl From<ResponseInfo> for SqlMessageData {
    fn from(info: ResponseInfo) -> Self {
        Self {
            number: info.number(),
            state: info.state(),
            severity: info.severity(),
            message: info.message().to_owned(),
            server: info.server().to_owned(),
            procedure: info.procedure().to_owned(),
            line: info.line(),
        }
    }
}

#[pyclass(name = "SqlMessage", frozen, from_py_object)]
#[derive(Clone)]
pub struct PySqlMessage {
    data: SqlMessageData,
}

impl PySqlMessage {
    fn from_data(data: SqlMessageData) -> Self {
        Self { data }
    }
}

#[pymethods]
impl PySqlMessage {
    #[getter]
    fn number(&self) -> u32 {
        self.data.number
    }

    #[getter]
    fn state(&self) -> u8 {
        self.data.state
    }

    #[getter]
    fn severity(&self) -> u8 {
        self.data.severity
    }

    #[getter]
    fn message(&self) -> &str {
        &self.data.message
    }

    #[getter]
    fn server(&self) -> &str {
        &self.data.server
    }

    #[getter]
    fn procedure(&self) -> &str {
        &self.data.procedure
    }

    #[getter]
    fn line(&self) -> u32 {
        self.data.line
    }

    fn __repr__(&self) -> String {
        format!(
            "SqlMessage(number={}, state={}, severity={}, line={})",
            self.data.number, self.data.state, self.data.severity, self.data.line
        )
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ResultSummaryData {
    pub(crate) result_set_count: usize,
    pub(crate) done: Vec<DoneResultData>,
    pub(crate) messages: Vec<SqlMessageData>,
    pub(crate) return_status: Option<i32>,
}

#[pyclass(name = "ResultSummary", frozen, from_py_object)]
#[derive(Clone)]
pub struct PyResultSummary {
    data: Arc<ResultSummaryData>,
}

impl PyResultSummary {
    pub(crate) fn from_data(data: Arc<ResultSummaryData>) -> Self {
        Self { data }
    }
}

#[pymethods]
impl PyResultSummary {
    #[getter]
    fn result_set_count(&self) -> usize {
        self.data.result_set_count
    }

    #[getter]
    fn done(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let values = self
            .data
            .done
            .iter()
            .cloned()
            .map(|data| Py::new(py, PyDoneResult::from_data(data)))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyTuple::new(py, values)?.unbind())
    }

    #[getter]
    fn messages(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let values = self
            .data
            .messages
            .iter()
            .cloned()
            .map(|data| Py::new(py, PySqlMessage::from_data(data)))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyTuple::new(py, values)?.unbind())
    }

    #[getter]
    fn return_status(&self) -> Option<i32> {
        self.data.return_status
    }

    #[getter]
    fn output_parameters(&self, py: Python<'_>) -> Py<PyDict> {
        PyDict::new(py).unbind()
    }

    fn __repr__(&self) -> String {
        format!(
            "ResultSummary(result_set_count={}, done_count={}, message_count={}, \
             has_return_status={})",
            self.data.result_set_count,
            self.data.done.len(),
            self.data.messages.len(),
            self.data.return_status.is_some(),
        )
    }
}
