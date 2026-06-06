"""
L6 Ruida Driver — command encoding, queued script execution, and status monitoring integration.

RdDriver provides:
- Script interpretation via Background Script Runner (queue-based daemon thread)
- Encoded command transmission through the Session (L5)
- Status and reply listener infrastructure forwarding to application callbacks
- Internal machine status tracking (position, status bits, card ID)
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from rpascript.encoding import (
    encode_command,
    is_set_file_sum,
    parse_value,
    should_include_in_checksum,
)
from rpascript.interpreter import ScriptParser
from rpalib.ruida_transcoder import RdDecoder, RdEncoder
from ruidadriver.rd_session import RdSession
from ruidadriver.rd_status import RdStatusEvent
from ruidadriver.rd_transport import RdTransport
import protocols.ruida.ruida_protocol as rdap


class RdDriver:
    """Ruida Driver Layer (L6) — script interpretation and background execution.

    Manages script execution via a background daemon thread, with queued
    command transmission, connection-aware retry, and status/reply event
    forwarding to registered application listeners.

    Usage::
        driver = RdDriver(session)
        driver.start_script_runner()
        driver.run(['GET_SETTING MEM_CARD_ID'])
        # ... script executes in background ...
        driver.stop_script_runner()
    """

    # Ping command — MEM_CARD_ID reply detects controller changes
    _PING_SCRIPT = ['GET_SETTING MEM_CARD_ID']

    # Query command segment — sent at configured query_interval
    _QUERY_SCRIPT = [
        'GET_SETTING MEM_MACHINE_STATUS',
        'GET_SETTING MEM_CURRENT_POSITION_X',
        'GET_SETTING MEM_CURRENT_POSITION_Y',
        'GET_SETTING MEM_CURRENT_POSITION_Z',
        'GET_SETTING MEM_CURRENT_POSITION_U',
    ]

    # Commands triggered on MEM_CARD_ID reply
    _BED_SIZE_SCRIPT = [
        'GET_SETTING MEM_BED_SIZE_X',
        'GET_SETTING MEM_BED_SIZE_Y',
    ]

    # Machine status bit name → mask mapping (used by _handle_wait)
    _STATUS_NAME_TO_BIT = {
        'MACHINE_STATUS_MOVING': rdap.MACHINE_STATUS_MOVING[0],
        'MACHINE_STATUS_PART_END': rdap.MACHINE_STATUS_PART_END[0],
        'MACHINE_STATUS_JOB_RUNNING': rdap.MACHINE_STATUS_JOB_RUNNING[0],
    }

    def __init__(self, session: RdSession) -> None:
        """Initialize RdDriver.

        Args:
            session: RdSession instance providing transport and status access.
        """
        self._session = session
        self._script_queue: queue.Queue = queue.Queue()
        self._runner_thread: threading.Thread | None = None
        self._status_listeners: list[Callable] = []
        self._error_listeners: list[Callable[[str], None]] = []
        self._reply_listeners: list[Callable] = []
        self._lock: threading.RLock = threading.RLock()
        self._shutdown: threading.Event = threading.Event()
        self._cancel_flag: bool = False
        self._machine_status: dict[int, Any] = {}

    # ---- Listener Registration ----

    def register_status_listener(self, listener: Callable[[RdStatusEvent], None]) -> None:
        """Register a listener for RdStatusEvent notifications. Thread-safe."""
        with self._lock:
            self._status_listeners.append(listener)

    def register_error_listener(self, listener: Callable[[str], None]) -> None:
        """Register a listener for error message notifications. Thread-safe."""
        with self._lock:
            self._error_listeners.append(listener)

    def register_reply_listener(self, listener: Callable[[list[bytearray]], None]) -> None:
        """Register a listener for raw reply data notifications. Thread-safe."""
        with self._lock:
            self._reply_listeners.append(listener)

    # ---- Internal Callbacks ----

    def _on_status_event(self, event: RdStatusEvent) -> None:
        """Forward RdStatus events to registered listeners. Thread-safe via copy-on-iterate."""
        with self._lock:
            listeners = list(self._status_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                pass  # Isolate bad callbacks

    def _on_reply(self, replies: list[bytearray]) -> None:
        """Internal reply handler: decode for status tracking, then forward to reply listeners.

        Decodes each GET_SETTING reply bytearray to extract address and value,
        updates internal machine status tracking, fires MACHINE_STATUS_* events,
        and queues BED_SIZE_SCRIPT on MEM_CARD_ID replies.
        """
        decoder = RdDecoder()
        for raw_reply in replies:
            address = decoder.decode_address(raw_reply)  # e.g., 0x0400
            value = decoder.decode_value(raw_reply)      # e.g., 3 for machine status
            with self._lock:
                self._machine_status[address] = value
            # Parse machine status bits into individual events (outside _lock to avoid callback reentrancy)
            if address == 0x0400:  # MEM_MACHINE_STATUS (rd_mt convention: msb<<8|lsb)
                for event in self._parse_machine_status(value):
                    self._on_status_event(event)
            # On MEM_CARD_ID reply, queue bed size queries
            if address == 0x057E:  # MEM_CARD_ID (rd_mt convention: msb<<8|lsb)
                self.run(self._BED_SIZE_SCRIPT)
        # Forward raw reply data to registered reply listeners
        with self._lock:
            listeners = list(self._reply_listeners)
        for listener in listeners:
            try:
                listener(replies)
            except Exception:
                pass  # Isolate bad callbacks

    def _parse_machine_status(self, value: int) -> list[RdStatusEvent]:
        """Parse MEM_MACHINE_STATUS value into RdStatusEvent list."""
        events = []
        if value & rdap.MACHINE_STATUS_MOVING[0]:
            events.append(RdStatusEvent.MACHINE_STATUS_MOVING)
        if value & rdap.MACHINE_STATUS_PART_END[0]:
            events.append(RdStatusEvent.MACHINE_STATUS_PART_END)
        if value & rdap.MACHINE_STATUS_JOB_RUNNING[0]:
            events.append(RdStatusEvent.MACHINE_STATUS_JOB_RUNNING)
        return events

    # ---- Script Runner Lifecycle ----

    def start_script_runner(self) -> None:
        """Start the background script runner thread and register session listeners.

        Configures RdStatus with ping/query commands, then starts the status monitor.
        Idempotent — no-op if runner is already alive.

        Order is critical:
        1. Configure ping/query commands (harmless before status starts)
        2. Start the runner thread (so self.run() is safe when listeners fire)
        3. Register session listeners (runner is already running)
        4. Start the status monitor LAST (replies arrive to a fully-initialized driver)
        """
        if self._runner_thread and self._runner_thread.is_alive():
            return

        self._shutdown.clear()
        self._cancel_flag = False

        # 1. Configure RdStatus with ping/query commands (before starting anything)
        parser = ScriptParser()

        ping_parsed = parser.parse_lines(self._PING_SCRIPT)
        ping_binary = encode_command(ping_parsed[0], parser.mnemonic_map, parser.mt_map, RdEncoder())
        self._session.status.set_ping_command(ping_binary)

        query_parsed = parser.parse_lines(self._QUERY_SCRIPT)
        query_binary = [encode_command(cmd, parser.mnemonic_map, parser.mt_map, RdEncoder())
                        for cmd in query_parsed]
        self._session.status.set_query_commands(query_binary)

        # 2. Start the runner thread BEFORE registering listeners,
        #    so self.run() is safe as soon as any listener fires.
        self._runner_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._runner_thread.start()

        # 3. Register session listeners (runner is ready)
        self._session.status.register_status_listener(self._on_status_event)
        self._session.transport.register_reply_listener(self._on_reply)

        # 4. Start the status monitor LAST — from this point, replies can arrive
        #    and will be handled by a fully-initialized driver
        self._session.status.start()

    def stop_script_runner(self) -> None:
        """Stop the background script runner thread and unregister session listeners.

        Sends shutdown sentinel, joins thread (2s timeout), and unregisters listeners.
        Idempotent — no-op if already stopped.
        """
        if self._runner_thread is None:
            return

        self._shutdown.set()
        self._script_queue.put(None)  # Sentinel to unblock get()

        self._runner_thread.join(timeout=2.0)

        # Clean up listeners
        self._session.status.unregister_status_listener(self._on_status_event)
        self._session.transport.unregister_reply_listener(self._on_reply)

        self._runner_thread = None

    # ---- Background Script Runner ----

    def _run_loop(self) -> None:
        """Background script runner core loop.

        Waits for scripts on the queue, parses them, encodes commands,
        and sends via the session transport. Handles connection guard,
        error recovery, and shutdown.
        """
        encoder = RdEncoder()
        while not self._shutdown.is_set():
            try:
                script = self._script_queue.get()
                if script is None:
                    break  # Sentinel shutdown

                parser = ScriptParser()
                parsed = parser.parse_lines(script)
                encoded = []
                file_checksum = 0
                set_file_sum_idx: int | None = None     # index in `encoded` for the placeholder
                set_file_sum_value = None # parsed value if present

                for cmd in parsed:
                    if cmd.get('type') == 'NEW_PACKET':
                        continue
                    if cmd.get('type') == 'DELAY':
                        self._handle_delay(cmd)
                        continue
                    if cmd.get('type') == 'WAIT':
                        self._handle_wait(cmd)
                        continue
                    raw = encode_command(cmd, parser.mnemonic_map, parser.mt_map, encoder)
                    if not raw:
                        continue

                    if is_set_file_sum(cmd, parser.mnemonic_map):
                        if set_file_sum_value is not None or set_file_sum_idx is not None:
                            raise ValueError("Duplicate SET_FILE_SUM — at most one per file")
                        if cmd['params']:
                            set_file_sum_value = parse_value(cmd['params'][0], 'checksum', 'uint_35')
                        else:
                            # Omitted: extend raw with placeholder bytes for later fill
                            raw.extend(b'\x00' * 5)
                            set_file_sum_idx = len(encoded)
                        encoded.append(raw)
                        # DO NOT include SET_FILE_SUM bytes in file_checksum
                    elif should_include_in_checksum(cmd, parser.mnemonic_map):
                        file_checksum += sum(raw)
                        encoded.append(raw)
                    else:
                        encoded.append(raw)

                # Post-loop: verify or fill SET_FILE_SUM
                if set_file_sum_value is not None:
                    if file_checksum != set_file_sum_value:
                        raise ValueError(
                            f"SET_FILE_SUM value {set_file_sum_value} does not match "
                            f"accumulated file checksum {file_checksum}"
                        )
                elif set_file_sum_idx is not None:
                    # Fill omitted checksum: encode value, patch the placeholder bytearray
                    encoded_sum = encoder.encode_uint35(file_checksum)
                    raw_sfs = encoded[set_file_sum_idx]
                    raw_sfs[-5:] = encoded_sum  # last 5 bytes are the uint35 placeholder

                if encoded and self._session.is_connected:
                    with self._lock:
                        if self._cancel_flag:
                            self._cancel_flag = False  # Consume even on success
                    self._session.transport.write(encoded)
                elif encoded and not self._session.is_connected:
                    with self._lock:
                        if self._cancel_flag:
                            self._cancel_flag = False
                            continue  # Drop script, don't requeue
                    # Not connected: requeue script for retry, notify via status listener
                    self._script_queue.put(script)
                    self._notify_script_skipped()
            except Exception as exc:
                # Log error, notify, continue to next script
                self._notify_script_error(str(exc))

    # ---- Script Execution API ----

    def run(self, script: list[str]) -> None:
        """Queue a script for background execution.

        Args:
            script: List of rpascript-formatted command lines.

        Raises:
            RuntimeError: If script runner is not started.
        """
        with self._lock:
            if self._runner_thread is None or not self._runner_thread.is_alive():
                raise RuntimeError("Script runner not started. Call start_script_runner() first.")
            if not script:
                return  # Empty script is a no-op
            self._script_queue.put(script)

    def cancel_script(self) -> None:
        """Cancel all queued scripts and prevent current script from requeuing.

        Clears the script queue and sets a flag so the current _run_loop
        iteration skips requeuing the script on disconnect.
        """
        with self._lock:
            while not self._script_queue.empty():
                try:
                    self._script_queue.get_nowait()
                except queue.Empty:
                    break
            self._cancel_flag = True

    # ---- Error / Skip Notification ----

    def _notify_script_error(self, message: str) -> None:
        """Notify listeners that a script encountered an encoding/parsing error.

        Iterates snapshot of _status_listeners with try/except per callback.
        Also forwards the error message to registered error listeners.
        """
        with self._lock:
            listeners = list(self._status_listeners)
            error_listeners = list(self._error_listeners)
        for listener in listeners:
            try:
                listener(RdStatusEvent.SCRIPT_ERROR)
            except Exception:
                pass
        for listener in error_listeners:
            try:
                listener(message)
            except Exception:
                pass

    def _notify_script_skipped(self) -> None:
        """Notify listeners that a script was skipped due to disconnect.

        Uses existing DISCONNECTED event — no new RdStatusEvent member needed.
        """
        with self._lock:
            listeners = list(self._status_listeners)
        for listener in listeners:
            try:
                listener(RdStatusEvent.DISCONNECTED)
            except Exception:
                pass

    # ---- Flow-Control Handlers ----

    @staticmethod
    def _parse_timeout(to_str: str) -> float:
        """Parse time spec like '5s' or '5000ms' into seconds (float)."""
        s = to_str.strip()
        # Remove internal whitespace between number and unit
        s = ''.join(s.split())
        if s.endswith('ms'):
            seconds = float(s[:-2]) / 1000.0
        elif s.endswith('s'):
            seconds = float(s[:-1])
        else:
            raise ValueError(
                f"Invalid time format: '{to_str}'. Use e.g., 5s, 500ms"
            )
        if seconds <= 0:
            raise ValueError(
                f"Timeout must be positive, got '{to_str}'"
            )
        return seconds

    def _resolve_status_bit(self, status_name: str) -> int | None:
        """Resolve a MACHINE_STATUS_* name to its bit mask.

        Only MACHINE_STATUS_* names are supported.
        """
        return self._STATUS_NAME_TO_BIT.get(status_name)

    def _handle_delay(self, cmd: dict) -> None:
        """Handle a DELAY flow-control command: sleep for specified time."""
        params = cmd.get('params', [])
        if not params:
            self._notify_script_error("DELAY requires a time argument")
            return
        try:
            seconds = self._parse_timeout(params[0])
        except ValueError as e:
            self._notify_script_error(str(e))
            return
        # Sleep with shutdown check (interruptible)
        deadline = time.monotonic() + seconds
        while not self._shutdown.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.1))

    def _handle_wait(self, cmd: dict) -> None:
        """Handle a WAIT flow-control command: poll machine status bit.

        Wait for a MACHINE_STATUS_* bit to become active (set), or if
        prefixed with ``!``, wait for the full lifecycle: active then inactive.

        Supports optional to=<timeout> parameter (e.g. '30s', '5000ms').
        """
        params = cmd.get('params', [])
        if not params:
            self._notify_script_error("WAIT requires a status argument")
            return

        status_token = params[0]
        invert = status_token.startswith('!')
        status_name = status_token[1:] if invert else status_token

        bit_mask = self._resolve_status_bit(status_name)
        if bit_mask is None:
            self._notify_script_error(
                f"Unknown machine status: '{status_name}'. "
                f"Use MACHINE_STATUS_MOVING, MACHINE_STATUS_PART_END, "
                f"or MACHINE_STATUS_JOB_RUNNING"
            )
            return

        # Parse optional timeout
        timeout = None
        to_str = cmd.get('to')
        if to_str is not None:
            try:
                timeout = self._parse_timeout(to_str)
            except ValueError as e:
                self._notify_script_error(str(e))
                return

        deadline = None if timeout is None else time.monotonic() + timeout

        if invert:
            # Invert mode: wait for bit to become ACTIVE, then INACTIVE
            # First check if already active — if so, skip the 'wait for set' phase
            with self._lock:
                current = self._machine_status.get(0x0400, 0)
            if not (current & bit_mask):
                # Phase 1: wait for 0→1 transition
                while not self._shutdown.is_set():
                    if deadline and time.monotonic() >= deadline:
                        self._notify_script_error(
                            f"Timeout waiting for {status_token}"
                        )
                        return
                    with self._lock:
                        current = self._machine_status.get(0x0400, 0)
                    if current & bit_mask:
                        break
                    time.sleep(0.05)
            # Phase 2: wait for 1→0 transition
            while not self._shutdown.is_set():
                if deadline and time.monotonic() >= deadline:
                    # Not an error — the job had started and the deadline
                    # applies to the total lifecycle
                    return
                with self._lock:
                    current = self._machine_status.get(0x0400, 0)
                if not (current & bit_mask):
                    break
                time.sleep(0.05)
        else:
            # Normal mode: wait for bit to become SET
            while not self._shutdown.is_set():
                if deadline and time.monotonic() >= deadline:
                    self._notify_script_error(
                        f"Timeout waiting for {status_token}"
                    )
                    return
                with self._lock:
                    current = self._machine_status.get(0x0400, 0)
                if current & bit_mask:
                    break
                time.sleep(0.05)

    # ---- Properties ----

    @property
    def session(self) -> RdSession:
        """The underlying RdSession instance."""
        return self._session

    @property
    def machine_status(self) -> dict[int, Any]:
        """Current machine status dict (address → decoded value). Read-only snapshot."""
        with self._lock:
            return dict(self._machine_status)
