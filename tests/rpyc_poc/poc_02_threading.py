"""
PoC 2: Background Threading in RPyC Services

Demonstrates that an RPyC service method can start a background thread
that outlives the service method call. This is critical for TuiAdapter
because RdDriver.run() executes in a background thread.

The pattern:
  1. Client calls server.start_background_work()
  2. Server spawns a threading.Thread and returns immediately
  3. The background thread does work independently
  4. Client can poll for completion via another exposed method
"""

from __future__ import annotations

import threading
import time

from rpyc import Service
from rpyc.utils.factory import connect
from rpyc.utils.server import ThreadedServer


class ThreadingService(Service):
    """Service that can start background worker threads."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bg_result: str | None = None
        self._bg_lock = threading.Lock()
        self._bg_thread_count = 0
        self._bg_threads: list[threading.Thread] = []

    def _worker(self, worker_id: int, delay: float) -> None:
        """Background task: sleep, then store a result."""
        print(f"  [server] Worker {worker_id} started (will sleep {delay}s)")
        time.sleep(delay)
        with self._bg_lock:
            self._bg_result = f"worker_{worker_id}_done"
            self._bg_thread_count -= 1
        print(f"  [server] Worker {worker_id} finished")

    def exposed_start_background_work(self, delay: float = 0.5) -> int:
        """Start a background thread and return immediately.

        Returns the worker ID so the client can track it.
        """
        with self._bg_lock:
            self._bg_thread_count += 1
            worker_id = self._bg_thread_count

        t = threading.Thread(
            target=self._worker,
            args=(worker_id, delay),
            daemon=True,
        )
        self._bg_threads.append(t)
        t.start()
        print(f"  [server] Started worker {worker_id} (daemon={t.daemon})")
        return worker_id

    def exposed_get_result(self) -> str | None:
        """Return the most recent background result (polled by client)."""
        with self._bg_lock:
            return self._bg_result

    def exposed_get_active_count(self) -> int:
        """Return how many background threads are still running."""
        with self._bg_lock:
            return self._bg_thread_count


def run_client(port: int) -> None:
    """Connect, start background work, poll for completion, verify."""
    time.sleep(0.3)
    conn = connect("127.0.0.1", port)

    print("  [client] Calling start_background_work(delay=0.5)...")
    wid = conn.root.start_background_work(0.5)
    print(f"  [client] Server returned immediately with worker_id={wid}")

    # The call returned immediately — the worker runs in the background.
    # Poll until the result appears.
    print("  [client] Polling for background result...")
    deadline = time.monotonic() + 5.0
    result = None
    while time.monotonic() < deadline:
        result = conn.root.get_result()
        if result is not None:
            break
        time.sleep(0.1)

    if result is None:
        print("  [client] FAIL: Background work did not complete in time!")
    else:
        print(f"  [client] OK: Background result: {result!r}")

    active = conn.root.get_active_count()
    print(f"  [client] Active background threads: {active}")
    conn.close()
    print("  [client] Done.")


def run_server(port: int) -> None:
    """Start the RPyC server."""
    server = ThreadedServer(ThreadingService, port=port, auto_register=False)
    timer = threading.Timer(5.0, server.close)
    timer.start()
    print(f"  [server] Listening on 127.0.0.1:{port}")
    server.start()


def main() -> None:
    print("=== PoC 2: Background Threading ===")
    PORT = 18872

    server_thread = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    server_thread.start()

    client_thread = threading.Thread(target=run_client, args=(PORT,), daemon=True)
    client_thread.start()

    client_thread.join(timeout=6)
    server_thread.join(timeout=1)
    print("=== PoC 2 Complete ===")


if __name__ == "__main__":
    main()
