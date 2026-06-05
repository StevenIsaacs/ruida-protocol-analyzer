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
import os

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Footer, Header, Input, RichLog, Static

from rpascript.interpreter import ScriptParser, reconstruct_script_line
from rpalib.ruida_transcoder import RdDecoder
from ruidadriver.ruida_driver import RdDriver
from ruidadriver.rd_session import RdSession
from ruidadriver.rd_status import RdStatusEvent

from protocols.ruida.ruida_protocol import MT

import asyncio


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

    _SLASH_COMMANDS: tuple[str, ...] = ('help', 'load', 'exec', 'clear', 'quit', 'log')
    _NORMAL_COMMANDS: tuple[str, ...] = ('session',)

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

    #status-log, #reply-log {
        text-style: dim;
    }

    #status-log {
        height: 1fr;
        border-bottom: solid $surface;
    }

    #reply-log {
        height: 1fr;
        border-bottom: solid $surface;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $surface;
        text-style: dim;
    }

    #suggest-popup {
        height: auto;
        max-height: 10;
        border-top: solid $primary;
        background: $panel;
        overflow-y: auto;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session: RdSession | None = None
        self._ruida_driver: RdDriver | None = None
        self._parser = ScriptParser()
        self._decoder = RdDecoder()
        self._event_count = 0
        self._reply_count = 0
        self._script_count = 0
        self._logging_enabled: bool = True
        self._introspect_map: dict[str, Callable[[], Any]] = {
            'session':   lambda: self._session,
            'transport': lambda: self._session.transport if self._session else None,
            'driver':    lambda: self._ruida_driver,
            'status':    lambda: self._session.status if self._session else None,
            'parser':    lambda: self._parser,
            'decoder':   lambda: self._decoder,
        }
        self._loaded_script: list[str] = []
        self._suggest_popup = RichLog(
            id="suggest-popup", highlight=True, markup=True, max_lines=10
        )
        self._cmd_descriptions: dict[str, str] = {
            'help': 'Show help text',
            'load': 'Load a script file from disk',
            'exec': 'Execute the loaded script',
            'clear': 'Clear all log panels and loaded script',
            'quit': 'Exit the TUI',
            'log': 'Toggle display of status/reply messages (on|off|status)',
            'session': 'Start or end a controller session (start udp=<IP> usb=<device> / end)',
        }
        self._command_history: list[str] = []
        self._history_index: int | None = None
        self._position: dict[str, float | str | None] = {'X': None, 'Y': None, 'Z': None, 'U': None, 'Card': None, 'BedX': None, 'BedY': None}
        self._session_disconnected: bool = False
        self._last_cmd_was_get_setting: bool = False

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
        yield Static(id="status-bar")

    def on_mount(self) -> None:
        """Widgets are ready — cache references and log startup message."""
        self._log_widget = self.query_one("#log-area", RichLog)
        self._status_log = self.query_one("#status-log", RichLog)
        self._reply_log = self.query_one("#reply-log", RichLog)
        self._status_bar = self.query_one("#status-bar", Static)
        self._update_status_bar()

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

        # Add to command history (skip consecutive duplicates)
        if self._command_history and self._command_history[-1] == line:
            pass  # consecutive duplicate, skip
        else:
            self._command_history.append(line)
            if len(self._command_history) > 500:
                self._command_history.pop(0)
        self._history_index = None  # reset browsing position

        if self._suggest_popup.is_attached:
            self._suggest_popup.remove()

        # Introspection mode: !<path> [args...]
        if line.startswith('!'):
            result = self._handle_introspect(line[1:].strip())
            self._log_info(result)
            return
        # Help shortcut: ? as first character
        if line == '?':
            self._log_info(self._handle_help())
            return
        # Slash-prefixed TUI commands
        if line.startswith('/'):
            self._handle_slash_command(line)
            return
        self._log_script(line)

        # Clear stale flag to prevent misattribution of QUERY replies
        self._last_cmd_was_get_setting = False

        try:
            # Parse the line as a single rpascript command
            parsed = self._parser.parse_lines([line])
            if not parsed:
                return

            cmd = parsed[0]
            self._script_count += 1

            # Only track GET_SETTING commands with valid, resolvable addresses
            # to prevent stale flag from showing QUERY replies as GET_SETTING results
            if cmd.get('mnemonic', '') == 'GET_SETTING':
                params = cmd.get('params', [])
                if params and self._is_resolvable_address(params[0]):
                    self._last_cmd_was_get_setting = True
                else:
                    reason = f"unknown address: {params[0]}" if params else "missing address"
                    self._log_error(f"Invalid GET_SETTING: {reason}")
                    return
            else:
                self._last_cmd_was_get_setting = False

            if cmd['type'] == 'SESSION_START':
                await self._start_session(**cmd['params'])
            elif cmd['type'] == 'SESSION_END':
                await self._stop_session()
            else:
                if self._ruida_driver is None:
                    self._log_error("No active session. Use 'session start udp=<IP> usb=<device>' first.")
                    return
                try:
                    reconstructed = reconstruct_script_line(cmd)
                    self._ruida_driver.run([reconstructed])
                except RuntimeError as e:
                    self._log_error(str(e))
        except Exception as e:
            self._log_error(f"{type(e).__name__}: {e}")

    @on(Input.Changed, "#command-input")
    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/filter command popup as user types."""
        value = event.value

        # Slash commands
        if value.startswith('/'):
            prefix = value[1:].strip()
            if not prefix:
                matches = list(self._SLASH_COMMANDS)
            else:
                matches = [c for c in self._SLASH_COMMANDS if c.startswith(prefix.lower())]

            if not self._suggest_popup.is_attached:
                self.query_one("#log-panel").mount(self._suggest_popup, before="#command-input")
            self._suggest_popup.clear()
            if matches:
                self._suggest_popup.write("[bold]Commands:[/bold]")
                for cmd in matches:
                    self._suggest_popup.write(f"  /{cmd:<12} {self._cmd_descriptions[cmd]}")
            else:
                self._suggest_popup.write("[dim]No matching commands[/dim]")
            return

        # Normal commands (not introspection, not help query)
        if value and not value.startswith(('!', '?')):
            clean = value.strip().lower()

            if ' ' in clean:
                # Space detected — lock to the matched command root, no more filtering
                root_cmd = clean.split(' ', 1)[0]
                if root_cmd in self._NORMAL_COMMANDS:
                    matches = [root_cmd]
                else:
                    matches = []
            else:
                # No space — filter by prefix match on the first word
                matches = [c for c in self._NORMAL_COMMANDS if c.startswith(clean)]

            if matches:
                if not self._suggest_popup.is_attached:
                    self.query_one("#log-panel").mount(self._suggest_popup, before="#command-input")
                self._suggest_popup.clear()
                self._suggest_popup.write("[bold]Commands:[/bold]")
                for cmd in matches:
                    self._suggest_popup.write(f"  {cmd:<12} {self._cmd_descriptions[cmd]}")
                return

        # No popup needed — remove if attached
        if self._suggest_popup.is_attached:
            self._suggest_popup.remove()

    # ------------------------------------------------------------------
    # Command history (Up/Down navigation)
    # ------------------------------------------------------------------

    @on(Key)
    def on_command_key(self, event: Key) -> None:
        """Navigate command history with Up/Down arrow keys.

        Only responds when the command-input widget is focused.
        """
        inp = self.query_one("#command-input", Input)
        if not inp.has_focus:
            return

        if event.key == "up":
            event.stop()
            if not self._command_history:
                return
            if self._history_index is None:
                self._history_index = len(self._command_history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                return  # already at oldest
            cmd = self._command_history[self._history_index]
            inp.value = cmd
            inp.cursor_position = len(cmd)

        elif event.key == "down":
            event.stop()
            if self._history_index is None:
                return  # not browsing history
            if self._history_index < len(self._command_history) - 1:
                self._history_index += 1
                cmd = self._command_history[self._history_index]
            else:
                # At newest entry -> clear input
                self._history_index = None
                cmd = ""
            inp.value = cmd
            inp.cursor_position = len(cmd)

    # ------------------------------------------------------------------
    # Slash-command handlers
    # ------------------------------------------------------------------

    def _handle_help(self) -> str:
        """Return formatted help text covering all command categories."""
        cmd_list = "\n".join(f"  /{cmd:<12} {self._cmd_descriptions[cmd]}" for cmd in self._SLASH_COMMANDS)
        return (
            "[bold]TUI Commands[/bold] (prefix with /):\n"
            f"{cmd_list}\n"
            "  ?                 Alias for /help\n"
            "\n"
            "[bold]Introspection[/bold] (prefix with !):\n"
            "  !<object>[.<attr>] [args...]  Inspect or call objects\n"
            "  Available: session, transport, driver, status, parser, decoder\n"
            "\n"
            "[bold]Ruida Commands[/bold] (no prefix):\n"
            "  session start udp=<IP> usb=<device>    Connect to a controller\n"
            "  session end               Disconnect\n"
            "  <rpascript command>       Send command to controller"
        )

    def _handle_slash_command(self, raw: str) -> None:
        """Dispatch a /-prefixed TUI command to its handler."""
        parts = raw[1:].split(None, 1)  # strip leading /
        if not parts:
            self._log_error("Empty command. Type /help or ? for available commands.")
            return
        cmd = parts[0].lower()
        if cmd not in self._SLASH_COMMANDS:
            self._log_error(f"Unknown TUI command: /{cmd}. Type /help or ? for available commands.")
            return
        args = parts[1] if len(parts) > 1 else ''
        if cmd == 'help':
            self._log_info(self._handle_help())
        elif cmd == 'load':
            self._cmd_load(args)
        elif cmd == 'exec':
            self._cmd_exec()
        elif cmd == 'clear':
            self._cmd_clear()
        elif cmd == 'quit':
            self._cmd_quit()
        elif cmd == 'log':
            self._cmd_log(args)

    def _cmd_load(self, path: str) -> None:
        """Load a script file into memory."""
        if not path:
            self._log_error("Usage: /load <path>")
            return
        path = os.path.expanduser(path)
        try:
            with open(path, 'r') as f:
                content = f.read()
            lines = [l for l in content.splitlines() if l.strip()]
            if not lines:
                self._log_error(f"File is empty or contains only blank lines: {path}")
                return
            self._loaded_script = lines
            self._log_info(f"Loaded {len(lines)} lines from {path}")
        except FileNotFoundError:
            self._log_error(f"File not found: {path}")
        except PermissionError:
            self._log_error(f"Permission denied: {path}")
        except UnicodeDecodeError:
            self._log_error(f"File is not a valid text file: {path}")
        except Exception as e:
            self._log_error(f"Error reading {path}: {type(e).__name__}: {e}")

    def _cmd_exec(self) -> None:
        """Execute the loaded script."""
        if not self._loaded_script:
            self._log_error("No script loaded. Use /load <path> first.")
            return
        if self._ruida_driver is None:
            self._log_error("No active session. Use 'session start udp=<IP> usb=<device>' first.")
            return
        self._log_info(f"Executing {len(self._loaded_script)} lines...")
        self.run_script(self._loaded_script)

    def _cmd_clear(self) -> None:
        """Clear all log panels and loaded script."""
        self._log_widget.clear()
        self._status_log.clear()
        self._reply_log.clear()
        self._loaded_script = []
        self._log_info("Logs cleared")

    def _cmd_quit(self) -> None:
        """Exit the TUI."""
        self.exit()

    def _cmd_log(self, args: str) -> None:
        """Handle /log subcommands: on, off, status, or toggle."""
        action = args.strip().lower()
        if action in ('', 'toggle'):
            self._logging_enabled = not self._logging_enabled
            state = "ON" if self._logging_enabled else "OFF"
            self._log_info(f"Logging is {state}")
        elif action == 'on':
            self._logging_enabled = True
            self._log_info("Logging enabled")
        elif action == 'off':
            self._logging_enabled = False
            self._log_info("Logging disabled (status/reply suppressed)")
        elif action == 'status':
            state = "ON" if self._logging_enabled else "OFF"
            self._log_info(f"Logging is {state}")
        else:
            self._log_error("Usage: /log [on|off|status]")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _start_session(self, udp: str = '', usb: str | None = None) -> None:
        """Connect to a Ruida controller and start the script runner.

        Opens the transport, creates the driver, then starts the script runner
        (which registers listeners, starts the runner thread, and starts the
        status monitor). The status monitor is started LAST so that reply events
        arrive to a fully-initialized driver with a running script runner.
        """
        if self._session is not None:
            self._log_error("Session already active. Use 'session end' first.")
            return

        usb_device = usb if usb else ''

        loop = asyncio.get_running_loop()

        if udp:
            resolved = await loop.run_in_executor(None, _resolve_hostname, udp, 50200)
            if resolved is None:
                self._log_error(f"Unable to resolve '{udp}'. Check the address and try again.")
                return
            udp = resolved

        try:
            session = RdSession()
            session.transport.configure(
                udp_host=udp,
                usb_device=usb_device,
            )

            self._log_info(f"Connecting (udp={udp}, usb={usb_device})...")

            # Open transport (fast, non-blocking for UDP)
            if not session.transport.open():
                self._log_error("Failed to open transport")
                return

            # Create driver and register TUI listeners
            driver = RdDriver(session)
            driver.register_status_listener(self.on_status_event)
            driver.register_reply_listener(self.on_reply_data)

            # Start the script runner:
            # 1. Configures ping/query commands on status monitor
            # 2. Starts the runner thread (so self.run() is safe)
            # 3. Registers internal reply listener on transport
            # 4. Starts the status monitor LAST — replies arrive to a ready driver
            driver.start_script_runner()

            self._session = session
            self._ruida_driver = driver

            self._log_info("Session started successfully")
            self._update_status_bar()

        except Exception as e:
            self._log_error(f"Failed to start session: {e}")
            if self._ruida_driver is not None:
                self._ruida_driver.stop_script_runner()
                self._ruida_driver = None
            if self._session is not None:
                self._session.disconnect()
                self._session = None

    async def _stop_session(self) -> None:
        """Disconnect from the controller and clean up resources."""
        if self._ruida_driver is None and self._session is None:
            self._log_info("No active session.")
            return

        try:
            if self._ruida_driver is not None:
                self._ruida_driver.stop_script_runner()
                self._ruida_driver = None

            if self._session is not None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: self._session.disconnect())
                self._session = None

            self._log_info("Session ended")
            self._update_status_bar()

        except Exception as e:
            self._log_error(f"Error stopping session: {e}")
            self._session = None
            self._ruida_driver = None

    # ------------------------------------------------------------------
    # AppAdapter-compatible interface (called from driver background thread)
    # ------------------------------------------------------------------

    def on_status_event(self, event: RdStatusEvent) -> None:
        """Handle a status event from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        """
        def _update() -> None:
            if self._logging_enabled:
                self._status_log.write(f"[STATUS] {event.value}")
            self._event_count += 1
            if event in (RdStatusEvent.DISCONNECTED, RdStatusEvent.TERMINATED):
                self._session_disconnected = True
            elif event is RdStatusEvent.CONNECTED:
                self._session_disconnected = False
            self._update_status_bar()
        self.call_from_thread(_update)

    def on_reply_data(self, replies: list[bytearray]) -> None:
        """Handle reply data from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        Decodes raw reply bytearrays to MEM_ mnemonics with decoded values.
        """
        def _update() -> None:
            decoder = RdDecoder()
            for raw in replies:
                formatted = self._format_reply(raw, decoder)
                if self._logging_enabled:
                    self._reply_log.write(formatted)
                    # If the last command was a GET_SETTING, also show the
                    # decoded reply in the main log pane
                    if self._last_cmd_was_get_setting:
                        self._log_widget.write(f"  ← {formatted}")
                        self._last_cmd_was_get_setting = False  # reset — only log the first reply
            self._reply_count += len(replies)
            self._update_status_bar()
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
        if self._ruida_driver is None:
            def _error() -> None:
                self._log_error("No active session to run script.")
            self.call_from_thread(_error)
            return

        def _run() -> None:
            try:
                self._ruida_driver.run(script)
                self._script_count += len(script)
                self._update_status_bar()
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

    def _format_reply(self, reply: bytearray, decoder: RdDecoder | None = None) -> str:
        """Format a GET_SETTING reply bytearray as MEM_ mnemonic with decoded value.

        Uses the MT memory table from ruida_protocol to look up the address
        and decode the value using the appropriate type decoder.
        Avoids the prime method of RdDecoder which requires a non-None output emitter.
        """
        addr = (reply[2] << 8) | reply[3]
        msb = (addr >> 8) & 0xFF
        lsb = addr & 0xFF

        mt_entry = MT.get(msb, {}).get(lsb)
        if mt_entry is not None:
            mnemonic, spec = mt_entry[0], mt_entry[1]
            if decoder is None:
                d = RdDecoder()
            else:
                d = decoder

            # Set up decoder fields manually (avoid prime() which calls self.out.verbose())
            d.format = spec[0]
            d.rd_type = spec[2]
            d.data = bytearray([])
            d.value = None
            d.cstring = d.rd_type == 'cstring'

            # Set _length for to_int/to_uint
            from protocols.ruida.ruida_protocol import RD_TYPES, RDT_BYTES
            d._length = RD_TYPES.get(d.rd_type, [0, 5])[RDT_BYTES]

            # Call the decoder method directly by name
            decoder_method = getattr(d, f'rd_{spec[1]}')
            data_bytes = reply[4:9]

            try:
                decoded = decoder_method(data_bytes)
            except Exception:
                decoded = None

            # Track position values for the status bar
            # Addresses follow rd_mt convention: (MT_msb << 8) | MT_lsb
            # MEM_CURRENT_POSITION_X at MT[0x04][0x21] → 0x0421
            # MEM_CURRENT_POSITION_Y at MT[0x04][0x31] → 0x0431
            # MEM_CURRENT_POSITION_Z at MT[0x04][0x41] → 0x0441
            # MEM_CURRENT_POSITION_U at MT[0x04][0x51] → 0x0451
            if addr == 0x0421:
                self._position['X'] = d.value
            elif addr == 0x0431:
                self._position['Y'] = d.value
            elif addr == 0x0441:
                self._position['Z'] = d.value
            elif addr == 0x0451:
                self._position['U'] = d.value
            # MEM_CARD_ID at MT[0x05][0x7E] → 0x057E
            elif addr == 0x057E:
                self._position['Card'] = d.value
            # MEM_BED_SIZE_X at MT[0x00][0x26] → 0x0026
            elif addr == 0x0026:
                self._position['BedX'] = d.value
            # MEM_BED_SIZE_Y at MT[0x00][0x36] → 0x0036
            elif addr == 0x0036:
                self._position['BedY'] = d.value

            if decoded:
                return f"{mnemonic}: {decoded}"

        # Fallback: raw hex format
        if decoder is None:
            val = RdDecoder().decode_value(reply)
        else:
            val = decoder.decode_value(reply)
        return f"0x{addr:04X}: {val}"

    def _update_status_bar(self) -> None:
        """Update the bottom status bar with connection info, counters, and position."""
        # Connection info
        if self._session_disconnected:
            conn = "[red]Disconnected[/red]"
        elif self._ruida_driver is not None and self._session is not None and self._session.is_connected:
            conn = "[green]Connected[/green]"
        elif self._session is not None:
            conn = "[yellow]Connecting[/yellow]"
        else:
            conn = "[red]Disconnected[/red]"

        # Transport info
        if self._session is not None:
            transport = self._session.transport
            if transport.is_udp:
                transport_info = transport._udp_host
            elif transport.is_usb:
                transport_info = transport._usb_device
            else:
                transport_info = ""
        else:
            transport_info = ""

        # Counters
        counters = f"Events: {self._event_count}  Replies: {self._reply_count}  Scripts: {self._script_count}"

        # Machine info (Card, BedX, BedY)
        machine_parts = []
        card = self._position.get('Card')
        if card is not None:
            machine_parts.append(f"Card: {card}")
        else:
            machine_parts.append("Card: —")
        bedx = self._position.get('BedX')
        if bedx is not None:
            machine_parts.append(f"BedX: [bold]{bedx:.3f}[/bold]")
        else:
            machine_parts.append("BedX: —")
        bedy = self._position.get('BedY')
        if bedy is not None:
            machine_parts.append(f"BedY: [bold]{bedy:.3f}[/bold]")
        else:
            machine_parts.append("BedY: —")
        machine = "  ".join(machine_parts)

        # Position
        pos_parts = []
        for axis in ('X', 'Y', 'Z', 'U'):
            v = self._position[axis]
            if v is not None:
                pos_parts.append(f"{axis}: [bold]{v:.3f}[/bold]")
            else:
                pos_parts.append(f"{axis}: —")
        pos = "  ".join(pos_parts)

        self._status_bar.update(f"{conn}  {transport_info}  |  {machine}  |  {counters}  |  {pos}")

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
        if self._ruida_driver is not None:
            self._ruida_driver.stop_script_runner()
            self._ruida_driver = None
        if self._session is not None:
            self._session.disconnect()
            self._session = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_resolvable_address(self, token: str) -> bool:
        """Check if a GET_SETTING address token can be resolved (MT mnemonic or numeric)."""
        if token in self._parser._mt_map:
            return True
        try:
            int(token, 0)
            return True
        except ValueError:
            return False


# ------------------------------------------------------------------
# Module-level entry point
# ------------------------------------------------------------------

def _resolve_hostname(host: str, port: int = 50200) -> str | None:
    """Resolve a hostname to an IP address. Returns IP string or None on failure.

    Performs DNS resolution via socket.getaddrinfo in a thread pool with
    a 5-second timeout. For valid IP addresses, returns the host unchanged.
    """
    import concurrent.futures
    import ipaddress
    import socket

    if not host:
        return ''  # Empty is OK (USB mode)

    # Already an IP? No DNS needed.
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    # Has spaces? Can't be a valid hostname.
    if ' ' in host:
        return None

    # Resolve hostname via DNS with timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, host, port)
        try:
            result = future.result(timeout=5.0)
            # Extract IP from getaddrinfo result
            # Result format: [(family, type, proto, canonname, sockaddr), ...]
            ip = result[0][4][0]
            return ip
        except concurrent.futures.TimeoutError:
            return None
        except socket.gaierror:
            return None


def run_tui() -> None:
    """Run the RdsAdapter TUI application.

    Creates an RdsAdapter instance and enters the Textual event loop.
    Blocks until the user quits (Ctrl+C).
    """
    app = RdsAdapter()
    app.run()
