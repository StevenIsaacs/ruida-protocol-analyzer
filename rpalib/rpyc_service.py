"""
RPyC service for remote TuiAdapter control.

Exposes all 10 TuiAdapter API items as remote callable methods
with authentication and TLS support.
"""

from __future__ import annotations

import logging
import socket
import queue
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
        self._registered_wrappers = threading.local()
        # Shared callback queue — single thread prevents lock contention on RPyC netrefs
        self._callback_queue: queue.Queue = queue.Queue(maxsize=100)
        self._callback_thread = threading.Thread(target=self._callback_loop, daemon=True)
        self._callback_thread.start()

    def _ensure_callback_thread(self) -> None:
        """Shared callback thread started in __init__. No-op."""

    def _callback_loop(self) -> None:
        """Process queued callbacks one at a time on a single background thread.

        Uses rpyc.async_() for non-blocking netref calls — sends the request
        and returns immediately. Prevents backpressure on the callback queue
        when the client is slow to serve the connection.
        """
        while True:
            try:
                listener, arg = self._callback_queue.get(timeout=1.0)
                try:
                    # Non-blocking netref call — fire and forget
                    async_listener = rpyc.async_(listener)
                    async_listener(arg)
                except Exception as e:
                    self._adapter._log_warning(f"[EMU] callback: {e}")
            except queue.Empty:
                continue
            except Exception as e:
                self._adapter._log_warning(f"[EMU] callback loop: {e}")

    def _fire_async(self, listener: Callable, arg: Any, label: str) -> None:
        """Queue a callback for async delivery on the shared callback thread.

        The callback thread uses rpyc.async_() for truly non-blocking
        netref calls, so queued events are processed rapidly without
        waiting for client responses. Queue overflow drains oldest events.
        """
        self._ensure_callback_thread()
        try:
            self._callback_queue.put_nowait((listener, arg))
        except queue.Full:
            # Drop the oldest event to make room for the newest
            try:
                self._callback_queue.get_nowait()
                self._callback_queue.put_nowait((listener, arg))
            except queue.Empty:
                pass  # raced with consumer — event was already processed
            self._adapter._log_warning(f"[EMU] {label} overflow: oldest callback dropped (queue full)")

    def on_connect(self, conn):
        """Log when a client connects."""
        try:
            host, port = conn._channel.stream.sock.getpeername()[:2]
        except Exception as exc:
            self._adapter._log_warning(f"RPC client connect - failed to get peer: {exc}")
            host, port = "unknown", 0
        self._client_peer.value = f"{host}:{port}"
        self._adapter._log_info(f"RPC client connected from {host}:{port}")

    def on_disconnect(self, conn):
        """Clean up per-connection state when a client disconnects."""
        peer = getattr(self._client_peer, 'value', 'unknown:0')
        self._adapter._log_info(f"RPC client disconnected ({peer})")

        # Unregister all stored wrappers to prevent stale-callback warnings
        wrappers = self._registered_wrappers
        # Check if any wrappers were registered on this connection's thread
        if not any(getattr(wrappers, k, None) for k in ('status', 'error', 'reply')):
            return
        for key, unregister_name in [
            ('status', 'unregister_status_listener'),
            ('error', 'unregister_error_listener'),
            ('reply', 'unregister_reply_listener'),
        ]:
            listeners = getattr(wrappers, key, [])
            unregister = getattr(self._adapter, unregister_name, None)
            if unregister is None:
                continue
            for listener in listeners:
                try:
                    unregister(listener)
                except Exception as exc:
                    try:
                        self._adapter._log_warning(
                            f"RPC disconnect cleanup ({unregister_name}): {exc}"
                        )
                    except Exception:
                        pass  # Cleanup path — nothing actionable if logging fails
            # Clear the list
            listeners.clear()

    # --- Lifecycle ---

    def exposed_start(self, udp_host: str | None = None, usb_device: str | None = None) -> bool:
        self._adapter._log_info(f"[EMU] RPC start(udp_host={udp_host!r}, usb_device={usb_device!r})")
        return self._adapter.start(udp_host=udp_host, usb_device=usb_device)

    def exposed_stop(self) -> None:
        self._adapter._log_info("[EMU] RPC stop()")
        self._adapter.stop()

    def exposed_run(self, script: list[str], auto_checksum: bool = False) -> Any:
        # Convert netref to local list on the handler thread, where the RPyC
        # connection is alive. RPyC passes lists by reference (netref), not by
        # value — only tuples and simple types are brine-dumpable. Iterating a
        # netref from a background thread or after the handler returns is fragile.
        local_script = list(script)
        self._adapter._log_info(f"[EMU] RPC run(script={len(local_script)} lines, auto_checksum={auto_checksum})")
        # The adapter's run_script() internally uses call_from_thread() to
        # bridge to the TUI event loop thread, then calls driver.run() which
        # queues the script and returns quickly. No separate background thread
        # needed — the handler thread blocks briefly via call_from_thread's
        # future.result() and the TUI thread executes the driver call.
        try:
            self._adapter.run(local_script, auto_checksum=auto_checksum)
        except Exception as e:
            self._adapter._log_error("[EMU] RPC run failed: %s", e)
        return None

    # --- Listeners (netref callbacks) ---

    def exposed_register_status_listener(self, listener: Callable) -> None:
        self._adapter._log_info(f"[EMU] RPC register_status_listener({listener!r})")
        def wrapper(event):
            try:
                # Convert non-serializable types to brine-dumpable forms
                if isinstance(event, str):
                    converted = event     # RdStatusEvent.name → already a str
                elif isinstance(event, dict):
                    converted = dict(event)  # StatusDict → plain dict
                else:
                    converted = event.name   # RdStatusEvent enum → str name
                # Fire on background thread to avoid blocking status monitor
                self._fire_async(listener, converted, "status")
            except Exception as e:
                self._adapter._log_warning(f"[EMU] status callback error: {e}")
        # Store wrapper per-connection for disconnect cleanup
        if not hasattr(self._registered_wrappers, 'status'):
            self._registered_wrappers.status = []
        self._registered_wrappers.status.append(wrapper)
        self._adapter.register_status_listener(wrapper)

    def exposed_register_error_listener(self, listener: Callable) -> None:
        self._adapter._log_info(f"[EMU] RPC register_error_listener({listener!r})")
        def wrapper(msg):
            try:
                # Fire on background thread to avoid blocking caller
                self._fire_async(listener, msg, "error")
            except Exception as e:
                self._adapter._log_warning(f"[EMU] error callback error: {e}")
        # Store wrapper per-connection for disconnect cleanup
        if not hasattr(self._registered_wrappers, 'error'):
            self._registered_wrappers.error = []
        self._registered_wrappers.error.append(wrapper)
        self._adapter.register_error_listener(wrapper)

    def exposed_register_reply_listener(self, listener: Callable) -> None:
        self._adapter._log_info(f"[EMU] RPC register_reply_listener({listener!r})")
        def wrapper(replies):
            try:
                # list[str] is not brine-dumpable; tuple[str, ...] is
                converted = tuple(replies)
                # Fire on background thread to avoid blocking caller
                self._fire_async(listener, converted, "reply")
            except Exception as e:
                self._adapter._log_warning(f"[EMU] reply callback error: {e}")
        # Store wrapper per-connection for disconnect cleanup
        if not hasattr(self._registered_wrappers, 'reply'):
            self._registered_wrappers.reply = []
        self._registered_wrappers.reply.append(wrapper)
        self._adapter.register_reply_listener(wrapper)

    def exposed_cancel_script(self) -> None:
        self._adapter._log_info("[EMU] RPC cancel_script()")
        self._adapter.cancel_script()

    # --- Properties ---

    def exposed_is_connected(self) -> bool:
        result = self._adapter.is_connected
        self._adapter._log_info(f"[EMU] RPC is_connected -> {result}")
        return result

    def exposed_machine_status(self) -> dict[int, Any]:
        result = self._adapter.machine_status
        self._adapter._log_info(f"[EMU] RPC machine_status -> {len(result)} items")
        return result

    # --- Static format utilities ---

    def exposed_format_reply_value(self, address: int, raw_reply: bytearray) -> tuple:
        self._adapter._log_info(f"[EMU] RPC format_reply_value(addr=0x{address:04X}, raw_len={len(raw_reply)})")
        return TuiAdapter.format_reply_value(address, raw_reply)

    def exposed_format_reply(self, reply: bytearray) -> str:
        self._adapter._log_info(f"[EMU] RPC format_reply(len={len(reply)})")
        return TuiAdapter.format_reply(reply)

    def exposed_format_reply_list(self, replies: list[bytearray]) -> list[str]:
        self._adapter._log_info(f"[EMU] RPC format_reply_list(count={len(replies)})")
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
