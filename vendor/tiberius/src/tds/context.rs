use super::codec::*;
use super::Collation;
use std::sync::Arc;

/// Context, that might be required to make sure we understand and are understood by the server
#[derive(Debug)]
pub(crate) struct Context {
    version: FeatureLevel,
    packet_size: u32,
    packet_id: u8,
    transaction_desc: [u8; 8],
    last_meta: Option<Arc<TokenColMetaData<'static>>>,
    spn: Option<String>,
    collation: Option<Collation>,
    initial_collation: Option<Collation>,
    utf8_support: bool,
}

impl Context {
    pub fn new() -> Context {
        Context {
            version: FeatureLevel::SqlServerN,
            packet_size: 4096,
            packet_id: 0,
            transaction_desc: [0; 8],
            last_meta: None,
            spn: None,
            collation: None,
            initial_collation: None,
            utf8_support: false,
        }
    }

    pub fn next_packet_id(&mut self) -> u8 {
        let id = self.packet_id;
        self.packet_id = self.packet_id.wrapping_add(1);
        id
    }

    pub fn set_last_meta(&mut self, meta: Arc<TokenColMetaData<'static>>) {
        self.last_meta.replace(meta);
    }

    pub fn last_meta(&self) -> Option<Arc<TokenColMetaData<'static>>> {
        self.last_meta.clone()
    }

    pub fn packet_size(&self) -> u32 {
        self.packet_size
    }

    pub fn set_packet_size(&mut self, new_size: u32) {
        self.packet_size = new_size;
    }

    pub fn transaction_descriptor(&self) -> [u8; 8] {
        self.transaction_desc
    }

    pub fn set_transaction_descriptor(&mut self, desc: [u8; 8]) {
        self.transaction_desc = desc;
    }

    pub fn collation(&self) -> Option<Collation> {
        self.collation
    }

    pub fn set_collation(&mut self, collation: Option<Collation>) {
        self.collation = collation;
    }

    pub fn capture_initial_collation(&mut self) {
        self.initial_collation = self.collation;
    }

    pub fn utf8_support(&self) -> bool {
        self.utf8_support
    }

    pub fn set_utf8_support(&mut self, supported: bool) {
        self.utf8_support = supported;
    }

    pub fn reset_for_connection_pool(&mut self) {
        // RESETCONNECTION rolls back a local transaction before processing the
        // next request. That request must therefore carry a zero transaction
        // descriptor rather than the descriptor from the previous lease.
        self.transaction_desc = [0; 8];
        self.last_meta = None;
        // The server applies RESETCONNECTION before decoding this request and
        // restores the LOGIN7 environment. Parameter TYPE_INFO in the same
        // request must therefore use the initial database collation, not a
        // collation observed after a prior lease executed USE.
        self.collation = self.initial_collation;
    }

    pub fn version(&self) -> FeatureLevel {
        self.version
    }

    pub fn set_spn(&mut self, host: impl AsRef<str>, port: u16) {
        self.spn = Some(format!("MSSQLSvc/{}:{}", host.as_ref(), port));
    }

    #[cfg(any(windows, all(unix, feature = "integrated-auth-gssapi")))]
    pub fn spn(&self) -> &str {
        self.spn.as_deref().unwrap_or("")
    }
}

#[cfg(test)]
mod tests {
    use super::Context;
    use crate::tds::Collation;

    #[test]
    fn pool_reset_clears_the_client_transaction_descriptor() {
        let mut context = Context::new();
        context.set_transaction_descriptor([7; 8]);

        context.reset_for_connection_pool();

        assert_eq!(context.transaction_descriptor(), [0; 8]);
    }

    #[test]
    fn pool_reset_restores_the_initial_login_collation() {
        let mut context = Context::new();
        let login_collation = Collation::new(0x0000_0409, 52);
        let changed_collation = Collation::new(0x0400_0409, 0);
        context.set_collation(Some(login_collation));
        context.capture_initial_collation();
        context.set_collation(Some(changed_collation));

        context.reset_for_connection_pool();

        assert_eq!(context.collation(), Some(login_collation));
    }

    #[test]
    fn pool_reset_preserves_negotiated_utf8_support() {
        let mut context = Context::new();
        context.set_utf8_support(true);

        context.reset_for_connection_pool();

        assert!(context.utf8_support());
    }
}
