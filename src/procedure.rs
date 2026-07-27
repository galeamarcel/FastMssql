use crate::parameter_conversion::{
    python_to_fast_parameter_at, python_to_typed_fast_parameter_value,
};
use crate::py_parameters::{Parameter, ParameterDirection, Parameters};
use crate::types::create_protocol_error;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;
use std::collections::HashSet;
use tiberius::{
    ColumnData, ColumnType, ResponseReturnValue, RpcParameter, SqlParameterKind, SqlParameterType,
};

const MAX_RPC_PARAMETERS: usize = 2_100;

#[derive(Clone)]
pub(crate) enum OutputKey {
    Name(String),
    Position(usize),
}

pub(crate) struct OutputSlot {
    wire_ordinal: Option<u16>,
    canonical_name: Option<String>,
    public_key: OutputKey,
    parameter_type: SqlParameterType,
}

pub(crate) struct ProcedureCall {
    procedure: String,
    arguments: Vec<RpcParameter>,
    output_slots: Vec<OutputSlot>,
    return_slot: Option<OutputSlot>,
}

impl ProcedureCall {
    pub(crate) fn into_parts(
        self,
    ) -> (
        String,
        Vec<RpcParameter>,
        Vec<OutputSlot>,
        Option<OutputSlot>,
    ) {
        (
            self.procedure,
            self.arguments,
            self.output_slots,
            self.return_slot,
        )
    }
}

pub(crate) struct RawOutputValue {
    pub(crate) key: OutputKey,
    pub(crate) column_type: ColumnType,
    pub(crate) value: ColumnData<'static>,
}

pub(crate) struct RawProcedureSummary {
    pub(crate) output_values: Vec<RawOutputValue>,
    pub(crate) return_key: Option<OutputKey>,
}

pub(crate) struct ProcedureResponseState {
    output_slots: Vec<OutputSlot>,
    received: Vec<Option<(ColumnType, ColumnData<'static>)>>,
    return_slot: Option<OutputSlot>,
}

impl ProcedureResponseState {
    pub(crate) fn new(output_slots: Vec<OutputSlot>, return_slot: Option<OutputSlot>) -> Self {
        let received = std::iter::repeat_with(|| None)
            .take(output_slots.len())
            .collect();
        Self {
            output_slots,
            received,
            return_slot,
        }
    }

    pub(crate) fn record_return_value(&mut self, value: ResponseReturnValue) -> PyResult<()> {
        let slot_index = self
            .output_slots
            .iter()
            .position(|slot| {
                slot.wire_ordinal == Some(value.ordinal())
                    && slot.canonical_name.as_ref().is_none_or(|expected| {
                        normalize_wire_parameter_name(value.name())
                            .is_some_and(|actual| actual.eq_ignore_ascii_case(expected))
                    })
            })
            .ok_or_else(|| {
                create_protocol_error("stored procedure returned an unknown output parameter")
            })?;

        let slot = &self.output_slots[slot_index];
        if !output_type_matches(&slot.parameter_type, value.column_type()) {
            return Err(create_protocol_error(
                "stored procedure output parameter type did not match its declaration",
            ));
        }
        if self.received[slot_index].is_some() {
            return Err(create_protocol_error(
                "stored procedure returned a duplicate output parameter",
            ));
        }

        self.received[slot_index] = Some((value.column_type(), value.value().clone()));
        Ok(())
    }

    pub(crate) fn finish(self) -> PyResult<RawProcedureSummary> {
        if self.received.iter().any(Option::is_none) {
            return Err(create_protocol_error(
                "stored procedure response omitted an output parameter",
            ));
        }

        let output_values = self
            .output_slots
            .into_iter()
            .zip(self.received)
            .map(|(slot, received)| {
                let (column_type, value) = received.ok_or_else(|| {
                    create_protocol_error("stored procedure response omitted an output parameter")
                })?;
                Ok(RawOutputValue {
                    key: slot.public_key,
                    column_type,
                    value,
                })
            })
            .collect::<PyResult<Vec<_>>>()?;

        Ok(RawProcedureSummary {
            output_values,
            return_key: self.return_slot.map(|slot| slot.public_key),
        })
    }
}

pub(crate) fn validate_procedure_name(value: &str) -> PyResult<String> {
    let mut component_count = 0usize;
    for component in value.split('.') {
        component_count += 1;
        if component_count > 3 || !valid_identifier(component, 128) {
            return Err(invalid_procedure_name());
        }
    }
    if component_count == 0 {
        return Err(invalid_procedure_name());
    }
    Ok(value.to_owned())
}

pub(crate) fn validate_parameter_name(value: &str) -> PyResult<(String, String)> {
    let canonical = value.strip_prefix('@').unwrap_or(value);
    if !valid_identifier(canonical, 127) {
        return Err(invalid_parameter_name());
    }
    Ok((canonical.to_owned(), format!("@{canonical}")))
}

pub(crate) fn build_procedure_call(
    procedure: &str,
    params: Option<&Bound<PyAny>>,
    py: Python<'_>,
) -> PyResult<ProcedureCall> {
    let procedure = validate_procedure_name(procedure)?;
    let mut builder = ProcedureCallBuilder::new(procedure);

    let Some(params) = params else {
        return Ok(builder.finish());
    };

    if let Ok(parameters) = params.extract::<Py<Parameters>>() {
        let parameters = parameters.borrow(py);
        let named = parameters.named.bind(py);
        if !parameters.positional.is_empty() && !named.is_empty() {
            return Err(PyValueError::new_err(
                "Stored procedure parameters cannot mix positional and named modes",
            ));
        }

        if !named.is_empty() {
            if named.len() > MAX_RPC_PARAMETERS + 1 {
                return Err(rpc_parameter_count_error());
            }
            let mut canonical_names = HashSet::with_capacity(named.len());
            for (index, (key, value)) in named.iter().enumerate() {
                let key = key
                    .extract::<String>()
                    .map_err(|_| invalid_parameter_name())?;
                let (canonical, wire_name) = validate_parameter_name(&key)?;
                if !canonical_names.insert(canonical.to_ascii_lowercase()) {
                    return Err(PyValueError::new_err(
                        "Stored procedure parameter names must be unique",
                    ));
                }
                let parameter = value.extract::<Py<Parameter>>().map_err(|_| {
                    PyValueError::new_err(
                        "Stored procedure named parameters must be Parameter descriptors",
                    )
                })?;
                builder.push_descriptor(
                    &parameter.borrow(py),
                    wire_name,
                    Some(canonical.clone()),
                    OutputKey::Name(canonical),
                    index,
                    py,
                )?;
            }
            return Ok(builder.finish());
        }

        if parameters.positional.len() > MAX_RPC_PARAMETERS + 1 {
            return Err(rpc_parameter_count_error());
        }
        for (index, parameter) in parameters.positional.iter().enumerate() {
            builder.push_descriptor(
                &parameter.borrow(py),
                String::new(),
                None,
                OutputKey::Position(index),
                index,
                py,
            )?;
        }
        return Ok(builder.finish());
    }

    let list = params
        .cast::<PyList>()
        .map_err(|_| PyValueError::new_err("Must be list or Parameters object"))?;
    if list.len() > MAX_RPC_PARAMETERS + 1 {
        return Err(rpc_parameter_count_error());
    }
    for (index, value) in list.iter().enumerate() {
        if let Ok(parameter) = value.extract::<Py<Parameter>>() {
            builder.push_descriptor(
                &parameter.borrow(py),
                String::new(),
                None,
                OutputKey::Position(index),
                index,
                py,
            )?;
        } else {
            builder.push_input_value(&value, index)?;
        }
    }

    Ok(builder.finish())
}

struct ProcedureCallBuilder {
    procedure: String,
    arguments: Vec<RpcParameter>,
    output_slots: Vec<OutputSlot>,
    return_slot: Option<OutputSlot>,
}

impl ProcedureCallBuilder {
    fn new(procedure: String) -> Self {
        Self {
            procedure,
            arguments: Vec::new(),
            output_slots: Vec::new(),
            return_slot: None,
        }
    }

    fn finish(self) -> ProcedureCall {
        ProcedureCall {
            procedure: self.procedure,
            arguments: self.arguments,
            output_slots: self.output_slots,
            return_slot: self.return_slot,
        }
    }

    fn push_input_value(&mut self, value: &Bound<PyAny>, parameter_index: usize) -> PyResult<()> {
        self.ensure_encoded_capacity()?;
        let parameter = python_to_fast_parameter_at(value, parameter_index)?;
        let (value, parameter_type) = parameter.into_rpc_parts();
        self.arguments.push(RpcParameter::new(
            String::new(),
            value,
            parameter_type,
            false,
            parameter_index,
        ));
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn push_descriptor(
        &mut self,
        parameter: &Parameter,
        wire_name: String,
        canonical_name: Option<String>,
        public_key: OutputKey,
        parameter_index: usize,
        py: Python<'_>,
    ) -> PyResult<()> {
        if parameter.expanded {
            return Err(PyValueError::new_err(
                "Expanded parameters are not supported by stored procedure RPC",
            ));
        }

        let value = parameter.value.bind(py);
        match parameter.direction {
            ParameterDirection::ReturnValue => {
                if !value.is_none()
                    || parameter
                        .sql_type
                        .as_ref()
                        .is_some_and(|sql_type| sql_type.kind() != SqlParameterKind::Int)
                {
                    return Err(PyValueError::new_err(
                        "RETURN_VALUE requires None and an omitted or INT SQL type",
                    ));
                }
                if self.return_slot.is_some() {
                    return Err(PyValueError::new_err(
                        "Only one RETURN_VALUE descriptor is allowed",
                    ));
                }
                self.return_slot = Some(OutputSlot {
                    wire_ordinal: None,
                    canonical_name,
                    public_key,
                    parameter_type: SqlParameterType::int(),
                });
                Ok(())
            }
            ParameterDirection::Output | ParameterDirection::InputOutput => {
                let sql_type = parameter.sql_type.as_ref().ok_or_else(|| {
                    PyValueError::new_err(
                        "OUTPUT and INPUT_OUTPUT parameters require an explicit SQL type",
                    )
                })?;
                if parameter.direction == ParameterDirection::Output && !value.is_none() {
                    return Err(PyValueError::new_err(
                        "OUTPUT parameters require a None input value",
                    ));
                }
                self.push_encoded_descriptor(
                    value,
                    sql_type,
                    wire_name,
                    canonical_name,
                    public_key,
                    parameter_index,
                    true,
                )
            }
            ParameterDirection::Input => {
                self.ensure_encoded_capacity()?;
                let parameter = match parameter.sql_type.as_ref() {
                    Some(sql_type) => {
                        python_to_typed_fast_parameter_value(value, sql_type, parameter_index)?
                    }
                    None => python_to_fast_parameter_at(value, parameter_index)?,
                };
                let (value, parameter_type) = parameter.into_rpc_parts();
                self.arguments.push(RpcParameter::new(
                    wire_name,
                    value,
                    parameter_type,
                    false,
                    parameter_index,
                ));
                Ok(())
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn push_encoded_descriptor(
        &mut self,
        value: &Bound<PyAny>,
        sql_type: &SqlParameterType,
        wire_name: String,
        canonical_name: Option<String>,
        public_key: OutputKey,
        parameter_index: usize,
        by_ref: bool,
    ) -> PyResult<()> {
        self.ensure_encoded_capacity()?;
        let wire_ordinal =
            u16::try_from(self.arguments.len()).map_err(|_| rpc_parameter_count_error())?;
        let parameter = python_to_typed_fast_parameter_value(value, sql_type, parameter_index)?;
        let (value, parameter_type) = parameter.into_rpc_parts();
        self.arguments.push(RpcParameter::new(
            wire_name,
            value,
            parameter_type,
            by_ref,
            parameter_index,
        ));
        self.output_slots.push(OutputSlot {
            wire_ordinal: Some(wire_ordinal),
            canonical_name,
            public_key,
            parameter_type: sql_type.clone(),
        });
        Ok(())
    }

    fn ensure_encoded_capacity(&self) -> PyResult<()> {
        if self.arguments.len() == MAX_RPC_PARAMETERS {
            Err(rpc_parameter_count_error())
        } else {
            Ok(())
        }
    }
}

fn valid_identifier(value: &str, max_length: usize) -> bool {
    if value.is_empty() || value.len() > max_length || !value.is_ascii() {
        return false;
    }
    let mut bytes = value.bytes();
    let Some(first) = bytes.next() else {
        return false;
    };
    (first.is_ascii_alphabetic() || matches!(first, b'_' | b'#'))
        && bytes
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'@' | b'$' | b'#'))
}

fn normalize_wire_parameter_name(value: &str) -> Option<&str> {
    let canonical = value.strip_prefix('@').unwrap_or(value);
    valid_identifier(canonical, 127).then_some(canonical)
}

fn invalid_procedure_name() -> PyErr {
    PyValueError::new_err("Invalid stored procedure name")
}

fn invalid_parameter_name() -> PyErr {
    PyValueError::new_err("Invalid stored procedure parameter name")
}

fn rpc_parameter_count_error() -> PyErr {
    PyValueError::new_err("Stored procedure RPC supports at most 2,100 encoded parameters")
}

fn output_type_matches(parameter_type: &SqlParameterType, column_type: ColumnType) -> bool {
    match parameter_type.kind() {
        SqlParameterKind::Bit => matches!(column_type, ColumnType::Bit | ColumnType::Bitn),
        SqlParameterKind::TinyInt => column_type == ColumnType::Int1,
        SqlParameterKind::SmallInt => column_type == ColumnType::Int2,
        SqlParameterKind::Int => matches!(column_type, ColumnType::Int4 | ColumnType::Intn),
        SqlParameterKind::BigInt => column_type == ColumnType::Int8,
        SqlParameterKind::Real => column_type == ColumnType::Float4,
        SqlParameterKind::Float => {
            matches!(
                column_type,
                ColumnType::Float4 | ColumnType::Float8 | ColumnType::Floatn
            )
        }
        SqlParameterKind::Decimal | SqlParameterKind::Numeric => {
            matches!(column_type, ColumnType::Decimaln | ColumnType::Numericn)
        }
        SqlParameterKind::Char => column_type == ColumnType::BigChar,
        SqlParameterKind::VarChar => column_type == ColumnType::BigVarChar,
        SqlParameterKind::NChar => column_type == ColumnType::NChar,
        SqlParameterKind::NVarChar => column_type == ColumnType::NVarchar,
        SqlParameterKind::Binary => column_type == ColumnType::BigBinary,
        SqlParameterKind::VarBinary => column_type == ColumnType::BigVarBin,
        SqlParameterKind::UniqueIdentifier => column_type == ColumnType::Guid,
        SqlParameterKind::Date => column_type == ColumnType::Daten,
        SqlParameterKind::Time => column_type == ColumnType::Timen,
        SqlParameterKind::DateTime => {
            matches!(column_type, ColumnType::Datetime | ColumnType::Datetimen)
        }
        SqlParameterKind::SmallDateTime => {
            matches!(column_type, ColumnType::Datetime4 | ColumnType::Datetimen)
        }
        SqlParameterKind::DateTime2 => column_type == ColumnType::Datetime2,
        SqlParameterKind::DateTimeOffset => column_type == ColumnType::DatetimeOffsetn,
        SqlParameterKind::Xml => column_type == ColumnType::Xml,
    }
}

#[cfg(test)]
mod tests {
    use super::{validate_parameter_name, validate_procedure_name};

    #[test]
    fn identifiers_use_the_closed_rpc_grammar_and_boundaries() {
        assert_eq!(
            validate_procedure_name(&format!("dbo.{}", "p".repeat(128))).unwrap(),
            format!("dbo.{}", "p".repeat(128))
        );
        assert_eq!(
            validate_parameter_name(&format!("@{}", "p".repeat(127))).unwrap(),
            ("p".repeat(127), format!("@{}", "p".repeat(127)))
        );

        for invalid in [
            "",
            "dbo..p",
            "a.b.c.d",
            "1proc",
            "$proc",
            "[dbo].[proc]",
            "dbo.proc;",
            "próc",
        ] {
            assert!(validate_procedure_name(invalid).is_err());
        }
        for invalid in ["", "@", "@@p", "1p", "$p", "[p]", "p;", "p p", "pä"] {
            assert!(validate_parameter_name(invalid).is_err());
        }
    }
}
