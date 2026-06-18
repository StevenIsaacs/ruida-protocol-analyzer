"""
PoC 1: Netref Callbacks

Demonstrates that RPyC can call client-side callbacks via netrefs.
Tests three callback types:
  - plain function
  - lambda
  - instance method

Key finding for TuiAdapter: listener callbacks (register_status_listener,
register_error_listener, register_reply_listener) can be passed as netrefs
and will be invoked on the client side when the server fires them.
"""

from __future__ import annotations

import threading
import time

from rpyc import Service
from rpyc.utils.factory import connect
from rpyc.utils.server import ThreadedServer


class CallbackService(Service):
    """A minimal service that accepts callbacks and tests them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._callbacks: list[object] = []

    def exposed_register_callback(self, callback: object) -> None:
        """Store a callback (netref) from the client."""
        self._callbacks.append(callback)

    def exposed_fire_callbacks(self, message: str) -> None:
        """Invoke all stored callbacks with the given message."""
        for cb in self._callbacks:
            # Calling a netref dispatches back to the client process.
            cb(message)

    def exposed_callback_count(self) -> int:
        return len(self._callbacks)


# ---- Client-side callbacks ----
def plain_function(msg: str) -> None:
    """A plain module-level function used as a callback."""
    print(f"    [client] plain_function received: {msg!r}")


class CallbackHolder:
    """A class whose instance method will be used as a callback."""

    def __init__(self, name: str = "instance"):
        self.name = name

    def method(self, msg: str) -> None:
        """An instance method used as a callback."""
        print(f"    [client] {self.name}.method received: {msg!r}")


def run_client(port: int) -> None:
    """Connect to the server, register callbacks, trigger them, then exit."""
    time.sleep(0.3)  # Wait for server to start
    conn = connect("127.0.0.1", port)

    # --- Test 1: plain function ---
    print("  [client] Registering plain_function")
    conn.root.register_callback(plain_function)

    # --- Test 2: lambda ---
    print("  [client] Registering lambda")
    conn.root.register_callback(lambda msg: print(f"    [client] lambda received: {msg!r}"))

    # --- Test 3: instance method ---
    holder = CallbackHolder("CallbackHolder")
    print("  [client] Registering instance method")
    conn.root.register_callback(holder.method)

    # Fire all callbacks from the server
    print("  [client] Requesting server to fire callbacks...")
    conn.root.fire_callbacks("Hello from server!")

    # Wait a moment for netref responses to arrive
    time.sleep(0.3)
    print(f"  [client] Callbacks registered on server: {conn.root.callback_count()}")
    conn.close()
    print("  [client] Done.")


def run_server(port: int) -> None:
    """Start the RPyC server and keep it alive briefly for the client."""
    server = ThreadedServer(CallbackService, port=port, auto_register=False)
    # Schedule a stop after 5 seconds so the test doesn't hang
    timer = threading.Timer(5.0, server.close)
    timer.start()
    print(f"  [server] Listening on 127.0.0.1:{port}")
    server.start()


def main() -> None:
    print("=== PoC 1: Netref Callbacks ===")
    PORT = 18871

    server_thread = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    server_thread.start()

    client_thread = threading.Thread(target=run_client, args=(PORT,), daemon=True)
    client_thread.start()

    client_thread.join(timeout=6)
    server_thread.join(timeout=1)
    print("=== PoC 1 Complete ===")


if __name__ == "__main__":
    main()
