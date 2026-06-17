"""
L7 TuiAdapter — Textual-based TUI for interactive Ruida script execution.

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
import sys
import types
import threading
import gc
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import argparse
from rpalib.rpa_emitter import RpaEmitter
try:
    from rpalib.bokeh_app import BokehApp
except ImportError:
    BokehApp = None
from protocols.ruida.ruida_analyzer import RuidaProtocolAnalyzer
from rpascript.generator import ScriptGenerator

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import DescendantFocus, Key
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Header, Input, RichLog, Static

from rpalib.ruida_transcoder import RdDecoder, RdEncoder
from rpascript.encoding import encode_command, is_resolvable_address, parse_value
from rpascript.interpreter import ScriptParser, reconstruct_script_line
from ruidadriver.rd_status import RdStatusEvent
from ruidadriver.ruida_driver import RdDriver, StatusDict

_log = logging.getLogger(__name__)


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


class FileBrowserTree(DirectoryTree):
    """DirectoryTree that filters files by allowed extensions.

    Directories are always shown to allow navigation. When allowed_extensions
    is None, all files are shown.
    """

    def __init__(
        self,
        path: str | Path,
        allowed_extensions: set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(path, **kwargs)
        self._allowed_extensions = allowed_extensions

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for p in paths:
            if p.is_dir():
                yield p
            elif self._allowed_extensions is None:
                yield p
            elif p.suffix.lower() in self._allowed_extensions:
                yield p


class ErrorScreen(ModalScreen):
    """Modal screen that displays a crash traceback and waits for keypress."""

    CSS = """
    ErrorScreen {
        align: center middle;
    }
    #error-box {
        width: 80%;
        height: 80%;
        border: thick $error;
        background: $surface;
    }
    #error-title {
        padding: 1 2;
        text-style: bold;
        background: $error;
        color: $text;
    }
    #error-detail {
        height: 1fr;
        padding: 1 2;
    }
    #error-footer {
        padding: 1 2;
        text-style: dim;
        text-align: center;
    }
    """

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="error-box"):
            yield Static("⚠ Application Crashed", id="error-title")
            yield RichLog(id="error-detail", highlight=True, markup=True)
            yield Static("Press any key to exit.", id="error-footer")

    def on_mount(self) -> None:
        """Render the traceback into the detail panel."""
        from rich.traceback import Traceback

        detail = self.query_one("#error-detail", RichLog)
        tb = Traceback.from_exception(
            type(self._error),
            self._error,
            self._error.__traceback__,
        )
        detail.write(tb)

    def on_key(self, event: Key) -> None:
        """Any key exits the app."""
        self.app.exit(return_code=1)


def _deep_getsizeof(obj: Any, seen: set[int] | None = None, _depth: int = 500) -> tuple[int, bool]:
    """Recursively compute deep memory footprint of an object.

    Walks __dict__, __slots__, and container items (dict, list, tuple, set)
    to sum sys.getsizeof for the object and all objects it transitively
    references.  Stops recursion at primitive types (int, float, str, bytes,
    bool, NoneType) and shared runtime types (type, ModuleType, etc.).

    Uses id()-based cycle detection via the *seen* set.

    Args:
        obj: The object to measure.
        seen: Set of object ids already visited (for cycle detection).

    Returns:
        Tuple of (total deep size in bytes, True if depth limit was exceeded).
    """
    _PRIMITIVE_TYPES = (int, float, str, bytes, bool, type(None))
    _STOP_TYPES = (
        type,
        types.ModuleType,
        types.FunctionType,
        types.BuiltinFunctionType,
        types.BuiltinMethodType,
        types.MethodType,
        types.CodeType,
        types.FrameType,
        types.TracebackType,
        types.GeneratorType,
    )

    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return (0, False)
    seen.add(obj_id)

    # Base size of the object itself
    try:
        total = sys.getsizeof(obj)
    except (TypeError, AttributeError):
        total = 0

    # Stop recursion at primitives, shared runtime types, or depth limit
    if isinstance(obj, _PRIMITIVE_TYPES + _STOP_TYPES):
        return (total, False)
    if _depth <= 0:
        return (total, True)

    # Walk based on container type
    depth_hit = False
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            ks, kh = _deep_getsizeof(k, seen, _depth - 1)
            vs, vh = _deep_getsizeof(v, seen, _depth - 1)
            total += ks + vs
            depth_hit = depth_hit or kh or vh
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in list(obj):
            item_s, item_h = _deep_getsizeof(item, seen, _depth - 1)
            total += item_s
            depth_hit = depth_hit or item_h

    # Walk instance attributes via __dict__ and __slots__
    if hasattr(obj, '__dict__') and obj.__dict__ is not None:
        d_s, d_h = _deep_getsizeof(obj.__dict__, seen, _depth - 1)
        total += d_s
        depth_hit = depth_hit or d_h

    for _cls in type(obj).__mro__:
        slots = getattr(_cls, '__slots__', ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if slot == '__dict__':
                continue  # Already handled above
            if hasattr(obj, slot):
                try:
                    val = getattr(obj, slot)
                    v_s, v_h = _deep_getsizeof(val, seen, _depth - 1)
                    total += v_s
                    depth_hit = depth_hit or v_h
                except (AttributeError, TypeError):
                    continue

    return (total, depth_hit)


class TuiAdapter(App):
    """Textual-based TUI for interactive Ruida script execution.

    Implements the AppAdapter interface (duck-typing compatible) combined with
    Textual's App (TUI framework) to provide a terminal UI for connecting to
    Ruida controllers, executing rpascript commands, and monitoring status/reply
    events in real-time.

    Usage::
        app = TuiAdapter()
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
        "import",
        "tail",
        "list",
        "save",
        "stop",
        "plot",
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

    #status-log {
        text-style: dim;
    }

    #status-log {
        height: 1fr;
        border-bottom: solid $surface;
    }

    #reply-log {
        height: 1fr;
        border-bottom: solid $surface;
        padding: 1 2;
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

    FileBrowserTree {
        max-height: 15;
        border-top: solid $primary;
        background: $panel;
        margin: 0 1;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ruida_driver: RdDriver | None = None
        self._last_udp_host: str = ""
        self._last_usb_device: str = ""
        self._parser = ScriptParser()
        self._decoder = RdDecoder()
        self._event_count = 0
        self._reply_count = 0
        self._script_count = 0
        self._logging_enabled: bool = True
        self._introspect_map: dict[str, Callable[[], Any]] = {
            "session": lambda: self._ruida_driver,
            "transport": lambda: (
                self._ruida_driver._session.transport if self._ruida_driver._session else None
            ),
            "driver": lambda: self._ruida_driver,
            "status": lambda: (
                self._ruida_driver._session.status if self._ruida_driver._session else None
            ),
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
            "exec": "Execute job from loaded script (/exec script for all lines)",
            "clear": "Clear all log panels, loaded script, head, and tail",
            "quit": "Exit the TUI",
            "log": "Toggle display of status/reply messages (on|off|status)",
            "session": "Start or end a controller session (start udp=<IP> usb=<device> to=<timeout> / end)",
            "head": "Load a script file to prepend to job on execution",
            "import": "Import tshark <file> [magic=0xNN] as a script",
            "tail": "Load a script file to append to job on execution",
            "list": "Display loaded script (/list script), composed job (/list job), head (/list head), or tail (/list tail)",
            "save": "Save composed job to a file (/save job <path>)",
            "stop": "Stop the current operation (session connection or script execution). Also bound to Escape.",
            "plot": "Plot loaded script moves in a Bokeh visualization",
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
        # File browser tree state
        self._file_browser: FileBrowserTree | None = None
        self._file_browse_cmd: str = ""

        # Memory monitor state
        self._mem_prev: dict[str, int] | None = None
        self._mem_initial: dict[str, int] = {}
        self._mem_timer: Any = None

        # GC object counter state
        self._gc_prev: dict[str, tuple[int, int, bool]] | None = None
        self._gc_initial: dict[str, tuple[int, int, bool]] = {}

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
                yield Static(id="reply-log", markup=True)
        yield Static(id="status-bar")

    def on_mount(self) -> None:
        """Widgets are ready — cache references, load history, and log startup message."""
        self._log_widget = self.query_one("#log-area", RichLog)
        self._status_log = self.query_one("#status-log", RichLog)
        self._reply_log = self.query_one("#reply-log", Static)
        self._status_bar = self.query_one("#status-bar", Static)
        # Restrict focus to command input only — Tab stays on Input
        self._log_widget.can_focus = False
        self._status_log.can_focus = False
        self._update_status_bar()
        self._load_command_history()
        self.query_one("#command-input", Input).focus()
        # Start memory monitor
        self._mem_timer = self.set_interval(15, self._update_mem_monitor)
        self._update_mem_monitor()

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

            # Validate GET_SETTING commands have resolvable addresses
            if cmd.get("mnemonic", "") == "GET_SETTING":
                params = cmd.get("params", [])
                if not params or not self._is_resolvable_address(params[0]):
                    reason = (
                        f"unknown address: {params[0]}" if params else "missing address"
                    )
                    self._log_error(f"Invalid GET_SETTING: {reason}")
                    return

            if cmd["type"] == "SESSION_START":
                asyncio.create_task(self._start_session(**cmd["params"]))
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
            self._dismiss_file_browser()
            if self._suggest_popup.is_attached:
                self._suggest_popup.remove()
            return
        value = event.value

        # --- File-browse detection: must precede slash suggest logic ---
        cmd, path_part = self._check_file_browse_trigger(value)
        if cmd:
            self._show_file_browser(cmd, path_part)
            return
        elif self._file_browser is not None:
            # Was browsing but command changed — dismiss
            self._dismiss_file_browser()

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
        # File browser keyboard handling — takes priority over history/suggest
        if self._file_browser is not None:
            if event.key == "escape":
                event.stop()
                self._dismiss_file_browser()
                inp.focus()
                return
            if event.key == "tab":
                event.stop()
                if inp.has_focus:
                    self._file_browser.focus()
                else:
                    inp.focus()
                return
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
        elif cmd == "import":
            self._cmd_import(args)
        elif cmd == "tail":
            self._cmd_tail(args)
        elif cmd == "list":
            self._cmd_list(args)
        elif cmd == "save":
            self._cmd_save(args)
        elif cmd == "stop":
            self._cmd_stop(args)
        elif cmd == "plot":
            self._cmd_plot(args)

    # ------------------------------------------------------------------
    # _ImportCollector — in-memory script line collector for /import
    # ------------------------------------------------------------------

    class _ImportCollector:
        """In-memory equivalent of ScriptGenerator — accumulates .rds lines
        from decoded parser command data instead of writing to a file."""

        def __init__(self) -> None:
            self.lines: list[str] = []
            self._pending_line: str | None = None
            self._pending_expect: str | None = None
            self._last_cmd_n = 0
            self._packet_count = 0

        @staticmethod
        def _extract_reply_expect(decoded: str) -> str | None:
            """Extract the reply value from a decoded command string.

            Returns the reply value as a string, or '?' for unknown/TBD values,
            or None if no reply is present.
            """
            if ":Reply:" in decoded:
                reply_part = decoded.split(":Reply:", 1)[1]
                if "Unknown" in reply_part or "TBD" in reply_part:
                    return "?"
                return reply_part
            return None

        def write_command(
            self,
            *,
            label,
            cmd_values,
            param_list,
            command,
            sub_command,
            decoded,
            cmd_n,
        ) -> None:
            """Receive a decoded command from the parser callback.

            Mirrors ScriptGenerator.write_command — buffers the formatted line
            until any reply callback arrives (same cmd_n) so the reply value
            can be captured as ``= expected``.
            """
            # Reply on same cmd_n → capture expected value
            if cmd_n == self._last_cmd_n:
                if self._pending_expect is None:
                    self._pending_expect = self._extract_reply_expect(decoded)
                return

            # New command → flush any previously buffered line first
            self._flush_pending()

            self._last_cmd_n = cmd_n
            self._pending_expect = None
            line = ScriptGenerator._format_line(label, param_list, cmd_values, decoded)
            self._pending_line = line

        def on_new_packet(self) -> None:
            """Called once per host→controller packet, before any commands in it."""
            self._flush_pending()
            self._packet_count += 1
            if self._packet_count > 1:
                self.lines.append("NEW_PACKET")

        def _flush_pending(self) -> None:
            """Write the buffered command line, appending ``= <expect>`` if a
            reply value was captured from a subsequent callback on the same cmd_n."""
            if self._pending_line is None:
                return
            line = self._pending_line
            if self._pending_expect is not None:
                line += f"  = {self._pending_expect}"
            self.lines.append(line)
            self._pending_line = None

        def get_script(self) -> list[str]:
            """Flush any remaining buffered line and return all collected lines."""
            self._flush_pending()
            return self.lines

    def _cmd_import(self, args: str) -> None:
        """Import a tshark capture file as a script.

        Decodes the tshark file in-process using the RuidaProtocolAnalyzer
        pipeline, converts decoded commands to .rds script lines via the
        _ImportCollector, and loads the result into _loaded_script for
        /exec or /save.
        """
        if not args:
            self._log_error("Usage: /import <path> [magic=0xNN]")
            return

        tokens = args.split()
        path = os.path.expanduser(tokens[0])

        # Parse optional arguments
        magic = 0x88
        for tok in tokens[1:]:
            if tok.startswith("magic="):
                try:
                    val = tok.split("=", 1)[1]
                    if val.lower().startswith("0x"):
                        magic = int(val, 16) & 0xFF
                    else:
                        raise ValueError
                except (ValueError, IndexError):
                    self._log_error(f"Invalid magic number: {tok}")
                    return

        if not os.path.isfile(path):
            self._log_error(f"File not found: {path}")
            return

        # Build minimal args namespace for the decode pipeline
        ns = argparse.Namespace(
            magic=magic,
            input_file=path,
            input_encoding="utf-8",
            verbose=False,
            raw=False,
            unswizzled=False,
            stop_on_error=False,
            quiet=True,
            output_file=None,
        )

        output = RpaEmitter(ns)
        try:
            fp = open(path, "r", encoding="utf-8")
            analyzer = RuidaProtocolAnalyzer(ns, fp, output)
            collector = self._ImportCollector()
            analyzer.parser.on_command = collector.write_command
            analyzer.on_new_packet = collector.on_new_packet
            analyzer.decode()
            script = collector.get_script()
        except SyntaxError as e:
            self._log_error(f"Decode error: {e}")
            return
        except ValueError as e:
            self._log_error(f"Command formatting error: {e}")
            return
        except RuntimeError as e:
            self._log_error(f"Decode error: {e}")
            return
        except OSError as e:
            self._log_error(f"File error: {e}")
            return
        finally:
            fp.close()

        if not script:
            self._log_warning(f"No commands found in {path}")
            self._loaded_script = []
            return

        self._loaded_script = script
        self._log_info(f"Imported {len(script)} lines from {path}")

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

        Defaults to executing only the job portion (START_PROCESS to EOF).
        Use '/exec script' to execute all loaded commands.
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
            script = self._build_job_script(self._loaded_script)
            if not script:
                self._log_error("No job commands found (no START_PROCESS/EOF markers).")
                return
            self._log_info(f"Executing {len(script)} job commands...")
            self.run_script(script, auto_checksum=True)
        elif action == "script":
            self._log_info(f"Executing {len(self._loaded_script)} lines...")
            self.run_script(self._loaded_script)
        else:
            self._log_error(f"Unknown exec action: '{action}'. Usage: /exec [script]")

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
        self._reply_log.update("")
        self._loaded_script = []
        self._head_script = []
        self._tail_script = []
        self._mem_initial = {}
        self._mem_prev = None
        self._gc_initial = {}
        self._gc_prev = None
        self._log_info("Logs, head, and tail cleared")

    def _cmd_quit(self) -> None:
        """Exit the TUI."""
        self.exit()

    def action_stop(self) -> None:
        """Handle Escape key: stop current operation."""
        self._cmd_stop("")

    # ------------------------------------------------------------------
    # Exception handling
    # ------------------------------------------------------------------

    def _handle_exception(self, error: BaseException) -> None:
        """Override default: keep app alive and show persistent error screen.

        Textual's default _handle_exception calls panic() which calls
        _close_messages_no_wait(), shutting down the app immediately
        and printing the traceback to stderr after alt-screen restore.
        Instead, we populate _exit_renderables (for terminal fallback)
        and schedule a screen push via call_later so the user sees the
        error and must press a key to exit.
        """
        from rich.text import Text as RichText
        from rich.traceback import Traceback

        self._exit_renderables = [
            RichText(f"Fatal error: {error}", style="bold red"),
            Traceback.from_exception(
                type(error), error, error.__traceback__
            ),
        ]
        # Do NOT call panic() or _fatal_error() — keep the app alive
        self.call_later(self._show_error_screen, error)

    def _show_error_screen(self, error: BaseException) -> None:
        """Push the ErrorScreen onto the screen stack.

        Falls back to terminal exit if push_screen fails (e.g., no
        screen stack yet).
        """
        try:
            self.push_screen(ErrorScreen(error))
        except Exception:
            import sys
            sys.exit(1)

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

    def _cmd_plot(self, args: str = "") -> None:
        """Plot the loaded script in a Bokeh visualization."""
        if not self._loaded_script:
            self._log_error("No script loaded. Use /load <path> first.")
            return

        if BokehApp is None:
            self._log_error("Bokeh is not installed. Install with: pip install bokeh")
            return

        from protocols.ruida.rpa_plotter import RpaPlotter

        parsed = self._parser.parse_lines(self._loaded_script)
        if not parsed:
            self._log_error("No commands found in script.")
            return

        ns = argparse.Namespace(
            input_file="<script>",
            output_file=None,
            bokeh_port=5006,
            quiet=True,
            stop_on_error=False,
            verbose=False,
            raw=False,
            unswizzled=False,
            magic=0x88,
            input_encoding="utf-8",
        )

        out = RpaEmitter(ns)
        plotter = RpaPlotter(out, "Script Plot")
        plotter.plot.enable()

        cmd_id = 0
        for cmd in parsed:
            cmd_type = cmd.get("type")
            if cmd_type in ("SESSION_START", "SESSION_END", "DELAY", "WAIT", "NEW_PACKET"):
                continue

            mnemonic = cmd.get("mnemonic")
            if not mnemonic:
                continue

            info = self._parser.mnemonic_map.get(mnemonic)
            if info is None:
                continue

            prefix_byte = info[0]

            if len(info) == 4:
                sub_cmd = info[2]
                cmd_entry = info[3]
            else:
                sub_cmd = info[1] if len(info) >= 2 else None
                cmd_entry = info[2] if len(info) > 2 else None

            param_specs = cmd_entry[1:] if cmd_entry and len(cmd_entry) > 1 else ()
            param_values = cmd.get("params", [])

            values = []
            for i, spec in enumerate(param_specs):
                if i >= len(param_values):
                    break
                if not isinstance(spec, tuple) or len(spec) < 2:
                    continue
                decoder_fn = spec[1]
                rd_type = spec[2] if len(spec) >= 3 else None
                token = param_values[i].strip()
                if "=" in token:
                    _, token = token.split("=", 1)
                try:
                    values.append(parse_value(token, decoder_fn, rd_type))
                except Exception:
                    continue

            cmd_id += 1
            try:
                plotter.cmd_update(cmd_id, mnemonic, prefix_byte, sub_cmd, values)
            except Exception:
                continue

        if cmd_id == 0:
            self._log_error("No plot-relevant commands found in script.")
            return

        try:
            bokeh_app = BokehApp(ns, plotter.plot)
            if bokeh_app.start(port=5006):
                self._log_info(
                    "Bokeh visualization: http://localhost:{}".format(bokeh_app.port)
                )
            else:
                self._log_error("Failed to start Bokeh server.")
        except Exception as e:
            self._log_error("Failed to start Bokeh server: {}".format(e))

    # ------------------------------------------------------------------
    # File browser helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_extensions_for_cmd(cmd: str) -> set[str] | None:
        """Return allowed file extensions for a file-path command.

        Returns None to allow all extensions, or a set of lowercase extensions.
        """
        if cmd in ("/load", "/head", "/tail"):
            return {".rds"}
        if cmd == "/import":
            return {".log", ".txt"}
        if cmd == "/save":
            return None  # All files
        return set()  # Changed from None to set() — unknown commands show no files

    def _resolve_start_path(self, path_part: str) -> Path:
        """Resolve a partial path to a starting directory for the file browser.

        Handles ~/ expansion and falls back to cwd on unresolvable paths.
        """
        expanded = os.path.expanduser(path_part.strip())
        if not expanded:
            return Path.cwd()
        if os.path.isdir(expanded):
            return Path(expanded)
        if os.path.isfile(expanded):
            return Path(expanded).parent
        # Partial path — try parent directory
        parent = os.path.dirname(expanded)
        if parent and os.path.isdir(parent):
            return Path(parent)
        return Path.cwd()

    def _check_file_browse_trigger(self, value: str) -> tuple[str | None, str]:
        """Check if the input value is a file-path command with at least one space.

        Returns (cmd, path_part) if triggered, or (None, '') if not.
        The cmd is a slash-prefixed command like '/load', '/save', etc.
        The path_part is whatever the user typed after the command (may be empty).
        """
        if not value.startswith("/"):
            return (None, "")

        # Find first space to split command from rest
        space_idx = value.find(" ")
        if space_idx == -1:
            return (None, "")  # No space yet — still typing command name

        cmd = value[:space_idx].lower()
        rest = value[space_idx:].strip()

        # Simple path-taking commands: /load, /head, /tail, /import
        simple_cmds = {"/load", "/head", "/tail", "/import"}
        if cmd in simple_cmds:
            return (cmd, rest)

        # /save job <path> — require "job" subcommand word
        if cmd == "/save":
            if rest == "job" or rest.startswith("job "):
                path_part = rest[3:].strip() if len(rest) > 3 else ""
                return (cmd, path_part)
            return (None, "")

        return (None, "")

    def _show_file_browser(self, cmd_name: str, path_part: str) -> None:
        """Mount the file browser tree widget and dismiss the suggest popup."""
        # Dismiss any existing popups
        if self._suggest_popup.is_attached:
            self._suggest_popup.remove()
        self._suggest_matches = []
        self._suggest_mode = ""

        allowed_exts = self._file_extensions_for_cmd(cmd_name)
        start_path = self._resolve_start_path(path_part)

        # Re-use existing browser if path and command haven't changed
        if (self._file_browser is not None
            and self._file_browse_cmd == cmd_name
            and self._file_browser.path == start_path
        ):
            if not self._file_browser.has_focus:
                self._file_browser.focus()
            return

        # Dismiss any existing file browser
        if self._file_browser is not None:
            if self._file_browser.is_attached:
                self._file_browser.remove()
            self._file_browser = None
        self._file_browse_cmd = ""

        browser = FileBrowserTree(start_path, allowed_extensions=allowed_exts)
        browser.border_title = f"[bold]Select {cmd_name} file[/bold]"
        self._file_browser = browser
        self._file_browse_cmd = cmd_name

        self.query_one("#log-panel").mount(browser, before="#command-input")
        browser.focus()

    def _dismiss_file_browser(self) -> None:
        """Remove the file browser tree and reset state."""
        if self._file_browser is not None:
            if self._file_browser.is_attached:
                self._file_browser.remove()
            self._file_browser = None
        self._file_browse_cmd = ""

    def _set_input_to_path(self, path: Path) -> None:
        """Set the command input value to the user's selected file path."""
        input_widget = self.query_one("#command-input", Input)
        path_str = str(path)

        if self._file_browse_cmd == "/save":
            prefix = "/save job "
        elif self._file_browse_cmd:
            prefix = f"{self._file_browse_cmd} "
        else:
            return

        new_value = f"{prefix}{path_str}"
        self._suppress_popup = True
        input_widget.value = new_value
        input_widget.cursor_position = len(new_value)
        input_widget.focus()
        self._dismiss_file_browser()

    @on(DirectoryTree.FileSelected)
    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Handle file selection from the file browser tree."""
        event.stop()
        if event.control is not self._file_browser:
            return
        self._set_input_to_path(event.path)

    @on(DescendantFocus)
    def on_focus_changed(self, event: DescendantFocus) -> None:
        """Auto-dismiss file browser when focus leaves it or the command input."""
        if self._file_browser is None:
            return
        new_focused = event.widget
        if new_focused is not self._file_browser and new_focused is not self.query_one("#command-input", Input):
            self._dismiss_file_browser()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _start_session(
        self, udp: str | None = None, usb: str | None = None, to: str | None = None
    ) -> None:
        """Connect to a Ruida controller and start the script runner.

        Creates an RdDriver, registers TUI listeners, then calls
        driver.start() which creates the session, opens the transport,
        starts the script runner and status monitor, and returns.
        """
        # Resolve None params against last-used values so params persist
        # across session end/start cycles even when RdDriver is discarded.
        if udp is None:
            udp = self._last_udp_host
        if usb is None:
            usb = self._last_usb_device

        if not udp and not usb:
            self._log_error(
                "No connection parameters. Provide udp=<host> or usb=<device>."
            )
            return

        if self._ruida_driver is not None:
            self._ruida_driver.start(udp_host=udp, usb_device=usb)
            self._last_udp_host = udp
            self._last_usb_device = usb
            return

        timeout: float | None = None
        if to is not None:
            try:
                timeout = _parse_timeout_spec(to)
            except ValueError as e:
                self._log_error(str(e))
                return

        # Check pyserial availability before attempting USB connection
        if usb:
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
            self._log_info(f"Connecting (udp={udp}, usb={usb})...")

            driver = RdDriver()
            driver.register_status_listener(self.on_status_event)

            driver.register_error_listener(self.on_error)
            driver.register_reply_listener(self.on_reply_data)

            opened = driver.start(udp_host=udp, usb_device=usb)
            self._last_udp_host = udp
            self._last_usb_device = usb
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
                self._session_connected.clear()
                self._ruida_driver = None

    async def _stop_session(self) -> None:
        """Disconnect from the controller and clean up resources."""
        if self._ruida_driver is None:
            self._log_info("No active session.")
            return

        try:
            self._ruida_driver.stop()
            self._session_connected.clear()
            self._ruida_driver = None
            self._log_info("Session ended")
            self._update_status_bar()

        except Exception as e:
            self._log_error(f"Error stopping session: {e}")
            self._session_connected.clear()
            self._ruida_driver = None

    async def _teardown_session(self) -> None:
        """Tear down the current session (stop driver, disconnect).

        Used by timeout/cancel paths in _start_session.
        """
        if self._ruida_driver is not None:
            self._ruida_driver.stop()
            self._session_connected.clear()
            self._ruida_driver = None

        self._session_connected.clear()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # AppAdapter-compatible interface (called from driver background thread)
    # ------------------------------------------------------------------

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
            # Determine transport type for log messages
            transport_type = ""
            if (
                self._ruida_driver is not None
                and self._ruida_driver._session is not None
            ):
                transport = self._ruida_driver._session.transport
                if transport.is_usb:
                    transport_type = "USB"
                elif transport.is_udp:
                    transport_type = "UDP"
            suffix = f" ({transport_type})" if transport_type else ""

            if event in (RdStatusEvent.DISCONNECTED, RdStatusEvent.TERMINATED):
                if not self._session_disconnected or event is RdStatusEvent.TERMINATED:
                    msg = (
                        "Disconnected (session ended)"
                        if event is RdStatusEvent.TERMINATED
                        else f"Disconnected{suffix}"
                    )
                    self._log_info(msg)
                self._session_disconnected = True
                self._session_connected.clear()
            elif event is RdStatusEvent.CONNECTED:
                if self._session_disconnected:
                    self._log_info(f"Connected{suffix}")
                self._session_disconnected = False
                self._session_connected.set()
            self._update_status_bar()

        self.call_from_thread(_update)

    def on_reply_data(self, replies: list[str]) -> None:
        """Handle formatted reply data from the driver.

        Logs script command replies to the main TUI window.
        Thread-safe: bridges from driver thread to asyncio thread.
        """
        self.call_from_thread(self._write_replies, replies)

    def _write_replies(self, replies: list[str]) -> None:
        """Write reply strings to the main log area (asyncio thread only)."""
        for formatted in replies:
            self._log_widget.write(f"  ← {formatted}")

    def on_error(self, message: str) -> None:
        """Handle an error condition. Thread-safe via call_from_thread."""

        def _update() -> None:
            self._log_error(message)

        self.call_from_thread(_update)

    def run_script(self, script: list[str], auto_checksum: bool = False) -> None:
        """Queue a script for execution.

        Args:
            script: List of rpascript-formatted command lines.
            auto_checksum: If True, auto-calculate SET_FILE_SUM on mismatch
                with a warning instead of raising.

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
                self._ruida_driver.run(script, auto_checksum=auto_checksum)
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
        # Handle 'self.' prefix for TuiAdapter itself
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
        if not hasattr(self, '_log_widget'):
            return
        self._log_widget.write(f"[SCRIPT] {line}")

    def _log_info(self, message: str) -> None:
        """Log an informational message in cyan."""
        if not hasattr(self, '_log_widget'):
            return
        self._log_widget.write(f"[bold cyan]{message}[/bold cyan]")

    def _log_error(self, message: str) -> None:
        """Log an error message in bold red."""
        if not hasattr(self, '_log_widget'):
            return
        self._log_widget.write(f"[bold red]ERROR: {message}[/bold red]")

    def _log_warning(self, message: str) -> None:
        """Log a warning message in bold yellow."""
        if not hasattr(self, '_log_widget'):
            return
        self._log_widget.write(f"[bold yellow]WARNING: {message}[/bold yellow]")

    def _update_status_bar(self) -> None:
        """Update the bottom status bar with connection info, counters, and position."""
        # Connection info
        if self._session_disconnected:
            conn = "[red]Disconnected[/red]"
        elif self._ruida_driver is not None and self._ruida_driver.is_connected:
            conn = "[green]Connected[/green]"
        elif self._ruida_driver is not None:
            conn = "[yellow]Connecting[/yellow]"
        else:
            conn = "[red]Disconnected[/red]"

        # Transport info
        if self._ruida_driver is not None and self._ruida_driver._session is not None:
            transport = self._ruida_driver._session.transport
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

    def start(self, udp_host: str | None = None, usb_device: str | None = None) -> bool:
        """Start the driver session.

        Emulates RdDriver.start(). Creates a new RdDriver if none exists,
        registers TUI listeners, and delegates to RdDriver.start().

        Args:
            udp_host: UDP host address or hostname.
            usb_device: USB serial device path.

        Returns:
            True if transport opened immediately, False if retry needed.
        """
        if self._ruida_driver is None:
            self._ruida_driver = RdDriver()
            self._ruida_driver.register_status_listener(self.on_status_event)
            self._ruida_driver.register_error_listener(self.on_error)
            self._ruida_driver.register_reply_listener(self.on_reply_data)
        result = self._ruida_driver.start(udp_host=udp_host, usb_device=usb_device)
        self._log_info(
            f"[EMU] driver.start(udp_host={udp_host!r}, usb_device={usb_device!r}) -> {result}"
        )
        return result

    def stop(self) -> None:
        """AppAdapter interface — stop the driver if running."""
        if self._ruida_driver is not None:
            self._log_info("[EMU] driver.stop()")
            self._ruida_driver.stop()
            self._ruida_driver = None

    def run(
        self,
        script: list[str] | None = None,
        auto_checksum: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Queue a script for execution, or start the TUI event loop.

        When *script* is None, delegates to ``App.run(self, **kwargs)`` so that
        ``run_tui()`` can call ``app.run()`` with no arguments and enter the
        Textual event loop normally.

        When *script* is provided, emulates ``RdDriver.run()``: logs the first
        3 command lines to the TUI and stores the script in ``_loaded_script``
        for ``/list`` access.

        Args:
            script: List of rpascript-formatted command lines, or None to
                start the TUI event loop.
            auto_checksum: If True, auto-calculate SET_FILE_SUM on mismatch.
            **kwargs: Forwarded to ``App.run()`` when *script* is None.
        """
        if script is None:
            # Called from run_tui() — start the TUI event loop
            return App.run(self, **kwargs)

        # Emulation path — log first 3 lines with truncation indicator
        if len(script) <= 3:
            preview = " / ".join(script)
        else:
            preview = " / ".join(script[:3]) + f" ... ({len(script)} lines)"
        self._log_info(f"[EMU] driver.run({preview})")

        # Store for /list access
        self._loaded_script = list(script)

        self.run_script(script, auto_checksum=auto_checksum)

    def register_status_listener(
        self, listener: Callable[[RdStatusEvent | StatusDict], None]
    ) -> None:
        """Register a status event listener.

        Emulates RdDriver.register_status_listener(). Delegates to the
        underlying driver if active, raises RuntimeError otherwise.
        """
        if self._ruida_driver is None:
            raise RuntimeError("No active driver. Call start() first.")
        self._ruida_driver.register_status_listener(listener)
        self._log_info(f"[EMU] register_status_listener({listener!r})")

    def register_error_listener(self, listener: Callable[[str], None]) -> None:
        """Register an error listener.

        Emulates RdDriver.register_error_listener().
        """
        if self._ruida_driver is None:
            raise RuntimeError("No active driver. Call start() first.")
        self._ruida_driver.register_error_listener(listener)
        self._log_info(f"[EMU] register_error_listener({listener!r})")

    def register_reply_listener(self, listener: Callable[[list[str]], None]) -> None:
        """Register a reply listener.

        Emulates RdDriver.register_reply_listener().
        """
        if self._ruida_driver is None:
            raise RuntimeError("No active driver. Call start() first.")
        self._ruida_driver.register_reply_listener(listener)
        self._log_info(f"[EMU] register_reply_listener({listener!r})")

    def cancel_script(self) -> None:
        """Cancel the currently running script.

        Emulates RdDriver.cancel_script().
        """
        if self._ruida_driver is not None:
            self._ruida_driver.cancel_script()
            self._log_info("[EMU] cancel_script()")

    @property
    def is_connected(self) -> bool:
        """Return whether the driver is connected.

        Emulates RdDriver.is_connected.
        """
        result = self._ruida_driver is not None and self._ruida_driver.is_connected
        self._log_info(f"[EMU] is_connected -> {result}")
        return result

    @property
    def machine_status(self) -> dict[int, Any]:
        """Return the current machine status dict.

        Emulates RdDriver.machine_status.
        """
        if self._ruida_driver is None:
            self._log_info("[EMU] machine_status -> {} (no driver)")
            return {}
        result = self._ruida_driver.machine_status
        self._log_info(f"[EMU] machine_status -> {len(result)} items")
        return result

    @staticmethod
    def format_reply_value(
        address: int, raw_reply: bytearray
    ) -> tuple[str | None, str]:
        """Format a single reply value.

        Emulates RdDriver.format_reply_value().
        """
        _log.info(f"[EMU] format_reply_value(addr=0x{address:04X}, raw_len={len(raw_reply)})")
        return RdDriver.format_reply_value(address, raw_reply)

    @staticmethod
    def format_reply(reply: bytearray) -> str:
        """Format a reply bytearray.

        Emulates RdDriver.format_reply().
        """
        _log.info(f"[EMU] format_reply(len={len(reply)})")
        return RdDriver.format_reply(reply)

    @staticmethod
    def format_reply_list(replies: list[bytearray]) -> list[str]:
        """Format a list of reply bytearrays.

        Emulates RdDriver.format_reply_list().
        """
        _log.info(f"[EMU] format_reply_list(count={len(replies)})")
        return RdDriver.format_reply_list(replies)

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
        # Stop memory monitor timer to prevent widget access during teardown
        if self._mem_timer is not None:
            self._mem_timer.cancel()
            self._mem_timer = None
        self._save_command_history()
        if self._ruida_driver is not None:
            self._ruida_driver.stop()
            self._session_connected.clear()
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

    @staticmethod
    def _read_mem() -> dict[str, int]:
        """Read memory stats from /proc/self/status.

        Returns dict with keys: VmRSS, VmSize, VmPeak, Threads.
        Returns empty dict on any error (fail silent, monitor simply won't update).
        """
        try:
            with open("/proc/self/status") as f:
                data = f.read()
        except OSError:
            return {}
        result: dict[str, int] = {}
        fields = {"VmRSS", "VmSize", "VmPeak", "Threads"}
        for line in data.splitlines():
            for field in fields:
                if line.startswith(field + ":"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            result[field] = int(parts[1])
                        except ValueError:
                            pass
        return result

    @staticmethod
    def _render_mem_display(
        cur: dict[str, int],
        prev: dict[str, int] | None,
        initial: dict[str, int],
    ) -> str:
        """Format memory stats as a 4-line tabular display.

        Line 1: column headers (VmRSS KB, VmSize KB, VmPeak KB, Threads)
        Line 2: Mem:   current values
        Line 3: Change: delta since previous update (yellow if non-zero)
        Line 4: Total:  total change since start
        """
        fields = ["VmRSS", "VmSize", "VmPeak", "Threads"]
        col_w = [9, 9, 9, 7]  # right-aligned column widths
        sep = "  "  # inter-column gap (2 spaces)
        pad = " " * 8  # 8-char label column (left-aligned Mem:/Change:/Total:)

        # Helper: right-pad a value string in its column
        def _col(s: str, i: int) -> str:
            return f"{s:>{col_w[i]}}"

        # Helper: format delta value with sign, optionally yellow
        def _fmt_delta(d: int, i: int) -> str:
            if d > 0:
                text = f"+{d}"
            elif d < 0:
                text = str(d)
            else:
                text = "0"
            padded = _col(text, i)
            if d != 0:
                padded = f"[yellow]{padded}[/yellow]"
            return padded

        # Line 1: header
        headers = ["VmRSS KB", "VmSize KB", "VmPeak KB", "Threads"]
        hdr_line = pad + sep + sep.join(_col(h, i) for i, h in enumerate(headers))

        # Line 2: Mem (current values)
        cur_vals = [cur.get(f, 0) for f in fields]
        mem_line = f"{'Mem:':<8}" + sep + sep.join(
            _col(str(v), i) for i, v in enumerate(cur_vals)
        )

        # Line 3: Change (delta since previous update)
        if prev is None:
            chg_cells = [_col("-", i) for i in range(4)]
        else:
            chg_cells = []
            for i, f in enumerate(fields):
                d = cur.get(f, 0) - prev.get(f, 0)
                chg_cells.append(_fmt_delta(d, i))
        chg_line = f"{'Change:':<8}" + sep + sep.join(chg_cells)

        # Line 4: Total (delta since start)
        if not initial:
            tot_cells = [_col("-", i) for i in range(4)]
        else:
            tot_cells = []
            for i, f in enumerate(fields):
                d = cur.get(f, 0) - initial.get(f, 0)
                if d > 0:
                    text = f"+{d}"
                elif d < 0:
                    text = str(d)
                else:
                    text = "0"
                tot_cells.append(_col(text, i))
        tot_line = f"{'Total:':<8}" + sep + sep.join(tot_cells)

        return f"{hdr_line}\n{mem_line}\n{chg_line}\n{tot_line}"

    @staticmethod
    def _count_gc_objects() -> dict[str, tuple[int, int, bool]]:
        """Count GC-tracked Ruida PA class instances and their total memory.

        Calls gc.collect(), then iterates gc.get_objects(), filtering to
        classes whose __module__ starts with a Ruida PA package prefix.

        Returns:
            dict mapping class name -> (instance_count, total_bytes, depth_exceeded),
            sorted by total_bytes descending, truncated to top 20.
            Empty dict if gc.collect() itself fails (fail-silent).
        """
        try:
            gc.collect()
        except (AttributeError, TypeError, OSError):
            return {}

        counter: dict[str, tuple[int, int, bool]] = {}
        for obj in gc.get_objects():
            try:
                mod = type(obj).__module__
                if not (
                    mod == "rpa"
                    or mod.startswith(("rpalib.", "protocols.", "rpascript.", "ruidadriver."))
                ):
                    continue
                cls_name = type(obj).__name__
                count, mem, depth_hit = counter.get(cls_name, (0, 0, False))
                obj_mem, obj_hit = _deep_getsizeof(obj)
                counter[cls_name] = (count + 1, mem + obj_mem, depth_hit or obj_hit)
            except (AttributeError, TypeError, OSError):
                continue  # Skip objects that cause errors during inspection

        # Sort by total_bytes descending, take top 20
        try:
            sorted_items = sorted(
                counter.items(), key=lambda kv: kv[1][1], reverse=True
            )[:20]
            return dict(sorted_items)
        except (AttributeError, TypeError, OSError):
            return {}

    @staticmethod
    def _render_gc_display(
        cur: dict[str, tuple[int, int, bool]],
        prev: dict[str, tuple[int, int, bool]] | None,
        initial: dict[str, tuple[int, int, bool]],
    ) -> str:
        """Format GC object counts as a 21-line table (header + 20 data rows).

        Columns: Class(15L)  Count(10R)  Mem(10R)  Change(10R)  Total(10R)

        Args:
            cur: Current snapshot — {class_name: (count, mem_bytes, depth_exceeded)}
            prev: Previous snapshot for Change delta, or None for first update.
            initial: First snapshot for Total delta, or empty for first update.

        Returns:
            Formatted string with Textual markup for non-zero deltas.
        """
        col_w = [15, 10, 10, 10, 10]
        sep = "  "

        # Header row: Class left-aligned, others right-aligned
        headers = ["Class", "Count", "Mem", "Change", "Total"]
        cells: list[str] = []
        for i, h in enumerate(headers):
            if i == 0:
                cells.append(f"{h:<{col_w[i]}}")
            else:
                cells.append(f"{h:>{col_w[i]}}")
        hdr_line = sep.join(cells)

        lines: list[str] = []
        for cls_name in cur:
            cnt, mem, depth_exceeded = cur[cls_name]
            # Change delta
            if prev is None:
                chg = "-"
                chg_str = f"{chg:>{col_w[3]}}"
            else:
                _, p_mem, _ = prev.get(cls_name, (0, 0, False))
                d = mem - p_mem
                if d > 0:
                    chg_str = f"[yellow]{d:>+{col_w[3]}}[/yellow]"
                elif d < 0:
                    chg_str = f"[yellow]{d:>{col_w[3]}}[/yellow]"
                else:
                    chg_str = f"{'0':>{col_w[3]}}"
            # Total delta
            if not initial:
                tot = "-"
                tot_str = f"{tot:>{col_w[4]}}"
            else:
                i_cnt, i_mem, _ = initial.get(cls_name, (0, 0, False))
                td = mem - i_mem
                if td > 0:
                    tot_str = f"{td:>+{col_w[4]}}"
                elif td < 0:
                    tot_str = f"{td:>{col_w[4]}}"
                else:
                    tot_str = f"{'0':>{col_w[4]}}"
            # Mem column
            mem_str = f"{mem:>{col_w[2]}}"
            # Count column
            cnt_str = f"{cnt:>{col_w[1]}}"

            if depth_exceeded:
                cls_str = f"[orange]{cls_name:<{col_w[0]}}[/orange]"
            else:
                cls_str = f"{cls_name:<{col_w[0]}}"
            lines.append(
                sep.join([cls_str, cnt_str, mem_str, chg_str, tot_str])
            )

        return hdr_line + "\n" + "\n".join(lines)

    async def _update_mem_monitor(self) -> None:
        """Timer callback: read memory, count GC objects, render display, update cache."""
        cur = self._read_mem()
        if not cur:
            return

        # --- Memory stats (inline, fast /proc read) ---
        if not self._mem_initial:
            self._mem_initial = dict(cur)
            self._mem_prev = dict(cur)
            rendered_mem = self._render_mem_display(cur, None, {})
        else:
            rendered_mem = self._render_mem_display(cur, self._mem_prev, self._mem_initial)
            self._mem_prev = dict(cur)

        # --- GC object stats (offloaded to executor to avoid blocking event loop) ---
        loop = asyncio.get_running_loop()
        gc_cur = await loop.run_in_executor(None, self._count_gc_objects)

        if gc_cur:
            if not self._gc_initial:
                self._gc_initial = dict(gc_cur)
                self._gc_prev = dict(gc_cur)
                rendered_gc = self._render_gc_display(gc_cur, None, {})
            else:
                rendered_gc = self._render_gc_display(
                    gc_cur, self._gc_prev, self._gc_initial
                )
                self._gc_prev = dict(gc_cur)

            self._reply_log.update(rendered_mem + "\n\n" + rendered_gc)
        else:
            self._reply_log.update(rendered_mem)

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
    """Run the TuiAdapter TUI application.

    Creates an TuiAdapter instance and enters the Textual event loop.
    Blocks until the user quits (Ctrl+C).
    """
    app = TuiAdapter()
    app.run()
