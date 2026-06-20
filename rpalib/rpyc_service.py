"""
RPyC service for remote TuiAdapter control.

Exposes all 10 TuiAdapter API items as remote callable methods
with authentication and TLS support.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any, Callable

import rpyc
from rpyc.utils.authenticators import AuthenticationError
from rpyc.utils.factory import connect_stream
from rpyc.utils.server import ThreadedServer

from rpascript.tui_adapter import TuiAdapter

_log = logging.getLogger(__name__)


class RpycTuiService(rpyc.Service):
    """RPyC service wrapping TuiAdapter for remote access."""

    def __init__(self, tui_adapter: TuiAdapter | None = None):
        self._adapter = tui_adapter or TuiAdapter()
        self._lock = threading.Lock()
        self._client_peer = threading.local()

    def on_connect(self, conn):
        """Log when a client connects."""
        try:
            host, port = conn._channel.stream.sock.getpeername()[:2]
        except Exception as exc:
            _log.warning("[EMU] Failed to get client peer address: %s", exc)
            host, port = "unknown", 0
        self._client_peer.value = f"{host}:{port}"
        _log.info("[EMU] RPC client connected from %s:%s", host, port)

    def on_disconnect(self, conn):
        """Log when a client disconnects."""
        peer = getattr(self._client_peer, 'value', 'unknown:0')
        _log.info("[EMU] RPC client disconnected (%s)", peer)

    # --- Lifecycle ---

    def exposed_start(self, udp_host: str | None = None, usb_device: str | None = None) -> bool:
        _log.info("[EMU] RPC start(udp_host=%r, usb_device=%r)", udp_host, usb_device)
        return self._adapter.start(udp_host=udp_host, usb_device=usb_device)

    def exposed_stop(self) -> None:
        _log.info("[EMU] RPC stop()")
        self._adapter.stop()

    def exposed_run(self, script: list[str], auto_checksum: bool = False) -> Any:
        _log.info("[EMU] RPC run(script=%d lines, auto_checksum=%s)", len(script), auto_checksum)
        # Run in background thread so RPyC handler doesn't block
        def _run():
            try:
                self._adapter.run(script, auto_checksum=auto_checksum)
            except Exception as e:
                _log.error("[EMU] RPC run failed: %s", e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return None

    # --- Listeners (netref callbacks) ---

    def exposed_register_status_listener(self, listener: Callable) -> None:
        _log.info("[EMU] RPC register_status_listener(%r)", listener)
        def wrapper(event):
            try:
                listener(event)
            except Exception as e:
                _log.warning("[EMU] status callback error: %s", e)
        self._adapter.register_status_listener(wrapper)

    def exposed_register_error_listener(self, listener: Callable) -> None:
        _log.info("[EMU] RPC register_error_listener(%r)", listener)
        def wrapper(msg):
            try:
                listener(msg)
            except Exception as e:
                _log.warning("[EMU] error callback error: %s", e)
        self._adapter.register_error_listener(wrapper)

    def exposed_register_reply_listener(self, listener: Callable) -> None:
        _log.info("[EMU] RPC register_reply_listener(%r)", listener)
        def wrapper(replies):
            try:
                listener(replies)
            except Exception as e:
                _log.warning("[EMU] reply callback error: %s", e)
        self._adapter.register_reply_listener(wrapper)

    def exposed_cancel_script(self) -> None:
        _log.info("[EMU] RPC cancel_script()")
        self._adapter.cancel_script()

    # --- Properties ---

    def exposed_is_connected(self) -> bool:
        result = self._adapter.is_connected
        _log.info("[EMU] RPC is_connected -> %s", result)
        return result

    def exposed_machine_status(self) -> dict[int, Any]:
        result = self._adapter.machine_status
        _log.info("[EMU] RPC machine_status -> %d items", len(result))
        return result

    # --- Static format utilities ---

    @staticmethod
    def exposed_format_reply_value(address: int, raw_reply: bytearray) -> tuple:
        _log.info("[EMU] RPC format_reply_value(addr=0x%04X, raw_len=%d)", address, len(raw_reply))
        return TuiAdapter.format_reply_value(address, raw_reply)

    @staticmethod
    def exposed_format_reply(reply: bytearray) -> str:
        _log.info("[EMU] RPC format_reply(len=%d)", len(reply))
        return TuiAdapter.format_reply(reply)

    @staticmethod
    def exposed_format_reply_list(replies: list[bytearray]) -> list[str]:
        _log.info("[EMU] RPC format_reply_list(count=%d)", len(replies))
        return TuiAdapter.format_reply_list(replies)


def start_rpyc_server(
    tui_adapter: TuiAdapter | None = None,
    host: str = "127.0.0.1",
    port: int = 18812,
    cert_path: str | None = None,
    key_path: str | None = None,
    ca_path: str | None = None,
    token: str | None = None,
    auto_start: bool = True,
) -> ThreadedServer:
    """Start the RPyC server.

    Args:
        tui_adapter: Optional TuiAdapter instance. Creates a new one if None.
        host: Bind address (default: 127.0.0.1).
        port: Bind port (default: 18812).
        cert_path: Path to TLS certificate (enables TLS if provided).
        key_path: Path to TLS private key.
        ca_path: Path to CA certificate for client cert verification.
        token: Authentication token. Empty/None allows localhost without token.
        auto_start: Whether to call server.start() immediately (default: True).
                    Set to False to start the server manually later.

    Returns:
        The started ThreadedServer instance.
    """
    service = RpycTuiService(tui_adapter)

    # Build authenticator if token is provided
    authenticator = None
    if token is not None:
        authenticator = _make_authenticator(token)

    # Build TLS configuration if cert is provided
    if cert_path and key_path:
        import ssl

        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert_path, keyfile=key_path)
        if ca_path:
            ssl_ctx.load_verify_locations(ca_path)
            ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        server = ThreadedServer(
            service,
            hostname=host,
            port=port,
            ssl_ctx=ssl_ctx,
            authenticator=authenticator,
        )
    else:
        server = ThreadedServer(
            service,
            hostname=host,
            port=port,
            authenticator=authenticator,
        )

    _log.info(
        "RPyC server starting on %s:%s (TLS=%s, auth=%s)",
        host,
        port,
        "yes" if cert_path else "no",
        "yes" if token else "no",
    )
    if auto_start:
        server.start()
    return server


def _make_authenticator(token: str) -> Callable:
    """Create a token authenticator.

    Returns an authenticator function suitable for ThreadedServer.

    Protocol:
    - Client connects TCP socket
    - Client sends: 1 byte length + N bytes token (UTF-8)
    - Server validates with constant-time comparison

    Localhost connections with empty/no token are allowed:
    - No data sent at all (recv returns empty) → allowed for localhost
    - Empty token sent (1-byte length prefix with value 0) → allowed for localhost
    """
    import hmac

    token_bytes = token.encode("utf-8")

    def authenticator(sock: socket.socket) -> tuple[socket.socket, object]:
        """Authenticate a client connection.

        Returns (socket, credentials) on success.
        Raises AuthenticationError on failure.
        """
        peername = sock.getpeername()
        is_local = peername and peername[0] in ("127.0.0.1", "::1", "localhost")

        # Read token length (1 byte)
        raw_len = sock.recv(1)
        if not raw_len:
            if is_local:
                # Localhost with no token — allow
                return sock, {"user": "local", "authenticated": False}
            raise AuthenticationError("No token provided by non-localhost client")

        token_len = raw_len[0]
        client_token = sock.recv(token_len)

        if len(client_token) != token_len:
            raise AuthenticationError("Token truncated")

        # Empty token from localhost is allowed
        if is_local and token_len == 0 and client_token == b"":
            return sock, {"user": "local", "authenticated": False}

        if not hmac.compare_digest(client_token, token_bytes):
            raise AuthenticationError("Invalid token")

        return sock, {"user": "token-auth", "authenticated": True}

    return authenticator
