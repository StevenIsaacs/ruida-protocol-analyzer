"""
L7 RdsAdapter — Textual-based TUI for interactive Ruida script execution.

Provides a terminal user interface for connecting to Ruida laser controllers,
executing rpascript commands interactively, and monitoring status/reply events
in real-time via the AppAdapter → RdDriver → RdSession stack.
"""

from __future__ import annotations

from typing import Any, Callable

import ast
import functools
import inspect

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from rpascript.interpreter import ScriptParser, reconstruct_script_line
from rpalib.ruida_transcoder import RdDecoder
from ruidadriver.ruida_driver import RdDriver
from ruidadriver.rd_session import RdSession
from ruidadriver.rd_status import RdStatusEvent


class RdsAdapter(App):
    """Textual-based TUI for interactive Ruida script execution.

    Implements the AppAdapter interface (duck-typing compatible) combined with
    Textual's App (TUI framework) to provide a terminal UI for connecting to
    Ruida controllers, executing rpascript commands, and monitoring status/reply
    events in real-time.

    Usage::
        app = RdsAdapter()
        app.run()  # Blocks until user quits
    """

    TITLE = "Ruida Script TUI"
    SUB_TITLE = "Interactive Ruida Controller Interface"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    CSS = """
    #main-container {
        height: 1fr;
    }

    #log-panel {
        width: 3fr;
        border-right: solid $primary;
    }

    #log-area {
        height: 1fr;
    }

    #command-input {
        dock: bottom;
        height: 3;
    }

    #side-panel {
        width: 1fr;
    }

    #status-log {
        height: 1fr;
        border-bottom: solid $surface;
    }

    #reply-log {
        height: 1fr;
        border-bottom: solid $surface;
    }

    #counter-display {
        height: 3;
        padding: 0 1;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session: RdSession | None = None
        self._driver: RdDriver | None = None
        self._parser = ScriptParser()
        self._decoder = RdDecoder()
        self._event_count = 0
        self._reply_count = 0
        self._script_count = 0
        self._introspect_map: dict[str, Callable[[], Any]] = {
            'session':   lambda: self._session,
            'transport': lambda: self._session.transport if self._session else None,
            'driver':    lambda: self._driver,
            'status':    lambda: self._session.status if self._session else None,
            'parser':    lambda: self._parser,
            'decoder':   lambda: self._decoder,
        }

    # ------------------------------------------------------------------
    # Textual App lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Create the TUI layout widgets."""
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="log-panel"):
                yield RichLog(id="log-area", highlight=True, markup=True, max_lines=1000)
                yield Input(id="command-input", placeholder="> Enter command (session start/end, or rpascript)...")
            with Vertical(id="side-panel"):
                yield RichLog(id="status-log", highlight=True, markup=True, max_lines=50)
                yield RichLog(id="reply-log", highlight=True, markup=True, max_lines=50)
                yield Static(id="counter-display")
        yield Footer()

    def on_mount(self) -> None:
        """Widgets are ready — cache references and log startup message."""
        self._log_widget = self.query_one("#log-area", RichLog)
        self._status_log = self.query_one("#status-log", RichLog)
        self._reply_log = self.query_one("#reply-log", RichLog)
        self._counter_widget = self.query_one("#counter-display", Static)

        self._log_widget.write("[bold green]Ruida Script TUI[/bold green]")
        self._log_widget.write("Type 'session start udp=<IP>' to connect to a controller.")
        self._log_widget.write("Type 'session end' to disconnect.")
        self._log_widget.write("Type any rpascript command between session start/end.")
        self._log_widget.write("Type !<object> to inspect objects (e.g., !session, !transport._package)")
        self._log_widget.write("")
        self._update_counters()

    # ------------------------------------------------------------------
    # Command input handling
    # ------------------------------------------------------------------

    @on(Input.Submitted, "#command-input")
    async def on_command(self, event: Input.Submitted) -> None:
        """Handle command input submission from the user."""
        line = event.input.value.strip()
        event.input.clear()
        if not line:
            return

        # Introspection mode: !<path> [args...]
        if line.startswith('!'):
            result = self._handle_introspect(line[1:].strip())
            self._log_info(result)
            return

        self._log_script(line)

        # Parse the line as a single rpascript command
        parsed = self._parser.parse_lines([line])
        if not parsed:
            return

        cmd = parsed[0]
        self._script_count += 1

        if cmd['type'] == 'SESSION_START':
            await self._start_session(**cmd['params'])
        elif cmd['type'] == 'SESSION_END':
            await self._stop_session()
        else:
            if self._driver is None:
                self._log_error("No active session. Use 'session start udp=...' first.")
                return
            try:
                reconstructed = reconstruct_script_line(cmd)
                self._driver.run([reconstructed])
            except RuntimeError as e:
                self._log_error(str(e))

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _start_session(self, udp: str = '', usb: str | None = None) -> None:
        """Connect to a Ruida controller and start the script runner.

        Executes the blocking connect() call in a thread executor to avoid
        freezing the TUI event loop.
        """
        if self._session is not None:
            self._log_error("Session already active. Use 'session end' first.")
            return

        usb_device = usb if usb else ''

        try:
            session = RdSession()
            session.transport.configure(
                udp_host=udp,
                usb_device=usb_device,
            )

            self._log_info(f"Connecting (udp={udp}, usb={usb_device})...")

            # connect() blocks — run in executor to avoid freezing TUI
            success = await self.run_in_executor(
                lambda: session.connect(timeout=5000)
            )

            if not success:
                self._log_error("Connection failed: timeout (5s)")
                session.disconnect()
                return

            self._session = session
            driver = RdDriver(session)

            # Register self as listeners for status events and reply data
            driver.register_status_listener(self.on_status_event)
            driver.register_reply_listener(self.on_reply_data)

            driver.start_script_runner()
            self._driver = driver

            self._log_info("Session started successfully")
            self._update_counters()

        except Exception as e:
            self._log_error(f"Failed to start session: {e}")
            # Clean up on failure
            if self._session is not None:
                self._session.disconnect()
                self._session = None
            if self._driver is not None:
                self._driver = None

    async def _stop_session(self) -> None:
        """Disconnect from the controller and clean up resources."""
        if self._driver is None and self._session is None:
            self._log_info("No active session.")
            return

        try:
            if self._driver is not None:
                self._driver.stop_script_runner()
                self._driver = None

            if self._session is not None:
                await self.run_in_executor(lambda: self._session.disconnect())
                self._session = None

            self._log_info("Session ended")
            self._update_counters()

        except Exception as e:
            self._log_error(f"Error stopping session: {e}")
            self._session = None
            self._driver = None

    # ------------------------------------------------------------------
    # AppAdapter-compatible interface (called from driver background thread)
    # ------------------------------------------------------------------

    def on_status_event(self, event: RdStatusEvent) -> None:
        """Handle a status event from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        """
        def _update() -> None:
            self._status_log.write(f"[STATUS] {event.value}")
            self._event_count += 1
            self._update_counters()
        self.call_from_thread(_update)

    def on_reply_data(self, replies: list[bytearray]) -> None:
        """Handle reply data from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        Decodes raw reply bytearrays to human-readable address:value format.
        """
        def _update() -> None:
            for raw in replies:
                addr = self._decoder.decode_address(raw)
                val = self._decoder.decode_value(raw)
                self._reply_log.write(f"[REPLY]  0x{addr:04X}: {val}")
                self._reply_count += 1
            self._update_counters()
        self.call_from_thread(_update)

    def on_error(self, message: str) -> None:
        """Handle an error condition. Thread-safe via call_from_thread."""
        def _update() -> None:
            self._log_error(message)
        self.call_from_thread(_update)

    def run_script(self, script: list[str]) -> None:
        """Queue a script for execution.

        Thread-safe: bridges to the asyncio thread via call_from_thread
        so this method can be called from any thread.
        """
        if self._driver is None:
            def _error() -> None:
                self._log_error("No active session to run script.")
            self.call_from_thread(_error)
            return

        def _run() -> None:
            try:
                self._driver.run(script)
                self._script_count += len(script)
                self._update_counters()
            except RuntimeError as e:
                self._log_error(str(e))
        self.call_from_thread(_run)

    # ------------------------------------------------------------------
    # Introspection (!) subsystem
    # ------------------------------------------------------------------

    def _resolve_path(self, path: str) -> tuple[Any, str | None]:
        """Resolve a dotted path against the introspection object map.

        Returns (resolved_object, error_message).
        On success, error_message is None.
        On failure, resolved_object is None and error_message describes the issue.
        """
        # Handle 'self.' prefix for RdsAdapter itself
        if path.startswith('self.'):
            obj = self
            remaining = path[5:]
        elif path == 'self':
            return (self, None)
        else:
            # Split off the root object name
            parts = path.split('.', 1)
            root_name = parts[0]
            try:
                obj = self._introspect_map[root_name]()
            except KeyError:
                known = ', '.join(sorted(self._introspect_map.keys()))
                return (None, f"Unknown object: {root_name}. Known: {known}")
            remaining = parts[1] if len(parts) > 1 else ''

        # Walk the attribute chain
        if remaining:
            try:
                obj = functools.reduce(getattr, remaining.split('.'), obj)
            except AttributeError as e:
                return (None, f"No such attribute: {path} ({e})")

        return (obj, None)

    def _handle_introspect(self, expr: str) -> str:
        """Handle a !-prefixed introspection expression.

        No parentheses → variable view (repr).
        With parentheses → method call with args, or signature display if no args.
        """
        expr = expr.strip()
        if not expr:
            return "Usage: !<object>[.<attribute>] [args...]"

        # Split on first '(' to detect method call
        paren_idx = expr.find('(')
        if paren_idx == -1:
            # No parens: split on space for potential args
            parts = expr.split(None, 1)
            path = parts[0]
            args_raw = parts[1] if len(parts) > 1 else ''

            obj, err = self._resolve_path(path)
            if err:
                return err

            if args_raw:
                # Space-separated args → call the method
                args = self._parse_introspect_args(args_raw)
                try:
                    result = obj(*args)
                    return repr(result)
                except TypeError as e:
                    return f"TypeError: {e}"
                except Exception as e:
                    return f"Error calling {path}: {type(e).__name__}: {e}"

            # No args → show signature for callables, repr for variables
            if callable(obj):
                return self._format_signature(obj)
            return repr(obj)

        # Method call with parens
        path = expr[:paren_idx].strip()
        args_part = expr[paren_idx+1:]

        # Find matching close paren
        if not args_part.endswith(')'):
            return "Syntax error: unclosed parenthesis"
        args_str = args_part[:-1].strip()

        obj, err = self._resolve_path(path)
        if err:
            return err

        if not callable(obj):
            return f"{path} is not callable (type: {type(obj).__name__})"

        if not args_str:
            # No arguments — show signature
            return self._format_signature(obj)

        # Parse arguments
        args = self._parse_introspect_args(args_str)

        try:
            result = obj(*args)
            return repr(result)
        except TypeError as e:
            return f"TypeError: {e}"
        except Exception as e:
            return f"Error calling {path}: {type(e).__name__}: {e}"

    def _format_signature(self, obj: Any) -> str:
        """Format an object's signature for display."""
        try:
            sig = inspect.signature(obj)
            return f"{getattr(obj, '__name__', type(obj).__name__)}{sig}"
        except (ValueError, TypeError):
            return repr(obj)

    def _parse_introspect_args(self, args_str: str) -> list[Any]:
        """Parse a comma-separated argument string into Python values.

        Tries ast.literal_eval first. Falls back to hex→bytearray conversion
        for hex-formatted strings starting with 0x.
        """
        if not args_str:
            return []

        result = []
        for arg in args_str.split(','):
            arg = arg.strip()
            if not arg:
                continue

            # Try ast.literal_eval first
            try:
                val = ast.literal_eval(arg)
                result.append(val)
                continue
            except (ValueError, SyntaxError):
                pass

            # Try hex→bytearray conversion (starts with 0x, contains only hex chars)
            clean = arg[2:] if arg.startswith('0x') else arg
            if not clean:
                continue
            try:
                if all(c in '0123456789abcdefABCDEF' for c in clean) and len(clean) % 2 == 0:
                    val = bytearray.fromhex(clean)
                    result.append(val)
                    continue
            except ValueError:
                pass

            # Fallback: treat as string
            result.append(arg)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_script(self, line: str) -> None:
        """Log a script command to the log area with [SCRIPT] prefix."""
        self._log_widget.write(f"[SCRIPT] {line}")

    def _log_info(self, message: str) -> None:
        """Log an informational message in cyan."""
        self._log_widget.write(f"[bold cyan]{message}[/bold cyan]")

    def _log_error(self, message: str) -> None:
        """Log an error message in bold red."""
        self._log_widget.write(f"[bold red]ERROR: {message}[/bold red]")

    def _update_counters(self) -> None:
        """Update the counter display in the side panel."""
        self._counter_widget.update(
            f"Events: {self._event_count}  |  Replies: {self._reply_count}  |  Scripts: {self._script_count}"
        )

    # ------------------------------------------------------------------
    # AppAdapter-compatible no-ops (TUI creates sessions on demand)
    # ------------------------------------------------------------------

    def create_driver_and_session(self) -> None:
        """AppAdapter interface — TUI creates sessions on demand via command input."""
        pass

    def start(self) -> None:
        """AppAdapter interface — Textual's App.run() handles lifecycle."""
        pass

    def stop(self) -> None:
        """AppAdapter interface — cleanup handled by on_exit or session end."""
        pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def on_exit(self) -> None:
        """Clean up active session when TUI exits.

        Ensures the driver runner is stopped and the session is disconnected
        when the user quits (Ctrl+C) without explicitly running 'session end'.
        """
        if self._driver is not None:
            self._driver.stop_script_runner()
            self._driver = None
        if self._session is not None:
            self._session.disconnect()
            self._session = None


# ------------------------------------------------------------------
# Module-level entry point
# ------------------------------------------------------------------

def run_tui() -> None:
    """Run the RdsAdapter TUI application.

    Creates an RdsAdapter instance and enters the Textual event loop.
    Blocks until the user quits (Ctrl+C).
    """
    app = RdsAdapter()
    app.run()
