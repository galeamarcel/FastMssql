#[cfg(feature = "sql-browser-tokio")]
mod tokio;

#[cfg(feature = "sql-browser-async-std")]
mod async_std;

#[cfg(feature = "sql-browser-smol")]
mod smol;

#[cfg(all(
    test,
    any(
        feature = "sql-browser-async-std",
        feature = "sql-browser-tokio",
        feature = "sql-browser-smol"
    )
))]
mod tests;

use crate::client::Config;
use async_trait::async_trait;
#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
use std::borrow::Cow;

#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
const CLNT_UCAST_INST: u8 = 0x04;
#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
const SVR_RESP: u8 = 0x05;
#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
const SQL_BROWSER_HEADER_LEN: usize = 3;
#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
const SQL_BROWSER_MAX_INSTANCE_BYTES: usize = 32;
#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
const SQL_BROWSER_MAX_RESPONSE_PAYLOAD: usize = 1_024;

/// An extension trait to a `TcpStream` to find a port and connecting to a
/// named database instance.
///
/// Only needed on Windows platforms, where the server port is not known and the
/// address is in the form of `hostname\\INSTANCE`.
#[async_trait]
pub trait SqlBrowser {
    /// If the given builder defines a named instance, finds the correct port
    /// and returns a `TcpStream` to be used in the [`Client`]. If instance name
    /// is not defined, connects directly to the given host and port.
    ///
    /// [`Client`]: struct.Client.html
    async fn connect_named(builder: &Config) -> crate::Result<Self>
    where
        Self: Sized + Send + Sync;
}

#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
fn build_instance_request(instance_name: &str) -> crate::Result<Vec<u8>> {
    let instance_bytes = instance_name.as_bytes();

    if instance_bytes.is_empty() {
        return Err(crate::Error::Conversion(Cow::Borrowed(
            "SQL Browser instance name must not be empty",
        )));
    }

    if instance_bytes.contains(&0) {
        return Err(crate::Error::Conversion(Cow::Borrowed(
            "SQL Browser instance name must not contain NUL",
        )));
    }

    if instance_bytes.len() > SQL_BROWSER_MAX_INSTANCE_BYTES {
        return Err(crate::Error::Conversion(Cow::Borrowed(
            "SQL Browser instance name exceeds the 32-byte protocol limit",
        )));
    }

    let mut request = Vec::with_capacity(1 + instance_bytes.len() + 1);
    request.push(CLNT_UCAST_INST);
    request.extend_from_slice(instance_bytes);
    request.push(0);
    Ok(request)
}

#[cfg(any(
    feature = "sql-browser-async-std",
    feature = "sql-browser-tokio",
    feature = "sql-browser-smol"
))]
fn parse_instance_response(response: &[u8], _instance_name: &str) -> crate::Result<u16> {
    fn protocol_error(message: &'static str) -> crate::Error {
        crate::Error::Protocol(Cow::Borrowed(message))
    }

    if response.len() < SQL_BROWSER_HEADER_LEN {
        return Err(protocol_error("SQL Browser response header is truncated"));
    }

    if response[0] != SVR_RESP {
        return Err(protocol_error("SQL Browser response type is invalid"));
    }

    let payload_len = usize::from(u16::from_le_bytes([response[1], response[2]]));
    if payload_len > SQL_BROWSER_MAX_RESPONSE_PAYLOAD {
        return Err(protocol_error(
            "SQL Browser response exceeds the 1024-byte protocol limit",
        ));
    }

    let expected_len = SQL_BROWSER_HEADER_LEN + payload_len;
    if response.len() != expected_len {
        return Err(protocol_error(
            "SQL Browser response length does not match its header",
        ));
    }

    let payload = &response[SQL_BROWSER_HEADER_LEN..];
    let mut fields = payload.split(|byte| *byte == b';');
    let mut tcp_value = None;

    while let Some(key) = fields.next() {
        let Some(value) = fields.next() else {
            if key.is_empty() {
                break;
            }

            return Err(protocol_error(
                "SQL Browser response field has no paired value",
            ));
        };

        if key.is_empty() {
            if value.is_empty() {
                continue;
            }

            return Err(protocol_error("SQL Browser response field name is empty"));
        }

        if !key.eq_ignore_ascii_case(b"tcp") {
            continue;
        }

        if tcp_value.is_some() {
            return Err(protocol_error(
                "SQL Browser response contains duplicate TCP fields",
            ));
        }

        tcp_value = Some(value);
    }

    let port_bytes =
        tcp_value.ok_or_else(|| protocol_error("SQL Browser response has no TCP field"))?;
    if port_bytes.is_empty() || !port_bytes.iter().all(u8::is_ascii_digit) {
        return Err(protocol_error("SQL Browser TCP port is not decimal"));
    }

    let mut port = 0_u32;
    for digit in port_bytes {
        port = port
            .checked_mul(10)
            .and_then(|value| value.checked_add(u32::from(*digit - b'0')))
            .ok_or_else(|| protocol_error("SQL Browser TCP port is out of range"))?;
    }

    if !(1..=u32::from(u16::MAX)).contains(&port) {
        return Err(protocol_error("SQL Browser TCP port is out of range"));
    }

    u16::try_from(port).map_err(|_| protocol_error("SQL Browser TCP port is out of range"))
}

#[cfg(any(feature = "sql-browser-async-std", feature = "sql-browser-smol"))]
fn get_port_from_sql_browser_reply(
    buf: Vec<u8>,
    len: usize,
    instance_name: &str,
) -> crate::Result<u16> {
    let response = buf.get(..len).ok_or_else(|| {
        crate::Error::Protocol(Cow::Borrowed(
            "SQL Browser receive length exceeds its buffer",
        ))
    })?;
    parse_instance_response(response, instance_name)
}
