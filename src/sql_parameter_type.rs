use tiberius::{SqlParameterType, TypeLength};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Modifier {
    Number(u16),
    Max,
}

#[derive(Debug, PartialEq, Eq)]
struct ParsedDeclaration {
    name: String,
    modifiers: Vec<Modifier>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ParameterTypeMetadata {
    pub(crate) precision: Option<u8>,
    pub(crate) scale: Option<u8>,
    pub(crate) length: Option<TypeLength>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ParameterTypeError;

pub(crate) fn parse_sql_parameter_type(
    declaration: &str,
    metadata: ParameterTypeMetadata,
) -> Result<SqlParameterType, ParameterTypeError> {
    let parsed = parse_declaration(declaration)?;
    let no_metadata =
        metadata.precision.is_none() && metadata.scale.is_none() && metadata.length.is_none();

    match parsed.name.as_str() {
        "BIT" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::bit()),
        "TINYINT" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::tiny_int()),
        "SMALLINT" if parsed.modifiers.is_empty() && no_metadata => {
            Ok(SqlParameterType::small_int())
        }
        "INT" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::int()),
        "BIGINT" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::big_int()),
        "REAL" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::real()),
        "FLOAT" if metadata.scale.is_none() && metadata.length.is_none() => {
            let inline = one_number_or_absent(&parsed.modifiers)?;
            let precision =
                reconcile(inline.map(u8_from_u16).transpose()?, metadata.precision)?.unwrap_or(53);
            SqlParameterType::float(precision).map_err(|_| ParameterTypeError)
        }
        "DECIMAL" | "NUMERIC" if metadata.length.is_none() => {
            let inline = two_numbers_or_absent(&parsed.modifiers)?;
            let inline_precision = inline
                .map(|(precision, _)| u8_from_u16(precision))
                .transpose()?;
            let inline_scale = inline.map(|(_, scale)| u8_from_u16(scale)).transpose()?;
            let precision =
                reconcile(inline_precision, metadata.precision)?.ok_or(ParameterTypeError)?;
            let scale = reconcile(inline_scale, metadata.scale)?.ok_or(ParameterTypeError)?;

            if parsed.name == "DECIMAL" {
                SqlParameterType::decimal(precision, scale).map_err(|_| ParameterTypeError)
            } else {
                SqlParameterType::numeric(precision, scale).map_err(|_| ParameterTypeError)
            }
        }
        "CHAR" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(&parsed.modifiers, metadata.length, None, false)?;
            SqlParameterType::char(limited_length(length)?).map_err(|_| ParameterTypeError)
        }
        "VARCHAR" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(
                &parsed.modifiers,
                metadata.length,
                Some(TypeLength::Limited(8000)),
                true,
            )?;
            SqlParameterType::varchar(length).map_err(|_| ParameterTypeError)
        }
        "NCHAR" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(&parsed.modifiers, metadata.length, None, false)?;
            SqlParameterType::nchar(limited_length(length)?).map_err(|_| ParameterTypeError)
        }
        "NVARCHAR" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(
                &parsed.modifiers,
                metadata.length,
                Some(TypeLength::Limited(4000)),
                true,
            )?;
            SqlParameterType::nvarchar(length).map_err(|_| ParameterTypeError)
        }
        "BINARY" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(&parsed.modifiers, metadata.length, None, false)?;
            SqlParameterType::binary(limited_length(length)?).map_err(|_| ParameterTypeError)
        }
        "VARBINARY" if metadata.precision.is_none() && metadata.scale.is_none() => {
            let length = resolve_length(
                &parsed.modifiers,
                metadata.length,
                Some(TypeLength::Limited(8000)),
                true,
            )?;
            SqlParameterType::varbinary(length).map_err(|_| ParameterTypeError)
        }
        "UNIQUEIDENTIFIER" if parsed.modifiers.is_empty() && no_metadata => {
            Ok(SqlParameterType::unique_identifier())
        }
        "DATE" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::date()),
        "TIME" if metadata.precision.is_none() && metadata.length.is_none() => {
            let scale = resolve_scale(&parsed.modifiers, metadata.scale)?;
            SqlParameterType::time(scale).map_err(|_| ParameterTypeError)
        }
        "DATETIME" if parsed.modifiers.is_empty() && no_metadata => {
            Ok(SqlParameterType::date_time())
        }
        "SMALLDATETIME" if parsed.modifiers.is_empty() && no_metadata => {
            Ok(SqlParameterType::small_date_time())
        }
        "DATETIME2" if metadata.precision.is_none() && metadata.length.is_none() => {
            let scale = resolve_scale(&parsed.modifiers, metadata.scale)?;
            SqlParameterType::date_time2(scale).map_err(|_| ParameterTypeError)
        }
        "DATETIMEOFFSET" if metadata.precision.is_none() && metadata.length.is_none() => {
            let scale = resolve_scale(&parsed.modifiers, metadata.scale)?;
            SqlParameterType::date_time_offset(scale).map_err(|_| ParameterTypeError)
        }
        "XML" if parsed.modifiers.is_empty() && no_metadata => Ok(SqlParameterType::xml()),
        _ => Err(ParameterTypeError),
    }
}

fn parse_declaration(input: &str) -> Result<ParsedDeclaration, ParameterTypeError> {
    let bytes = input.as_bytes();
    let mut cursor = 0;
    skip_ascii_whitespace(bytes, &mut cursor);

    let start = cursor;
    while bytes
        .get(cursor)
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
    {
        cursor += 1;
    }
    if start == cursor {
        return Err(ParameterTypeError);
    }
    let name = input[start..cursor].to_ascii_uppercase();
    skip_ascii_whitespace(bytes, &mut cursor);

    let mut modifiers = Vec::new();
    if bytes.get(cursor) == Some(&b'(') {
        cursor += 1;
        skip_ascii_whitespace(bytes, &mut cursor);
        modifiers.push(parse_modifier(input, bytes, &mut cursor)?);
        skip_ascii_whitespace(bytes, &mut cursor);

        if bytes.get(cursor) == Some(&b',') {
            cursor += 1;
            skip_ascii_whitespace(bytes, &mut cursor);
            modifiers.push(parse_modifier(input, bytes, &mut cursor)?);
            skip_ascii_whitespace(bytes, &mut cursor);
        }

        if bytes.get(cursor) != Some(&b')') {
            return Err(ParameterTypeError);
        }
        cursor += 1;
        skip_ascii_whitespace(bytes, &mut cursor);
    }

    if cursor != bytes.len() {
        return Err(ParameterTypeError);
    }

    Ok(ParsedDeclaration { name, modifiers })
}

fn parse_modifier(
    input: &str,
    bytes: &[u8],
    cursor: &mut usize,
) -> Result<Modifier, ParameterTypeError> {
    let start = *cursor;
    while bytes.get(*cursor).is_some_and(|byte| byte.is_ascii_digit()) {
        *cursor += 1;
    }
    if start != *cursor {
        return input[start..*cursor]
            .parse::<u16>()
            .map(Modifier::Number)
            .map_err(|_| ParameterTypeError);
    }

    while bytes
        .get(*cursor)
        .is_some_and(|byte| byte.is_ascii_alphabetic())
    {
        *cursor += 1;
    }
    if start != *cursor && input[start..*cursor].eq_ignore_ascii_case("MAX") {
        Ok(Modifier::Max)
    } else {
        Err(ParameterTypeError)
    }
}

fn skip_ascii_whitespace(bytes: &[u8], cursor: &mut usize) {
    while bytes
        .get(*cursor)
        .is_some_and(|byte| byte.is_ascii_whitespace())
    {
        *cursor += 1;
    }
}

fn one_number_or_absent(modifiers: &[Modifier]) -> Result<Option<u16>, ParameterTypeError> {
    match modifiers {
        [] => Ok(None),
        [Modifier::Number(value)] => Ok(Some(*value)),
        _ => Err(ParameterTypeError),
    }
}

fn two_numbers_or_absent(modifiers: &[Modifier]) -> Result<Option<(u16, u16)>, ParameterTypeError> {
    match modifiers {
        [] => Ok(None),
        [Modifier::Number(precision), Modifier::Number(scale)] => Ok(Some((*precision, *scale))),
        _ => Err(ParameterTypeError),
    }
}

fn resolve_scale(modifiers: &[Modifier], keyword: Option<u8>) -> Result<u8, ParameterTypeError> {
    let inline = one_number_or_absent(modifiers)?
        .map(u8_from_u16)
        .transpose()?;
    Ok(reconcile(inline, keyword)?.unwrap_or(7))
}

fn resolve_length(
    modifiers: &[Modifier],
    keyword: Option<TypeLength>,
    default: Option<TypeLength>,
    allow_max: bool,
) -> Result<TypeLength, ParameterTypeError> {
    let inline = match modifiers {
        [] => None,
        [Modifier::Number(value)] => Some(TypeLength::Limited(*value)),
        [Modifier::Max] if allow_max => Some(TypeLength::Max),
        _ => return Err(ParameterTypeError),
    };

    reconcile(inline, keyword)?
        .or(default)
        .ok_or(ParameterTypeError)
}

fn reconcile<T: Copy + Eq>(
    inline: Option<T>,
    keyword: Option<T>,
) -> Result<Option<T>, ParameterTypeError> {
    match (inline, keyword) {
        (Some(inline), Some(keyword)) if inline != keyword => Err(ParameterTypeError),
        (Some(value), _) | (_, Some(value)) => Ok(Some(value)),
        (None, None) => Ok(None),
    }
}

fn u8_from_u16(value: u16) -> Result<u8, ParameterTypeError> {
    u8::try_from(value).map_err(|_| ParameterTypeError)
}

fn limited_length(length: TypeLength) -> Result<u16, ParameterTypeError> {
    match length {
        TypeLength::Limited(length) => Ok(length),
        TypeLength::Max => Err(ParameterTypeError),
    }
}

#[cfg(test)]
mod tests {
    use super::{ParameterTypeMetadata, parse_sql_parameter_type};
    use tiberius::TypeLength;

    fn no_metadata() -> ParameterTypeMetadata {
        ParameterTypeMetadata {
            precision: None,
            scale: None,
            length: None,
        }
    }

    #[test]
    fn canonicalizes_the_closed_declaration_surface() {
        for (declaration, canonical) in [
            (" int ", "INT"),
            ("FLOAT", "FLOAT(53)"),
            ("FLOAT ( 24 )", "FLOAT(24)"),
            ("DECIMAL ( 38 , 12 )", "DECIMAL(38,12)"),
            ("varchar(max)", "VARCHAR(MAX)"),
            ("nvarchar", "NVARCHAR(4000)"),
            ("TIME", "TIME(7)"),
            ("datetimeoffset(3)", "DATETIMEOFFSET(3)"),
        ] {
            assert_eq!(
                parse_sql_parameter_type(declaration, no_metadata())
                    .unwrap()
                    .declaration(),
                canonical
            );
        }
    }

    #[test]
    fn keyword_metadata_completes_or_confirms_a_declaration() {
        let decimal = parse_sql_parameter_type(
            "decimal",
            ParameterTypeMetadata {
                precision: Some(19),
                scale: Some(4),
                length: None,
            },
        )
        .unwrap();
        assert_eq!(decimal.declaration(), "DECIMAL(19,4)");

        let varchar = parse_sql_parameter_type(
            "varchar",
            ParameterTypeMetadata {
                precision: None,
                scale: None,
                length: Some(TypeLength::Limited(32)),
            },
        )
        .unwrap();
        assert_eq!(varchar.declaration(), "VARCHAR(32)");
    }

    #[test]
    fn rejects_unsafe_unsupported_or_conflicting_declarations() {
        for declaration in [
            "",
            "INT;",
            "INT -- comment",
            "VARCHAR(10); SELECT 1",
            "VARCHAR((10))",
            "MONEY",
            "TABLE",
        ] {
            assert!(parse_sql_parameter_type(declaration, no_metadata()).is_err());
        }

        assert!(
            parse_sql_parameter_type(
                "VARCHAR(10)",
                ParameterTypeMetadata {
                    precision: None,
                    scale: None,
                    length: Some(TypeLength::Limited(11)),
                },
            )
            .is_err()
        );
    }
}
