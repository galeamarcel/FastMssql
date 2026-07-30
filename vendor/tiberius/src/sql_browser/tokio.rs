use super::SqlBrowser;
use crate::client::Config;
use async_trait::async_trait;
use net::{TcpStream, UdpSocket};
use std::{io, net::SocketAddr};
use tokio::{
    net,
    time::{self, Duration},
};
use tracing::Level;

const SQL_BROWSER_TIMEOUT: Duration = Duration::from_secs(1);
const SQL_BROWSER_RECEIVE_BUFFER_LEN: usize =
    super::SQL_BROWSER_HEADER_LEN + super::SQL_BROWSER_MAX_RESPONSE_PAYLOAD + 1;

#[async_trait]
impl SqlBrowser for TcpStream {
    /// This method can be used to connect to SQL Server named instances
    /// when on a Windows platform with the `sql-browser-tokio` feature
    /// enabled. Please see the crate examples for more detailed examples.
    async fn connect_named(builder: &Config) -> crate::Result<Self> {
        let browser_request = builder
            .instance_name
            .as_deref()
            .map(super::build_instance_request)
            .transpose()?;
        let addrs = net::lookup_host(builder.get_addr()).await?;
        let mut last_error = None;

        for mut addr in addrs {
            if let (Some(instance_name), Some(request)) =
                (builder.instance_name.as_deref(), browser_request.as_deref())
            {
                // First resolve the instance to a port via the
                // SSRP protocol/MS-SQLR protocol [1]
                // [1] https://msdn.microsoft.com/en-us/library/cc219703.aspx

                let local_bind = if addr.is_ipv4() {
                    SocketAddr::from(([0, 0, 0, 0], 0))
                } else {
                    SocketAddr::from(([0_u16; 8], 0))
                };

                tracing::event!(
                    Level::TRACE,
                    "Resolving a named SQL Server instance through SQL Browser"
                );

                let socket = match UdpSocket::bind(local_bind).await {
                    Ok(socket) => socket,
                    Err(error) => {
                        last_error = Some(error.into());
                        continue;
                    }
                };

                if let Err(error) = socket.connect(addr).await {
                    last_error = Some(error.into());
                    continue;
                }

                if let Err(error) = socket.send(request).await {
                    last_error = Some(error.into());
                    continue;
                }

                let mut response = [0_u8; SQL_BROWSER_RECEIVE_BUFFER_LEN];
                let len = match time::timeout(SQL_BROWSER_TIMEOUT, socket.recv(&mut response)).await
                {
                    Ok(Ok(len)) => len,
                    Ok(Err(error)) => {
                        last_error = Some(error.into());
                        continue;
                    }
                    Err(_) => {
                        last_error = Some(
                            io::Error::new(
                                io::ErrorKind::TimedOut,
                                "SQL Browser response timed out after one second",
                            )
                            .into(),
                        );
                        continue;
                    }
                };

                let port = match super::parse_instance_response(&response[..len], instance_name) {
                    Ok(port) => port,
                    Err(error) => {
                        last_error = Some(error);
                        continue;
                    }
                };
                tracing::event!(
                    Level::TRACE,
                    "SQL Browser returned a named-instance TCP endpoint"
                );
                addr.set_port(port);
            };

            match TcpStream::connect(addr).await {
                Ok(stream) => {
                    stream.set_nodelay(true)?;
                    return Ok(stream);
                }
                Err(error) => last_error = Some(error.into()),
            }
        }

        Err(last_error.unwrap_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "server host resolved to no addresses",
            )
            .into()
        }))
    }
}
