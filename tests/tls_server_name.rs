use e_imzo::client::{Client, Disconnected};
use openssl::{
    asn1::Asn1Time,
    bn::BigNum,
    hash::MessageDigest,
    pkey::PKey,
    rsa::Rsa,
    ssl::{NameType, SniError, SslAcceptor, SslAlert, SslMethod},
    x509::{X509, X509NameBuilder, extension::SubjectAlternativeName},
};
use std::{
    io::ErrorKind,
    net::{TcpListener, TcpStream},
    thread,
    time::{Duration, Instant},
};
use tungstenite::handshake::server::{Request, Response};

fn accept_with_deadline(listener: &TcpListener) -> Result<TcpStream, String> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match listener.accept() {
            Ok((stream, _)) => return Ok(stream),
            Err(err) if err.kind() == ErrorKind::WouldBlock && Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(10));
            }
            Err(err) => return Err(format!("accept: {err}")),
        }
    }
}

#[test]
fn ip_literal_connection_does_not_send_host_and_port_as_sni() {
    let key = PKey::from_rsa(Rsa::generate(2048).unwrap()).unwrap();
    let mut name = X509NameBuilder::new().unwrap();
    name.append_entry_by_text("CN", "localhost").unwrap();
    let name = name.build();
    let mut certificate = X509::builder().unwrap();
    certificate.set_version(2).unwrap();
    certificate
        .set_serial_number(&BigNum::from_u32(1).unwrap().to_asn1_integer().unwrap())
        .unwrap();
    certificate.set_subject_name(&name).unwrap();
    certificate.set_issuer_name(&name).unwrap();
    certificate.set_pubkey(&key).unwrap();
    certificate
        .set_not_before(Asn1Time::days_from_now(0).unwrap().as_ref())
        .unwrap();
    certificate
        .set_not_after(Asn1Time::days_from_now(1).unwrap().as_ref())
        .unwrap();
    let alternative_name = SubjectAlternativeName::new()
        .ip("127.0.0.1")
        .build(&certificate.x509v3_context(None, None))
        .unwrap();
    certificate.append_extension(alternative_name).unwrap();
    certificate.sign(&key, MessageDigest::sha256()).unwrap();

    let mut acceptor = SslAcceptor::mozilla_intermediate(SslMethod::tls_server()).unwrap();
    acceptor.set_private_key(&key).unwrap();
    acceptor.set_certificate(&certificate.build()).unwrap();
    acceptor.check_private_key().unwrap();
    acceptor.set_servername_callback(|ssl, alert| {
        if ssl.servername(NameType::HOST_NAME).is_some() {
            *alert = SslAlert::ILLEGAL_PARAMETER;
            Err(SniError::ALERT_FATAL)
        } else {
            Ok(())
        }
    });
    let acceptor = acceptor.build();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    listener.set_nonblocking(true).unwrap();
    let server = thread::spawn(move || -> Result<(), String> {
        let stream = accept_with_deadline(&listener)?;
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .map_err(|err| err.to_string())?;
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .map_err(|err| err.to_string())?;
        let stream = acceptor.accept(stream).map_err(|err| err.to_string())?;
        let socket = tungstenite::accept_hdr(stream, |request: &Request, response: Response| {
            assert_eq!(request.uri().path(), "/service/cryptapi");
            Ok(response)
        })
        .map_err(|err| err.to_string())?;
        drop(socket);
        Ok(())
    });

    let connection = Client::<Disconnected>::connect(Some(format!("wss://{address}")));
    let server_result = server.join().expect("TLS server thread panicked");
    assert!(
        connection.is_ok(),
        "client handshake failed: {:?}",
        connection.err()
    );
    assert!(
        server_result.is_ok(),
        "server handshake failed: {server_result:?}"
    );
}
