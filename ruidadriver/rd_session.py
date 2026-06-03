"""L5 Ruida Session — thin wrapper around RdTransport and RdStatus.

RdSession provides the top-level session API for upper layers:
- connect/disconnect/shutdown lifecycle
- is_connected, is_usb, is_udp property delegation
"""

from __future__ import annotations

import threading
from typing import Optional

from ruidadriver.rd_transport import RdTransport
from ruidadriver.rd_status import RdStatus, RdStatusEvent


class RdSession:
    """Ruida Session Layer (L5) — manages a transport+status session.

    Combines transport configuration/connection (L4) with status monitoring
    (L5) into a single session API used by upper layers (L6-L7).

    Usage::
        session = RdSession()
        session.transport.configure(udp_host='192.168.1.100')
        session.connect(timeout=5000)
        # ... use session.transport.write(...) ...
        session.disconnect()
    """

    def __init__(self) -> None:
        self.transport = RdTransport()
        self.status = RdStatus(transport=self.transport)

        # Synchronization
        self._lock = threading.Lock()
        self._connected_event = threading.Event()

    def connect(self, timeout: int = 1000) -> bool:
        """Open transport and start status monitoring.

        Opens the transport, starts RdStatus, then waits for CONNECTED event
        within the given timeout. If already connected, returns True (idempotent).

        Args:
            timeout: ms to wait for CONNECTED event (default 1000).

        Returns:
            True if connected successfully, False on timeout/failure.
        """
        if self.is_connected:
            return True

        self._connected_event.clear()

        # Register temporary listener to catch CONNECTED
        def _on_status(event: RdStatusEvent) -> None:
            if event is RdStatusEvent.CONNECTED:
                self._connected_event.set()
            elif event is RdStatusEvent.TERMINATED:
                self._connected_event.set()  # Unblock on termination too

        self.status.register_status_listener(_on_status)

        try:
            # Open transport
            if not self.transport.open():
                return False

            # Start status monitor
            self.status.start()

            # Wait for CONNECTED event
            if not self._connected_event.wait(timeout / 1000.0):
                # Timeout — clean up and return False
                self.disconnect()
                return False

            return True
        finally:
            # Remove the temporary listener using public API
            self.status.unregister_status_listener(_on_status)

    def disconnect(self) -> None:
        """Stop status monitoring and close transport. Idempotent."""
        self.status.stop()
        self.transport.close()

    def shutdown(self) -> None:
        """Cleanup alias for disconnect()."""
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        """True when transport is open AND RdStatus has confirmed connection."""
        return self.status.is_connected

    @property
    def is_usb(self) -> bool:
        """Delegate to transport.is_usb."""
        return self.transport.is_usb

    @property
    def is_udp(self) -> bool:
        """Delegate to transport.is_udp."""
        return self.transport.is_udp
