use std::sync::OnceLock;

use pyo3::exceptions::PyValueError;
use pyo3::types::{
    PyByteArray, PyBytes, PyDict, PyFrozenSet, PyList, PyMemoryView, PySet, PyString, PyTuple,
};
use pyo3::{IntoPyObjectExt, Py, PyAny, prelude::*};
use tiberius::{ColumnData, ColumnType, FromSql};

use crate::types::ConversionError;

/// Cached handle to `decimal.Decimal` — imported once, reused for every row.
/// Stored as `Option` to allow initialization via `get_or_init()` with fallible closure.
static DECIMAL_CLASS: OnceLock<Option<Py<PyAny>>> = OnceLock::new();
static UUID_CLASS: OnceLock<Option<Py<PyAny>>> = OnceLock::new();

/// Return a `Bound` reference to `decimal.Decimal`, initializing the cache on
/// the very first call and simply re-binding on every subsequent call.
#[inline]
pub(crate) fn get_decimal_class(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    let cls = DECIMAL_CLASS
        .get_or_init(|| {
            py.import("decimal")
                .and_then(|m| m.getattr("Decimal"))
                .map(|d| d.unbind())
                .ok()
        })
        .as_ref()
        .ok_or_else(|| PyValueError::new_err("Failed to initialize decimal.Decimal"))?;
    Ok(cls.bind(py))
}

/// Return the canonical `uuid.UUID` class, cached after its first import.
#[inline]
pub(crate) fn get_uuid_class(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    let cls = UUID_CLASS
        .get_or_init(|| {
            py.import("uuid")
                .and_then(|module| module.getattr("UUID"))
                .map(|uuid| uuid.unbind())
                .ok()
        })
        .as_ref()
        .ok_or_else(|| PyValueError::new_err("Failed to initialize uuid.UUID"))?;
    Ok(cls.bind(py))
}

#[inline]
fn money_is_exactly_representable(value: f64) -> bool {
    value.abs() <= 900_719_925_474.099_1_f64
}

/// Convert a tiberius `Numeric` to the canonical decimal string accepted by
/// Python's `decimal.Decimal()` constructor.
///
/// `Numeric::to_string()` delegates to its `Debug` impl which formats as
/// `"{int_part}.{dec_part:0scale$}"`. For negative sub-integer values (e.g.
/// `-0.00001`, stored as `value=-1, scale=5`) `dec_part()` returns `-1`, so
/// the string becomes `"0.-0001"` — not parseable by Python, raising
/// `decimal.InvalidOperation`.
///
/// This function builds the string directly from the raw `value` (i128) and
/// `scale` (u8), correctly handling all sign/magnitude combinations.
#[inline]
fn numeric_to_decimal_string(numeric: tiberius::numeric::Numeric) -> String {
    let value = numeric.value();
    let scale = numeric.scale() as usize;

    if scale == 0 {
        return format!("{}", value);
    }

    let sign = if value < 0 { "-" } else { "" };
    let abs_value = value.unsigned_abs();
    // Zero-pad to at least `scale + 1` digits so there is always an integer part.
    let digits = format!("{:0>width$}", abs_value, width = scale + 1);
    let split_pos = digits.len() - scale;
    let (int_part, frac_part) = digits.split_at(split_pos);

    format!("{}{}.{}", sign, int_part, frac_part)
}

#[inline(always)]
pub(crate) fn column_data_to_python(
    value: &ColumnData<'static>,
    col_type: ColumnType,
    py: Python<'_>,
) -> PyResult<Py<PyAny>> {
    match col_type {
        ColumnType::Int1
        | ColumnType::Int2
        | ColumnType::Int4
        | ColumnType::Int8
        | ColumnType::Intn => match value {
            ColumnData::U8(Some(value)) => (*value).into_py_any(py),
            ColumnData::I16(Some(value)) => (*value).into_py_any(py),
            ColumnData::I32(Some(value)) => (*value).into_py_any(py),
            ColumnData::I64(Some(value)) => (*value).into_py_any(py),
            ColumnData::U8(None)
            | ColumnData::I16(None)
            | ColumnData::I32(None)
            | ColumnData::I64(None) => Ok(py.None()),
            _ => Err(column_conversion_error("integer")),
        },
        ColumnType::Float4 | ColumnType::Float8 | ColumnType::Floatn => match value {
            ColumnData::F32(Some(value)) => (*value as f64).into_py_any(py),
            ColumnData::F64(Some(value)) => (*value).into_py_any(py),
            ColumnData::F32(None) | ColumnData::F64(None) => Ok(py.None()),
            _ => Err(column_conversion_error("floating-point")),
        },
        ColumnType::NVarchar
        | ColumnType::NChar
        | ColumnType::BigVarChar
        | ColumnType::BigChar
        | ColumnType::Text
        | ColumnType::NText => match value {
            ColumnData::String(Some(value)) => value.as_ref().into_py_any(py),
            ColumnData::String(None) => Ok(py.None()),
            _ => Err(column_conversion_error("string")),
        },
        ColumnType::Image | ColumnType::BigVarBin | ColumnType::BigBinary => match value {
            ColumnData::Binary(Some(value)) => value.as_ref().into_py_any(py),
            ColumnData::Binary(None) => Ok(py.None()),
            _ => Err(column_conversion_error("binary")),
        },
        ColumnType::Bit | ColumnType::Bitn => match value {
            ColumnData::Bit(Some(value)) => (*value).into_py_any(py),
            ColumnData::Bit(None) => Ok(py.None()),
            _ => Err(column_conversion_error("bit")),
        },
        ColumnType::Money | ColumnType::Money4 => match value {
            ColumnData::F64(Some(value)) => {
                if col_type == ColumnType::Money && !money_is_exactly_representable(*value) {
                    return Err(ConversionError::new_err(
                        "MONEY value exceeds the exact conversion range; \
                         CAST the expression AS DECIMAL(19,4) in SQL",
                    ));
                }
                get_decimal_class(py)?
                    .call1((format!("{value:.4}"),))
                    .map(Bound::unbind)
            }
            ColumnData::F64(None) => Ok(py.None()),
            _ => Err(column_conversion_error("money")),
        },
        ColumnType::Decimaln | ColumnType::Numericn => match value {
            ColumnData::Numeric(Some(value)) => get_decimal_class(py)?
                .call1((numeric_to_decimal_string(*value),))
                .map(Bound::unbind),
            ColumnData::Numeric(None) => Ok(py.None()),
            _ => Err(column_conversion_error("decimal")),
        },
        ColumnType::Datetime | ColumnType::Datetimen | ColumnType::Datetime2 => {
            from_column_data::<chrono::NaiveDateTime>(value, "datetime")?
                .map_or_else(|| Ok(py.None()), |value| value.into_py_any(py))
        }
        ColumnType::Datetime4 => from_column_data::<chrono::NaiveDateTime>(value, "smalldatetime")?
            .map_or_else(|| Ok(py.None()), |value| value.into_py_any(py)),
        ColumnType::Daten => from_column_data::<chrono::NaiveDate>(value, "date")?
            .map_or_else(|| Ok(py.None()), |value| value.into_py_any(py)),
        ColumnType::Timen => from_column_data::<chrono::NaiveTime>(value, "time")?
            .map_or_else(|| Ok(py.None()), |value| value.into_py_any(py)),
        ColumnType::DatetimeOffsetn => {
            from_column_data::<chrono::DateTime<chrono::FixedOffset>>(value, "datetimeoffset")?
                .map_or_else(|| Ok(py.None()), |value| value.into_py_any(py))
        }
        ColumnType::Guid => match value {
            ColumnData::Guid(Some(value)) => {
                let kwargs = PyDict::new(py);
                kwargs.set_item("bytes", PyBytes::new(py, value.as_bytes()))?;
                Ok(get_uuid_class(py)?.call((), Some(&kwargs))?.unbind())
            }
            ColumnData::Guid(None) => Ok(py.None()),
            _ => Err(column_conversion_error("uniqueidentifier")),
        },
        ColumnType::Xml => match value {
            ColumnData::Xml(Some(value)) => value.to_string().into_py_any(py),
            ColumnData::Xml(None) => Ok(py.None()),
            _ => Err(column_conversion_error("xml")),
        },
        ColumnType::SSVariant | ColumnType::Udt => match value {
            ColumnData::String(Some(value)) => value.as_ref().into_py_any(py),
            ColumnData::String(None) => Ok(py.None()),
            _ => Err(column_conversion_error("unsupported metadata")),
        },
        ColumnType::Null => Ok(py.None()),
    }
}

fn from_column_data<T>(value: &ColumnData<'static>, type_name: &'static str) -> PyResult<Option<T>>
where
    for<'a> T: FromSql<'a>,
{
    T::from_sql(value).map_err(|_| column_conversion_error(type_name))
}

fn column_conversion_error(type_name: &'static str) -> PyErr {
    PyValueError::new_err(format!("Failed to convert SQL Server {type_name} value"))
}

pub fn is_expandable_iterable(obj: &Bound<PyAny>) -> PyResult<bool> {
    // Fast path: scalar types
    if obj.is_instance_of::<PyString>()
        || obj.is_instance_of::<PyBytes>()
        || obj.is_instance_of::<PyByteArray>()
        || obj.is_instance_of::<PyMemoryView>()
    {
        return Ok(false);
    }

    // Structural pointer checks (substantially faster than full object casting structures)
    if obj.is_instance_of::<PyList>()
        || obj.is_instance_of::<PyTuple>()
        || obj.is_instance_of::<PySet>()
        || obj.is_instance_of::<PyFrozenSet>()
    {
        return Ok(true);
    }

    // Dynamic fallback with string lookup tracking optimization
    obj.hasattr(pyo3::intern!(obj.py(), "__iter__"))
}

#[cfg(test)]
mod tests {
    use super::money_is_exactly_representable;

    #[test]
    fn money_precision_guard_matches_f64_integer_boundary() {
        assert!(money_is_exactly_representable(900_719_925_474.099_1));
        assert!(money_is_exactly_representable(-900_719_925_474.099_1));
        assert!(!money_is_exactly_representable(922_337_203_685_477.6));
        assert!(!money_is_exactly_representable(-922_337_203_685_477.6));
    }
}
