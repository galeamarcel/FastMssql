use crate::tds::codec::TokenSspi;
use crate::{
    client::Connection,
    tds::codec::{
        FeatureAck, TokenColMetaData, TokenDone, TokenEnvChange, TokenError, TokenFeatureExtAck,
        TokenInfo, TokenLoginAck, TokenOrder, TokenReturnValue, TokenRow,
    },
    Error, SqlReadBytes, TokenType,
};
use futures_util::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite},
    stream::{BoxStream, TryStreamExt},
};
use std::{convert::TryFrom, sync::Arc};
use tracing::{event, Level};

#[derive(Debug)]
#[allow(dead_code)]
pub enum ReceivedToken {
    NewResultset(Arc<TokenColMetaData<'static>>),
    Row(TokenRow<'static>),
    Done(TokenDone),
    DoneInProc(TokenDone),
    DoneProc(TokenDone),
    ReturnStatus(i32),
    ReturnValue(TokenReturnValue),
    Order(TokenOrder),
    EnvChange(TokenEnvChange),
    Info(TokenInfo),
    LoginAck(TokenLoginAck),
    Sspi(TokenSspi),
    FeatureExtAck(TokenFeatureExtAck),
    Error(TokenError),
    TableName(usize),
    ColInfo(usize),
}

pub(crate) struct TokenStream<'a, S: AsyncRead + AsyncWrite + Unpin + Send> {
    conn: &'a mut Connection<S>,
    last_error: Option<Error>,
}

async fn consume_ushort_payload<R>(src: &mut R) -> crate::Result<usize>
where
    R: SqlReadBytes + Unpin,
{
    let byte_len = src.read_u16_le().await? as usize;
    let mut remaining = byte_len;
    let mut discard = [0_u8; 1024];

    while remaining > 0 {
        let chunk_len = remaining.min(discard.len());
        src.read_exact(&mut discard[..chunk_len]).await?;
        remaining -= chunk_len;
    }

    Ok(byte_len)
}

impl<'a, S> TokenStream<'a, S>
where
    S: AsyncRead + AsyncWrite + Unpin + Send,
{
    pub(crate) fn new(conn: &'a mut Connection<S>) -> Self {
        Self {
            conn,
            last_error: None,
        }
    }

    pub(crate) async fn flush_done(self) -> crate::Result<TokenDone> {
        let mut stream = self.try_unfold();
        let mut last_error = None;
        let mut routing = None;

        loop {
            match stream.try_next().await? {
                Some(ReceivedToken::Error(error)) => {
                    if last_error.is_none() {
                        last_error = Some(error);
                    }
                }
                Some(ReceivedToken::Done(token)) => match (last_error, routing) {
                    (Some(error), _) => return Err(Error::Server(error)),
                    (_, Some(routing)) => return Err(routing),
                    (_, _) => return Ok(token),
                },
                Some(ReceivedToken::EnvChange(TokenEnvChange::Routing { host, port })) => {
                    routing = Some(Error::Routing { host, port });
                }
                Some(_) => (),
                None => return Err(crate::Error::Protocol("Never got DONE token.".into())),
            }
        }
    }

    #[cfg(any(windows, feature = "integrated-auth-gssapi"))]
    pub(crate) async fn flush_sspi(self) -> crate::Result<TokenSspi> {
        let mut stream = self.try_unfold();
        let mut last_error = None;

        loop {
            match stream.try_next().await? {
                Some(ReceivedToken::Error(error)) => {
                    if last_error.is_none() {
                        last_error = Some(error);
                    }
                }
                Some(ReceivedToken::Sspi(token)) => return Ok(token),
                Some(_) => (),
                None => match last_error {
                    Some(err) => return Err(crate::Error::Server(err)),
                    None => return Err(crate::Error::Protocol("Never got SSPI token.".into())),
                },
            }
        }
    }

    async fn get_col_metadata(&mut self) -> crate::Result<ReceivedToken> {
        let meta = Arc::new(TokenColMetaData::decode(self.conn).await?);
        self.conn.context_mut().set_last_meta(meta.clone());

        event!(
            Level::TRACE,
            token = "COLMETADATA",
            column_count = meta.columns.len()
        );

        Ok(ReceivedToken::NewResultset(meta))
    }

    async fn get_row(&mut self) -> crate::Result<ReceivedToken> {
        let return_value = TokenRow::decode(self.conn).await?;

        event!(
            Level::TRACE,
            token = "ROW",
            column_count = return_value.len()
        );
        Ok(ReceivedToken::Row(return_value))
    }

    async fn get_nbc_row(&mut self) -> crate::Result<ReceivedToken> {
        let return_value = TokenRow::decode_nbc(self.conn).await?;

        event!(
            Level::TRACE,
            token = "NBCROW",
            column_count = return_value.len()
        );
        Ok(ReceivedToken::Row(return_value))
    }

    async fn get_return_value(&mut self) -> crate::Result<ReceivedToken> {
        let return_value = TokenReturnValue::decode(self.conn).await?;
        event!(
            Level::TRACE,
            token = "RETURNVALUE",
            ordinal = return_value.param_ordinal
        );
        Ok(ReceivedToken::ReturnValue(return_value))
    }

    async fn get_return_status(&mut self) -> crate::Result<ReceivedToken> {
        let status = self.conn.read_i32_le().await?;
        event!(Level::TRACE, token = "RETURNSTATUS");
        Ok(ReceivedToken::ReturnStatus(status))
    }

    async fn get_error(&mut self) -> crate::Result<ReceivedToken> {
        let err = TokenError::decode(self.conn).await?;

        if self.last_error.is_none() {
            self.last_error = Some(Error::Server(err.clone()));
        }

        event!(
            Level::ERROR,
            token = "ERROR",
            number = err.code,
            state = err.state,
            severity = err.class
        );
        Ok(ReceivedToken::Error(err))
    }

    async fn get_order(&mut self) -> crate::Result<ReceivedToken> {
        let order = TokenOrder::decode(self.conn).await?;
        event!(
            Level::TRACE,
            token = "ORDER",
            column_count = order.column_indexes.len()
        );
        Ok(ReceivedToken::Order(order))
    }

    async fn get_done_value(&mut self) -> crate::Result<ReceivedToken> {
        let done = TokenDone::decode(self.conn).await?;
        event!(Level::TRACE, token = "DONE");
        Ok(ReceivedToken::Done(done))
    }

    async fn get_done_proc_value(&mut self) -> crate::Result<ReceivedToken> {
        let done = TokenDone::decode(self.conn).await?;
        event!(Level::TRACE, token = "DONEPROC");
        Ok(ReceivedToken::DoneProc(done))
    }

    async fn get_done_in_proc_value(&mut self) -> crate::Result<ReceivedToken> {
        let done = TokenDone::decode(self.conn).await?;
        event!(Level::TRACE, token = "DONEINPROC");
        Ok(ReceivedToken::DoneInProc(done))
    }

    async fn get_env_change(&mut self) -> crate::Result<ReceivedToken> {
        let change = TokenEnvChange::decode(self.conn).await?;

        match &change {
            TokenEnvChange::PacketSize(new_size, _) => {
                self.conn.context_mut().set_packet_size(*new_size);
            }
            TokenEnvChange::BeginTransaction(desc) => {
                self.conn.context_mut().set_transaction_descriptor(*desc);
            }
            TokenEnvChange::CommitTransaction
            | TokenEnvChange::RollbackTransaction
            | TokenEnvChange::DefectTransaction => {
                self.conn.context_mut().set_transaction_descriptor([0; 8]);
            }
            TokenEnvChange::SqlCollation { new, .. } => {
                self.conn.context_mut().set_collation(*new);
            }
            _ => (),
        }

        event!(Level::INFO, token = "ENVCHANGE");

        Ok(ReceivedToken::EnvChange(change))
    }

    async fn get_info(&mut self) -> crate::Result<ReceivedToken> {
        let info = TokenInfo::decode(self.conn).await?;
        event!(
            Level::INFO,
            token = "INFO",
            number = info.number,
            state = info.state,
            severity = info.class
        );
        Ok(ReceivedToken::Info(info))
    }

    async fn get_login_ack(&mut self) -> crate::Result<ReceivedToken> {
        let ack = TokenLoginAck::decode(self.conn).await?;
        event!(
            Level::INFO,
            token = "LOGINACK",
            interface = ack.interface,
            version = ack.version
        );
        Ok(ReceivedToken::LoginAck(ack))
    }

    async fn get_feature_ext_ack(&mut self) -> crate::Result<ReceivedToken> {
        let ack = TokenFeatureExtAck::decode(self.conn).await?;
        if let Some(supported) = ack.features.iter().find_map(|feature| match feature {
            FeatureAck::Utf8Support(supported) => Some(*supported),
            _ => None,
        }) {
            self.conn.context_mut().set_utf8_support(supported);
        }
        event!(
            Level::INFO,
            "FeatureExtAck with {} features",
            ack.features.len()
        );
        Ok(ReceivedToken::FeatureExtAck(ack))
    }

    async fn get_sspi(&mut self) -> crate::Result<ReceivedToken> {
        let sspi = TokenSspi::decode_async(self.conn).await?;
        event!(Level::TRACE, "SSPI response");
        Ok(ReceivedToken::Sspi(sspi))
    }

    pub fn try_unfold(self) -> BoxStream<'a, crate::Result<ReceivedToken>> {
        let stream = futures_util::stream::try_unfold(self, |mut this| async move {
            if this.conn.is_eof() {
                match this.last_error {
                    None => return Ok(None),
                    Some(error) => return Err(error),
                }
            }

            let ty_byte = this.conn.read_u8().await?;

            let ty = TokenType::try_from(ty_byte)
                .map_err(|_| Error::Protocol(format!("invalid token type {:x}", ty_byte).into()))?;

            let token = match ty {
                TokenType::ReturnStatus => this.get_return_status().await?,
                TokenType::ColMetaData => this.get_col_metadata().await?,
                TokenType::Row => this.get_row().await?,
                TokenType::NbcRow => this.get_nbc_row().await?,
                TokenType::Done => this.get_done_value().await?,
                TokenType::DoneProc => this.get_done_proc_value().await?,
                TokenType::DoneInProc => this.get_done_in_proc_value().await?,
                TokenType::ReturnValue => this.get_return_value().await?,
                TokenType::Error => this.get_error().await?,
                TokenType::Order => this.get_order().await?,
                TokenType::TableName => {
                    let byte_len = consume_ushort_payload(this.conn).await?;
                    event!(Level::TRACE, token = "TABNAME", byte_len = byte_len);
                    ReceivedToken::TableName(byte_len)
                }
                TokenType::ColInfo => {
                    let byte_len = consume_ushort_payload(this.conn).await?;
                    event!(Level::TRACE, token = "COLINFO", byte_len = byte_len);
                    ReceivedToken::ColInfo(byte_len)
                }
                TokenType::EnvChange => this.get_env_change().await?,
                TokenType::Info => this.get_info().await?,
                TokenType::LoginAck => this.get_login_ack().await?,
                TokenType::Sspi => this.get_sspi().await?,
                TokenType::FeatureExtAck => this.get_feature_ext_ack().await?,
            };

            Ok(Some((token, this)))
        });

        Box::pin(stream)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::IoErrorKind;
    use crate::sql_read_bytes::test_utils::IntoSqlReadBytes;
    use bytes::BytesMut;

    #[tokio::test]
    async fn tib_safe_003_tabname_payload_preserves_the_next_token() {
        let mut source = BytesMut::from(&[3, 0, 0x11, 0x22, 0x33, 0xfd][..]).into_sql_read_bytes();

        let consumed = consume_ushort_payload(&mut source)
            .await
            .expect("valid TABNAME payload must be consumed");

        assert_eq!(consumed, 3);
        assert_eq!(
            source.read_u8().await.expect("next token must remain"),
            0xfd
        );
    }

    #[tokio::test]
    async fn tib_safe_003_colinfo_payload_preserves_the_next_token() {
        let mut source = BytesMut::from(&[2, 0, 0x44, 0x55, 0xd1][..]).into_sql_read_bytes();

        let consumed = consume_ushort_payload(&mut source)
            .await
            .expect("valid COLINFO payload must be consumed");

        assert_eq!(consumed, 2);
        assert_eq!(
            source.read_u8().await.expect("next token must remain"),
            0xd1
        );
    }

    #[tokio::test]
    async fn tib_safe_003_truncated_browse_payload_is_typed_io_error() {
        let mut source = BytesMut::from(&[3, 0, 0x11, 0x22][..]).into_sql_read_bytes();

        let error = consume_ushort_payload(&mut source)
            .await
            .expect_err("truncated browse payload must fail");

        assert!(matches!(
            error,
            Error::Io {
                kind: IoErrorKind::UnexpectedEof,
                ..
            }
        ));
    }
}
