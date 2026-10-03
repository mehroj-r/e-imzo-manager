# Vendored E-IMZO SDK

This directory contains the published `e-imzo` 0.4.0 Rust client library, with a manager-side compatibility correction applied on 2026-10-03.

- Upstream: https://github.com/rust-lang-uz/e-imzo
- Published source: https://crates.io/api/v1/crates/e-imzo/0.4.0/download
- Original crate SHA-256: `7511e896507ad667ed758c519a545f1ad27dece8d2c565a01f174371a78eae16`
- Upstream source commit: `a228f58b871c0342a9664397b294b1a115e39174`
- License: MIT OR Apache-2.0; see `LICENSE-MIT` and `LICENSE-APACHE`.

## Local change

In `src/client.rs`, `Client::connect` now passes `url.host_str().unwrap()` to `TlsConnector::connect`, rather than `remote_addr.as_str()`. The TCP destination still includes its port, but the TLS server name must not. Passing `127.0.0.1:64443` as the server name causes Java to reject the handshake with an illegal-parameter alert.

The connector's existing certificate-verification behavior is unchanged. The Java signing backend is not included in this directory and is not patched.

The root integration test `tests/tls_server_name.rs` exercises the production connection against a strict loopback TLS/WebSocket server. SDK development/Nix files and registry-cache markers were omitted; source files, referenced manifests, README, license texts and source provenance are retained.
