"""
L7 RdsAdapter — Textual-based TUI for interactive Ruida script execution.

Provides a terminal user interface for connecting to Ruida laser controllers,
executing rpascript commands interactively, and monitoring status/reply events
in real-time via the AppAdapter → RdDriver → RdSession stack.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import inspect
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Header, Input, RichLog, Static

from rpalib.ruida_transcoder import RdDecoder, RdEncoder
from rpascript.encoding import encode_command, is_resolvable_address
from rpascript.interpreter import ScriptParser, reconstruct_script_line
from ruidadriver.rd_status import RdStatusEvent
from ruidadriver.ruida_driver import RdDriver, StatusDict


def _parse_timeout_spec(to_str: str) -> float:
    """Parse a timeout spec like '5s' or '5000ms' into seconds (float).

    Raises ValueError on invalid format.
    """
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s)", to_str.strip())
    if not match:
        raise ValueError(f"Invalid timeout format: '{to_str}'. Use e.g., 5s, 500ms")
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return value / 1000.0
    return value


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
        ("escape", "stop", "Stop"),
    ]

    _SLASH_COMMANDS: tuple[str, ...] = (
        "help",
        "load",
        "exec",
        "clear",
        "quit",
        "log",
        "head",
        "tail",
        "list",
        "save",
        "stop",
    )
    _NORMAL_COMMANDS: tuple[str, ...] = ("session",)

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
        self._ruida_driver: RdDriver | None = None
        self._parser = ScriptParser()
        self._decoder = RdDecoder()
        self._event_count = 0
        self._reply_count = 0
        self._script_count = 0
        self._logging_enabled: bool = True
        self._introspect_map: dict[str, Callable[[], Any]] = {
            "session": lambda: self._ruida_driver,
            "transport": lambda: self._ruida_driver.transport if self._ruida_driver else None,
            "driver": lambda: self._ruida_driver,
            "status": lambda: self._ruida_driver.status_monitor if self._ruida_driver else None,
            "parser": lambda: self._parser,
            "decoder": lambda: self._decoder,
        }
        self._loaded_script: list[str] = []
        self._head_script: list[str] = []
        self._tail_script: list[str] = []
        self._session_connected = asyncio.Event()
        self._session_start_cancel = asyncio.Event()
        self._suggest_popup = RichLog(
            id="suggest-popup", highlight=True, markup=True, max_lines=10
        )
        self._cmd_descriptions: dict[str, str] = {
            "help": "Show help text",
            "load": "Load a script file from disk",
            "exec": "Execute the loaded script",
            "clear": "Clear all log panels, loaded script, head, and tail",
            "quit": "Exit the TUI",
            "log": "Toggle display of status/reply messages (on|off|status)",
            "session": "Start or end a controller session (start udp=<IP> usb=<device> to=<timeout> / end)",
            "head": "Load a script file to prepend to job on execution",
            "tail": "Load a script file to append to job on execution",
            "list": "Display loaded script (/list script), composed job (/list job), head (/list head), or tail (/list tail)",
            "save": "Save composed job to a file (/save job <path>)",
            "stop": "Stop the current operation (session connection or script execution). Also bound to Escape.",
        }
        self._suggest_matches: list[str] = []
        self._suggest_selected: int = 0
        self._suggest_mode: str = ""  # 'slash', 'introspect', or '' when no popup
        self._suppress_popup: bool = (
            False  # Suppress on_input_changed for programmatic value changes
        )
        self._command_history: list[str] = []
        self._history_index: int | None = None
        self._position: dict[str, tuple | None] = {
            "X": None,
            "Y": None,
            "Z": None,
            "U": None,
            "Card": None,
            "BedX": None,
            "BedY": None,
        }
        self._last_coord_change: dict[str, float] = {
            "X": 0.0,
            "Y": 0.0,
            "Z": 0.0,
            "U": 0.0,
        }
        self._session_disconnected: bool = False
        self._machine_status: int = 0
        self._machine_status_formatted: str = "0"
        self._status_bits: dict[str, bool] = {
            "MACHINE_STATUS_MOVING": False,
            "MACHINE_STATUS_PART_END": False,
            "MACHINE_STATUS_JOB_RUNNING": False,
        }
        self._last_cmd_was_get_setting: bool = False

    # ------------------------------------------------------------------
    # Textual App lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Create the TUI layout widgets."""
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="log-panel"):
                yield RichLog(
                    id="log-area", highlight=True, markup=True, max_lines=1000
                )
                yield Input(
                    id="command-input",
                    placeholder="> Enter command (session start/end, or rpascript)...",
                )
            with Vertical(id="side-panel"):
                yield RichLog(
                    id="status-log", highlight=True, markup=True, max_lines=50
                )
                yield RichLog(id="reply-log", highlight=True, markup=True, max_lines=50)
        yield Static(id="status-bar")

    def on_mount(self) -> None:
        """Widgets are ready — cache references, load history, and log startup message."""
        self._log_widget = self.query_one("#log-area", RichLog)
        self._status_log = self.query_one("#status-log", RichLog)
        self._reply_log = self.query_one("#reply-log", RichLog)
        self._status_bar = self.query_one("#status-bar", Static)
        # Restrict focus to command input only — Tab stays on Input
        self._log_widget.can_focus = False
        self._status_log.can_focus = False
        self._reply_log.can_focus = False
        self._update_status_bar()
        self._load_command_history()
        self.query_one("#command-input", Input).focus()

    # ------------------------------------------------------------------
    # Command input handling
    # ------------------------------------------------------------------

    def _render_suggest_popup(self) -> None:
        """Render the suggestion popup with current selection highlighted.

        Only shows items within a visible window around the selected item to
        keep the selection on screen when the list exceeds max_lines.
        """
        self._suggest_popup.clear()
        if not self._suggest_matches:
            self._suggest_popup.write("[dim]No matching commands[/dim]")
            return

        # Compute visible window centered on selected item
        max_items = self._suggest_popup.max_lines - 1  # reserve 1 line for header
        total = len(self._suggest_matches)
        half = max_items // 2
        start = max(0, self._suggest_selected - half)
        end = min(total, start + max_items)
        # If we're below max_items, shift window up
        if end - start < max_items:
            start = max(0, end - max_items)

        if self._suggest_mode == "slash":
            self._suggest_popup.write("[bold]Commands:[/bold]")
            for i in range(start, end):
                cmd = self._suggest_matches[i]
                line = f"  /{cmd:<12} {self._cmd_descriptions[cmd]}"
                if i == self._suggest_selected:
                    self._suggest_popup.write(f"[reverse]{line}[/reverse]")
                else:
                    self._suggest_popup.write(line)
        elif self._suggest_mode == "introspect":
            self._suggest_popup.write("[bold]Introspect:[/bold]")
            for i in range(start, end):
                obj = self._suggest_matches[i]
                line = f"  ?{obj}"
                if i == self._suggest_selected:
                    self._suggest_popup.write(f"[reverse]{line}[/reverse]")
                else:
                    self._suggest_popup.write(line)

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

        # Introspection mode: ?<object>[.<attr>] [args...]
        if line.startswith("?"):
            expr = line[1:].strip()
            if not expr:
                # Just '?' — show available introspection objects
                known = ", ".join(sorted(self._introspect_map.keys()))
                self._log_widget.write("[bold]?[/bold]")
                self._log_info(f"Introspect: {known}")
                return
            result = self._handle_introspect(expr)
            self._log_widget.write(f"[bold]?{expr}[/bold]")
            self._log_info(result)
            return
        # Slash-prefixed TUI commands
        if line.startswith("/"):
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

            # Pre-encode regular commands to show wire-format bytes in the log
            if cmd["type"] not in ("SESSION_START", "SESSION_END", "DELAY", "WAIT"):
                try:
                    encoded = encode_command(
                        cmd,
                        self._parser.mnemonic_map,
                        self._parser._mt_map,
                        RdEncoder(),
                    )
                    hex_str = " ".join(f"{b:02X}" for b in encoded)
                    self._log_widget.write(
                        f"[dim]         ⇒ {hex_str} ({len(encoded)} bytes)[/dim]"
                    )
                except Exception as e:
                    self._log_error(f"Encoding failed: {e}")

            # Only track GET_SETTING commands with valid, resolvable addresses
            # to prevent stale flag from showing QUERY replies as GET_SETTING results
            if cmd.get("mnemonic", "") == "GET_SETTING":
                params = cmd.get("params", [])
                if params and self._is_resolvable_address(params[0]):
                    self._last_cmd_was_get_setting = True
                else:
                    reason = (
                        f"unknown address: {params[0]}" if params else "missing address"
                    )
                    self._log_error(f"Invalid GET_SETTING: {reason}")
                    return
            else:
                self._last_cmd_was_get_setting = False

            if cmd["type"] == "SESSION_START":
                await self._start_session(**cmd["params"])
            elif cmd["type"] == "SESSION_END":
                await self._stop_session()
            else:
                if self._ruida_driver is None:
                    self._log_error(
                        "No active session. Use 'session start udp=<IP> usb=<device>' first."
                    )
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
        # Suppress popup for programmatic value changes (e.g., history recall)
        if self._suppress_popup:
            self._suppress_popup = False
            self._suggest_matches = []
            self._suggest_mode = ""
            if self._suggest_popup.is_attached:
                self._suggest_popup.remove()
            return
        value = event.value

        # Slash commands
        if value.startswith("/"):
            prefix = value[1:].strip()
            if not prefix:
                matches = list(self._SLASH_COMMANDS)
            else:
                matches = [
                    c for c in self._SLASH_COMMANDS if c.startswith(prefix.lower())
                ]

            if not self._suggest_popup.is_attached:
                self.query_one("#log-panel").mount(
                    self._suggest_popup, before="#command-input"
                )
            if matches:
                self._suggest_matches = matches
                self._suggest_selected = 0
                self._suggest_mode = "slash"
            else:
                self._suggest_matches = []
                self._suggest_mode = ""
            self._render_suggest_popup()
            return

        # Introspection objects: ?<object>
        if value.startswith("?"):
            prefix = value[1:].strip()
            known = list(self._introspect_map.keys())
            if not prefix:
                matches = sorted(known)
            else:
                matches = sorted(k for k in known if k.startswith(prefix.lower()))

            if not self._suggest_popup.is_attached:
                self.query_one("#log-panel").mount(
                    self._suggest_popup, before="#command-input"
                )
            if matches:
                self._suggest_matches = matches
                self._suggest_selected = 0
                self._suggest_mode = "introspect"
            else:
                self._suggest_matches = []
                self._suggest_mode = ""
            self._render_suggest_popup()
            return

        # Normal commands (not introspection, not help query)
        if value and not value.startswith("?"):
            clean = value.strip().lower()

            if " " in clean:
                # Space detected — lock to the matched command root, no more filtering
                root_cmd = clean.split(" ", 1)[0]
                if root_cmd in self._NORMAL_COMMANDS:
                    matches = [root_cmd]
                else:
                    matches = []
            else:
                # No space — filter by prefix match on the first word
                matches = [c for c in self._NORMAL_COMMANDS if c.startswith(clean)]

            if matches:
                if not self._suggest_popup.is_attached:
                    self.query_one("#log-panel").mount(
                        self._suggest_popup, before="#command-input"
                    )
                self._suggest_popup.clear()
                self._suggest_popup.write("[bold]Commands:[/bold]")
                for cmd in matches:
                    self._suggest_popup.write(
                        f"  {cmd:<12} {self._cmd_descriptions[cmd]}"
                    )
                return

        # No popup needed — remove if attached
        if self._suggest_popup.is_attached:
            self._suggest_popup.remove()
            self._suggest_matches = []
            self._suggest_mode = ""

    # ------------------------------------------------------------------
    # Command history (Up/Down navigation)
    # ------------------------------------------------------------------

    @on(Key)
    def on_command_key(self, event: Key) -> None:
        """Navigate command history with Up/Down arrow keys.

        Only responds when the command-input widget is focused.
        """
        inp = self.query_one("#command-input", Input)
        popup_has_focus = (
            self._suggest_popup.is_attached and self._suggest_popup.has_focus
        )
        if not inp.has_focus and not popup_has_focus:
            return

        if event.key == "up":
            event.stop()
            if popup_has_focus and self._suggest_matches:
                self._suggest_selected = (self._suggest_selected - 1) % len(
                    self._suggest_matches
                )
                self._render_suggest_popup()
                return
            if not self._command_history:
                return
            if self._history_index is None:
                self._history_index = len(self._command_history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                return  # already at oldest
            cmd = self._command_history[self._history_index]
            self._suppress_popup = True
            inp.value = cmd
            inp.cursor_position = len(cmd)

        elif event.key == "down":
            event.stop()
            if popup_has_focus and self._suggest_matches:
                self._suggest_selected = (self._suggest_selected + 1) % len(
                    self._suggest_matches
                )
                self._render_suggest_popup()
                return
            if self._history_index is None:
                return  # not browsing history
            if self._history_index < len(self._command_history) - 1:
                self._history_index += 1
                cmd = self._command_history[self._history_index]
            else:
                # At newest entry -> clear input
                self._history_index = None
                cmd = ""
            self._suppress_popup = True
            inp.value = cmd
            inp.cursor_position = len(cmd)

        elif event.key == "enter":
            """Confirm selection from suggest popup."""
            if popup_has_focus and self._suggest_matches:
                event.stop()
                selected = self._suggest_matches[self._suggest_selected]
                prefix = "/" if self._suggest_mode == "slash" else "?"
                self._suppress_popup = True
                completed_val = f"{prefix}{selected}"
                inp.focus()  # Must be BEFORE value set (selects old value, harmless)
                inp.value = completed_val  # Set autocompleted value; End key moves cursor to end
                self.post_message(Key("end", None))
                self._suggest_popup.remove()
                self._suggest_matches = []
                self._suggest_mode = ""
                return

        elif event.key == "tab":
            """Tab autocomplete for / and ? command prefixes."""
            # If popup has focus, autofill with selected item
            if popup_has_focus and self._suggest_matches:
                event.stop()
                selected = self._suggest_matches[self._suggest_selected]
                prefix = "/" if self._suggest_mode == "slash" else "?"
                self._suppress_popup = True
                completed_val = f"{prefix}{selected}"
                inp.focus()  # Must be BEFORE value set (selects old value, harmless)
                inp.value = completed_val  # Set autocompleted value; End key moves cursor to end
                self.post_message(Key("end", None))
                self._suggest_popup.remove()
                self._suggest_matches = []
                self._suggest_mode = ""
                return

            # Existing autocomplete when input has focus
            if not self._suggest_popup.is_attached:
                return
            event.stop()
            value = inp.value

            if value.startswith("/"):
                prefix = value[1:].strip()
                if not prefix:
                    matches = list(self._SLASH_COMMANDS)
                else:
                    matches = [
                        c for c in self._SLASH_COMMANDS if c.startswith(prefix.lower())
                    ]
                if len(matches) == 1:
                    completed_val = f"/{matches[0]}"
                    inp.value = completed_val
                    self.post_message(Key("end", None))

            elif value.startswith("?"):
                prefix = value[1:].strip()
                known = list(self._introspect_map.keys())
                if not prefix:
                    matches = sorted(known)
                else:
                    matches = sorted(k for k in known if k.startswith(prefix.lower()))
                if len(matches) == 1:
                    completed_val = f"?{matches[0]}"
                    inp.value = completed_val
                    self.post_message(Key("end", None))

    # ------------------------------------------------------------------
    # Slash-command handlers
    # ------------------------------------------------------------------

    def _handle_help(self) -> str:
        """Return formatted help text covering all command categories."""
        cmd_list = "\n".join(
            f"  /{cmd:<12} {self._cmd_descriptions[cmd]}"
            for cmd in self._SLASH_COMMANDS
        )
        return (
            "[bold]TUI Commands[/bold] (prefix with /):\n"
            f"{cmd_list}\n"
            "[bold]Introspection[/bold] (prefix with ?):\n"
            "  ?<object>[.<attr>] [args...]  Inspect or call objects\n"
            "  ?                 List available introspection objects\n"
            "  Available: session, transport, driver, status, parser, decoder\n"
            "\n"
            "[bold]Ruida Commands[/bold] (no prefix):\n"
            "  session start udp=<IP> usb=<device> to=<timeout>  Connect to a controller (to: optional, e.g. 5s or 5000ms)\n"
            "  session end               Disconnect\n"
            "  <rpascript command>       Send command to controller\n"
            "\n"
            "[bold]Flow Control[/bold] (for loaded scripts):\n"
            "  delay <time>              Pause execution (e.g. 5s, 100ms)\n"
            "  wait <status> [to=...]    Wait for MACHINE_STATUS_* bit\n"
            "  wait !<status> [to=...]   Wait for lifecycle (active then inactive)\n"
            "  Statuses: MACHINE_STATUS_MOVING, MACHINE_STATUS_PART_END,\n"
            "            MACHINE_STATUS_JOB_RUNNING\n"
            "  to=   Optional timeout (e.g. to=30s). Default: forever\n"
        )

    def _handle_slash_command(self, raw: str) -> None:
        """Dispatch a /-prefixed TUI command to its handler."""
        parts = raw[1:].split(None, 1)  # strip leading /
        if not parts:
            self._log_error("Empty command. Type /help or ? for available commands.")
            return
        cmd = parts[0].lower()
        if cmd not in self._SLASH_COMMANDS:
            self._log_error(
                f"Unknown TUI command: /{cmd}. Type /help or ? for available commands."
            )
            return
        args = parts[1] if len(parts) > 1 else ""
        if cmd == "help":
            self._log_info(self._handle_help())
        elif cmd == "load":
            self._cmd_load(args)
        elif cmd == "exec":
            self._cmd_exec(args)
        elif cmd == "clear":
            self._cmd_clear()
        elif cmd == "quit":
            self._cmd_quit()
        elif cmd == "log":
            self._cmd_log(args)
        elif cmd == "head":
            self._cmd_head(args)
        elif cmd == "tail":
            self._cmd_tail(args)
        elif cmd == "list":
            self._cmd_list(args)
        elif cmd == "save":
            self._cmd_save(args)
        elif cmd == "stop":
            self._cmd_stop(args)

    def _cmd_load(self, path: str) -> None:
        """Load a script file into memory."""
        if not path:
            self._log_error("Usage: /load <path>")
            return
        path = os.path.expanduser(path)
        try:
            with open(path, "r") as f:
                content = f.read()
            lines = [line for line in content.splitlines() if line.strip()]
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

    def _cmd_head(self, path: str) -> None:
        """Load a script file to prepend to job on execution."""
        if not path:
            self._log_error("Usage: /head <path>")
            return
        path = os.path.expanduser(path)
        try:
            with open(path, "r") as f:
                content = f.read()
            lines = [line for line in content.splitlines() if line.strip()]
            if not lines:
                self._log_error(f"File is empty or contains only blank lines: {path}")
                return
            self._head_script = lines
            self._log_info(f"Head loaded: {len(lines)} lines from {path}")
        except FileNotFoundError:
            self._log_error(f"File not found: {path}")
        except PermissionError:
            self._log_error(f"Permission denied: {path}")
        except UnicodeDecodeError:
            self._log_error(f"File is not a valid text file: {path}")
        except Exception as e:
            self._log_error(f"Error reading {path}: {type(e).__name__}: {e}")

    def _cmd_tail(self, path: str) -> None:
        """Load a script file to append to job on execution."""
        if not path:
            self._log_error("Usage: /tail <path>")
            return
        path = os.path.expanduser(path)
        try:
            with open(path, "r") as f:
                content = f.read()
            lines = [line for line in content.splitlines() if line.strip()]
            if not lines:
                self._log_error(f"File is empty or contains only blank lines: {path}")
                return
            self._tail_script = lines
            self._log_info(f"Tail loaded: {len(lines)} lines from {path}")
        except FileNotFoundError:
            self._log_error(f"File not found: {path}")
        except PermissionError:
            self._log_error(f"Permission denied: {path}")
        except UnicodeDecodeError:
            self._log_error(f"File is not a valid text file: {path}")
        except Exception as e:
            self._log_error(f"Error reading {path}: {type(e).__name__}: {e}")

    def _cmd_exec(self, args: str = "") -> None:
        """Execute the loaded script.

        If args is 'job', execute only commands from START_PROCESS to EOF.
        Otherwise execute all loaded commands.
        """
        if not self._loaded_script:
            self._log_error("No script loaded. Use /load <path> first.")
            return
        if self._ruida_driver is None:
            self._log_error(
                "No active session. Use 'session start udp=<IP> usb=<device>' first."
            )
            return
        action = args.strip().lower()
        if action == "":
            self._log_info(f"Executing {len(self._loaded_script)} lines...")
            self.run_script(self._loaded_script)
        elif action == "job":
            script = self._build_job_script(self._loaded_script)
            if not script:
                self._log_error("No job commands found (no START_PROCESS/EOF markers).")
                return
            self._log_info(f"Executing {len(script)} job commands...")
            self.run_script(script)
        else:
            self._log_error(f"Unknown exec action: '{action}'. Usage: /exec [job]")

    @staticmethod
    def _filter_job_commands(lines: list[str]) -> list[str]:
        """Filter lines to only include commands between START_PROCESS and EOF (inclusive).

        Excludes GET_SETTING and NEW_PACKET directives — they are not part of the job.
        """
        in_job = False
        result: list[str] = []
        for line in lines:
            stripped = line.strip().upper()
            if stripped == "START_PROCESS" or stripped.startswith("START_PROCESS "):
                in_job = True
            if in_job:
                # Skip GET_SETTING and NEW_PACKET — not part of the job
                if stripped.startswith("GET_SETTING") or stripped.startswith(
                    "NEW_PACKET"
                ):
                    continue
                result.append(line)
            if stripped == "EOF" or stripped.startswith("EOF "):
                break
        return result

    def _build_job_script(self, lines: list[str]) -> list[str]:
        """Compose head + job (START_PROCESS→EOF) + tail into a single script.

        Returns empty list if no job markers are found in the input lines.
        Callers are responsible for reporting empty-job errors.
        """
        job = self._filter_job_commands(lines)
        if not job:
            return []
        return self._head_script + job + self._tail_script

    def _cmd_clear(self) -> None:
        """Clear all log panels, loaded script, head, and tail."""
        self._log_widget.clear()
        self._status_log.clear()
        self._reply_log.clear()
        self._loaded_script = []
        self._head_script = []
        self._tail_script = []
        self._log_info("Logs, head, and tail cleared")

    def _cmd_quit(self) -> None:
        """Exit the TUI."""
        self.exit()

    def action_stop(self) -> None:
        """Handle Escape key: stop current operation."""
        self._cmd_stop("")

    def _cmd_log(self, args: str) -> None:
        """Handle /log subcommands: on, off, status, or toggle."""
        action = args.strip().lower()
        if action in ("", "toggle"):
            self._logging_enabled = not self._logging_enabled
            state = "ON" if self._logging_enabled else "OFF"
            self._log_info(f"Logging is {state}")
        elif action == "on":
            self._logging_enabled = True
            self._log_info("Logging enabled")
        elif action == "off":
            self._logging_enabled = False
            self._log_info("Logging disabled (status/reply suppressed)")
        elif action == "status":
            state = "ON" if self._logging_enabled else "OFF"
            self._log_info(f"Logging is {state}")
        else:
            self._log_error("Usage: /log [on|off|status]")

    def _cmd_list(self, args: str) -> None:
        """Handle /list subcommands: script, job, head, or tail."""
        action = args.strip().lower()
        if action == "script":
            if not self._loaded_script:
                self._log_info("No script loaded. Use /load <path> first.")
                return
            self._log_info(f"Loaded script ({len(self._loaded_script)} lines):")
            for line in self._loaded_script:
                self._log_widget.write(f"  {line}")
        elif action == "job":
            if not self._loaded_script:
                self._log_info("No script loaded. Use /load <path> first.")
                return
            composed = self._build_job_script(self._loaded_script)
            if not composed:
                self._log_error("No job commands found (no START_PROCESS/EOF markers).")
                return
            self._log_info(f"Composed job ({len(composed)} lines):")
            for line in composed:
                self._log_widget.write(f"  {line}")
        elif action == "head":
            if not self._head_script:
                self._log_info("No head script loaded. Use /head <path> first.")
                return
            self._log_info(f"Head script ({len(self._head_script)} lines):")
            for line in self._head_script:
                self._log_widget.write(f"  {line}")
        elif action == "tail":
            if not self._tail_script:
                self._log_info("No tail script loaded. Use /tail <path> first.")
                return
            self._log_info(f"Tail script ({len(self._tail_script)} lines):")
            for line in self._tail_script:
                self._log_widget.write(f"  {line}")
        else:
            self._log_error("Usage: /list [job|script|head|tail]")

    def _cmd_save(self, args: str) -> None:
        """Handle /save subcommands: job <path>."""
        parts = args.strip().split(None, 1)
        if not parts or parts[0] != "job" or len(parts) < 2:
            self._log_error("Usage: /save job <path>")
            return
        path = parts[1]
        if not self._loaded_script:
            self._log_error("No script loaded. Use /load <path> first.")
            return
        composed = self._build_job_script(self._loaded_script)
        if not composed:
            self._log_error("No job commands to save (no START_PROCESS/EOF markers).")
            return
        path = os.path.expanduser(path)
        try:
            with open(path, "w") as f:
                f.write("\n".join(composed) + "\n")
            self._log_info(f"Job saved to {path} ({len(composed)} lines)")
        except PermissionError:
            self._log_error(f"Permission denied: {path}")
        except OSError as e:
            self._log_error(f"Error writing {path}: {type(e).__name__}: {e}")

    def _cmd_stop(self, args: str) -> None:
        """Stop current operation (session connection wait or script execution)."""
        driver_stopped = False
        if self._ruida_driver is not None:
            driver_stopped = True
            self._ruida_driver.cancel_script()

        session_stopped = False
        if not self._session_connected.is_set():
            session_stopped = True

        if session_stopped and driver_stopped:
            self._session_start_cancel.set()
            self._log_info("Session start cancelled (pending scripts dropped)")
        elif session_stopped:
            self._session_start_cancel.set()
            self._log_info("Session start cancelled")
        elif driver_stopped:
            self._log_info("Script execution stopped")
        else:
            self._log_info("Nothing to stop")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _start_session(
        self, udp: str = "", usb: str | None = None, to: str | None = None
    ) -> None:
        """Connect to a Ruida controller and start the script runner.

        Creates an RdDriver, registers TUI listeners, then calls
        driver.start() which creates the session, opens the transport,
        starts the script runner and status monitor, and returns.
        """
        if self._ruida_driver is not None:
            self._log_error("Session already active. Use 'session end' first.")
            return

        usb_device = usb if usb else ""

        timeout: float | None = None
        if to is not None:
            try:
                timeout = _parse_timeout_spec(to)
            except ValueError as e:
                self._log_error(str(e))
                return

        # Check pyserial availability before attempting USB connection
        if usb_device:
            try:
                import serial  # noqa: F401
            except ImportError:
                self._log_error(
                    "pyserial is not installed. "
                    "Install it with: pip install ruida-protocol-analyzer[serial]"
                )
                return

        loop = asyncio.get_running_loop()

        if udp:
            resolved = await loop.run_in_executor(None, _resolve_hostname, udp, 50200)
            if resolved is None:
                self._log_error(
                    f"Unable to resolve '{udp}'. Check the address and try again."
                )
                return
            udp = resolved

        try:
            self._log_info(f"Connecting (udp={udp}, usb={usb_device})...")

            driver = RdDriver()
            driver.register_status_listener(self.on_status_event)
            driver.register_lifecycle_listener(self.on_lifecycle_event)
            driver.register_error_listener(self.on_error)
            driver.register_reply_listener(self.on_reply_data)

            opened = driver.start(udp_host=udp, usb_device=usb_device)
            if not opened:
                self._log_info("Transport not available yet (retrying in background)")

            self._ruida_driver = driver

            # Wait for connection with optional timeout + cancel support
            self._session_connected.clear()
            self._session_start_cancel.clear()

            connect_task = asyncio.create_task(self._session_connected.wait())
            cancel_task = asyncio.create_task(self._session_start_cancel.wait())

            done, pending = await asyncio.wait(
                [connect_task, cancel_task],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel unfinished tasks
            for task in pending:
                task.cancel()

            if connect_task in done:
                self._log_info("Session started successfully")
            elif cancel_task in done:
                self._log_error("Session start cancelled by user")
                await self._teardown_session()
                return
            else:
                self._log_error("Session connection timeout")
                await self._teardown_session()
                return

            self._update_status_bar()

        except Exception as e:
            self._log_error(f"Failed to start session: {e}")
            if self._ruida_driver is not None:
                self._ruida_driver.stop()
                self._ruida_driver = None

    async def _stop_session(self) -> None:
        """Disconnect from the controller and clean up resources."""
        if self._ruida_driver is None:
            self._log_info("No active session.")
            return

        try:
            self._ruida_driver.stop()
            self._ruida_driver = None
            self._log_info("Session ended")
            self._update_status_bar()

        except Exception as e:
            self._log_error(f"Error stopping session: {e}")
            self._ruida_driver = None

    async def _teardown_session(self) -> None:
        """Tear down the current session (stop driver, disconnect).

        Used by timeout/cancel paths in _start_session.
        """
        if self._ruida_driver is not None:
            self._ruida_driver.stop()
            self._ruida_driver = None

        self._session_connected.clear()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # AppAdapter-compatible interface (called from driver background thread)
    # ------------------------------------------------------------------

    def on_lifecycle_event(self, event: RdStatusEvent) -> None:
        """Handle a lifecycle event from the driver (CONNECTED/DISCONNECTED/TERMINATED).

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        """

        def _update() -> None:
            if self._logging_enabled:
                self._status_log.write(f"[STATUS] {event.value}")
            self._event_count += 1
            if event in (RdStatusEvent.DISCONNECTED, RdStatusEvent.TERMINATED):
                self._session_disconnected = True
                self._session_connected.clear()
            elif event is RdStatusEvent.CONNECTED:
                self._session_disconnected = False
                self._session_connected.set()
            self._update_status_bar()

        self.call_from_thread(_update)

    def on_status_event(self, event: RdStatusEvent | StatusDict) -> None:
        """Handle a status event from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        """

        def _update() -> None:
            if isinstance(event, dict):
                # StatusDict received — update tracked values
                for key, value in event.items():
                    if key == "MEM_CURRENT_POSITION_X":
                        raw, formatted = value
                        self._position["X"] = (raw, formatted)
                        self._last_coord_change["X"] = time.time()
                    elif key == "MEM_CURRENT_POSITION_Y":
                        raw, formatted = value
                        self._position["Y"] = (raw, formatted)
                        self._last_coord_change["Y"] = time.time()
                    elif key == "MEM_CURRENT_POSITION_Z":
                        raw, formatted = value
                        self._position["Z"] = (raw, formatted)
                        self._last_coord_change["Z"] = time.time()
                    elif key == "MEM_CURRENT_POSITION_U":
                        raw, formatted = value
                        self._position["U"] = (raw, formatted)
                        self._last_coord_change["U"] = time.time()
                    elif key == "MEM_CARD_ID":
                        raw, formatted = value
                        self._position["Card"] = (raw, formatted)
                    elif key == "MEM_BED_SIZE_X":
                        raw, formatted = value
                        self._position["BedX"] = (raw, formatted)
                    elif key == "MEM_BED_SIZE_Y":
                        raw, formatted = value
                        self._position["BedY"] = (raw, formatted)
                    elif key == "MEM_MACHINE_STATUS":
                        raw, formatted = value
                        self._machine_status = int(raw)
                        self._machine_status_formatted = formatted
                    elif key in (
                        "MACHINE_STATUS_MOVING",
                        "MACHINE_STATUS_PART_END",
                        "MACHINE_STATUS_JOB_RUNNING",
                    ):
                        self._status_bits[key] = bool(value)
                    else:
                        logging.getLogger(__name__).warning(
                            "Unknown status key in StatusDict: %s = %r", key, value
                        )
                if self._logging_enabled:
                    self._status_log.write(f"[STATUS] {dict(event)}")
                self._event_count += 1
                self._update_status_bar()
                return

            # Script events received via status listener path
            # (_notify_script_skipped / _notify_script_error in driver)
            if self._logging_enabled:
                self._status_log.write(f"[STATUS] {event.value}")
            self._event_count += 1
            if event is RdStatusEvent.DISCONNECTED:
                self._session_disconnected = True
                self._session_connected.clear()
            self._update_status_bar()

        self.call_from_thread(_update)

    def on_reply_data(self, replies: list[str]) -> None:
        """Handle formatted reply data from the driver.

        Called from the driver's background thread. Bridges to the asyncio
        event loop thread via call_from_thread for safe widget updates.
        Receives already-formatted reply strings from the driver.
        """

        def _update() -> None:
            for formatted in replies:
                if self._logging_enabled:
                    self._reply_log.write(formatted)
                    # If the last command was a GET_SETTING, also show the
                    # decoded reply in the main log pane
                    if self._last_cmd_was_get_setting:
                        self._log_widget.write(f"  ← {formatted}")
                        self._last_cmd_was_get_setting = (
                            False  # reset — only log the first reply
                        )
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

        Thread-safe: can be called from any thread.
        """
        if self._ruida_driver is None:

            def _error() -> None:
                self._log_error("No active session to run script.")

            if threading.get_ident() == self._thread_id:
                _error()
            else:
                self.call_from_thread(_error)
            return

        def _run() -> None:
            try:
                self._ruida_driver.run(script)
                self._script_count += len(script)
                self._update_status_bar()
            except RuntimeError as e:
                self._log_error(str(e))

        if threading.get_ident() == self._thread_id:
            _run()
        else:
            self.call_from_thread(_run)

    # ------------------------------------------------------------------
    # Introspection (?) subsystem
    # ------------------------------------------------------------------

    def _resolve_path(self, path: str) -> tuple[Any, str | None]:
        """Resolve a dotted path against the introspection object map.

        Returns (resolved_object, error_message).
        On success, error_message is None.
        On failure, resolved_object is None and error_message describes the issue.
        """
        # Handle 'self.' prefix for RdsAdapter itself
        if path.startswith("self."):
            obj = self
            remaining = path[5:]
        elif path == "self":
            return (self, None)
        else:
            # Split off the root object name
            parts = path.split(".", 1)
            root_name = parts[0]
            try:
                obj = self._introspect_map[root_name]()
            except KeyError:
                known = ", ".join(sorted(self._introspect_map.keys()))
                return (None, f"Unknown object: {root_name}. Known: {known}")
            remaining = parts[1] if len(parts) > 1 else ""

        # Walk the attribute chain
        if remaining:
            try:
                obj = functools.reduce(getattr, remaining.split("."), obj)
            except AttributeError as e:
                return (None, f"No such attribute: {path} ({e})")

        return (obj, None)

    def _handle_introspect(self, expr: str) -> str:
        """Handle a ?-prefixed introspection expression.

        No parentheses → variable view (repr).
        With parentheses → method call with args, or signature display if no args.
        """
        expr = expr.strip()
        if not expr:
            return "Usage: !<object>[.<attribute>] [args...]"

        # Split on first '(' to detect method call
        paren_idx = expr.find("(")
        if paren_idx == -1:
            # No parens: split on space for potential args
            parts = expr.split(None, 1)
            path = parts[0]
            args_raw = parts[1] if len(parts) > 1 else ""

            obj, err = self._resolve_path(path)
            if err:
                return err

            if args_raw:
                # Space-separated args → call the method
                args = self._parse_introspect_args(args_raw)
                try:
                    result = obj(*args)
                    return self._format_value(result)
                except TypeError as e:
                    return f"TypeError: {e}"
                except Exception as e:
                    return f"Error calling {path}: {type(e).__name__}: {e}"

            # No args → show signature for callables, repr for variables
            if callable(obj):
                return self._format_signature(obj)
            return self._format_value(obj)

        # Method call with parens
        path = expr[:paren_idx].strip()
        args_part = expr[paren_idx + 1 :]

        # Find matching close paren
        if not args_part.endswith(")"):
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
            return self._format_value(result)
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

    def _format_value(self, value: Any) -> str:
        """Format a Python value for readable multi-line TUI display.

        Lists/tuples/dicts: one item per line with 2-space indentation.
        Multi-line strings (docstrings): literal line breaks.
        Other values: repr() output.
        """
        if isinstance(value, dict):
            if not value:
                return "{}"
            lines = ["{"]
            for k, v in value.items():
                v_fmt = self._format_value(v)
                if "\n" in v_fmt:
                    lines.append(f"  {repr(k)}:")
                    for sub in v_fmt.split("\n"):
                        lines.append(f"    {sub}")
                else:
                    lines.append(f"  {repr(k)}: {v_fmt}")
            lines.append("}")
            return "\n".join(lines)

        if isinstance(value, (list, tuple)):
            if not value:
                return "[]" if isinstance(value, list) else "()"
            bracket_open = "[" if isinstance(value, list) else "("
            bracket_close = "]" if isinstance(value, list) else ")"
            lines = [bracket_open]
            for item in value:
                item_fmt = self._format_value(item)
                for sub in item_fmt.split("\n"):
                    lines.append(f"  {sub}")
                lines[-1] += ","
            lines.append(bracket_close)
            return "\n".join(lines)

        if isinstance(value, str) and "\n" in value:
            # Multi-line string (docstring) — display with literal line breaks
            return value

        return repr(value)

    def _parse_introspect_args(self, args_str: str) -> list[Any]:
        """Parse a comma-separated argument string into Python values.

        Tries ast.literal_eval first. Falls back to hex→bytearray conversion
        for hex-formatted strings starting with 0x.
        """
        if not args_str:
            return []

        result = []
        for arg in args_str.split(","):
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
            clean = arg[2:] if arg.startswith("0x") else arg
            if not clean:
                continue
            try:
                if (
                    all(c in "0123456789abcdefABCDEF" for c in clean)
                    and len(clean) % 2 == 0
                ):
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

    def _update_status_bar(self) -> None:
        """Update the bottom status bar with connection info, counters, and position."""
        # Connection info
        if self._session_disconnected:
            conn = "[red]Disconnected[/red]"
        elif (
            self._ruida_driver is not None
            and self._ruida_driver.is_connected
        ):
            conn = "[green]Connected[/green]"
        elif self._ruida_driver is not None:
            conn = "[yellow]Connecting[/yellow]"
        else:
            conn = "[red]Disconnected[/red]"

        # Transport info
        if self._ruida_driver is not None and self._ruida_driver.transport is not None:
            transport = self._ruida_driver.transport
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

        # Machine info (Card, BedX, BedY) — use pre-formatted values from StatusDict
        machine_parts = []
        card = self._position.get("Card")
        if card is not None:
            _, formatted = card
            machine_parts.append(f"Card: {formatted}")
        else:
            machine_parts.append("Card: —")
        bedx = self._position.get("BedX")
        if bedx is not None:
            _, formatted = bedx
            machine_parts.append(f"BedX: [bold]{formatted}[/bold]")
        else:
            machine_parts.append("BedX: —")
        bedy = self._position.get("BedY")
        if bedy is not None:
            _, formatted = bedy
            machine_parts.append(f"BedY: [bold]{formatted}[/bold]")
        else:
            machine_parts.append("BedY: —")
        machine = "  ".join(machine_parts)

        # Machine status indicators (MOVE, PART, JOB)
        status_parts = []
        if self._status_bits["MACHINE_STATUS_MOVING"]:
            status_parts.append("[bold green]MOVE[/bold green]")
        else:
            status_parts.append("MOVE")
        if self._status_bits["MACHINE_STATUS_PART_END"]:
            status_parts.append("[bold green]PART[/bold green]")
        else:
            status_parts.append("PART")
        if self._status_bits["MACHINE_STATUS_JOB_RUNNING"]:
            status_parts.append("[bold green]JOB[/bold green]")
        else:
            status_parts.append("JOB")
        indicators = " ".join(status_parts)

        # Position — use pre-formatted values from StatusDict
        now = time.time()
        pos_parts = []
        for axis in ("X", "Y", "Z", "U"):
            v = self._position[axis]
            if v is not None:
                _, formatted = v
                if now - self._last_coord_change.get(axis, 0.0) < 2.0:
                    pos_parts.append(f"[bold yellow]{axis}: {formatted}[/bold yellow]")
                else:
                    pos_parts.append(f"{axis}: [bold]{formatted}[/bold]")
            else:
                pos_parts.append(f"{axis}: —")
        pos = "  ".join(pos_parts)

        self._status_bar.update(
            f"{conn}  {transport_info}  |  {indicators}  |  {machine}  |  {counters}  |  {pos}"
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
        """AppAdapter interface — stop the driver if running."""
        if self._ruida_driver is not None:
            self._ruida_driver.stop()
            self._ruida_driver = None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _on_exit_app(self) -> None:
        """Save command history, then clean up active session when TUI exits.

        Overrides the internal Textual lifecycle hook _on_exit_app (called when
        the app exits) to persist command history and tear down the session.
        In Textual 8.x, the shutdown message is ExitApp, which dispatches to
        _on_exit_app — NOT on_exit (which has no matching message class).
        """
        self._save_command_history()
        if self._ruida_driver is not None:
            self._ruida_driver.stop()
            self._ruida_driver = None
        await super()._on_exit_app()

    @staticmethod
    def _history_path() -> str:
        """Return path to the command history file (XDG config dir)."""
        config_dir = os.path.expanduser("~/.config/ruida-tui")
        return os.path.join(config_dir, "command_history.json")

    def _load_command_history(self) -> None:
        """Load command history from disk. Silently handles missing/corrupt files."""
        path = self._history_path()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, list) and all(isinstance(item, str) for item in data):
                self._command_history = data[-500:]
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            pass  # Start with empty history

    def _save_command_history(self) -> None:
        """Save command history to disk. Silently handles write failures."""
        path = self._history_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._command_history[-500:], f)
        except (OSError, PermissionError):
            pass  # Non-fatal if we can't save history

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_resolvable_address(self, token: str) -> bool:
        """Check if a GET_SETTING address token can be resolved (MT mnemonic or numeric)."""
        return is_resolvable_address(token, self._parser._mt_map)


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
        return ""  # Empty is OK (USB mode)

    # Already an IP? No DNS needed.
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    # Has spaces? Can't be a valid hostname.
    if " " in host:
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
