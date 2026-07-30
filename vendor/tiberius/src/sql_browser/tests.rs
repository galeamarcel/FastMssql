use super::{build_instance_request, parse_instance_response};
use crate::error::Error;

const INSTANCE_NAME: &str = "FASTMSSQL";

fn response(payload: &[u8]) -> Vec<u8> {
    let declared = u16::try_from(payload.len()).expect("test payload must fit u16");
    let mut response = Vec::with_capacity(3 + payload.len());
    response.push(0x05);
    response.extend_from_slice(&declared.to_le_bytes());
    response.extend_from_slice(payload);
    response
}

fn assert_request_rejected(instance_name: &str) {
    let error = build_instance_request(instance_name)
        .expect_err("invalid instance name must fail before UDP I/O");
    assert!(
        matches!(error, Error::Conversion(_)),
        "request validation must return a controlled conversion error, got {error:?}"
    );
}

fn assert_response_rejected(bytes: &[u8]) {
    let error = parse_instance_response(bytes, INSTANCE_NAME)
        .expect_err("invalid SQL Browser response must be rejected");
    assert!(
        matches!(error, Error::Protocol(_)),
        "wire validation must return a controlled protocol error, got {error:?}"
    );
}

#[test]
fn instance_request_is_exact_and_nul_terminated() {
    assert_eq!(
        build_instance_request(INSTANCE_NAME).expect("valid request"),
        b"\x04FASTMSSQL\0"
    );
}

#[test]
fn instance_request_enforces_encoded_transport_boundaries() {
    assert_request_rejected("");
    assert_request_rejected("FAST\0MSSQL");
    assert_request_rejected(&"x".repeat(33));
    assert_request_rejected(&"é".repeat(17));

    let boundary = "x".repeat(32);
    let request = build_instance_request(&boundary).expect("32 encoded bytes are valid");
    assert_eq!(request.len(), 34);
    assert_eq!(request.first(), Some(&0x04));
    assert_eq!(&request[1..33], boundary.as_bytes());
    assert_eq!(request.last(), Some(&0x00));
    assert_eq!(request.iter().filter(|byte| **byte == 0x00).count(), 1);
}

#[test]
fn valid_response_parses_one_case_insensitive_tcp_port() {
    for payload in [
        b"ServerName;host;InstanceName;FASTMSSQL;tcp;14333;".as_slice(),
        b"ServerName;host;TCP;51433;".as_slice(),
        b"ServerName;host;TcP;65535;".as_slice(),
    ] {
        let bytes = response(payload);
        let expected = if payload.windows(5).any(|window| window == b"14333") {
            14333
        } else if payload.windows(5).any(|window| window == b"51433") {
            51433
        } else {
            65535
        };
        assert_eq!(
            parse_instance_response(&bytes, INSTANCE_NAME).expect("valid response"),
            expected
        );
    }
}

#[test]
fn unrelated_multibyte_fields_do_not_require_utf8() {
    let bytes = response(b"ServerName;\xff\xfe;InstanceName;\x80;tcp;1433;");
    assert_eq!(
        parse_instance_response(&bytes, INSTANCE_NAME).expect("ASCII tcp field remains valid"),
        1433
    );
}

#[test]
fn malformed_headers_and_sizes_are_total_and_bounded() {
    for bytes in [
        Vec::new(),
        vec![0x05],
        vec![0x05, 0x00],
        vec![0x04, 0x00, 0x00],
        vec![0x05, 0x01, 0x00],
        vec![0x05, 0x00, 0x00, b'x'],
    ] {
        assert_response_rejected(&bytes);
    }

    let mut trailing = response(b"tcp;1433;");
    trailing[1..3].copy_from_slice(&8_u16.to_le_bytes());
    assert_response_rejected(&trailing);

    let oversized = response(&vec![b'x'; 1_025]);
    assert_response_rejected(&oversized);
}

#[test]
fn response_accepts_the_exact_payload_limit() {
    let prefix = b"ServerName;";
    let suffix = b";tcp;1433;";
    let mut payload = Vec::with_capacity(1_024);
    payload.extend_from_slice(prefix);
    payload.resize(1_024 - suffix.len(), b'x');
    payload.extend_from_slice(suffix);
    assert_eq!(payload.len(), 1_024);
    let bytes = response(&payload);

    assert_eq!(
        parse_instance_response(&bytes, INSTANCE_NAME).expect("1,024-byte payload is valid"),
        1433
    );
}

#[test]
fn missing_duplicate_and_invalid_tcp_fields_are_rejected() {
    for payload in [
        b"ServerName;host;".as_slice(),
        b"tcp;".as_slice(),
        b"tcp;;".as_slice(),
        b"tcp;1433;TCP;1434;".as_slice(),
        b"tcp;+1433;".as_slice(),
        b"tcp; 1433;".as_slice(),
        b"tcp;14x33;".as_slice(),
        b"tcp;0;".as_slice(),
        b"tcp;65536;".as_slice(),
    ] {
        assert_response_rejected(&response(payload));
    }
}
