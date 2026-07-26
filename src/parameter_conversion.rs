use crate::py_parameters::Parameters;
use crate::type_mapping;
use crate::types::create_parameter_conversion_error;
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, NaiveTime};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyByteArray, PyByteArrayMethods, PyBytes, PyFloat, PyInt, PyList, PyMemoryView,
    PyString, PyTime, PyTuple,
};
use smallvec::SmallVec;

/// SQL Server's RPC parameter ceiling includes the two parameters that
/// Tiberius adds for `sp_executesql`: `@stmt` and `@params`.
pub(crate) const MAX_USER_QUERY_PARAMETERS: usize = 2_098;

#[derive(Debug, Clone)]
pub struct FastParameter {
    pub(crate) value: FastParameterValue,
}

#[derive(Debug, Clone)]
pub(crate) enum FastParameterValue {
    Null(TypedNull),
    Bool(bool),
    I64(i64),
    F64(f64),
    String(String),
    Bytes(Vec<u8>),
    Numeric(tiberius::numeric::Numeric),
    Date(NaiveDate),
    Time(NaiveTime),
    DateTime(NaiveDateTime),
}

impl FastParameter {
    fn new(value: FastParameterValue) -> Self {
        Self { value }
    }
}

impl tiberius::ToSql for FastParameter {
    fn to_sql(&self) -> tiberius::ColumnData<'_> {
        match &self.value {
            FastParameterValue::Null(t) => t.to_sql(),
            FastParameterValue::Bool(b) => b.to_sql(),
            FastParameterValue::I64(i) => i.to_sql(),
            FastParameterValue::F64(f) => f.to_sql(),
            FastParameterValue::String(s) => s.to_sql(),
            FastParameterValue::Bytes(b) => b.to_sql(),
            FastParameterValue::Numeric(n) => n.to_sql(),
            FastParameterValue::Date(d) => d.to_sql(),
            FastParameterValue::Time(t) => t.to_sql(),
            FastParameterValue::DateTime(dt) => dt.to_sql(),
        }
    }
}

pub fn python_to_fast_parameter(obj: &Bound<PyAny>) -> PyResult<FastParameter> {
    python_to_fast_parameter_at(obj, 0)
}

fn python_to_fast_parameter_at(
    obj: &Bound<PyAny>,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    if obj.is_none() {
        return Ok(FastParameter::new(FastParameterValue::Null(TypedNull::U8)));
    }

    // Typed nulls
    if let Ok(tn) = obj.extract::<TypedNull>() {
        return Ok(FastParameter::new(FastParameterValue::Null(tn)));
    }

    // Python bool is a subclass of int, so it must be detected first.
    if let Ok(py_b) = obj.cast::<PyBool>() {
        return Ok(FastParameter::new(FastParameterValue::Bool(py_b.is_true())));
    }
    if let Ok(py_i) = obj.cast::<PyInt>() {
        return py_i
            .extract::<i64>()
            .map(|value| FastParameter::new(FastParameterValue::I64(value)))
            .map_err(|_| PyValueError::new_err("Int too large"));
    }
    if let Ok(py_s) = obj.cast::<PyString>() {
        let s = py_s
            .to_str()
            .map_err(|_| PyValueError::new_err("String parameter contains invalid UTF-8"))?;
        return Ok(FastParameter::new(FastParameterValue::String(s.to_owned())));
    }
    if let Ok(py_f) = obj.cast::<PyFloat>() {
        return Ok(FastParameter::new(FastParameterValue::F64(py_f.value())));
    }
    if let Ok(py_by) = obj.cast::<PyBytes>() {
        return Ok(FastParameter::new(FastParameterValue::Bytes(
            py_by.as_bytes().to_vec(),
        )));
    }
    if let Ok(py_by) = obj.cast::<PyByteArray>() {
        return Ok(FastParameter::new(FastParameterValue::Bytes(
            py_by.to_vec(),
        )));
    }
    if obj.is_instance_of::<PyMemoryView>() {
        let py_by = PyByteArray::from(obj)?;
        return Ok(FastParameter::new(FastParameterValue::Bytes(
            py_by.to_vec(),
        )));
    }
    if let Ok(py_time) = obj.cast::<PyTime>() {
        return time_to_fast_parameter(py_time, parameter_index);
    }
    if let Ok(py_dt) = obj.extract::<NaiveDateTime>() {
        return Ok(FastParameter::new(FastParameterValue::DateTime(py_dt)));
    }
    if let Ok(py_dt) = obj.extract::<DateTime<FixedOffset>>() {
        return Ok(FastParameter::new(FastParameterValue::DateTime(
            py_dt.naive_local(),
        )));
    }
    if let Ok(py_date) = obj.extract::<NaiveDate>() {
        return Ok(FastParameter::new(FastParameterValue::Date(py_date)));
    }
    if is_decimal_instance(obj)? {
        return decimal_to_fast_parameter(obj, parameter_index);
    }

    // Fallback for custom types
    if let Ok(i) = obj.extract::<i64>() {
        Ok(FastParameter::new(FastParameterValue::I64(i)))
    } else {
        Err(PyValueError::new_err(format!(
            "Unsupported type: {}",
            obj.get_type().name()?
        )))
    }
}

fn time_to_fast_parameter(
    py_time: &Bound<PyTime>,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    let offset = py_time.call_method0("utcoffset").map_err(|_| {
        create_parameter_conversion_error(
            parameter_index,
            "TIME(7)",
            "invalid_time",
            "Time parameter conversion failed",
        )
    })?;
    if !offset.is_none() {
        return Err(create_parameter_conversion_error(
            parameter_index,
            "TIME(7)",
            "timezone_not_supported",
            "Aware time parameter is not supported because SQL Server TIME has no offset",
        ));
    }

    let time = py_time.extract::<NaiveTime>().map_err(|_| {
        create_parameter_conversion_error(
            parameter_index,
            "TIME(7)",
            "invalid_time",
            "Time parameter conversion failed",
        )
    })?;

    Ok(FastParameter::new(FastParameterValue::Time(time)))
}

fn is_decimal_instance(obj: &Bound<PyAny>) -> PyResult<bool> {
    obj.is_instance(type_mapping::get_decimal_class(obj.py())?)
}

#[derive(Clone, Copy)]
enum DecimalConversionFailure {
    NonFinite,
    PrecisionOverflow,
    Invalid,
}

impl DecimalConversionFailure {
    fn reason(self) -> &'static str {
        match self {
            Self::NonFinite => "non_finite",
            Self::PrecisionOverflow => "precision_overflow",
            Self::Invalid => "invalid_decimal",
        }
    }

    fn message(self) -> &'static str {
        match self {
            Self::NonFinite => "Decimal parameter must be finite",
            Self::PrecisionOverflow => "Decimal parameter exceeds SQL Server NUMERIC precision 38",
            Self::Invalid => "Decimal parameter conversion failed",
        }
    }
}

fn decimal_conversion_error(parameter_index: usize, failure: DecimalConversionFailure) -> PyErr {
    create_parameter_conversion_error(
        parameter_index,
        "NUMERIC",
        failure.reason(),
        failure.message(),
    )
}

fn decimal_to_fast_parameter(
    obj: &Bound<PyAny>,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    let decimal_class = type_mapping::get_decimal_class(obj.py()).map_err(|_| {
        decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
    })?;

    let is_finite = decimal_class
        .getattr("is_finite")
        .and_then(|method| method.call1((obj,)))
        .and_then(|result| result.extract::<bool>())
        .map_err(|_| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
        })?;
    if !is_finite {
        return Err(decimal_conversion_error(
            parameter_index,
            DecimalConversionFailure::NonFinite,
        ));
    }

    let components = decimal_class
        .getattr("as_tuple")
        .and_then(|method| method.call1((obj,)))
        .map_err(|_| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
        })?;
    let sign = components
        .getattr("sign")
        .and_then(|value| value.extract::<u8>())
        .map_err(|_| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
        })?;
    if sign > 1 {
        return Err(decimal_conversion_error(
            parameter_index,
            DecimalConversionFailure::Invalid,
        ));
    }

    let digits_object = components.getattr("digits").map_err(|_| {
        decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
    })?;
    let digits = digits_object.cast::<PyTuple>().map_err(|_| {
        decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
    })?;
    let coefficient_digits = digits.len().max(1);
    if coefficient_digits > 38 {
        return Err(decimal_conversion_error(
            parameter_index,
            DecimalConversionFailure::PrecisionOverflow,
        ));
    }

    let mut coefficient = 0_i128;
    for digit in digits.iter() {
        let digit = digit.extract::<u8>().map_err(|_| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::Invalid)
        })?;
        if digit > 9 {
            return Err(decimal_conversion_error(
                parameter_index,
                DecimalConversionFailure::Invalid,
            ));
        }
        coefficient = coefficient
            .checked_mul(10)
            .and_then(|value| value.checked_add(i128::from(digit)))
            .ok_or_else(|| {
                decimal_conversion_error(
                    parameter_index,
                    DecimalConversionFailure::PrecisionOverflow,
                )
            })?;
    }

    let exponent = components
        .getattr("exponent")
        .and_then(|value| value.extract::<i64>())
        .map_err(|_| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::PrecisionOverflow)
        })?;

    let scale = if exponent > 0 {
        if coefficient != 0 {
            let exponent = usize::try_from(exponent).map_err(|_| {
                decimal_conversion_error(
                    parameter_index,
                    DecimalConversionFailure::PrecisionOverflow,
                )
            })?;
            if exponent > 38_usize.saturating_sub(coefficient_digits) {
                return Err(decimal_conversion_error(
                    parameter_index,
                    DecimalConversionFailure::PrecisionOverflow,
                ));
            }
            for _ in 0..exponent {
                coefficient = coefficient.checked_mul(10).ok_or_else(|| {
                    decimal_conversion_error(
                        parameter_index,
                        DecimalConversionFailure::PrecisionOverflow,
                    )
                })?;
            }
        }
        0
    } else {
        let scale = exponent.checked_neg().ok_or_else(|| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::PrecisionOverflow)
        })?;
        u8::try_from(scale)
            .ok()
            .filter(|scale| *scale <= 38)
            .ok_or_else(|| {
                decimal_conversion_error(
                    parameter_index,
                    DecimalConversionFailure::PrecisionOverflow,
                )
            })?
    };

    let precision = if coefficient == 0 {
        1_usize
    } else {
        coefficient_digits
            + usize::try_from(exponent.max(0)).map_err(|_| {
                decimal_conversion_error(
                    parameter_index,
                    DecimalConversionFailure::PrecisionOverflow,
                )
            })?
    }
    .max(usize::from(scale));
    if precision > 38 {
        return Err(decimal_conversion_error(
            parameter_index,
            DecimalConversionFailure::PrecisionOverflow,
        ));
    }

    if sign == 1 {
        coefficient = coefficient.checked_neg().ok_or_else(|| {
            decimal_conversion_error(parameter_index, DecimalConversionFailure::PrecisionOverflow)
        })?;
    }

    Ok(FastParameter::new(FastParameterValue::Numeric(
        tiberius::numeric::Numeric::new_with_scale(coefficient, scale),
    )))
}

/// Convert a `&[FastParameter]` into a `SmallVec` of `&dyn tiberius::ToSql` fat-pointer
/// references for passing directly to tiberius `query`/`execute` methods.
///
/// Performance characteristics:
/// - ≤16 parameters → zero heap allocation (entire buffer on the stack)
/// - >16 parameters → exactly one heap allocation
///
/// Buffer reuse across async call boundaries is architecturally impossible in safe Rust
/// because the references borrow from per-call locals. This helper centralises the
/// conversion so the pattern is implemented once and used consistently everywhere.
#[inline]
pub fn params_as_sql_refs(params: &[FastParameter]) -> SmallVec<[&dyn tiberius::ToSql; 16]> {
    // slice::iter() provides an ExactSizeIterator, so SmallVec's FromIterator impl
    // can honour the size hint and stay on the stack for ≤16 elements.
    params.iter().map(|p| p as &dyn tiberius::ToSql).collect()
}

pub fn convert_parameters_to_fast(
    parameters: Option<&Bound<PyAny>>,
    py: Python,
) -> PyResult<SmallVec<[FastParameter; 16]>> {
    if let Some(params) = parameters {
        if let Ok(params_obj) = params.extract::<Py<Parameters>>() {
            let list = params_obj.bind(py).call_method0("to_list")?;
            python_params_to_fast_parameters(list.cast::<PyList>()?)
        } else if let Ok(list) = params.cast::<PyList>() {
            python_params_to_fast_parameters(list)
        } else {
            Err(PyValueError::new_err("Must be list or Parameters object"))
        }
    } else {
        Ok(SmallVec::new())
    }
}

fn python_params_to_fast_parameters(
    params: &Bound<PyList>,
) -> PyResult<SmallVec<[FastParameter; 16]>> {
    let len = params.len();

    if len > MAX_USER_QUERY_PARAMETERS {
        return Err(PyValueError::new_err(format!(
            "Too many parameters: {} provided, but FastMssql supports maximum 2,098 user \
             parameters per query (SQL Server RPC limit 2,100 minus 2 internal parameters)",
            len
        )));
    }

    // SmallVec optimization:
    // - 0-16 parameters: Zero heap allocations (stack only)
    // - 17+ parameters: Single heap allocation (very rare case)
    // - No unnecessary into_vec() conversion
    let mut result: SmallVec<[FastParameter; 16]> = SmallVec::with_capacity(len);

    for param in params.iter() {
        if type_mapping::is_expandable_iterable(&param)? {
            // Calculate remaining budget and pass it to prevent unbounded generator expansion
            let remaining = MAX_USER_QUERY_PARAMETERS.saturating_sub(result.len());
            let parameter_index = result.len();
            expand_iterable_to_fast_params(&param, &mut result, remaining, parameter_index)?;
        } else {
            let parameter_index = result.len();
            result.push(python_to_fast_parameter_at(&param, parameter_index)?);
        }
    }

    // Final validation: ensure we haven't exceeded the limit
    if result.len() > MAX_USER_QUERY_PARAMETERS {
        return Err(PyValueError::new_err(format!(
            "FastMssql user parameter limit exceeded: {} parameters (max: 2,098)",
            result.len()
        )));
    }

    Ok(result)
}

/// Expand a Python iterable into individual FastParameter objects with minimal allocations.
///
/// **IMPORTANT**: The `remaining` parameter enforces a hard limit on expansion to prevent DoS attacks
/// from generators that could otherwise yield unlimited items. This function will short-circuit
/// and return an error if the remaining budget is exhausted before the iterator is consumed.
fn expand_iterable_to_fast_params<T>(
    iterable: &Bound<PyAny>,
    result: &mut T,
    mut remaining: usize,
    mut parameter_index: usize,
) -> PyResult<()>
where
    T: Extend<FastParameter>,
{
    use pyo3::types::{PyList, PyTuple};

    // Fast path for common collection types - avoid iterator overhead
    if let Ok(list) = iterable.cast::<PyList>() {
        for item in list.iter() {
            if remaining == 0 {
                return Err(PyValueError::new_err(
                    "Parameter expansion exceeded FastMssql limit of 2,098 user parameters per query",
                ));
            }
            let param = python_to_fast_parameter_at(&item, parameter_index)?;
            result.extend(std::iter::once(param));
            remaining -= 1;
            parameter_index += 1;
        }
        return Ok(());
    }

    if let Ok(tuple) = iterable.cast::<PyTuple>() {
        for item in tuple.iter() {
            if remaining == 0 {
                return Err(PyValueError::new_err(
                    "Parameter expansion exceeded FastMssql limit of 2,098 user parameters per query",
                ));
            }
            let param = python_to_fast_parameter_at(&item, parameter_index)?;
            result.extend(std::iter::once(param));
            remaining -= 1;
            parameter_index += 1;
        }
        return Ok(());
    }

    // Fallback for generic iterables (generators, custom iterators, etc.) - use PyO3's optimized iteration
    // The remaining counter prevents unbounded expansion from malicious generators
    let py = iterable.py();
    let iter = iterable.call_method0("__iter__")?;

    let mut batch: SmallVec<[FastParameter; 16]> = SmallVec::new();

    loop {
        match iter.call_method0("__next__") {
            Ok(item) => {
                if remaining == 0 {
                    return Err(PyValueError::new_err(
                        "Parameter expansion exceeded FastMssql limit of 2,098 user parameters per query",
                    ));
                }
                batch.push(python_to_fast_parameter_at(&item, parameter_index)?);
                remaining -= 1;
                parameter_index += 1;

                // Batch extend every 16 items to reduce extend() call overhead
                if batch.len() == 16 {
                    result.extend(batch.drain(..));
                }
            }
            Err(err) => {
                // Check if it's StopIteration (normal end of iteration)
                if err.is_instance_of::<pyo3::exceptions::PyStopIteration>(py) {
                    break;
                } else {
                    return Err(err);
                }
            }
        }
    }

    // Extend any remaining items in the batch
    if !batch.is_empty() {
        result.extend(batch);
    }

    Ok(())
}

/// Class to store a typed null value
///
/// This is required as some SQL Server features such as stored procedures etc. sometimes require type information for which is
/// not possible for nulls when just using `None`. In such cases, SQL Server will complain about being unable to cast 'tinyint'
/// to the desired data type.
#[pyclass(name = "TypedNull", from_py_object)]
#[derive(Debug, Clone)]
pub enum TypedNull {
    U8,
    I16,
    I32,
    I64,
    F32,
    F64,
    Bit,
    String,
    Guid,
    Binary,
    Numeric,
    Xml,
    DateTime,
    SmallDateTime,
    Time,
    Date,
    DateTime2,
    DateTimeOffset,
}

impl tiberius::ToSql for TypedNull {
    fn to_sql(&self) -> tiberius::ColumnData<'_> {
        match self {
            TypedNull::U8 => tiberius::ColumnData::U8(None),
            TypedNull::I16 => tiberius::ColumnData::I16(None),
            TypedNull::I32 => tiberius::ColumnData::I32(None),
            TypedNull::I64 => tiberius::ColumnData::I64(None),
            TypedNull::F32 => tiberius::ColumnData::F32(None),
            TypedNull::F64 => tiberius::ColumnData::F64(None),
            TypedNull::Bit => tiberius::ColumnData::Bit(None),
            TypedNull::String => tiberius::ColumnData::String(None),
            TypedNull::Guid => tiberius::ColumnData::Guid(None),
            TypedNull::Binary => tiberius::ColumnData::Binary(None),
            TypedNull::Numeric => tiberius::ColumnData::Numeric(None),
            TypedNull::Xml => tiberius::ColumnData::Xml(None),
            TypedNull::DateTime => tiberius::ColumnData::DateTime(None),
            TypedNull::SmallDateTime => tiberius::ColumnData::SmallDateTime(None),
            TypedNull::Time => tiberius::ColumnData::Time(None),
            TypedNull::Date => tiberius::ColumnData::Date(None),
            TypedNull::DateTime2 => tiberius::ColumnData::DateTime2(None),
            TypedNull::DateTimeOffset => tiberius::ColumnData::DateTimeOffset(None),
        }
    }
}

#[pymethods]
impl TypedNull {
    #[classattr]
    const TINYINT: TypedNull = TypedNull::U8;
    #[classattr]
    const SMALLINT: TypedNull = TypedNull::I16;
    #[classattr]
    const INT: TypedNull = TypedNull::I32;
    #[classattr]
    const BIGINT: TypedNull = TypedNull::I64;
    #[classattr]
    const FLOAT32: TypedNull = TypedNull::F32;
    #[classattr]
    const FLOAT64: TypedNull = TypedNull::F64;
    #[classattr]
    const BIT: TypedNull = TypedNull::Bit;
    #[classattr]
    const STRING: TypedNull = TypedNull::String;
    #[classattr]
    const GUID: TypedNull = TypedNull::Guid;
    #[classattr]
    const BINARY: TypedNull = TypedNull::Binary;
    #[classattr]
    const NUMERIC: TypedNull = TypedNull::Numeric;
    #[classattr]
    const XML: TypedNull = TypedNull::Xml;
    #[classattr]
    const DATETIME: TypedNull = TypedNull::DateTime;
    #[classattr]
    const SMALLDATETIME: TypedNull = TypedNull::SmallDateTime;
    #[classattr]
    const TIME: TypedNull = TypedNull::Time;
    #[classattr]
    const DATE: TypedNull = TypedNull::Date;
    #[classattr]
    const DATETIME2: TypedNull = TypedNull::DateTime2;
    #[classattr]
    const DATETIMEOFFSET: TypedNull = TypedNull::DateTimeOffset;

    pub fn __str__(&self) -> String {
        match self {
            TypedNull::U8 => "TINYINT".into(),
            TypedNull::I16 => "SMALLINT".into(),
            TypedNull::I32 => "INT".into(),
            TypedNull::I64 => "BIGINT".into(),
            TypedNull::F32 => "FLOAT32".into(),
            TypedNull::F64 => "FLOAT64".into(),
            TypedNull::Bit => "BIT".into(),
            TypedNull::String => "STRING".into(),
            TypedNull::Guid => "GUID".into(),
            TypedNull::Binary => "BINARY".into(),
            TypedNull::Numeric => "NUMERIC".into(),
            TypedNull::Xml => "XML".into(),
            TypedNull::DateTime => "DATETIME".into(),
            TypedNull::SmallDateTime => "SMALLDATETIME".into(),
            TypedNull::Time => "TIME".into(),
            TypedNull::Date => "DATE".into(),
            TypedNull::DateTime2 => "DATETIME2".into(),
            TypedNull::DateTimeOffset => "DATETIMEOFFSET".into(),
        }
    }

    pub fn __repr__(&self) -> String {
        format!("TypedNull.{}", self.__str__())
    }
}
