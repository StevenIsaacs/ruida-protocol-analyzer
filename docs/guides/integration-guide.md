# Integration Guide

**Application:** `rpa.py` / `rpa-script`  
**Source:** `ruidadriver/ruida_driver.py` (RdDriver), `rpascript/tui_adapter.py` (TuiAdapter)  
**Status:** As-built (describes current implementation)

---

## 1. Introduction

This guide covers two integration paths for working with Ruida laser controllers programmatically:

| Audience | Path | Section |
|----------|------|---------|
| Application developers embedding controller control | Direct RdDriver API | [§2](#2-direct-rddriver-integration) |
| TUI/testing developers automating session workflows | TuiAdapter emulation | [§3](#3-tui-emulation-for-testing) |

### Prerequisites

- Python 3.10+
- Basic familiarity with Ruida laser controllers and the rpascript format (see [rpascript Guide](rpascript-guide.md))
- A Ruida controller on the local network (UDP) or connected via USB serial

### Companion Documents

| Document | What It Covers |
|----------|----------------|
| [RdDriver Interface](../api/RdDriver-interface.md) | Full API reference for the RdDriver class |
| [rpascript Guide](rpascript-guide.md) | Script format, command reference, flow-control directives |
| [TUI User Guide](tui-guide.md) | Interactive terminal application usage |

---

## 2. Direct RdDriver Integration

### 2.1 Minimal Integration

```python
from ruidadriver.ruida_driver import RdDriver

driver = RdDriver()
driver.register_status_listener(lambda e: print(f"[STATUS] {e}"))
driver.register_error_listener(lambda m: print(f"[ERROR] {m}"))

if not driver.start(udp_host="192.168.1.100"):
    print("Connection will retry in background...")

driver.run(["GET_SETTING MEM_CARD_ID"])
driver.run(["GET_SETTING MEM_MACHINE_STATUS"])
# ... script executes in background ...
driver.stop()
```

### 2.2 Full Lifecycle

A driver instance must go through a strict lifecycle:

```
__init__() → start() → [run() ... run()] → stop()
```

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `start` | `(udp_host=None, usb_device=None)` | `bool` | Create session, configure transport, open connection, start background runner. `True` if opened immediately, `False` if retry needed (retries in background). Reuses previous params when `None`. Idempotent on same params — no-op if already running. |
| `stop` | `()` | `None` | Stop runner thread (2s join timeout), disconnect session, unregister listeners. Idempotent. Connection params persist for next `start()`. |

**`start()` behavior notes:**
- If params are `None`, reuses values from the previous call.
- If a session exists with different params, calls `stop()` first, then creates a fresh session.
- If a session exists with the same params, returns `True` immediately (no-op).

**`stop()` behavior notes:**
- Sends a shutdown sentinel to the script queue, joins the runner thread with 2s timeout.
- Unregisters all session/transport listeners.
- Sets the internal session reference to `None`.

### 2.3 Listener Registration

All three methods are thread-safe and additive (no remove API).

| Method | Callback Signature | When Called |
|--------|-------------------|-------------|
| `register_status_listener` | `Callable[[RdStatusEvent \| StatusDict], None]` | Session events (CONNECTED, DISCONNECTED) and machine status changes (position, status bits) |
| `register_error_listener` | `Callable[[str], None]` | Script encoding/parsing/execution errors; VmRSS warnings |
| `register_reply_listener` | `Callable[[list[str]], None]` | Formatted reply strings for non-handled GET_SETTING commands |

**Important threading rules:**
- Listener callbacks fire from **background threads** (runner thread or handshake thread). UI applications must use thread-safe dispatch (e.g., `call_from_thread()` in Textual, `invokeLater()` in Qt).
- All listener lists are copied under `RLock` before iteration. Each callback is individually guarded — one faulty callback cannot block other listeners.
- Register listeners **before** calling `start()`. Listener registration does NOT retroactively fire for past events.

**Textual (TUI) bridge pattern:**

```python
def on_status_event(self, event: RdStatusEvent | StatusDict) -> None:
    self.call_from_thread(self._handle_status, event)

def _handle_status(self, event):
    # Runs on the asyncio thread — safe to update widgets
    self.status_log.write(f"[STATUS] {event}")
```

### 2.4 Script Execution

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `run` | `(script: list[str], auto_checksum: bool = False)` | `None` | Queue rpascript-formatted lines for background execution. Raises `RuntimeError` if runner not started. Empty scripts are silent no-op. |
| `cancel_script` | `()` | `None` | Clear all queued scripts and prevent current script from requeuing on disconnect. Thread-safe. |

**Flow-control commands** are processed inline by the driver (not sent to the controller):

| Command | Syntax | Description |
|---------|--------|-------------|
| `DELAY` | `DELAY 5s` or `DELAY 500ms` | Blocking sleep in the runner thread. Interruptible by `stop()`. |
| `WAIT` | `WAIT MACHINE_STATUS_MOVING` | Poll machine status bit until active (set). |
| `WAIT !` | `WAIT !MACHINE_STATUS_JOB_RUNNING to=30s` | Wait for full lifecycle: active → then inactive. Optional `to=` timeout. |

### 2.5 Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_connected` | `bool` | `True` if session exists AND controller is responding to pings. |
| `machine_status` | `dict[int, Any]` | Read-only snapshot of decoded memory values, keyed by memory address. Contains position coordinates, status bits, card ID, bed dimensions. |

### 2.6 Static Format Utilities

These pure formatting functions can be called without a driver instance:

- `format_reply_value(address, raw_reply) -> tuple[str | None, str]` — Decode a reply bytearray using the MT table.
- `format_reply(reply) -> str` — Format a GET_SETTING reply as a human-readable string (e.g., `"MEM_CARD_ID: 12345"`).
- `format_reply_list(replies) -> list[str]` — Map `format_reply` over a list of reply bytearrays.

### 2.7 Threading Model

```
┌──────────────────────────────────┐
│        Application Thread        │
│  start(), run(), stop()          │
│  register_*_listener()           │
├──────────────────────────────────┤
│   Background Script Runner (L6)  │  ← daemon thread
│  - dequeues scripts from queue   │
│  - encodes to binary             │
│  - calls transport.write()       │
│  - handles DELAY/WAIT commands   │
├──────────────────────────────────┤
│    Handshake Thread (L4)         │  ← daemon thread, inside RdTransport
│  - ACK/REPLY state machine       │
│  - unswizzles + validates data   │
│  - fires TransportEvent          │
├──────────────────────────────────┤
│   Status Monitor Thread (L5)     │  ← daemon thread, inside RdStatus
│  - ping/query scheduling         │
│  - auto-reconnect on failure     │
│  - fires RdStatusEvent           │
└──────────────────────────────────┘
```

Key threading rules:
1. **`start()` and `stop()` are blocking** — `stop()` joins the runner thread with 2s timeout and disconnects synchronously.
2. **`run()` is non-blocking** — appends to a `queue.Queue`; the runner thread processes asynchronously.
3. **Listener callbacks fire from background threads** — applications must use thread-safe dispatch for UI updates.

### 2.8 Error Handling

| Condition | Behavior |
|-----------|----------|
| `start()` with empty/unreachable host | Returns `False`; status monitor retries in background |
| `start()` with different params than prior call | Calls `stop()` first, then creates fresh session |
| `run()` before `start()` | Raises `RuntimeError("Script runner not started. Call start() first.")` |
| `run([])` (empty script) | Silent no-op |
| Script encoding error | Fires `SCRIPT_ERROR` + error listener; continues to next script |
| Transport disconnect mid-script | Re-queues full script; fires `DISCONNECTED` |
| `cancel_script()` during execution | Clears queue; current script iteration won't requeue |
| `SET_FILE_SUM` mismatch + `auto_checksum=False` | Raises `ValueError` with expected/actual values |
| `SET_FILE_SUM` mismatch + `auto_checksum=True` | Auto-recalculates checksum; logs warning; continues |
| Duplicate `SET_FILE_SUM` | Raises `ValueError("Duplicate SET_FILE_SUM")` |
| Listener callback raises exception | Caught by `except Exception: pass`; other listeners unaffected |

---

## 3. TUI Emulation for Testing

The `TuiAdapter` class in `rpascript/tui_adapter.py` wraps `RdDriver` with an emulation layer that logs operations and provides a programmatic interface outside the TUI event loop. This is useful for integration testing and automation.

### 3.1 Delegated API

All 9 API items delegate to the underlying `RdDriver` instance with `[EMU]` logging:

| Method | Signature | Notes |
|--------|-----------|-------|
| `start` | `(udp_host=None, usb_device=None) -> bool` | Creates RdDriver on first call, registers TUI listeners, delegates to `RdDriver.start()` |
| `stop` | `() -> None` | Delegates to `RdDriver.stop()`, clears driver reference |
| `run` | `(script=None, auto_checksum=False) -> Any` | Logs first 3 lines as preview, stores in `_loaded_script`, delegates to `run_script()` |
| `register_status_listener` | `(listener) -> None` | Delegates; raises `RuntimeError` if no active driver |
| `register_error_listener` | `(listener) -> None` | Delegates; raises `RuntimeError` if no active driver |
| `register_reply_listener` | `(listener) -> None` | Delegates; raises `RuntimeError` if no active driver |
| `cancel_script` | `() -> None` | Delegates; safe to call without active driver (no-op) |
| `is_connected` | *(property)* `-> bool` | Passthrough to `RdDriver.is_connected`; `False` if no active driver |
| `machine_status` | *(property)* `-> dict[int, Any]` | Passthrough to `RdDriver.machine_status`; `{}` if no active driver |

### 3.2 Programmatic TuiAdapter Usage

```python
from rpascript.tui_adapter import TuiAdapter

# Create adapter without starting the TUI event loop
adapter = TuiAdapter()
adapter.start(udp_host="192.168.1.100")

adapter.run([
    "GET_SETTING MEM_CARD_ID",
    "MOVE_ABS_XY X=100mm Y=200mm",
    "SET_FILE_SUM",
], auto_checksum=True)

# Access loaded script
print(adapter._loaded_script)  # ["GET_SETTING MEM_CARD_ID", ...]
print(adapter.is_connected)    # True if controller is responding
print(adapter.machine_status)  # {0x057E: (12345, "12345"), ...}

adapter.stop()
```

### 3.3 What Emulation Does NOT Do

This is critical to understand before using TuiAdapter for testing:

- **No controller response simulation** — cannot fake `CONNECTED`/`DISCONNECTED` events. The adapter requires real hardware to produce status updates.
- **No hardware timing** — emulated `DELAY`/`WAIT` commands still block via the real driver. The adapter does not accelerate or skip flow-control commands.
- **No status injection** — cannot inject fake `machine_status` values. `is_connected` and `machine_status` are passthrough properties that require a real connection.
- **No mock layer** — there is no in-memory simulation of controller behavior. All commands are sent to real hardware.

### 3.4 Checksum Discrepancy

When using `auto_checksum=True`, the auto-calculated checksum may not match checksums from LightBurn captures. There is a known ~220 byte discrepancy between this tool's checksum calculation and LightBurn's. Verify expected vs. calculated checksums manually when comparing against LightBurn output.

---

## 4. Integration Testing Patterns

### Pattern 1 — Offline Script Validation

Validate script syntax and structure before sending to hardware:

```python
from rpascript.interpreter import ScriptParser

parser = ScriptParser()
try:
    parsed = parser.parse_lines([
        "MOVE_ABS_XY X=100mm Y=200mm",
        "LASER_ON Power=80%",
        "SET_FILE_SUM",
    ])
    print(f"Parsed {len(parsed)} commands successfully")
except ValueError as e:
    print(f"Script validation error: {e}")
```

### Pattern 2 — Checksum Verification

Test checksum mismatch handling with `auto_checksum`:

```python
from ruidadriver.ruida_driver import RdDriver

driver = RdDriver()
# With auto_checksum=False (default), mismatch raises ValueError
try:
    driver.run(["MOVE_ABS_XY X=100mm Y=200mm", "SET_FILE_SUM = 99999"])
except ValueError as e:
    print(f"Expected checksum mismatch: {e}")

# With auto_checksum=True, it auto-fixes and continues
driver.run(["MOVE_ABS_XY X=100mm Y=200mm", "SET_FILE_SUM = 99999"],
           auto_checksum=True)  # no error
```

### Pattern 3 — Workflow Composition

Test head/job/tail assembly via TUI commands without a connection. Assemble commands from different segments:

```python
head = [
    "SET_ABSOLUTE",
    "MOVE_ABS_XY X=0mm Y=0mm",
]
job = [
    "MOVE_ABS_XY X=100mm Y=100mm",
    "LASER_ON Power=80%",
    "MOVE_ABS_XY X=200mm Y=200mm",
    "LASER_OFF",
]
tail = [
    "MOVE_ABS_XY X=0mm Y=0mm",
    "SET_FILE_SUM",
]

full_script = head + job + tail
# Compose via TUI: /head → /job → /tail → /list
```

### Pattern 4 — Flow Control

Test `DELAY` and `WAIT` behavior by examining the driver's flow-control handlers:

```python
script = [
    "DELAY 500ms",
    "WAIT MACHINE_STATUS_MOVING",
    "WAIT !MACHINE_STATUS_JOB_RUNNING to=30s",
    "MOVE_ABS_XY X=100mm Y=200mm",
]
# The driver processes these inline in the runner thread:
# - DELAY: time.sleep(0.5)
# - WAIT: polls machine status bit until set
# - WAIT !: polls until bit is set then cleared (with timeout)
```

### Pattern 5 — Capture Pipeline Round-Trip

Verify end-to-end data flow through the capture/decode/generate pipeline:

```
capture → /import log → /save job rds → /load rds → /list
```

```bash
# Step 1: Capture traffic
./capture 192.168.1.100 my-job.log

# Step 2: Import into TUI (saves editable script)
# TUI command: /import my-job.log
# TUI command: /save job my-job.rds

# Step 3: Load the saved script
# TUI command: /load my-job.rds
# TUI command: /list  # verify line count matches original
```

### Pattern 6 — Re-queue on Disconnect

Test that `cancel_script()` correctly prevents re-queue on disconnect:

```python
from ruidadriver.ruida_driver import RdDriver
import time

driver = RdDriver()
driver.start(udp_host="192.168.1.100")
driver.run(["MOVE_ABS_XY X=100mm Y=200mm" for _ in range(100)])

# Cancel mid-execution — current iteration won't requeue
driver.cancel_script()
# On disconnect: script is NOT re-queued (cancel flag is set)
```

---

## 5. End-to-End Pipeline Walkthrough

This walkthrough traces a single capture file through the entire toolchain.

### Step 1 — Capture

```bash
./capture 192.168.1.100 my-job.log
```

Produces `my-job.log` (tshark binary output).

### Step 2 — Import and Save as Script

```bash
# Start TUI
python rpascript/tui.py

# Inside TUI, import the capture
/import my-job.log

# Decode the captured commands and save as rpascript
/save job my-job.rds
```

The TUI decodes the binary capture into human-readable rpascript format and writes `my-job.rds`.

### Step 3 — Replay via rpa-script

```bash
# Generate tshark output from the script
rpa-script my-job.rds -o output.tshark
```

Produces `output.tshark` with re-encoded binary packets.

### Step 4 — Verify Round-Trip

```bash
# Decode both files and compare
python rpa.py my-job.log
python rpa.py output.tshark
```

The decoded output should have identical packet sequences (timestamps may vary). This verifies that the capture → script → re-encode pipeline preserves all command data.

---

## 6. AI Agent Integration Guidelines

This section is for AI agents (e.g., OpenCode) that are tasked with integrating RdDriver/TuiAdapter into an application.

### 6.1 Prerequisite Reading Chain

Before writing any integration code, read in this order:

1. **[AGENTS.md](../../AGENTS.md)** — Project overview, commands, architecture, key conventions
2. **[This guide](#1-introduction)** — Integration paths, patterns, pitfalls
3. **Relevant source files** (see below)

### 6.2 Key Source Files

| File | What It Contains |
|------|-----------------|
| `ruidadriver/ruida_driver.py` | RdDriver class (full lifecycle, listeners, flow control) — class starts at line 54, 791 lines total |
| `rpascript/tui_adapter.py` | TuiAdapter emulation layer — API items at lines 2237-2390 |
| `rpascript/interpreter.py` | ScriptParser for offline validation |

### 6.3 What to Give an Agent

For best results, include in your prompt:
- **Specific source file paths** (use the table above)
- **Concrete integration goal** (e.g., "Write a class that connects to the controller, runs these 3 commands, and reports the response")
- **Acceptance criteria** (e.g., "Must compile, must handle RuntimeError when start() is not called first")
- **Target framework/tech stack** (e.g., "FastAPI background task", "Qt application", "CLI script")

### 6.4 Agent-Friendly Patterns

These patterns from §4 require no hardware or minimal hardware:

| Pattern | Why Agent-Friendly |
|---------|-------------------|
| **Pattern 1 (Offline Validation)** | Pure logic — no hardware needed. Tests script parsing only. |
| **Pattern 3 (Workflow Composition)** | Tests script assembly logic without connection. Validates structure only. |
| **Pattern 5 (Capture Round-Trip)** | Tests data flow end-to-end using files only. Verifies no data loss. |

### 6.5 Common Pitfalls

- **`run()` requires `start()` first** — calling `run()` before `start()` raises `RuntimeError("Script runner not started...")`. Always confirm the lifecycle order.
- **Listeners do not retroactively fire** — register listeners before `start()`. Events that occur between `start()` and listener registration are lost.
- **Checksum discrepancy** — `auto_checksum=True` may not match LightBurn captures. If comparing against LightBurn output, verify checksums independently.
- **No mock layer** — `TuiAdapter` delegates to real hardware. It cannot simulate controller responses or inject fake status values. Unit tests requiring simulated hardware must implement their own mock layer.
- **`start()` returns `False` on failure** — this is not an exception. The driver retries in background. Check `is_connected` property to confirm connection status.

### 6.6 Verification Workflow

Since this project has no automated test infrastructure, agents should:

1. **Write a minimal smoke test** — instantiate `RdDriver`, register a listener, call `run` with an empty script, verify no crash.
2. **Run `python -m py_compile`** on all changed files to verify syntax.
3. **For cross-file changes**, also compile the files that import from changed modules.

---

## 7. Configuration Notes

- **Transport:** UDP (Ethernet) is default. USB (serial via pyserial) is optional — pass `usb_device=` instead of or in addition to `udp_host=`.
- **Ping interval:** 5000ms default. Queries every 1000ms.
- **Timeouts:** Per-command timeout 250ms, gross timeout 15s for long operations (home sequences, etc.).
- **Connection retry:** Every 1000ms when not connected.

---

## 8. Remote Control via RPyC

This section covers the RPyC (Remote Python Call) integration path, which makes all 10 TuiAdapter API items remotely callable. This enables headless control of Ruida laser controllers from external applications, CI/CD pipelines, or distributed systems.

### 8.1 Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Client Process                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  RPyC Client Connection                       │   │
│  │  • Connects via TCP + optional TLS            │   │
│  │  • Sends auth token (optional)                │   │
│  │  • Calls exposed_* methods via netref         │   │
│  └────────────┬─────────────────────────────────┘   │
└───────────────┼─────────────────────────────────────┘
                │  TCP (encrypted if TLS)
┌───────────────┼─────────────────────────────────────┐
│  ┌────────────┴─────────────────────────────────┐   │
│  │  RPyC Server (ThreadedServer)                 │   │
│  │  • Binds to host:port (default 127.0.0.1:18812)│   │
│  │  • Validates auth token (optional)             │   │
│  │  • Dispatches to RpycTuiService               │   │
│  └────────────┬─────────────────────────────────┘   │
│  ┌────────────┴─────────────────────────────────┐   │
│  │  RpycTuiService                               │   │
│  │  • Wraps TuiAdapter instance                  │   │
│  │  • Delegates all 10 API items                 │   │
│  │  • Wraps netref callbacks in error handlers   │   │
│  └────────────┬─────────────────────────────────┘   │
│  ┌────────────┴─────────────────────────────────┐   │
│  │  TuiAdapter                                    │   │
│  │  • Emulates RdDriver API                       │   │
│  │  • Manages driver lifecycle                    │   │
│  │  • All calls logged with [EMU] prefix          │   │
│  └────────────┬─────────────────────────────────┘   │
│  ┌────────────┴─────────────────────────────────┐   │
│  │  RdDriver                                     │   │
│  │  • Actual controller communication            │   │
│  └──────────────────────────────────────────────┘   │
│                   Server Process                     │
└─────────────────────────────────────────────────────┘
```

### 8.2 Starting the RPC Server

The RPC server is started from within the TUI using the `server start` command:

```bash
# Minimal (localhost, no auth)
server start

# Custom port
server start port=18812

# With authentication token (remote access)
server start host=0.0.0.0 token="s3cret!t0k3n"

# With TLS encryption
server start host=0.0.0.0 \
    cert=./rpyc-certs/server-cert.pem \
    key=./rpyc-certs/server-key.pem \
    token="s3cret!t0k3n"
```

To stop the server, use `server stop` from within the TUI.

| Parameter | Default      | Description                                    |
|-----------|--------------|------------------------------------------------|
| `host`    | `localhost`  | Bind address. `localhost`/`127.0.0.1` skips auth/TLS |
| `port`    | `18812`      | TCP port                                       |
| `cert`    | (none)       | TLS certificate path (ignored if localhost)    |
| `key`     | (none)       | TLS private key path (ignored if localhost)    |
| `token`   | (none)       | Auth token (ignored if localhost)              |

Parameters persist across `server start`/`stop` cycles — omitted values reuse the previous invocation's values.

### 8.3 Client Connection Example

```python
import socket
import rpyc
from rpyc.utils.factory import connect_stream
from rpyc.utils.classic import SocketStream


def connect_rpyc(host="127.0.0.1", port=18812, token=None):
    """Connect to the RPyC server and return the service root."""
    sock = socket.create_connection((host, port))

    if token:
        # Send auth token: 1 byte length + N bytes token
        token_bytes = token.encode("utf-8")
        sock.sendall(bytes([len(token_bytes)]) + token_bytes)
    else:
        # Send empty length byte for localhost
        sock.sendall(b"\x00")

    conn = connect_stream(SocketStream(sock))
    return conn.root


# --- Usage ---

# Connect
svc = connect_rpyc("127.0.0.1", 18812, token="s3cret!t0k3n")

# Start the driver
connected = svc.start(udp_host="192.168.1.100")
print(f"Connected: {connected}")

# Register a status listener (netref callback)
def on_status(event):
    print(f"[STATUS] {event}")

svc.register_status_listener(on_status)

# Run a script
svc.run(["GET_SETTING MEM_CARD_ID"], auto_checksum=True)

# Check connection
print(f"Is connected: {svc.is_connected()}")

# Stop
svc.stop()
```

### 8.4 Authentication Details

The token authentication protocol uses a simple length-prefixed exchange:

1. Client connects TCP socket
2. Client sends 1 byte (token length N) + N bytes (token UTF-8)
3. Server validates with constant-time comparison (`hmac.compare_digest`)
4. If valid, RPyC handshake proceeds normally

**Localhost exception:** Connections from `127.0.0.1`, `::1`, or `localhost` without a token (empty length byte) are allowed through. This enables local testing without auth while requiring tokens for remote connections.

**Auth failure behavior:** Invalid tokens cause the server to immediately close the connection. The client receives an `EOFError` or `ConnectionRefusedError` on the first RPyC call.

### 8.5 TLS Configuration

TLS is configured via RPyC's `ssl_ctx` parameter, which accepts a standard Python `ssl.SSLContext`:

| File                | Purpose                | Generated By                      |
|---------------------|------------------------|-----------------------------------|
| `ca-cert.pem`      | CA certificate         | `scripts/gen-rpyc-certs.sh`      |
| `ca-key.pem`       | CA private key (keep secret) | `scripts/gen-rpyc-certs.sh` |
| `server-cert.pem`  | Server certificate     | `scripts/gen-rpyc-certs.sh`      |
| `server-key.pem`   | Server private key (keep secret) | `scripts/gen-rpyc-certs.sh` |

Generate with:

```bash
./scripts/gen-rpyc-certs.sh ./rpyc-certs
```

This produces 4096-bit RSA certificates valid for 10 years with full X.509 v3 extensions (SKI, AKI, KeyUsage, ExtendedKeyUsage) required by Python 3.14+.

### 8.6 Netref Callback Caveats

Listener callbacks (`register_status_listener`, `register_error_listener`, `register_reply_listener`) are implemented as RPyC netrefs — the callback function is defined on the client side but invoked by the server.

**What works:**
- Plain functions, lambdas, and instance methods all work as callbacks
- Multiple callbacks can be registered simultaneously
- Callbacks execute on the client side in the client's RPyC connection thread

**What to watch for:**
- **Threading:** Callbacks fire from the server's dispatch thread. Long-running callbacks block the server for that connection. Keep callbacks fast or use your own thread pool.
- **Exceptions:** Exceptions raised in callbacks are caught and logged by `RpycTuiService` with a warning — they don't crash the server. Your callback should handle its own errors.
- **Serialization:** Arguments passed to callbacks must be serializable by RPyC. Basic types (`str`, `int`, `list`, `dict`), `bytearray`, and netrefs work. Custom objects may need explicit serialization.
- **No retroactive events:** Register a callback before events occur. Events between `start()` and callback registration are lost.

### 8.7 Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `ConnectionRefusedError` | Server not running | Check `host` and `port` in `server start` |
| `EOFError: connection closed by peer` | Auth token wrong or missing | Verify `token` in `server start` matches client code |
| `ssl.SSLError: certificate verify failed` | CA cert not trusted | Pass correct CA cert path to client |
| Callback not firing | Listener registered after event | Register before `start()` |
| `RuntimeError: Script runner not started` | `run()` called before `start()` | Call `svc.start()` before `svc.run()` |
| Slow RPC calls | Netref latency over network | Keep scripts small, use batch operations |
