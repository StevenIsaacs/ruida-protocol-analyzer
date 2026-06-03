"""
L7 RdsAdapter — Textual-based TUI for interactive Ruida script execution.

Provides a terminal user interface for connecting to Ruida laser controllers,
executing rpascript commands interactively, and monitoring status/reply events
in real-time via the AppAdapter → RdDriver → RdSession stack.
"""

from __future__ import annotations

from typing import Any

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
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "load_script", "Load"),
        ("ctrl+e", "execute_script", "Exec"),
        ("f1", "show_help", "Help"),
        ("ctrl+c", "clear_log", "Clear"),
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
        self._loaded_script: list[str] | None = None

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
    # Actions (key bindings)
    # ------------------------------------------------------------------

    def action_load_script(self) -> None:
        """Load a .rds script file (stub — full FilePicker in future version)."""
        self._log_info("Load script: not implemented in this version (planned for enhancement)")

    def action_execute_script(self) -> None:
        """Execute the loaded script against the active session."""
        if self._loaded_script is None:
            self._log_error("No script loaded. Use Ctrl+L to load one first.")
            return
        if self._driver is None:
            self._log_error("No active session. Use 'session start udp=...' first.")
            return
        self._log_info(f"Executing script ({len(self._loaded_script)} lines)...")
        try:
            self._driver.run(self._loaded_script)
            self._script_count += len(self._loaded_script)
            self._update_counters()
        except RuntimeError as e:
            self._log_error(str(e))

    def action_show_help(self) -> None:
        """Show help information in the log area."""
        self._log_widget.write("")
        self._log_widget.write("[bold]Ruida Script TUI Help[/bold]")
        self._log_widget.write("  session start udp=<IP>  — Connect to controller via UDP")
        self._log_widget.write("  session start usb=<dev> — Connect via USB")
        self._log_widget.write("  session end             — Disconnect from controller")
        self._log_widget.write("  <rpascript command>     — Execute any rpascript command")
        self._log_widget.write("  Ctrl+Q  Quit the application")
        self._log_widget.write("  Ctrl+L  Load a .rds script file")
        self._log_widget.write("  Ctrl+E  Execute the loaded script")
        self._log_widget.write("  F1      Show this help information")
        self._log_widget.write("  Ctrl+C  Clear the log area")
        self._log_widget.write("")

    def action_clear_log(self) -> None:
        """Clear the main log area."""
        self._log_widget.clear()

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
        when the user quits (Ctrl+Q) without explicitly running 'session end'.
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
    Blocks until the user quits (Ctrl+Q).
    """
    app = RdsAdapter()
    app.run()
