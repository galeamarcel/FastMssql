use connection_string::AdoNetString;
use pyo3::PyResult;
use pyo3::exceptions::PyValueError;
use tiberius::{Config, EncryptionLevel};

use crate::ssl_config::PySslConfig;

const ENCRYPT: &str = "encrypt";
const TRUST_SERVER_CERTIFICATE: &str = "trustservercertificate";
const TRUST_SERVER_CERTIFICATE_CA: &str = "trustservercertificateca";

fn invalid_connection_string(error: impl std::fmt::Display) -> pyo3::PyErr {
    PyValueError::new_err(format!("Invalid connection string: {error}"))
}

fn trust_server_certificate_enabled(options: &AdoNetString) -> bool {
    options.get(TRUST_SERVER_CERTIFICATE).is_some_and(|value| {
        let value = value.trim();
        value.eq_ignore_ascii_case("true") || value.eq_ignore_ascii_case("yes")
    })
}

fn has_tls_options(options: &AdoNetString) -> bool {
    [
        ENCRYPT,
        TRUST_SERVER_CERTIFICATE,
        TRUST_SERVER_CERTIFICATE_CA,
    ]
    .iter()
    .any(|key| options.contains_key(*key))
}

/// Build one Tiberius configuration from an ADO.NET connection string.
///
/// Tiberius 0.12 defaults an ADO.NET string without `Encrypt` to login-only
/// encryption, even though `Config::new()` defaults to required encryption.
/// FastMssql keeps the safer `Config::new()` policy for both construction
/// styles and requires an explicit connection-string opt-out for weaker modes.
///
/// TLS settings must come from either the connection string or `ssl_config`.
/// Validating that boundary before calling Tiberius also prevents its
/// trust-all/custom-CA conflict from panicking across the Python FFI boundary.
pub(crate) fn config_from_ado_string(
    connection_string: &str,
    ssl_config: Option<&PySslConfig>,
) -> PyResult<Config> {
    let options = connection_string
        .parse::<AdoNetString>()
        .map_err(invalid_connection_string)?;

    if trust_server_certificate_enabled(&options)
        && options.contains_key(TRUST_SERVER_CERTIFICATE_CA)
    {
        return Err(PyValueError::new_err(
            "TrustServerCertificate and TrustServerCertificateCA are mutually exclusive",
        ));
    }

    if ssl_config.is_some() && has_tls_options(&options) {
        return Err(PyValueError::new_err(
            "TLS settings cannot be provided in both connection_string and ssl_config",
        ));
    }

    let mut config =
        Config::from_ado_string(connection_string).map_err(invalid_connection_string)?;

    if let Some(ssl_config) = ssl_config {
        ssl_config.apply_to_config(&mut config);
    } else if !options.contains_key(ENCRYPT) {
        config.encryption(EncryptionLevel::Required);
    }

    Ok(config)
}
