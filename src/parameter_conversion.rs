use crate::py_parameters::{Parameter, ParameterDirection, Parameters};
use crate::type_mapping;
use crate::types::create_parameter_conversion_error;
use chrono::{
    DateTime, Datelike, Duration, FixedOffset, NaiveDate, NaiveDateTime, NaiveTime, Timelike,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyByteArray, PyByteArrayMethods, PyBytes, PyDate, PyDateTime, PyFloat, PyInt, PyList,
    PyMemoryView, PyString, PyTime, PyTuple,
};
use smallvec::SmallVec;
use tiberius::{SqlParameterKind, SqlParameterType, TypeLength};

/// SQL Server's RPC parameter ceiling includes the two parameters that
/// Tiberius adds for `sp_executesql`: `@stmt` and `@params`.
pub(crate) const MAX_USER_QUERY_PARAMETERS: usize = 2_098;

#[derive(Debug, Clone)]
pub struct FastParameter {
    pub(crate) value: FastParameterValue,
    sql_type: Option<SqlParameterType>,
}

#[derive(Debug, Clone)]
pub(crate) enum FastParameterValue {
    Null(TypedNull),
    Bool(bool),
    U8(u8),
    I16(i16),
    I32(i32),
    I64(i64),
    F32(f32),
    F64(f64),
    String(String),
    Bytes(Vec<u8>),
    Xml(tiberius::xml::XmlData),
    Numeric(tiberius::numeric::Numeric),
    Date(NaiveDate),
    Time(NaiveTime),
    DateTime(NaiveDateTime),
    DateTimeOffset(DateTime<FixedOffset>),
    TdsDate(tiberius::time::Date),
    TdsTime(tiberius::time::Time),
    TdsDateTime(tiberius::time::DateTime),
    TdsSmallDateTime(tiberius::time::SmallDateTime),
    TdsDateTime2(tiberius::time::DateTime2),
    TdsDateTimeOffset(tiberius::time::DateTimeOffset),
    Uuid(uuid::Uuid),
}

impl FastParameter {
    fn new(value: FastParameterValue) -> Self {
        Self {
            value,
            sql_type: None,
        }
    }

    fn typed(value: FastParameterValue, sql_type: SqlParameterType) -> Self {
        Self {
            value,
            sql_type: Some(sql_type),
        }
    }
}

impl tiberius::ToSql for FastParameter {
    fn to_sql(&self) -> tiberius::ColumnData<'_> {
        match &self.value {
            FastParameterValue::Null(t) => t.to_sql(),
            FastParameterValue::Bool(b) => b.to_sql(),
            FastParameterValue::U8(value) => value.to_sql(),
            FastParameterValue::I16(value) => value.to_sql(),
            FastParameterValue::I32(value) => value.to_sql(),
            FastParameterValue::I64(i) => i.to_sql(),
            FastParameterValue::F32(value) => value.to_sql(),
            FastParameterValue::F64(f) => f.to_sql(),
            FastParameterValue::String(s) => s.to_sql(),
            FastParameterValue::Bytes(b) => b.to_sql(),
            FastParameterValue::Xml(xml) => xml.to_sql(),
            FastParameterValue::Numeric(n) => n.to_sql(),
            FastParameterValue::Date(d) => d.to_sql(),
            FastParameterValue::Time(t) => t.to_sql(),
            FastParameterValue::DateTime(dt) => dt.to_sql(),
            FastParameterValue::DateTimeOffset(dt) => dt.to_sql(),
            FastParameterValue::TdsDate(value) => tiberius::ColumnData::Date(Some(*value)),
            FastParameterValue::TdsTime(value) => tiberius::ColumnData::Time(Some(*value)),
            FastParameterValue::TdsDateTime(value) => tiberius::ColumnData::DateTime(Some(*value)),
            FastParameterValue::TdsSmallDateTime(value) => {
                tiberius::ColumnData::SmallDateTime(Some(*value))
            }
            FastParameterValue::TdsDateTime2(value) => {
                tiberius::ColumnData::DateTime2(Some(*value))
            }
            FastParameterValue::TdsDateTimeOffset(value) => {
                tiberius::ColumnData::DateTimeOffset(Some(*value))
            }
            FastParameterValue::Uuid(uuid) => uuid.to_sql(),
        }
    }

    fn sql_parameter_type(&self) -> Option<SqlParameterType> {
        self.sql_type.clone()
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
    if is_uuid_instance(obj)? {
        return uuid_to_fast_parameter(obj, parameter_index);
    }
    if let Ok(py_time) = obj.cast::<PyTime>() {
        return time_to_fast_parameter(py_time, parameter_index);
    }
    if let Ok(py_datetime) = obj.cast::<PyDateTime>() {
        return datetime_to_fast_parameter(py_datetime, parameter_index);
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

fn python_to_typed_fast_parameter(
    obj: &Bound<PyAny>,
    sql_type: &SqlParameterType,
    direction: ParameterDirection,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    let declaration = sql_type.declaration();
    if direction != ParameterDirection::Input {
        return Err(typed_conversion_error(
            parameter_index,
            &declaration,
            "unsupported_direction",
            "Only INPUT parameter direction is supported by query execution",
        ));
    }

    if obj.is_none() {
        return Ok(FastParameter::typed(
            FastParameterValue::Null(typed_null_for(sql_type)),
            sql_type.clone(),
        ));
    }

    let value = match sql_type.kind() {
        SqlParameterKind::Bit => {
            let value = obj
                .cast::<PyBool>()
                .map_err(|_| wrong_value_kind(parameter_index, &declaration))?
                .is_true();
            FastParameterValue::Bool(value)
        }
        SqlParameterKind::TinyInt => {
            FastParameterValue::U8(extract_exact_integer(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::SmallInt => {
            FastParameterValue::I16(extract_exact_integer(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::Int => {
            FastParameterValue::I32(extract_exact_integer(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::BigInt => {
            FastParameterValue::I64(extract_exact_integer(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::Real => {
            let value = extract_exact_float(obj, parameter_index, &declaration)?;
            let value = value as f32;
            if !value.is_finite() {
                return Err(typed_conversion_error(
                    parameter_index,
                    &declaration,
                    "non_finite",
                    "Floating-point parameter must be finite",
                ));
            }
            FastParameterValue::F32(value)
        }
        SqlParameterKind::Float if sql_type.precision().unwrap_or(53) <= 24 => {
            let value = extract_exact_float(obj, parameter_index, &declaration)? as f32;
            if !value.is_finite() {
                return Err(typed_conversion_error(
                    parameter_index,
                    &declaration,
                    "non_finite",
                    "Floating-point parameter must be finite",
                ));
            }
            FastParameterValue::F32(value)
        }
        SqlParameterKind::Float => {
            FastParameterValue::F64(extract_exact_float(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::Decimal | SqlParameterKind::Numeric => {
            if !is_decimal_instance(obj)? {
                return Err(wrong_value_kind(parameter_index, &declaration));
            }
            let precision = sql_type.precision().unwrap_or(38);
            let scale = sql_type.scale().unwrap_or(0);
            FastParameterValue::Numeric(decimal_to_typed_numeric(
                obj,
                precision,
                scale,
                parameter_index,
                &declaration,
            )?)
        }
        SqlParameterKind::Char
        | SqlParameterKind::VarChar
        | SqlParameterKind::NChar
        | SqlParameterKind::NVarChar => {
            let value = extract_exact_string(obj, parameter_index, &declaration)?;
            validate_string_length(&value, sql_type, parameter_index, &declaration)?;
            FastParameterValue::String(value)
        }
        SqlParameterKind::Binary | SqlParameterKind::VarBinary => {
            let value = extract_exact_binary(obj, parameter_index, &declaration)?;
            if let Some(TypeLength::Limited(limit)) = sql_type.length()
                && value.len() > usize::from(limit)
            {
                return Err(typed_conversion_error(
                    parameter_index,
                    &declaration,
                    "length_overflow",
                    "Binary parameter exceeds the declared length",
                ));
            }
            FastParameterValue::Bytes(value)
        }
        SqlParameterKind::UniqueIdentifier => {
            FastParameterValue::Uuid(extract_explicit_uuid(obj, parameter_index, &declaration)?)
        }
        SqlParameterKind::Date => {
            if obj.is_instance_of::<PyDateTime>() || !obj.is_instance_of::<PyDate>() {
                return Err(wrong_value_kind(parameter_index, &declaration));
            }
            let date = obj
                .extract::<NaiveDate>()
                .map_err(|_| wrong_value_kind(parameter_index, &declaration))?;
            FastParameterValue::TdsDate(tds_date(date))
        }
        SqlParameterKind::Time => {
            let py_time = obj
                .cast::<PyTime>()
                .map_err(|_| wrong_value_kind(parameter_index, &declaration))?;
            let inferred = time_to_fast_parameter(py_time, parameter_index)?;
            let FastParameterValue::Time(time) = inferred.value else {
                return Err(wrong_value_kind(parameter_index, &declaration));
            };
            let (time, _) = scaled_tds_time(time, sql_type.scale().unwrap_or(7));
            FastParameterValue::TdsTime(time)
        }
        SqlParameterKind::DateTime => {
            let datetime = extract_naive_datetime(obj, parameter_index, &declaration)?;
            FastParameterValue::TdsDateTime(datetime_to_tds_datetime(
                datetime,
                parameter_index,
                &declaration,
            )?)
        }
        SqlParameterKind::SmallDateTime => {
            let datetime = extract_naive_datetime(obj, parameter_index, &declaration)?;
            FastParameterValue::TdsSmallDateTime(datetime_to_tds_smalldatetime(
                datetime,
                parameter_index,
                &declaration,
            )?)
        }
        SqlParameterKind::DateTime2 => {
            let datetime = extract_naive_datetime(obj, parameter_index, &declaration)?;
            FastParameterValue::TdsDateTime2(datetime_to_tds_datetime2(
                datetime,
                sql_type.scale().unwrap_or(7),
                parameter_index,
                &declaration,
            )?)
        }
        SqlParameterKind::DateTimeOffset => {
            FastParameterValue::TdsDateTimeOffset(datetime_to_tds_datetimeoffset(
                obj,
                sql_type.scale().unwrap_or(7),
                parameter_index,
                &declaration,
            )?)
        }
        SqlParameterKind::Xml => FastParameterValue::Xml(tiberius::xml::XmlData::new(
            extract_exact_string(obj, parameter_index, &declaration)?,
        )),
    };

    Ok(FastParameter::typed(value, sql_type.clone()))
}

fn typed_null_for(sql_type: &SqlParameterType) -> TypedNull {
    match sql_type.kind() {
        SqlParameterKind::Bit => TypedNull::Bit,
        SqlParameterKind::TinyInt => TypedNull::U8,
        SqlParameterKind::SmallInt => TypedNull::I16,
        SqlParameterKind::Int => TypedNull::I32,
        SqlParameterKind::BigInt => TypedNull::I64,
        SqlParameterKind::Real => TypedNull::F32,
        SqlParameterKind::Float if sql_type.precision().unwrap_or(53) <= 24 => TypedNull::F32,
        SqlParameterKind::Float => TypedNull::F64,
        SqlParameterKind::Decimal | SqlParameterKind::Numeric => TypedNull::Numeric,
        SqlParameterKind::Char
        | SqlParameterKind::VarChar
        | SqlParameterKind::NChar
        | SqlParameterKind::NVarChar => TypedNull::String,
        SqlParameterKind::Binary | SqlParameterKind::VarBinary => TypedNull::Binary,
        SqlParameterKind::UniqueIdentifier => TypedNull::Guid,
        SqlParameterKind::Date => TypedNull::Date,
        SqlParameterKind::Time => TypedNull::Time,
        SqlParameterKind::DateTime => TypedNull::DateTime,
        SqlParameterKind::SmallDateTime => TypedNull::SmallDateTime,
        SqlParameterKind::DateTime2 => TypedNull::DateTime2,
        SqlParameterKind::DateTimeOffset => TypedNull::DateTimeOffset,
        SqlParameterKind::Xml => TypedNull::Xml,
    }
}

fn typed_conversion_error(
    parameter_index: usize,
    sql_type: &str,
    reason: &str,
    message: &str,
) -> PyErr {
    create_parameter_conversion_error(parameter_index, sql_type, reason, message)
}

fn wrong_value_kind(parameter_index: usize, sql_type: &str) -> PyErr {
    typed_conversion_error(
        parameter_index,
        sql_type,
        "wrong_value_kind",
        "Python value has the wrong kind for the declared SQL parameter type",
    )
}

fn extract_exact_integer<T>(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<T>
where
    for<'py> T: FromPyObject<'py, 'py>,
{
    if obj.is_instance_of::<PyBool>() || !obj.is_instance_of::<PyInt>() {
        return Err(wrong_value_kind(parameter_index, sql_type));
    }
    obj.extract::<T>().map_err(|_| {
        typed_conversion_error(
            parameter_index,
            sql_type,
            "integer_out_of_range",
            "Integer parameter is outside the declared SQL type range",
        )
    })
}

fn extract_exact_float(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<f64> {
    let value = obj
        .cast::<PyFloat>()
        .map_err(|_| wrong_value_kind(parameter_index, sql_type))?
        .value();
    if !value.is_finite() {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "non_finite",
            "Floating-point parameter must be finite",
        ));
    }
    Ok(value)
}

fn extract_exact_string(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<String> {
    obj.cast::<PyString>()
        .map_err(|_| wrong_value_kind(parameter_index, sql_type))?
        .to_str()
        .map(str::to_owned)
        .map_err(|_| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "invalid_string",
                "String parameter conversion failed",
            )
        })
}

fn extract_exact_binary(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<Vec<u8>> {
    if let Ok(bytes) = obj.cast::<PyBytes>() {
        return Ok(bytes.as_bytes().to_vec());
    }
    if let Ok(bytes) = obj.cast::<PyByteArray>() {
        return Ok(bytes.to_vec());
    }
    if obj.is_instance_of::<PyMemoryView>() {
        return PyByteArray::from(obj)
            .map(|bytes| bytes.to_vec())
            .map_err(|_| wrong_value_kind(parameter_index, sql_type));
    }
    Err(wrong_value_kind(parameter_index, sql_type))
}

fn validate_string_length(
    value: &str,
    sql_type: &SqlParameterType,
    parameter_index: usize,
    declaration: &str,
) -> PyResult<()> {
    let Some(TypeLength::Limited(limit)) = sql_type.length() else {
        return Ok(());
    };
    let units = match sql_type.kind() {
        SqlParameterKind::NChar | SqlParameterKind::NVarChar => value.encode_utf16().count(),
        _ => value.chars().count(),
    };
    if units > usize::from(limit) {
        return Err(typed_conversion_error(
            parameter_index,
            declaration,
            "length_overflow",
            "String parameter exceeds the declared length",
        ));
    }
    Ok(())
}

fn extract_explicit_uuid(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<uuid::Uuid> {
    if is_uuid_instance(obj)? {
        let parameter = uuid_to_fast_parameter(obj, parameter_index)?;
        if let FastParameterValue::Uuid(uuid) = parameter.value {
            return Ok(uuid);
        }
    }
    if let Ok(value) = obj.cast::<PyString>() {
        return value
            .to_str()
            .ok()
            .and_then(|value| uuid::Uuid::parse_str(value).ok())
            .ok_or_else(|| {
                typed_conversion_error(
                    parameter_index,
                    sql_type,
                    "invalid_uuid",
                    "UUID parameter conversion failed",
                )
            });
    }
    Err(wrong_value_kind(parameter_index, sql_type))
}

fn rescale_decimal_digits(
    digits: &[u8],
    exponent: i64,
    negative: bool,
    precision: u8,
    target_scale: u8,
) -> Option<i128> {
    if !(1..=38).contains(&precision)
        || target_scale > precision
        || digits.is_empty()
        || digits.iter().any(|digit| *digit > 9)
    {
        return None;
    }

    let first_significant = digits.iter().position(|digit| *digit != 0);
    let Some(first_significant) = first_significant else {
        return Some(0);
    };
    let significant = &digits[first_significant..];
    let shift = exponent.checked_add(i64::from(target_scale))?;

    let mut coefficient = if shift >= 0 {
        let shift = usize::try_from(shift).ok()?;
        if significant.len().checked_add(shift)? > usize::from(precision) {
            return None;
        }
        let coefficient = parse_decimal_coefficient(significant)?;
        coefficient.checked_mul(10_i128.checked_pow(u32::try_from(shift).ok()?)?)?
    } else {
        let discarded = shift
            .checked_neg()
            .and_then(|value| usize::try_from(value).ok())
            .unwrap_or(usize::MAX);
        if discarded > significant.len() {
            0
        } else if discarded == significant.len() {
            i128::from(significant[0] >= 5)
        } else {
            let kept = significant.len() - discarded;
            if kept > usize::from(precision) {
                return None;
            }
            let mut coefficient = parse_decimal_coefficient(&significant[..kept])?;
            if significant[kept] >= 5 {
                coefficient = coefficient.checked_add(1)?;
            }
            coefficient
        }
    };

    let limit = 10_i128.checked_pow(u32::from(precision))?;
    if coefficient >= limit {
        return None;
    }
    if negative && coefficient != 0 {
        coefficient = coefficient.checked_neg()?;
    }

    Some(coefficient)
}

fn parse_decimal_coefficient(digits: &[u8]) -> Option<i128> {
    digits.iter().try_fold(0_i128, |coefficient, digit| {
        coefficient
            .checked_mul(10)
            .and_then(|value| value.checked_add(i128::from(*digit)))
    })
}

fn decimal_to_typed_numeric(
    obj: &Bound<PyAny>,
    precision: u8,
    target_scale: u8,
    parameter_index: usize,
    declaration: &str,
) -> PyResult<tiberius::numeric::Numeric> {
    let invalid = || {
        typed_conversion_error(
            parameter_index,
            declaration,
            "invalid_decimal",
            "Decimal parameter conversion failed",
        )
    };
    let decimal_class = type_mapping::get_decimal_class(obj.py()).map_err(|_| invalid())?;
    let is_finite = decimal_class
        .getattr("is_finite")
        .and_then(|method| method.call1((obj,)))
        .and_then(|result| result.extract::<bool>())
        .map_err(|_| invalid())?;
    if !is_finite {
        return Err(typed_conversion_error(
            parameter_index,
            declaration,
            "non_finite",
            "Decimal parameter must be finite",
        ));
    }

    let components = decimal_class
        .getattr("as_tuple")
        .and_then(|method| method.call1((obj,)))
        .map_err(|_| invalid())?;
    let sign = components
        .getattr("sign")
        .and_then(|value| value.extract::<u8>())
        .map_err(|_| invalid())?;
    if sign > 1 {
        return Err(invalid());
    }
    let exponent = components
        .getattr("exponent")
        .and_then(|value| value.extract::<i64>())
        .map_err(|_| invalid())?;
    let digits_object = components.getattr("digits").map_err(|_| invalid())?;
    let digits = digits_object.cast::<PyTuple>().map_err(|_| invalid())?;
    let mut decimal_digits = Vec::with_capacity(digits.len());
    for digit in digits.iter() {
        let digit = digit.extract::<u8>().map_err(|_| invalid())?;
        if digit > 9 {
            return Err(invalid());
        }
        decimal_digits.push(digit);
    }

    let coefficient = rescale_decimal_digits(
        &decimal_digits,
        exponent,
        sign == 1,
        precision,
        target_scale,
    )
    .ok_or_else(|| {
        typed_conversion_error(
            parameter_index,
            declaration,
            "precision_overflow",
            "Decimal parameter exceeds the declared precision",
        )
    })?;

    Ok(tiberius::numeric::Numeric::new_with_scale(
        coefficient,
        target_scale,
    ))
}

fn extract_naive_datetime(
    obj: &Bound<PyAny>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<NaiveDateTime> {
    let datetime = obj
        .cast::<PyDateTime>()
        .map_err(|_| wrong_value_kind(parameter_index, sql_type))?;
    let offset = datetime
        .call_method0("utcoffset")
        .map_err(|_| wrong_value_kind(parameter_index, sql_type))?;
    if !offset.is_none() {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "timezone_not_supported",
            "Declared SQL parameter type requires a naive datetime",
        ));
    }
    python_datetime_components(datetime, parameter_index, sql_type)
}

fn tds_date(date: NaiveDate) -> tiberius::time::Date {
    let epoch = NaiveDate::from_ymd_opt(1, 1, 1).expect("valid static epoch");
    let days = date.signed_duration_since(epoch).num_days();
    tiberius::time::Date::new(days as u32)
}

fn scaled_tds_time(time: NaiveTime, scale: u8) -> (tiberius::time::Time, bool) {
    let total_microseconds = u64::from(time.num_seconds_from_midnight()) * 1_000_000
        + u64::from(time.nanosecond() / 1_000);
    let increments = if scale == 7 {
        total_microseconds * 10
    } else {
        let divisor = 10_u64.pow(u32::from(6 - scale));
        (total_microseconds + divisor / 2) / divisor
    };
    let increments_per_day = 86_400_u64 * 10_u64.pow(u32::from(scale));
    let carry = increments == increments_per_day;
    (
        tiberius::time::Time::new(if carry { 0 } else { increments }, scale),
        carry,
    )
}

fn add_rounding_day(
    date: NaiveDate,
    carry: bool,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<NaiveDate> {
    let rounded_date = if carry {
        date.checked_add_signed(Duration::days(1)).ok_or_else(|| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "datetime_out_of_range",
                "Temporal rounding exceeds the SQL Server datetime range",
            )
        })?
    } else {
        date
    };
    if !(1..=9_999).contains(&rounded_date.year()) {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "datetime_out_of_range",
            "Temporal rounding exceeds the SQL Server datetime range",
        ));
    }
    Ok(rounded_date)
}

fn datetime_to_tds_datetime2(
    datetime: NaiveDateTime,
    scale: u8,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<tiberius::time::DateTime2> {
    let (time, carry) = scaled_tds_time(datetime.time(), scale);
    let date = add_rounding_day(datetime.date(), carry, parameter_index, sql_type)?;
    Ok(tiberius::time::DateTime2::new(tds_date(date), time))
}

fn datetime_to_tds_datetime(
    datetime: NaiveDateTime,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<tiberius::time::DateTime> {
    let minimum = NaiveDate::from_ymd_opt(1753, 1, 1).expect("valid static date");
    let epoch = NaiveDate::from_ymd_opt(1900, 1, 1).expect("valid static date");
    if datetime.date() < minimum {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "datetime_out_of_range",
            "Datetime parameter is outside the SQL Server DATETIME range",
        ));
    }
    let total_microseconds = u64::from(datetime.time().num_seconds_from_midnight()) * 1_000_000
        + u64::from(datetime.time().nanosecond() / 1_000);
    let fragments = (total_microseconds * 300 + 500_000) / 1_000_000;
    let fragments_per_day = 86_400_u64 * 300;
    let carry = fragments == fragments_per_day;
    let date = add_rounding_day(datetime.date(), carry, parameter_index, sql_type)?;
    let days = date.signed_duration_since(epoch).num_days();
    Ok(tiberius::time::DateTime::new(
        i32::try_from(days).map_err(|_| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "datetime_out_of_range",
                "Datetime parameter is outside the SQL Server DATETIME range",
            )
        })?,
        if carry { 0 } else { fragments as u32 },
    ))
}

fn datetime_to_tds_smalldatetime(
    datetime: NaiveDateTime,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<tiberius::time::SmallDateTime> {
    let minimum = NaiveDate::from_ymd_opt(1900, 1, 1).expect("valid static date");
    let maximum = NaiveDate::from_ymd_opt(2079, 6, 6).expect("valid static date");
    if datetime.date() < minimum || datetime.date() > maximum {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "datetime_out_of_range",
            "Datetime parameter is outside the SQL Server SMALLDATETIME range",
        ));
    }

    let seconds_in_minute =
        u64::from(datetime.second()) * 1_000_000 + u64::from(datetime.nanosecond() / 1_000);
    let mut minutes = datetime.hour() * 60 + datetime.minute();
    if seconds_in_minute >= 29_999_000 {
        minutes += 1;
    }
    let carry = minutes == 24 * 60;
    let date = add_rounding_day(datetime.date(), carry, parameter_index, sql_type)?;
    if date > maximum {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "datetime_out_of_range",
            "Temporal rounding exceeds the SQL Server SMALLDATETIME range",
        ));
    }
    let days = date.signed_duration_since(minimum).num_days();

    Ok(tiberius::time::SmallDateTime::new(
        u16::try_from(days).map_err(|_| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "datetime_out_of_range",
                "Datetime parameter is outside the SQL Server SMALLDATETIME range",
            )
        })?,
        if carry { 0 } else { minutes as u16 },
    ))
}

fn datetime_to_tds_datetimeoffset(
    obj: &Bound<PyAny>,
    scale: u8,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<tiberius::time::DateTimeOffset> {
    let datetime = obj
        .cast::<PyDateTime>()
        .map_err(|_| wrong_value_kind(parameter_index, sql_type))?;
    let inferred = datetime_to_fast_parameter(datetime, parameter_index)?;
    let FastParameterValue::DateTimeOffset(datetime) = inferred.value else {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "timezone_required",
            "DATETIMEOFFSET requires an aware datetime",
        ));
    };

    let local = datetime.naive_local();
    let (local_time, carry) = scaled_tds_time(local.time(), scale);
    let local_date = add_rounding_day(local.date(), carry, parameter_index, sql_type)?;
    let rounded_local = NaiveDateTime::new(
        local_date,
        tds_time_to_naive(local_time).ok_or_else(|| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "invalid_datetime",
                "Datetime offset parameter conversion failed",
            )
        })?,
    );
    let offset_minutes = datetime.offset().local_minus_utc() / 60;
    let utc = rounded_local
        .checked_sub_signed(Duration::minutes(i64::from(offset_minutes)))
        .ok_or_else(|| {
            typed_conversion_error(
                parameter_index,
                sql_type,
                "datetime_out_of_range",
                "Datetime offset local and UTC values must be between year 1 and 9999",
            )
        })?;
    if !(1..=9_999).contains(&utc.year()) {
        return Err(typed_conversion_error(
            parameter_index,
            sql_type,
            "datetime_out_of_range",
            "Datetime offset local and UTC values must be between year 1 and 9999",
        ));
    }
    let (utc_time, utc_carry) = scaled_tds_time(utc.time(), scale);
    debug_assert!(!utc_carry);
    let datetime2 = tiberius::time::DateTime2::new(tds_date(utc.date()), utc_time);
    Ok(tiberius::time::DateTimeOffset::new(
        datetime2,
        offset_minutes as i16,
    ))
}

fn tds_time_to_naive(time: tiberius::time::Time) -> Option<NaiveTime> {
    let nanoseconds = time.increments() * 10_u64.pow(u32::from(9 - time.scale()));
    NaiveTime::from_num_seconds_from_midnight_opt(
        (nanoseconds / 1_000_000_000) as u32,
        (nanoseconds % 1_000_000_000) as u32,
    )
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

fn datetime_to_fast_parameter(
    py_datetime: &Bound<PyDateTime>,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    let offset = py_datetime
        .call_method0("utcoffset")
        .map_err(|_| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;

    if offset.is_none() {
        let datetime = python_datetime_components(py_datetime, parameter_index, "DATETIME2(7)")?;
        return Ok(FastParameter::new(FastParameterValue::DateTime(datetime)));
    }

    let offset = offset
        .extract::<Duration>()
        .map_err(|_| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;
    let offset_microseconds = offset
        .num_microseconds()
        .ok_or_else(|| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;
    const MICROSECONDS_PER_MINUTE: i64 = 60 * 1_000_000;
    if offset_microseconds % MICROSECONDS_PER_MINUTE != 0 {
        return Err(create_parameter_conversion_error(
            parameter_index,
            "DATETIMEOFFSET(7)",
            "offset_not_whole_minute",
            "Datetime offset must be a whole number of minutes",
        ));
    }
    let offset_minutes = offset_microseconds / MICROSECONDS_PER_MINUTE;
    if !(-14 * 60..=14 * 60).contains(&offset_minutes) {
        return Err(create_parameter_conversion_error(
            parameter_index,
            "DATETIMEOFFSET(7)",
            "offset_out_of_range",
            "Datetime offset must be between -14:00 and +14:00",
        ));
    }
    let offset_seconds = i32::try_from(offset_minutes * 60)
        .map_err(|_| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;
    let fixed_offset = FixedOffset::east_opt(offset_seconds)
        .ok_or_else(|| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;
    let local_datetime =
        python_datetime_components(py_datetime, parameter_index, "DATETIMEOFFSET(7)")?;
    let datetime = local_datetime
        .and_local_timezone(fixed_offset)
        .single()
        .ok_or_else(|| datetime_conversion_error(parameter_index, "DATETIMEOFFSET(7)"))?;
    let valid_year = 1..=9_999;
    if !valid_year.contains(&datetime.naive_local().year())
        || !valid_year.contains(&datetime.naive_utc().year())
    {
        return Err(create_parameter_conversion_error(
            parameter_index,
            "DATETIMEOFFSET(7)",
            "datetime_out_of_range",
            "Datetime offset local and UTC values must be between year 1 and 9999",
        ));
    }

    Ok(FastParameter::new(FastParameterValue::DateTimeOffset(
        datetime,
    )))
}

fn python_datetime_components(
    py_datetime: &Bound<PyDateTime>,
    parameter_index: usize,
    sql_type: &str,
) -> PyResult<NaiveDateTime> {
    let extract = |name: &'static str| {
        py_datetime
            .getattr(name)
            .and_then(|value| value.extract::<u32>())
            .map_err(|_| datetime_conversion_error(parameter_index, sql_type))
    };
    let year = extract("year")?;
    let month = extract("month")?;
    let day = extract("day")?;
    let hour = extract("hour")?;
    let minute = extract("minute")?;
    let second = extract("second")?;
    let microsecond = extract("microsecond")?;

    NaiveDate::from_ymd_opt(
        i32::try_from(year).map_err(|_| datetime_conversion_error(parameter_index, sql_type))?,
        month,
        day,
    )
    .and_then(|date| date.and_hms_micro_opt(hour, minute, second, microsecond))
    .ok_or_else(|| datetime_conversion_error(parameter_index, sql_type))
}

fn datetime_conversion_error(parameter_index: usize, sql_type: &str) -> PyErr {
    create_parameter_conversion_error(
        parameter_index,
        sql_type,
        "invalid_datetime",
        "Datetime parameter conversion failed",
    )
}

fn is_uuid_instance(obj: &Bound<PyAny>) -> PyResult<bool> {
    obj.is_instance(type_mapping::get_uuid_class(obj.py())?)
}

fn uuid_conversion_error(parameter_index: usize) -> PyErr {
    create_parameter_conversion_error(
        parameter_index,
        "UNIQUEIDENTIFIER",
        "invalid_uuid",
        "UUID parameter conversion failed",
    )
}

fn uuid_to_fast_parameter(obj: &Bound<PyAny>, parameter_index: usize) -> PyResult<FastParameter> {
    let bytes_object = obj
        .getattr("bytes")
        .map_err(|_| uuid_conversion_error(parameter_index))?;
    let bytes = bytes_object
        .cast_into::<PyBytes>()
        .map_err(|_| uuid_conversion_error(parameter_index))?;
    let uuid = uuid::Uuid::from_slice(bytes.as_bytes())
        .map_err(|_| uuid_conversion_error(parameter_index))?;
    Ok(FastParameter::new(FastParameterValue::Uuid(uuid)))
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
            parameters_object_to_fast_parameters(&params_obj.borrow(py), py)
        } else if let Ok(list) = params.cast::<PyList>() {
            python_list_to_fast_parameters(list)
        } else {
            Err(PyValueError::new_err("Must be list or Parameters object"))
        }
    } else {
        Ok(SmallVec::new())
    }
}

fn parameters_object_to_fast_parameters(
    parameters: &Parameters,
    py: Python,
) -> PyResult<SmallVec<[FastParameter; 16]>> {
    let named_len = parameters.named.bind(py).len();
    if named_len > 0 {
        return Err(PyValueError::new_err(format!(
            "Named parameters are not supported by the SQL Server wire protocol. \
             Use positional parameters instead. Found {named_len} named parameter(s)"
        )));
    }
    if parameters.positional.len() > MAX_USER_QUERY_PARAMETERS {
        return Err(parameter_count_error(parameters.positional.len()));
    }

    let mut result = SmallVec::with_capacity(parameters.positional.len());
    for parameter in &parameters.positional {
        append_parameter_descriptor(&parameter.borrow(py), &mut result)?;
    }
    validate_final_parameter_count(&result)?;
    Ok(result)
}

fn python_list_to_fast_parameters(
    params: &Bound<PyList>,
) -> PyResult<SmallVec<[FastParameter; 16]>> {
    let len = params.len();

    if len > MAX_USER_QUERY_PARAMETERS {
        return Err(parameter_count_error(len));
    }

    // SmallVec optimization:
    // - 0-16 parameters: Zero heap allocations (stack only)
    // - 17+ parameters: Single heap allocation (very rare case)
    // - No unnecessary into_vec() conversion
    let mut result: SmallVec<[FastParameter; 16]> = SmallVec::with_capacity(len);

    for param in params.iter() {
        if let Ok(descriptor) = param.extract::<Py<Parameter>>() {
            append_parameter_descriptor(&descriptor.borrow(param.py()), &mut result)?;
        } else if type_mapping::is_expandable_iterable(&param)? {
            // Calculate remaining budget and pass it to prevent unbounded generator expansion
            let remaining = MAX_USER_QUERY_PARAMETERS.saturating_sub(result.len());
            let parameter_index = result.len();
            expand_iterable_to_fast_params(
                &param,
                &mut result,
                remaining,
                parameter_index,
                None,
                ParameterDirection::Input,
            )?;
        } else {
            let parameter_index = result.len();
            result.push(python_to_fast_parameter_at(&param, parameter_index)?);
        }
    }

    // Final validation: ensure we haven't exceeded the limit
    validate_final_parameter_count(&result)?;
    Ok(result)
}

fn append_parameter_descriptor(
    parameter: &Parameter,
    result: &mut SmallVec<[FastParameter; 16]>,
) -> PyResult<()> {
    Python::attach(|py| {
        if parameter.direction != ParameterDirection::Input {
            let declaration = parameter
                .sql_type
                .as_ref()
                .map_or_else(|| "INFERRED".to_owned(), SqlParameterType::declaration);
            return Err(typed_conversion_error(
                result.len(),
                &declaration,
                "unsupported_direction",
                "Only INPUT parameter direction is supported by query execution",
            ));
        }

        let value = parameter.value.bind(py);
        if parameter.expanded {
            let remaining = MAX_USER_QUERY_PARAMETERS.saturating_sub(result.len());
            let parameter_index = result.len();
            expand_iterable_to_fast_params(
                value,
                result,
                remaining,
                parameter_index,
                parameter.sql_type.as_ref(),
                parameter.direction,
            )
        } else {
            if result.len() == MAX_USER_QUERY_PARAMETERS {
                return Err(parameter_count_error(result.len() + 1));
            }
            let parameter_index = result.len();
            let converted = match parameter.sql_type.as_ref() {
                Some(sql_type) => python_to_typed_fast_parameter(
                    value,
                    sql_type,
                    parameter.direction,
                    parameter_index,
                )?,
                None if parameter.direction == ParameterDirection::Input => {
                    python_to_fast_parameter_at(value, parameter_index)?
                }
                None => {
                    return Err(typed_conversion_error(
                        parameter_index,
                        "INFERRED",
                        "unsupported_direction",
                        "Only INPUT parameter direction is supported by query execution",
                    ));
                }
            };
            result.push(converted);
            Ok(())
        }
    })
}

fn parameter_count_error(count: usize) -> PyErr {
    PyValueError::new_err(format!(
        "Too many parameters: {count} provided, but FastMssql supports maximum 2,098 user \
         parameters per query (SQL Server RPC limit 2,100 minus 2 internal parameters)"
    ))
}

fn validate_final_parameter_count(params: &[FastParameter]) -> PyResult<()> {
    if params.len() > MAX_USER_QUERY_PARAMETERS {
        return Err(parameter_count_error(params.len()));
    }
    Ok(())
}

/// Expand a Python iterable into individual FastParameter objects with minimal allocations.
///
/// **IMPORTANT**: The `remaining` parameter enforces a hard limit on expansion to prevent DoS attacks
/// from generators that could otherwise yield unlimited items. This function will short-circuit
/// and return an error if the remaining budget is exhausted before the iterator is consumed.
fn expand_iterable_to_fast_params(
    iterable: &Bound<PyAny>,
    result: &mut SmallVec<[FastParameter; 16]>,
    mut remaining: usize,
    mut parameter_index: usize,
    sql_type: Option<&SqlParameterType>,
    direction: ParameterDirection,
) -> PyResult<()> {
    use pyo3::types::{PyList, PyTuple};

    // Fast path for common collection types - avoid iterator overhead
    if let Ok(list) = iterable.cast::<PyList>() {
        for item in list.iter() {
            if remaining == 0 {
                return Err(PyValueError::new_err(
                    "Parameter expansion exceeded FastMssql limit of 2,098 user parameters per query",
                ));
            }
            let param =
                expanded_item_to_fast_parameter(&item, sql_type, direction, parameter_index)?;
            result.push(param);
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
            let param =
                expanded_item_to_fast_parameter(&item, sql_type, direction, parameter_index)?;
            result.push(param);
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
                batch.push(expanded_item_to_fast_parameter(
                    &item,
                    sql_type,
                    direction,
                    parameter_index,
                )?);
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

fn expanded_item_to_fast_parameter(
    item: &Bound<PyAny>,
    sql_type: Option<&SqlParameterType>,
    direction: ParameterDirection,
    parameter_index: usize,
) -> PyResult<FastParameter> {
    match sql_type {
        Some(sql_type) => {
            python_to_typed_fast_parameter(item, sql_type, direction, parameter_index)
        }
        None if direction == ParameterDirection::Input => {
            python_to_fast_parameter_at(item, parameter_index)
        }
        None => Err(typed_conversion_error(
            parameter_index,
            "INFERRED",
            "unsupported_direction",
            "Only INPUT parameter direction is supported by query execution",
        )),
    }
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

#[cfg(test)]
mod typed_parameter_tests {
    use super::{
        datetime_to_tds_datetime, datetime_to_tds_datetime2, datetime_to_tds_smalldatetime,
        rescale_decimal_digits, scaled_tds_time,
    };
    use chrono::{NaiveDate, NaiveTime};
    use pyo3::Python;

    #[test]
    fn decimal_rescaling_rounds_half_away_from_zero() {
        let positive = rescale_decimal_digits(&[1, 2, 3, 4, 5], -3, false, 6, 2).unwrap();
        let negative = rescale_decimal_digits(&[1, 2, 3, 4, 5], -3, true, 6, 2).unwrap();

        assert_eq!(positive, 1_235);
        assert_eq!(negative, -1_235);
    }

    #[test]
    fn decimal_rescaling_rejects_post_rounding_precision_overflow() {
        assert!(rescale_decimal_digits(&[9, 9, 9, 9, 9, 9, 5], -3, false, 6, 2).is_none());
    }

    #[test]
    fn explicitly_typed_decimal_rescaling_handles_input_beyond_numeric_38() {
        let forty_fractional_digits = [
            1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
            0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0,
        ];

        assert_eq!(
            rescale_decimal_digits(&forty_fractional_digits, -40, false, 6, 2),
            Some(12)
        );
        assert_eq!(rescale_decimal_digits(&[1], -100, false, 6, 2), Some(0));
        assert_eq!(rescale_decimal_digits(&[5], -3, true, 6, 2), Some(-1));
        assert_eq!(
            rescale_decimal_digits(&[9, 9, 9, 9, 9, 9, 5], -3, false, 6, 2),
            None
        );
    }

    #[test]
    fn temporal_rescaling_rounds_and_rolls_over_with_integer_arithmetic() {
        let ordinary = NaiveTime::from_hms_micro_opt(12, 34, 56, 123_500).unwrap();
        let rollover = NaiveTime::from_hms_micro_opt(23, 59, 59, 999_500).unwrap();

        let (ordinary, ordinary_carry) = scaled_tds_time(ordinary, 3);
        let (rollover, rollover_carry) = scaled_tds_time(rollover, 3);

        assert_eq!(ordinary.increments(), 45_296_124);
        assert_eq!(ordinary.scale(), 3);
        assert!(!ordinary_carry);
        assert_eq!(rollover.increments(), 0);
        assert!(rollover_carry);
    }

    #[test]
    fn temporal_rounding_rejects_year_10000_before_wire_encoding() {
        Python::initialize();
        let maximum = NaiveDate::from_ymd_opt(9999, 12, 31)
            .unwrap()
            .and_hms_micro_opt(23, 59, 59, 999_500)
            .unwrap();

        assert!(datetime_to_tds_datetime2(maximum, 3, 0, "DATETIME2(3)").is_err());
        assert!(datetime_to_tds_datetime(maximum, 0, "DATETIME").is_err());
    }

    #[test]
    fn smalldatetime_rounding_matches_sql_server_boundary() {
        let date = NaiveDate::from_ymd_opt(2024, 2, 29).unwrap();
        let rounds_down = date.and_hms_milli_opt(12, 34, 29, 998).unwrap();
        let rounds_up = date.and_hms_milli_opt(12, 34, 29, 999).unwrap();

        let rounds_down = datetime_to_tds_smalldatetime(rounds_down, 0, "SMALLDATETIME").unwrap();
        let rounds_up = datetime_to_tds_smalldatetime(rounds_up, 0, "SMALLDATETIME").unwrap();

        assert_eq!(rounds_down.seconds_fragments(), 12 * 60 + 34);
        assert_eq!(rounds_up.seconds_fragments(), 12 * 60 + 35);
    }
}
