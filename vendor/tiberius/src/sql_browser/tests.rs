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
fn tcp_text_in_an_unrelated_field_value_is_not_a_duplicate_key() {
    let bytes = response(b"InstanceName;tcp;IsClustered;No;tcp;1433;");
    assert_eq!(
        parse_instance_response(&bytes, INSTANCE_NAME)
            .expect("only the paired TCP key selects the port"),
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
        b"ServerName;host;orphan".as_slice(),
        b";nonempty;tcp;1433;".as_slice(),
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

#[cfg(feature = "sql-browser-tokio")]
mod tokio_transport {
    use super::{response, INSTANCE_NAME};
    use crate::{error::Error, Config, SqlBrowser};
    use std::io;
    use tokio::{
        net::{TcpListener, TcpStream, UdpSocket},
        sync::oneshot,
        time::{timeout, Duration, Instant},
    };

    const TEST_BOUND: Duration = Duration::from_secs(3);

    fn browser_config(browser_address: std::net::SocketAddr) -> Config {
        let mut config = Config::new();
        config.host(browser_address.ip().to_string());
        config.port(browser_address.port());
        config.instance_name(INSTANCE_NAME);
        config
    }

    async fn connect_named(config: Config) -> crate::Result<TcpStream> {
        <TcpStream as SqlBrowser>::connect_named(&config).await
    }

    #[tokio::test]
    async fn connected_browser_request_reaches_the_discovered_tcp_target() {
        let browser = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind SQL Browser fixture");
        let browser_address = browser.local_addr().expect("browser address");
        let target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind discovered TCP target");
        let target_port = target.local_addr().expect("target address").port();

        let responder = tokio::spawn(async move {
            let mut request = [0_u8; 64];
            let (length, peer) = timeout(TEST_BOUND, browser.recv_from(&mut request))
                .await
                .expect("browser request timeout")
                .expect("receive browser request");
            assert_eq!(&request[..length], b"\x04FASTMSSQL\0");

            let payload = format!("InstanceName;FASTMSSQL;tcp;{target_port};");
            browser
                .send_to(&response(payload.as_bytes()), peer)
                .await
                .expect("send browser response");
        });

        let accept = tokio::spawn(async move {
            timeout(TEST_BOUND, target.accept())
                .await
                .expect("discovered TCP accept timeout")
                .expect("accept discovered TCP target")
        });

        let client = timeout(TEST_BOUND, connect_named(browser_config(browser_address)))
            .await
            .expect("named connect exceeded test bound")
            .expect("named connect must succeed");
        let (server, _) = accept.await.expect("target accept task");
        responder.await.expect("browser responder task");

        assert_eq!(client.peer_addr().expect("client peer").port(), target_port);
        assert_eq!(
            server.local_addr().expect("server local").port(),
            target_port
        );
    }

    #[tokio::test]
    async fn oversized_datagram_cannot_become_valid_through_socket_truncation() {
        let browser = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind SQL Browser fixture");
        let browser_address = browser.local_addr().expect("browser address");
        let target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind forbidden TCP target");
        let target_port = target.local_addr().expect("target address").port();

        let responder = tokio::spawn(async move {
            let mut request = [0_u8; 64];
            let (_, peer) = timeout(TEST_BOUND, browser.recv_from(&mut request))
                .await
                .expect("browser request timeout")
                .expect("receive browser request");

            let mut payload = format!("ServerName;host;tcp;{target_port};Padding;").into_bytes();
            payload.resize(1_024, b'x');
            let mut oversized = response(&payload);
            oversized.push(b'x');
            assert_eq!(oversized.len(), 1_028);
            browser
                .send_to(&oversized, peer)
                .await
                .expect("send oversized response");
        });

        let error = timeout(TEST_BOUND, connect_named(browser_config(browser_address)))
            .await
            .expect("named connect exceeded test bound")
            .expect_err("oversized response must not select a TCP target");
        responder.await.expect("browser responder task");

        assert!(
            matches!(error, Error::Protocol(_)),
            "oversized response must be a protocol error, got {error:?}"
        );
        assert!(
            timeout(Duration::from_millis(250), target.accept())
                .await
                .is_err(),
            "truncated oversized response reached its embedded TCP target"
        );
    }

    #[tokio::test]
    async fn response_from_the_wrong_udp_source_is_ignored() {
        let browser = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind expected browser");
        let browser_address = browser.local_addr().expect("browser address");
        let attacker = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind wrong-source browser");
        let wrong_target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind wrong TCP target");
        let wrong_port = wrong_target
            .local_addr()
            .expect("wrong target address")
            .port();
        let correct_target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind correct TCP target");
        let correct_port = correct_target
            .local_addr()
            .expect("correct target address")
            .port();
        let (peer_sender, peer_receiver) = oneshot::channel();
        let (send_correct, receive_correct) = oneshot::channel();

        let responder = tokio::spawn(async move {
            let mut request = [0_u8; 64];
            let (_, peer) = timeout(TEST_BOUND, browser.recv_from(&mut request))
                .await
                .expect("expected-browser request timeout")
                .expect("receive expected-browser request");
            peer_sender.send(peer).expect("publish client UDP address");
            receive_correct.await.expect("correct-response signal");
            let payload = format!("tcp;{correct_port};");
            browser
                .send_to(&response(payload.as_bytes()), peer)
                .await
                .expect("send correct response");
        });

        let connect = tokio::spawn(connect_named(browser_config(browser_address)));
        let client_udp_address = timeout(TEST_BOUND, peer_receiver)
            .await
            .expect("client UDP address timeout")
            .expect("client UDP address channel");
        let wrong_payload = format!("tcp;{wrong_port};");
        attacker
            .send_to(&response(wrong_payload.as_bytes()), client_udp_address)
            .await
            .expect("send wrong-source response");

        let wrong_accept = timeout(Duration::from_millis(250), wrong_target.accept()).await;
        assert!(
            wrong_accept.is_err(),
            "a response from an unconnected UDP peer selected the TCP target"
        );

        send_correct.send(()).expect("signal correct response");
        let correct_accept = tokio::spawn(async move {
            timeout(TEST_BOUND, correct_target.accept())
                .await
                .expect("correct TCP accept timeout")
                .expect("accept correct TCP target")
        });
        let client = timeout(TEST_BOUND, connect)
            .await
            .expect("connect task timeout")
            .expect("connect task join")
            .expect("connect through expected browser");
        let (server, _) = correct_accept.await.expect("correct accept task");
        responder.await.expect("expected-browser responder");

        assert_eq!(
            client.peer_addr().expect("client peer").port(),
            correct_port
        );
        assert_eq!(
            server.local_addr().expect("server local").port(),
            correct_port
        );
    }

    #[tokio::test]
    async fn silent_browser_is_bounded_by_the_protocol_timer() {
        let silent_browser = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind silent browser");
        let browser_address = silent_browser.local_addr().expect("browser address");
        let started = Instant::now();

        let result = timeout(TEST_BOUND, connect_named(browser_config(browser_address)))
            .await
            .expect("inner SQL Browser timer did not bound the call");
        let elapsed = started.elapsed();

        assert!(result.is_err(), "silent browser unexpectedly connected");
        assert!(
            elapsed >= Duration::from_millis(800),
            "browser timeout fired too early: {elapsed:?}"
        );
        assert!(
            elapsed < Duration::from_millis(2_500),
            "browser timeout exceeded its bounded window: {elapsed:?}"
        );
        drop(silent_browser);
    }

    #[tokio::test]
    async fn discovered_tcp_failure_preserves_io_kind_and_detail() {
        let browser = UdpSocket::bind("127.0.0.1:0")
            .await
            .expect("bind SQL Browser fixture");
        let browser_address = browser.local_addr().expect("browser address");
        let closed_target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("reserve closed target");
        let closed_port = closed_target
            .local_addr()
            .expect("closed target address")
            .port();
        drop(closed_target);

        let responder = tokio::spawn(async move {
            let mut request = [0_u8; 64];
            let (_, peer) = timeout(TEST_BOUND, browser.recv_from(&mut request))
                .await
                .expect("browser request timeout")
                .expect("receive browser request");
            let payload = format!("tcp;{closed_port};");
            browser
                .send_to(&response(payload.as_bytes()), peer)
                .await
                .expect("send closed target response");
        });

        let error = timeout(TEST_BOUND, connect_named(browser_config(browser_address)))
            .await
            .expect("named connect exceeded test bound")
            .expect_err("closed discovered TCP port must fail");
        responder.await.expect("browser responder task");

        match error {
            Error::Io { kind, message } => {
                assert_ne!(
                    kind,
                    io::ErrorKind::NotFound,
                    "TCP refusal was replaced by a false host-resolution error"
                );
                assert!(
                    kind == io::ErrorKind::ConnectionRefused
                        || message.to_ascii_lowercase().contains("refused"),
                    "missing discovered TCP failure detail: {kind:?}: {message}"
                );
            }
            other => panic!("expected preserved I/O failure, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn no_instance_connects_directly_without_sql_browser() {
        let target = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind direct TCP target");
        let target_address = target.local_addr().expect("target address");
        let mut config = Config::new();
        config.host(target_address.ip().to_string());
        config.port(target_address.port());

        let accept = tokio::spawn(async move {
            timeout(TEST_BOUND, target.accept())
                .await
                .expect("direct TCP accept timeout")
                .expect("accept direct TCP target")
        });
        let client = timeout(TEST_BOUND, connect_named(config))
            .await
            .expect("direct connect exceeded test bound")
            .expect("direct connect must succeed");
        let (server, _) = accept.await.expect("direct accept task");

        assert_eq!(client.peer_addr().expect("client peer"), target_address);
        assert_eq!(server.local_addr().expect("server local"), target_address);
    }
}
