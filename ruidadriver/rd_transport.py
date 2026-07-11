"""L4 Ruida Transport — wrapping UdpTransport and UsbTransport.

Provides transport-agnostic interface for upper layers (L5+).
Handles transport selection, packing/unpacking, and command/response
sequencing via a dedicated handshake thread.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional

from protocols.ruida.ruida_protocol import ACK
from rpalib.rpa_swizzler import RpaSwizzler
from ruidadriver.transport import Transport, UdpTransport, UsbTransport
from ruidadriver.transport_events import TransportEvent


class RdTransport:
    """Ruida Transport coordinator.

    Wraps UdpTransport and UsbTransport, providing unified interface
    with automatic transport selection, swizzle packing, checksumming,
    and handshake sequencing in a background thread.
    """

    _HANDSHAKE_TIMEOUT = 0.2  # 200ms — queue poll interval

    def __init__(self) -> None:
        self._udp: UdpTransport | None = None
        self._usb: UsbTransport | None = None
        self._transport: Transport | None = None

        self._udp_host = ""
        self._usb_device = ""

        self._swizzler = RpaSwizzler()
        self._chunk_size = 1024
        self._timeout = 250  # ms per-call timeout
        self._gross_timeout = 15000  # ms overall gross timeout
        self._use_gross_timeout = False

        # Queues for handshake thread
        self._send_queue: queue.Queue[bytearray] = queue.Queue(maxsize=256)
        self._shutdown_event = threading.Event()

        # Listeners
        self._listener_lock = threading.Lock()
        self._status_listeners: list[Callable] = []
        self._reply_listeners: list[Callable] = []

        # Handshake thread
        self._handshake_thread: threading.Thread | None = None

    # ---- Configuration and Connection ----

    def configure(
        self,
        magic: int = 0x88,
        chunk_size: int = 1024,
        timeout: int = 1000,
        gross_timeout: int = 15000,
    ) -> None:
        """Configure transport parameters. Must be called before open()."""
        self._swizzler.set_magic(magic)
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._gross_timeout = gross_timeout

    def open(self, udp_host: str = "", usb_device: str = "") -> bool:
        """Open the preferred transport (USB first, then UDP).

        Args:
            udp_host: UDP host address. Empty string reuses value from a previous `open()` call.
            usb_device: USB device path. Empty string reuses value from a previous `open()` call.
        """
        if udp_host:
            if self._udp is None:
                self._udp = UdpTransport()
            self._udp_host = udp_host
        if usb_device:
            if self._usb is None:
                self._usb = UsbTransport()
            self._usb_device = usb_device

        # Stop old handshake thread BEFORE opening transport (closes socket).
        # This eliminates the race where the old thread could write through a closed socket.
        self._stop_handshake_thread()

        if self._usb and self._usb.open(self._usb_device):
            self._transport = self._usb
        elif self._udp and self._udp.open(self._udp_host, 50200):
            self._transport = self._udp
        else:
            return False
        # Clear stale send queue from any previous connection
        self._send_queue = queue.Queue(maxsize=256)
        self._start_handshake_thread()
        self._notify_status(TransportEvent.OPENED)
        return True

    def close(self) -> None:
        """Shutdown handshake thread and close transport."""
        self._shutdown_event.set()
        if self._handshake_thread:
            self._handshake_thread.join(timeout=2.0)
        if self._transport:
            self._transport.close()

        # Drain any pending sends so stale data doesn't linger
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                break

        self._notify_status(TransportEvent.CLOSED)

        # Clear listener lists — prevents stale references on reuse
        self._status_listeners.clear()
        self._reply_listeners.clear()

    def drain(self) -> None:
        """Drain all pending data from the underlying transport."""
        if self._transport and self._transport.is_open:
            self._transport.drain()

    # ---- Write ----

    def write(self, commands: list[bytearray]) -> None:
        """Chunk, package, and queue encoded commands for transmission.

        Accumulates commands into a buffer until chunk_size is exceeded,
        packages the buffer (swizzle + optional checksum), then queues it
        to the handshake thread's send queue.

        Uses timeout on put() to remain responsive to shutdown signals
        when the queue is full (e.g., during large job execution).
        """
        buf = bytearray()
        for cmd in commands:
            if buf and len(buf) + len(cmd) > self._chunk_size:
                # Non-blocking put with shutdown-aware retry
                self._put_with_retry(self._package(buf))
                buf = bytearray()
            buf.extend(cmd)
        if buf:
            self._put_with_retry(self._package(buf))

    def _put_with_retry(self, packet: bytearray) -> None:
        """Put a packet on the send queue with shutdown-aware retry.

        Uses a short timeout so the thread remains responsive to
        shutdown signals when the queue is full. Raises OSError
        if the handshake thread appears to be dead (max retries
        exhausted).
        """
        MAX_RETRIES = 1000
        retries = 0
        while True:
            try:
                self._send_queue.put(packet, timeout=0.1)
                return
            except queue.Full:
                if self._shutdown_event.is_set():
                    return  # Abort enqueue during shutdown — data will be dropped
                retries += 1
                if retries >= MAX_RETRIES:
                    raise OSError(
                        "Send queue consumer appears dead after "
                        f"{MAX_RETRIES} retries"
                    )
                continue  # Retry

    # ---- Packing / Unpacking ----

    def _package(self, data: bytearray) -> bytearray:
        """Swizzle data and prepend checksum for UDP transport."""
        payload = self._swizzler.swizzle(data)
        if self._transport and self._transport.is_udp:
            chk = sum(payload) & 0xFFFF
            return bytearray([(chk >> 8) & 0xFF, chk & 0xFF]) + payload
        return payload

    def _unpack_replies(self, data: bytes) -> list[bytearray]:
        """Unswizzle received data and split into individual GET_SETTING replies."""
        raw = self._swizzler.unswizzle(bytearray(data))
        replies: list[bytearray] = []
        # Each GET_SETTING reply is 9 bytes: 0xDA + 0x01 + msb + lsb + 5 data bytes
        for i in range(0, len(raw), 9):
            chunk = raw[i : i + 9]
            if len(chunk) < 9:
                break
            if chunk[0] != 0xDA:
                self._notify_status(TransportEvent.REPLY_ERROR)
                break
            if chunk[1] != 0x01:
                # Reply starts with 0xDA but second byte is unexpected — could be
                # an undiscovered reply type; notify rather than silently dropping.
                self._notify_status(TransportEvent.UNEXPECTED_REPLY)
                break
            replies.append(chunk)
        return replies

    # ---- Handshake Thread ----

    def _stop_handshake_thread(self) -> None:
        """Shut down the handshake thread. Idempotent — safe to call multiple times.

        The shutdown event is cleared before returning so that writes during
        the transport-reopen window are not silently dropped.
        """
        if self._handshake_thread is not None and self._handshake_thread.is_alive():
            self._shutdown_event.set()
            self._handshake_thread.join(timeout=2.0)
            # Note: If join times out (thread stuck in long blocking call), the
            # event is cleared and a new thread may start before the old one
            # exits. In practice the thread checks _shutdown_event every ~5-200ms,
            # so 2s is generous.
        self._shutdown_event.clear()

    def _start_handshake_thread(self) -> None:
        """Start a new handshake thread. Does NOT stop existing thread — call _stop_handshake_thread() first."""
        self._shutdown_event.clear()
        self._handshake_thread = threading.Thread(
            target=self._handshake_loop, daemon=True
        )
        self._handshake_thread.start()

    def _handshake_loop(self) -> None:
        """Main handshake loop: IDLE -> SEND -> ACK_PENDING/REPLY_PENDING -> IDLE."""
        try:
            state = "IDLE"
            packet: bytearray | None = None
            expect_reply = False

            while not self._shutdown_event.is_set():
                if state == "IDLE":
                    try:
                        packet = self._send_queue.get(timeout=self._HANDSHAKE_TIMEOUT)
                        state = "SEND"
                    except queue.Empty:
                        continue

                elif state == "SEND":
                    try:
                        self._transport.write(packet)
                    except OSError:
                        self._notify_status(TransportEvent.DROPPED)
                        state = "IDLE"
                        continue
                    if self._transport.is_udp:
                        state = "ACK_PENDING"
                    else:
                        # USB: no ACK; check if it contains GET_SETTING commands
                        expect_reply = self._has_get_setting(packet)
                        state = "REPLY_PENDING" if expect_reply else "IDLE"

                elif state == "ACK_PENDING":
                    try:
                        data = self._wait_for_data(self._timeout)
                    except OSError:
                        self._notify_status(TransportEvent.READ_ERROR)
                        state = "IDLE"
                        continue
                    if data is None:
                        self._notify_status(TransportEvent.TIMEOUT)
                        state = "IDLE"
                        continue
                    # Validate ACK (unswizzle then compare with logical ACK byte)
                    if len(data) == 1 and self._swizzler.unswizzle_byte(data[0], self._swizzler.magic) == ACK:
                        self._notify_status(TransportEvent.ACK_RECEIVED)
                        expect_reply = self._has_get_setting(packet)
                        state = "REPLY_PENDING" if expect_reply else "IDLE"
                    else:
                        self._notify_status(TransportEvent.REPLY_ERROR)
                        state = "IDLE"

                elif state == "REPLY_PENDING":
                    try:
                        data = self._wait_for_data(self._timeout)
                    except OSError:
                        self._notify_status(TransportEvent.READ_ERROR)
                        state = "IDLE"
                        continue
                    if data is None:
                        self._notify_status(TransportEvent.TIMEOUT)
                        state = "IDLE"
                        continue
                    replies = self._unpack_replies(data)
                    if replies:
                        self._notify_reply_listeners(replies)
                        self._notify_status(TransportEvent.REPLY_FORWARDED)
                        state = "IDLE"
                    # No valid replies yet (e.g., stray ACK) — stay in REPLY_PENDING
        except Exception:
            self._shutdown_event.set()
            raise

    def _wait_for_data(self, timeout_ms: int) -> Optional[bytes]:
        """Poll transport read with per-call timeout.

        Returns:
            None on timeout or shutdown.
            bytes on successful read.
            Raises OSError on transport read error (caller handles).
        """
        if self._use_gross_timeout:
            timeout_ms = self._gross_timeout
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if self._shutdown_event.is_set():
                return None
            data = self._transport.read(65536)
            if data:
                return data
            time.sleep(0.005)  # 5ms polling interval
        return None

    def _has_get_setting(self, packet: bytearray) -> bool:
        """Check if packet contains a GET_SETTING/memory command.

        Unswizzles the payload (skipping the 2-byte UDP checksum if present)
        and looks for the 0xDA memory command prefix byte.
        """
        offset = 2 if self._transport and self._transport.is_udp else 0
        payload = packet[offset:]
        unswizzled = self._swizzler.unswizzle(bytearray(payload))
        return 0xDA in unswizzled

    def _notify_status(self, event: TransportEvent) -> None:
        for listener in list(self._status_listeners):
            listener(event)

    def _notify_reply_listeners(self, replies: list[bytearray]) -> None:
        for listener in list(self._reply_listeners):
            listener(replies)

    # ---- Listener Registration ----

    def register_status_listener(self, listener: Callable) -> None:
        with self._listener_lock:
            self._status_listeners.append(listener)

    def unregister_status_listener(self, listener: Callable) -> None:
        """Remove a previously registered status listener."""
        with self._listener_lock:
            try:
                self._status_listeners.remove(listener)
            except ValueError:
                pass

    def register_reply_listener(self, listener: Callable) -> None:
        with self._listener_lock:
            self._reply_listeners.append(listener)

    def unregister_reply_listener(self, listener: Callable) -> None:
        """Remove a previously registered reply listener. Thread-safe via _listener_lock."""
        with self._listener_lock:
            try:
                self._reply_listeners.remove(listener)
            except ValueError:
                pass

    # ---- Properties ----

    @property
    def is_open(self) -> bool:
        return self._transport is not None and self._transport.is_open

    @property
    def is_usb(self) -> bool:
        return self._transport is not None and self._transport.is_usb

    @property
    def is_udp(self) -> bool:
        return self._transport is not None and self._transport.is_udp

    @property
    def has_usb(self) -> bool:
        return self._usb is not None

    def set_gross_timeout(self, state: bool) -> None:
        self._use_gross_timeout = state
