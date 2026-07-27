use crate::{SqlReadBytes, FEA_EXT_FEDAUTH, FEA_EXT_TERMINATOR, FEA_EXT_UTF8_SUPPORT};
use futures_util::AsyncReadExt;

#[derive(Debug)]
pub struct TokenFeatureExtAck {
    pub features: Vec<FeatureAck>,
}

#[derive(Debug)]
#[allow(dead_code)]
pub enum FedAuthAck {
    SecurityToken { nonce: Option<[u8; 32]> },
}

#[derive(Debug)]
#[allow(dead_code)]
pub enum FeatureAck {
    FedAuth(FedAuthAck),
    Utf8Support(bool),
}

impl TokenFeatureExtAck {
    pub(crate) async fn decode<R>(src: &mut R) -> crate::Result<Self>
    where
        R: SqlReadBytes + Unpin,
    {
        let mut features = Vec::new();
        loop {
            let feature_id = src.read_u8().await?;

            if feature_id == FEA_EXT_TERMINATOR {
                break;
            } else if feature_id == FEA_EXT_FEDAUTH {
                let data_len = src.read_u32_le().await?;

                let nonce = if data_len == 32 {
                    let mut n = [0u8; 32];
                    src.read_exact(&mut n).await?;

                    Some(n)
                } else if data_len == 0 {
                    None
                } else {
                    return Err(crate::Error::Protocol(
                        "invalid FEDAUTH feature acknowledgement length".into(),
                    ));
                };

                features.push(FeatureAck::FedAuth(FedAuthAck::SecurityToken { nonce }))
            } else if feature_id == FEA_EXT_UTF8_SUPPORT {
                let data_len = src.read_u32_le().await?;
                if data_len != 1 {
                    return Err(crate::Error::Protocol(
                        "invalid UTF8_SUPPORT feature acknowledgement length".into(),
                    ));
                }

                let value = src.read_u8().await?;
                if value > 1 {
                    return Err(crate::Error::Protocol(
                        "invalid UTF8_SUPPORT feature acknowledgement value".into(),
                    ));
                }

                features.push(FeatureAck::Utf8Support(value == 1))
            } else {
                return Err(crate::Error::Protocol(
                    format!("unsupported feature acknowledgement {feature_id:#04x}").into(),
                ));
            }
        }

        Ok(TokenFeatureExtAck { features })
    }
}

#[cfg(test)]
mod tests {
    use super::{FeatureAck, TokenFeatureExtAck};
    use crate::{tds::Context, Error, SqlReadBytes};
    use async_std::task::block_on;
    use futures_util::io::{AsyncRead, Cursor};
    use std::{
        pin::Pin,
        task::{Context as TaskContext, Poll},
    };

    struct TestReader {
        bytes: Cursor<Vec<u8>>,
        context: Context,
    }

    impl TestReader {
        fn new(bytes: Vec<u8>) -> Self {
            Self {
                bytes: Cursor::new(bytes),
                context: Context::new(),
            }
        }
    }

    impl AsyncRead for TestReader {
        fn poll_read(
            mut self: Pin<&mut Self>,
            cx: &mut TaskContext<'_>,
            buf: &mut [u8],
        ) -> Poll<std::io::Result<usize>> {
            Pin::new(&mut self.bytes).poll_read(cx, buf)
        }
    }

    impl SqlReadBytes for TestReader {
        fn debug_buffer(&self) {}

        fn context(&self) -> &Context {
            &self.context
        }

        fn context_mut(&mut self) -> &mut Context {
            &mut self.context
        }
    }

    #[test]
    fn decodes_utf8_support_acknowledgement() {
        let mut reader = TestReader::new(vec![0x0a, 1, 0, 0, 0, 1, 0xff]);

        let token = block_on(TokenFeatureExtAck::decode(&mut reader))
            .expect("valid UTF-8 acknowledgement should decode");

        assert!(matches!(
            token.features.as_slice(),
            [FeatureAck::Utf8Support(true)]
        ));
    }

    #[test]
    fn rejects_invalid_utf8_acknowledgement_without_panicking() {
        let mut reader = TestReader::new(vec![0x0a, 2, 0, 0, 0, 1, 0, 0xff]);

        let error = block_on(TokenFeatureExtAck::decode(&mut reader))
            .expect_err("invalid UTF-8 acknowledgement length must fail");

        assert!(matches!(error, Error::Protocol(_)));
    }
}
