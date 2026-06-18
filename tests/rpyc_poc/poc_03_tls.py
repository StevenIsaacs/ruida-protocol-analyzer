"""
PoC 3: TLS-Encrypted RPyC Connection

Demonstrates setting up an RPyC server and client communicating over TLS
with self-signed certificates.

Steps:
  1. Generate a self-signed CA certificate and a server certificate
     (signed by the CA) using Python's ``cryptography`` library.
  2. Server starts with ``SSLAuthenticator`` wrapping the socket.
  3. Client connects via ``rpyc.utils.factory.ssl_connect``.

Key finding for TuiAdapter: RPyC's built-in SSLAuthenticator + ssl_connect
provide a straightforward TLS integration path using standard Python ssl.
"""

from __future__ import annotations

import datetime
import os
import ssl
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from rpyc import Service
from rpyc.utils.authenticators import SSLAuthenticator
from rpyc.utils.factory import ssl_connect
from rpyc.utils.server import ThreadedServer


CERT_DIR: str | None = None


def _gen_key() -> rsa.RSAPrivateKey:
    """Generate a 2048-bit RSA private key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _gen_csr(
    key: rsa.RSAPrivateKey, cn: str
) -> x509.CertificateSigningRequest:
    """Generate a CSR for the given common name."""
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        )
        .sign(key, hashes.SHA256())
    )


def _sign_csr(
    csr: x509.CertificateSigningRequest,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
) -> x509.Certificate:
    """Sign a CSR with a CA certificate, producing a valid cert."""
    # Extract the SKI from the CA cert for the AuthorityKeyIdentifier
    ca_ski_ext = ca_cert.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    )
    ca_ski_value = ca_ski_ext.value

    server_ski = x509.SubjectKeyIdentifier.from_public_key(csr.public_key())
    return (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=365)
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .add_extension(server_ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_ski_value
            ),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def generate_certs(tmpdir: str) -> dict[str, str]:
    """Generate CA + server certs and return their paths.

    Returns a dict with keys: ca_cert, server_key, server_cert.
    """
    # --- CA ---
    ca_key = _gen_key()
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RPyC PoC CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=365 * 10)
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_cert_path = os.path.join(tmpdir, "ca.pem")
    with open(ca_cert_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    # --- Server cert ---
    server_key = _gen_key()
    server_csr = _gen_csr(server_key, "localhost")
    server_cert = _sign_csr(server_csr, ca_key, ca_cert)

    server_key_path = os.path.join(tmpdir, "server.key")
    with open(server_key_path, "wb") as f:
        f.write(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    server_cert_path = os.path.join(tmpdir, "server.crt")
    with open(server_cert_path, "wb") as f:
        f.write(server_cert.public_bytes(serialization.Encoding.PEM))

    return {
        "ca_cert": ca_cert_path,
        "server_key": server_key_path,
        "server_cert": server_cert_path,
    }


class TLSEchoService(Service):
    """Simple service that echoes a message back."""

    def exposed_hello(self, name: str) -> str:
        return f"Hello, {name}! (TLS-encrypted)"


def run_client(port: int, cert_paths: dict[str, str]) -> None:
    """Connect to the TLS server and invoke a method."""
    time.sleep(0.3)
    conn = ssl_connect(
        "127.0.0.1",
        port,
        keyfile=None,  # Client cert not required
        certfile=None,
        ca_certs=cert_paths["ca_cert"],
    )
    result = conn.root.hello("PoC 3")
    print(f"  [client] Server response: {result!r}")
    conn.close()
    print("  [client] TLS connection closed cleanly.")


def run_server(port: int, cert_paths: dict[str, str]) -> None:
    """Start the TLS-wrapped RPyC server."""
    authenticator = SSLAuthenticator(
        keyfile=cert_paths["server_key"],
        certfile=cert_paths["server_cert"],
        ca_certs=cert_paths["ca_cert"],
        cert_reqs=ssl.CERT_NONE,
    )
    server = ThreadedServer(
        TLSEchoService,
        port=port,
        authenticator=authenticator,
        auto_register=False,
    )
    timer = threading.Timer(5.0, server.close)
    timer.start()
    print(f"  [server] TLS listening on 127.0.0.1:{port}")
    server.start()


def main() -> None:
    print("=== PoC 3: TLS-Encrypted Connection ===")

    global CERT_DIR
    CERT_DIR = tempfile.mkdtemp(prefix="rpyc_poc3_")
    print(f"  [setup] Generating self-signed certs in {CERT_DIR}")
    cert_paths = generate_certs(CERT_DIR)
    print(f"  [setup] CA:      {cert_paths['ca_cert']}")
    print(f"  [setup] Key:     {cert_paths['server_key']}")
    print(f"  [setup] Cert:    {cert_paths['server_cert']}")

    PORT = 18873

    server_thread = threading.Thread(
        target=run_server, args=(PORT, cert_paths), daemon=True
    )
    server_thread.start()

    client_thread = threading.Thread(
        target=run_client, args=(PORT, cert_paths), daemon=True
    )
    client_thread.start()

    client_thread.join(timeout=6)
    server_thread.join(timeout=1)

    # Cleanup
    import shutil
    shutil.rmtree(CERT_DIR, ignore_errors=True)
    print("=== PoC 3 Complete ===")


if __name__ == "__main__":
    main()
