"""
PoC 4: Token-Based Authentication for RPyC

Demonstrates a custom authenticator that validates a token sent by the
client before the RPyC protocol starts.

Rules:
  - Empty token + localhost peer  → allowed (no auth required locally)
  - Correct token + any peer     → allowed
  - Wrong token + any peer       → rejected
  - Empty token + remote peer    → rejected

The token is sent as a length-prefixed byte sequence before the RPyC
protocol starts. The server authenticator consumes these bytes from the
raw socket, validates using constant-time comparison (hmac.compare_digest),
and then the normal RPyC protocol proceeds on the same socket.

Key finding for TuiAdapter: Custom authenticators are simple callables
``(socket) -> (wrapped_socket, credentials)``. They are invoked during the
connection handshake *before* any RPyC protocol data is exchanged.
"""

from __future__ import annotations

import socket as socket_module
import threading
import time
from hmac import compare_digest

from rpyc import Service
from rpyc.core.stream import SocketStream
from rpyc.utils.authenticators import AuthenticationError
from rpyc.utils.factory import connect_stream
from rpyc.utils.server import ThreadedServer

EXPECTED_TOKEN = "s3cret!t0k3n"


def is_localhost(peer: tuple[str, int]) -> bool:
    """Return True if the peer address is a loopback address."""
    host = peer[0]
    return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")


def token_authenticator(sock: socket_module.socket):
    """Authenticate a connection based on a length-prefixed token.

    Protocol:
      1 byte:  token length N
      N bytes: token content (UTF-8)

    Returns (sock, credentials_dict) on success.
    """
    peer = sock.getpeername()
    local = is_localhost(peer)

    # Read 1-byte length prefix
    raw_len = sock.recv(1)
    if not raw_len:
        raise AuthenticationError("No token received — connection closed")

    token_len = raw_len[0]
    token = sock.recv(token_len).decode("utf-8") if token_len else ""

    # Validation
    if compare_digest(token, EXPECTED_TOKEN):
        return sock, {"method": "token", "token": token}
    if local and token == "":
        return sock, {"method": "localhost", "token": None}
    raise AuthenticationError(
        f"Authentication rejected from {peer[0]}:{peer[1]} "
        f"(token_len={token_len}, is_local={local})"
    )


class SecureService(Service):
    """Service that reports the credentials used to connect."""

    def on_connect(self, conn: object) -> None:
        """Store the connection reference for accessing credentials."""
        self._conn = conn

    def exposed_whoami(self) -> dict:
        """Return the authentication credentials from this connection."""
        conn = getattr(self, "_conn", None)
        if conn is None:
            return {"method": "unknown", "error": "no connection ref"}
        return conn._config.get("credentials", {"method": "unknown"})


def _send_token_and_connect(
    host: str, port: int, token: str | None
):
    """Open a socket, send the token, then establish RPyC connection.

    Parameters
    ----------
    token : str | None
        If ``None``, no token data is sent (simulating an old client).
    """
    sock = socket_module.create_connection((host, port))

    if token is None:
        # Simulate a client that doesn't know about token auth at all
        # (the authenticator will get empty data and reject)
        pass
    else:
        token_bytes = token.encode("utf-8")
        sock.sendall(bytes([len(token_bytes)]) + token_bytes)

    stream = SocketStream(sock)
    conn = connect_stream(stream)
    return conn


def run_client_assert(
    port: int,
    token: str | None,
    expect_success: bool,
    label: str,
) -> None:
    """Connect with the given token and assert success/failure."""
    time.sleep(0.15)  # Avoid connection storms
    try:
        conn = _send_token_and_connect("127.0.0.1", port, token)
        # Attempt a request to verify the connection is actually live
        creds = conn.root.whoami()
        if expect_success:
            print(f"  [client] OK [{label}]: connected, credentials={creds}")
        else:
            print(f"  [client] FAIL [{label}]: expected rejection but got {creds}")
        conn.close()
    except Exception as e:
        if not expect_success:
            print(f"  [client] OK [{label}]: rejected as expected: {type(e).__name__}: {e}")
        else:
            print(f"  [client] FAIL [{label}]: unexpected error: {type(e).__name__}: {e}")


def run_client(port: int) -> None:
    """Run all auth test cases."""
    time.sleep(0.3)

    # Test 1: Correct token → allowed
    run_client_assert(port, EXPECTED_TOKEN, True, "correct-token")

    # Test 2: Wrong token → rejected
    run_client_assert(port, "wrong-token", False, "wrong-token")

    # Test 3: Empty token from localhost → allowed
    run_client_assert(port, "", True, "empty-token-localhost")

    # Test 4: No token at all (old client) → rejected
    run_client_assert(port, None, False, "no-token")

    # Test 5: Wrong token with localhost prefix → still rejected
    run_client_assert(port, "wrong", False, "wrong-token-localhost")

    print("  [client] All test cases complete.")


def run_server(port: int) -> None:
    """Start the authenticated RPyC server."""
    server = ThreadedServer(
        SecureService,
        port=port,
        authenticator=token_authenticator,
        auto_register=False,
    )
    timer = threading.Timer(8.0, server.close)
    timer.start()
    print(f"  [server] Auth-protected on 127.0.0.1:{port}")
    server.start()


def main() -> None:
    print("=== PoC 4: Token-Based Authentication ===")
    PORT = 18874

    server_thread = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    server_thread.start()

    client_thread = threading.Thread(target=run_client, args=(PORT,), daemon=True)
    client_thread.start()

    client_thread.join(timeout=10)
    server_thread.join(timeout=1)
    print("=== PoC 4 Complete ===")


if __name__ == "__main__":
    main()
