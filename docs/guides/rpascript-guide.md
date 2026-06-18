# rpascript Guide

**Format:** rpascript (`.rds`)  
**Consumed by:** `RdDriver.run(script: list[str])`  
**Status:** As-built (describes current implementation)

---

## 1. Script Format

A script is a list of strings passed to `RdDriver.run()`. Each line is one
command, comment, or directive. Lines are processed in order by the background
script runner.

### Comments

```rds
# This is an inline comment.
"""This is a block comment (triple-quote) that can span
multiple lines."""
```

- Inline comments start with `#` outside quoted strings.
- Escape `\#` for a literal `#` (e.g., color values).
- Block comments use `"""` delimiters and can span multiple lines.
- Comments and blank lines are silently skipped during execution.

### Session Meta-Commands

```rds
session start udp=192.168.1.100
session start udp=192.168.1.100 usb=ttyUSB0 to=10s
session end
```

- `session start` — Opens a transport connection. Requires at least
  `udp=<IP>` or `usb=<device>`. The optional `to=<timeout>` sets the
  connection timeout (e.g., `10s`, `5000ms`; default 5000ms).
- `session end` — Closes the active session.
- These are processed by `RdDriver.start()` / `RdDriver.stop()`, not
  sent to the controller as commands.

### Server Meta-Commands

```rds
server start host=127.0.0.1
server start host=0.0.0.0 port=18812 cert=server.pem key=server.key token=mytoken
server stop
```

- `server start` — Starts the RPyC remote control server. If `host` is
  `localhost` or `127.0.0.1`, TLS and authentication are disabled.
- `server stop` — Stops the running RPyC server.
- Parameters from previous `server start` are reused when omitted.
- These are processed by `start_rpyc_server()` / `server.stop()`, not
  sent to the controller as commands.

### NEW_PACKET Directive

```rds
NEW_PACKET
```

Marks a packet boundary. Used when the script is replayed through
`rpa-script` to generate tshark-format binary output. Does nothing
during live execution — commands are batched automatically.

### Regular Commands

```rds
MOVE MOVE_ABS_XY X=100mm Y=200mm
CORE NOP
GET_SETTING MEM_CARD_ID
SET_FILE_SUM
START_PROCESS
```

Each line is parsed as:

```
[type_group] [CMD] <mnemonic> [param=value ...] [= expected_reply]
```

- **Optional type group**: One of `CORE`, `MOVE`, `LASER`, `CONFIG`,
  `QUERY`, `ENGRAVE`, `CUT`, `FILE`, `SYSTEM` (or suffixed `_CMD`).
- **Optional `CMD` keyword**: e.g., `CORE CMD NOP`.
- **Mnemonic**: The command name from the protocol command table.
- **Parameters**: Space-separated `KEY=VALUE` pairs.
- **Expected reply** (optional): `= value` — used for synthetic reply
  generation in tshark output. Ignored during live execution.

---

## 2. Command Reference (by Function)

### 2.1 Movement & Positioning

| Mnemonic              | Parameters              | Description                           |
| --------------------- | ----------------------- | ------------------------------------- |
| `SET_ABSOLUTE`        | *(none)*                | Switch to absolute coordinate mode    |
| `MOVE_ABS_XY`         | `X=mm Y=mm`             | Rapid move to absolute X, Y           |
| `MOVE_REL_XY`         | `RelX=mm RelY=mm`        | Rapid move relative from current      |
| `MOVE_REL_X`          | `RelX=mm`               | Rapid move relative on X axis         |
| `MOVE_REL_Y`          | `RelY=mm`               | Rapid move relative on Y axis         |
| `CUT_ABS_XY`          | `X=mm Y=mm`             | Cut (engrave) move to absolute X, Y   |
| `CUT_REL_XY`          | `RelX=mm RelY=mm`        | Cut move relative from current        |
| `CUT_REL_X`           | `RelX=mm`               | Cut move relative on X axis           |
| `CUT_REL_Y`           | `RelY=mm`               | Cut move relative on Y axis           |
| `HOME_XY`             | *(none)*                | Home X and Y axes                     |
| `HOME_Z`              | *(none)*                | Home Z axis                           |
| `HOME_U`              | *(none)*                | Home U axis (rotary)                  |
| `REF_POINT_2`         | *(none)*                | Set reference point (origin)          |
| `REF_POINT_1`         | *(none)*                | Set reference point (alternative)     |
| `FOCUS_Z`             | *(none)*                | Auto-focus Z axis                     |
| `AXIS_X_MOVE`         | `X=mm`                  | Single-axis X move                    |
| `AXIS_Y_MOVE`         | `Y=mm`                  | Single-axis Y move                    |
| `AXIS_Z_MOVE`         | `Z=mm`                  | Single-axis Z move                    |
| `AXIS_U_MOVE`         | `U=mm`                  | Single-axis U move (rotary)           |
| `REL_MOVE_XY`         | `Option={0-3} X=mm Y=mm` | Rapid relative move with option       |

### 2.2 Laser Power

| Mnemonic           | Parameters      | Description                          |
| ------------------ | --------------- | ------------------------------------ |
| `IMD_POWER_1`      | `Power=%`       | Immediate power level for laser 1    |
| `END_POWER_1`      | `Power=%`       | End-of-move power level for laser 1  |
| `IMD_POWER_2`      | `Power=%`       | Immediate power level for laser 2    |
| `END_POWER_2`      | `Power=%`       | End-of-move power level for laser 2  |
| `MIN_POWER_1`      | `Power=%`       | Minimum power for laser 1            |
| `MAX_POWER_1`      | `Power=%`       | Maximum power for laser 1            |
| `MIN_POWER_2`      | `Power=%`       | Minimum power for laser 2            |
| `MAX_POWER_2`      | `Power=%`       | Maximum power for laser 2            |
| `MIN_POWER_1_PART` | `Part={n} Power=%` | Min power for a specific layer/part |
| `MAX_POWER_1_PART` | `Part={n} Power=%` | Max power for a specific layer/part |
| `THROUGH_POWER_1`  | `Power=%`       | Through (pierce) power for laser 1   |
| `LASER_INTERVAL`   | `{:.3f}mS`      | Laser pulse interval                 |
| `LASER_ON_DELAY`   | `{:.3f}mS`      | Delay before laser fires             |
| `LASER_OFF_DELAY`  | `{:.3f}mS`      | Delay after laser stops              |
| `ADD_DELAY`        | `{:.3f}mS`      | Additional delay per segment         |
| `FREQUENCY_PART`   | `Laser={n} Part={n} Freq=KHz` | Frequency for a specific layer |

### 2.3 Speed

| Mnemonic              | Parameters             | Description                      |
| --------------------- | ---------------------- | -------------------------------- |
| `SPEED_LASER_1`       | `Speed=mm/S`           | Cutting/engraving speed for laser 1 |
| `SPEED_LASER_1_PART`  | `Part={n} Speed=mm/S`  | Speed for a specific layer       |
| `SPEED_AXIS`          | `Speed=mm/S`           | Axis movement speed              |
| `SPEED_AXIS_MOVE`     | `Speed=mm/S`           | Axis rapid move speed            |
| `FORCE_ENG_SPEED`     | `Speed=mm/S`           | Force engraving speed override   |

### 2.4 File & Job Control

| Mnemonic                  | Parameters              | Description                          |
| ------------------------- | ----------------------- | ------------------------------------ |
| `START_PROCESS`           | *(none)*                | Begin processing the current job     |
| `STOP_PROCESS`            | *(none)*                | Stop processing                      |
| `PAUSE_PROCESS`           | *(none)*                | Pause processing                     |
| `RESTORE_PROCESS`         | *(none)*                | Resume paused processing             |
| `BLOCK_END`               | *(none)*                | End of a block                       |
| `SET_FILE_SUM`            | *(none)* or `= value`   | File checksum (see §7)               |
| `SET_FILE_NAME`           | `File:string`           | Set the file name for upload         |
| `DOCUMENT_FILE_UPLOAD`    | `FNum={n} {val} {val}`  | Upload document data                 |
| `DOCUMENT_FILE_END`       | *(none)*                | End of file upload                   |
| `DOCUMENT_NUMBER`         | `{n}`                   | Select document by number            |
| `SELECT_DOCUMENT`         | `{n}`                   | Select document by index             |
| `DELETE_DOCUMENT`         | `{val} {val}`           | Delete a document                    |
| `FILE_TRANSFER`           | *(none)*                | Enter file transfer mode             |

### 2.5 Settings & Queries

| Mnemonic                  | Parameters              | Description                          |
| ------------------------- | ----------------------- | ------------------------------------ |
| `GET_SETTING`             | `MEM_*`                 | Read a controller memory address     |
| `SET_SETTING`             | `MEM_* {val} {val}`     | Write to a controller memory address |
| `ENQ`                     | *(none)*                | Send keep-alive                      |
| `CORE NOP`                | *(none)*                | No operation                         |

### 2.6 System & Layer Control

| Mnemonic                  | Parameters              | Description                          |
| ------------------------- | ----------------------- | ------------------------------------ |
| `LAYER_END`               | *(none)*                | End of current layer                 |
| `WORK_MODE_1`—`6`         | *(none)*                | Select work mode                     |
| `LASER_DEVICE_0`—`1`      | *(none)*                | Select laser device                  |
| `AIR_ASSIST_ON`           | *(none)*                | Enable air assist                    |
| `AIR_ASSIST_OFF`          | *(none)*                | Disable air assist                   |
| `DB_HEAD`                 | *(none)*                | Dual-head mode                       |
| `LAYER_NUMBER_PART`       | `Part={n}`              | Select layer by number               |
| `LAYER_COLOR`             | `Color=#RRGGBB`         | Set layer color                      |
| `LAYER_COLOR_PART`        | `Part={n} Color=#RRGGBB` | Set color for a specific layer     |
| `EN_LASER_TUBE_START`     | `State=ON/OFF`         | Enable laser tube at start           |

### 2.7 Array Operations

| Mnemonic                  | Parameters              | Description                          |
| ------------------------- | ----------------------- | ------------------------------------ |
| `ARRAY_START`             | `{n}`                   | Begin array definition               |
| `ARRAY_END`               | *(none)*                | End array definition                 |
| `ARRAY_DIRECTION`         | `Dir={n}`               | Array direction                      |
| `ARRAY_REPEAT`            | *(7 integers)*          | Array repeat counts                  |
| `ARRAY_ADD`               | `X=mm Y=mm`             | Array step offset                    |
| `ARRAY_MIRROR`            | `{n}`                   | Mirror mode                          |
| `ARRAY_TOP_LEFT`          | `X=mm Y=mm`             | Array boundary top-left              |
| `ARRAY_BOTTOM_RIGHT`      | `X=mm Y=mm`             | Array boundary bottom-right          |

### 2.8 USB Rotary / Z / U Axis

| Mnemonic                  | Parameters              | Description                          |
| ------------------------- | ----------------------- | ------------------------------------ |
| `ENABLE_BLOCK_CUTTING`    | `State=ON/OFF`         | Enable block (rotary) cutting        |
| `FEED_AUTO_CALC`          | `{n}`                   | Auto-calculate feed length           |
| `FEED_LENGTH`             | `{val}`                 | Set feed length                      |
| `FEED_REPEAT`             | `{val} {val}`           | Feed repeat counts                   |
| `FEED_INFO`               | `{val}`                 | Feed information                     |
| `REL_MOVE_Z`              | `Option={0-3} Z=mm`     | Relative Z-axis move                 |
| `REL_MOVE_U`              | `Option={0-3} U=mm`     | Relative U-axis move (rotary)        |
| `ELEMENT_INDEX`           | `{n}`                   | Select element by index              |
| `ELEMENT_NAME`            | `String:string`         | Set element name                     |

---

## 3. Parameter Types & Units

When writing parameter values, the parser strips the unit suffix and
converts the numeric portion to the appropriate internal encoding.

| Parameter     | Example Values              | Unit/Format        | Notes                             |
| ------------- | --------------------------- | ------------------ | --------------------------------- |
| Coordinate    | `100.500`, `100.500mm`      | `{:.3f}mm`           | Micrometer precision internally   |
| Power         | `80`, `80.0%`               | `{:.1f}%`           | 0–100% range                      |
| Speed         | `150`, `150.000mm/S`        | `{:.3f}mm/S`        | Millimeters per second            |
| Frequency     | `20`, `20.000KHz`           | `{:.3f}KHz`         | Kilohertz                         |
| Time/Delay    | `5`, `5.000mS`, `500ms`     | `{:.3f}mS`           | Milliseconds. Also accepts `MS`.  |
| Switch/State  | `ON`, `OFF`, `1`, `0`, `TRUE`, `FALSE` | Boolean | Case-insensitive for words.        |
| Color         | `#FF0000`                   | `#RRGGBB`           | Hex color code                     |
| Part/Layer    | `1`, `2`, ...               | integer             | Layer number (index).              |
| Memory addr   | `MEM_CARD_ID`, `0x057E`    | mnemonic or hex     | Mnemonic from MT table (see §4).   |
| Integer       | `42`, `0x1A`                | plain / hex        | Signed or unsigned per command.    |
| File number   | `FNum:1`, `1`               | `uint14`            | Document file index.               |
| Option/Rapid  | `0`, `1`, `2`, `3`          | integer (0–3)       | ROT option: ORIGIN, LIGHT_ORIGIN, etc. |

### Suffix tolerance

All unit suffixes are case-insensitive and optional:
- `mm`, `MM` — coordinates
- `%` — power
- `mm/S`, `mm/s`, `MM/S` — speed
- `KHz`, `kHz` — frequency
- `mS`, `ms`, `MS` — time

A plain number without a suffix uses the command's default unit.

---

## 4. Memory Addresses

Use `GET_SETTING` and `SET_SETTING` with mnemonics from the MT table.
Mnemonics resolve to 2-byte memory addresses (MSB, LSB).

### Machine Status

| Mnemonic                    | Address   | Value Type      | Description                    |
| --------------------------- | --------- | --------------- | ------------------------------ |
| `MEM_MACHINE_STATUS`        | `0x0400` | `int` (bitmask) | bit 0=Moving, bit 1=Part End, bit 2=Job Running |
| `MEM_CURRENT_POSITION_X`    | `0x0420` | `float`/`int`   | Current X position             |
| `MEM_CURRENT_POSITION_Y`    | `0x0421` | `float`/`int`   | Current Y position             |
| `MEM_CURRENT_POSITION_Z`    | `0x0422` | `float`/`int`   | Current Z position             |
| `MEM_CURRENT_POSITION_U`    | `0x0423` | `float`/`int`   | Current U position (rotary)    |

### Card & Bed

| Mnemonic                    | Address   | Value Type      | Description                    |
| --------------------------- | --------- | --------------- | ------------------------------ |
| `MEM_CARD_ID`               | `0x057E` | `int`           | Card identifier                |
| `MEM_BED_SIZE_X`            | `0x057F` | `float`/`int`   | Bed width (X)                  |
| `MEM_BED_SIZE_Y`            | `0x0580` | `float`/`int`   | Bed height (Y)                 |

### Axis Configuration (Laser 1)

| Mnemonic                    | Address   | Description                        |
| --------------------------- | --------- | ---------------------------------- |
| `MEM_G0_VELOCITY`           | `0x005`  | Rapid (G0) velocity                |
| `MEM_HOME_VELOCITY`         | `0x00C`  | Home sequence velocity             |
| `MEM_LASER_PWM_FREQUENCY_1` | `0x011`  | PWM frequency for laser 1          |
| `MEM_LASER_MIN_POWER_1`     | `0x012`  | Minimum power setting for laser 1  |
| `MEM_LASER_MAX_POWER_1`     | `0x013`  | Maximum power setting for laser 1  |
| `MEM_AXIS_PRECISION_1`      | `0x021`  | Axis 1 precision (steps/mm)        |
| `MEM_AXIS_MAX_VELOCITY_1`   | `0x023`  | Axis 1 maximum velocity            |
| `MEM_AXIS_MAX_ACC_1`        | `0x025`  | Axis 1 maximum acceleration        |
| `MEM_BED_SIZE_X`            | `0x026`  | Bed width (X)                      |
| `MEM_BED_SIZE_Y`            | `0x036`  | Bed height (Y)                     |

Axis 2 and Axis 3 follow the same pattern at offsets `0x030`+ and
`0x040`+ respectively.

### Example

```rds
# Read card identity
GET_SETTING MEM_CARD_ID

# Read current position
GET_SETTING MEM_CURRENT_POSITION_X
GET_SETTING MEM_CURRENT_POSITION_Y
```

---

## 5. Flow Control

### DELAY

Pauses script execution for a specified duration. The delay runs in the
background runner thread and is interruptible by `cancel_script()` or
`stop()`.

```rds
DELAY 5s       # Wait 5 seconds
DELAY 500ms    # Wait 500 milliseconds
```

### WAIT

Polls a machine status bit until it becomes active. The runner thread
blocks during polling but remains responsive to shutdown signals.

```rds
# Wait for the machine to start moving
WAIT MACHINE_STATUS_MOVING

# Wait for the machine to stop moving
WAIT !MACHINE_STATUS_MOVING

# Wait for a job to complete (active → then inactive)
WAIT !MACHINE_STATUS_JOB_RUNNING     # No timeout
WAIT !MACHINE_STATUS_JOB_RUNNING to=30s  # With timeout
```

The `!` prefix means "wait for active, then wait for inactive" — it
handles the full lifecycle. Without `!`, it waits only for the bit to
become set.

The optional `to=` parameter sets a timeout (e.g., `30s`, `10000ms`).
If the timeout expires before the condition is met, the script continues
with an error event.

### Common Patterns

```rds
# Home and wait for completion
HOME_XY
WAIT !MACHINE_STATUS_MOVING to=30s

# Cut a line and wait for it to finish
SET_ABSOLUTE
MOVE_ABS_XY X=10mm Y=10mm
LASER_ON Power=80%
SPEED_LASER_1 Speed=150mm/S
CUT_ABS_XY X=110mm Y=10mm
LASER_OFF
WAIT !MACHINE_STATUS_MOVING to=30s
```

---

## 6. Checksum

The file checksum is a running sum of all engrave/cut command bytes.
It ensures the file was transmitted without corruption.

### Auto-Calculate (Placeholder Mode)

Write `SET_FILE_SUM` with no value and the runner fills in the correct
checksum after all preceding commands are encoded:

```rds
START_PROCESS
... engrave / cut commands ...
BLOCK_END
SET_FILE_SUM
```

The checksum is calculated from all commands between `START_PROCESS` and
`BLOCK_END` that are related to engraving, cutting, and layer
configuration. Memory commands (`GET_SETTING`, `SET_SETTING`), keyboard
commands, and `SET_FILE_SUM` itself are excluded.

### Verify Mode

Provide the expected checksum value when you know it:

```rds
SET_FILE_SUM = 9763961
```

The runner accumulates its own checksum and compares. A mismatch raises
`ValueError` (unless `auto_checksum=True`, in which case it logs a
warning and auto-corrects).

### Rules

- At most **one** `SET_FILE_SUM` per script.
- `SET_FILE_SUM` must come near the end — after all commands whose bytes
  contribute to the checksum.
- Excluded from checksum: `0xA7` (keypress), `0xDA` (SETTING/GET_SETTING),
  and `0xE5/0x05` (`SET_FILE_SUM` itself).

---

## 7. Complete Examples

### Homing and Position Query

```rds
# Home all axes
HOME_XY
HOME_Z
WAIT !MACHINE_STATUS_MOVING to=30s

# Read the resulting position
GET_SETTING MEM_CURRENT_POSITION_X
GET_SETTING MEM_CURRENT_POSITION_Y
GET_SETTING MEM_CURRENT_POSITION_Z
```

### Rectangle Cut with Power and Speed

```rds
# Setup
SET_ABSOLUTE
SPEED_LASER_1 Speed=120mm/S
IMD_POWER_1 Power=85%

# Navigate to start position
MOVE_ABS_XY X=50mm Y=50mm

# Cut a 100mm × 100mm rectangle
LASER_ON Power=85%
CUT_ABS_XY X=150mm Y=50mm
CUT_ABS_XY X=150mm Y=150mm
CUT_ABS_XY X=50mm Y=150mm
CUT_ABS_XY X=50mm Y=50mm
LASER_OFF

WAIT !MACHINE_STATUS_MOVING to=60s
```

### Multipower Cut with Layers

```rds
# Layer 1: Light engrave
CORE CMD LAYER_END           # End previous layer
SPEED_LASER_1 Speed=300mm/S
IMD_POWER_1 Power=30%
CUT_ABS_XY X=100mm Y=100mm

# Layer 2: Deep cut
CORE CMD LAYER_END
SPEED_LASER_1 Speed=50mm/S
IMD_POWER_1 Power=90%
CUT_ABS_XY X=200mm Y=100mm
```

### Job with File Checksum

```rds
HOME_XY
WAIT !MACHINE_STATUS_MOVING to=30s

SET_ABSOLUTE
START_PROCESS

SPEED_LASER_1 Speed=200mm/S
IMD_POWER_1 Power=75%

MOVE_ABS_XY X=10mm Y=10mm
LASER_ON
CUT_ABS_XY X=200mm Y=10mm
LASER_OFF

BLOCK_END
SET_FILE_SUM

GET_SETTING MEM_CARD_ID
```

### Array (Step-and-Repeat)

```rds
# Define a 2×3 array with 50mm spacing
ARRAY_START 1
ARRAY_REPEAT 2 0 0 3 0 0 0
ARRAY_ADD X=50mm Y=50mm
ARRAY_DIRECTION 1

# The element to repeat
START_PROCESS
SPEED_LASER_1 Speed=200mm/S
IMD_POWER_1 Power=80%
CUT_ABS_XY X=20mm Y=20mm

BLOCK_END
ARRAY_END
SET_FILE_SUM
```

---

## 8. Passing Scripts to RdDriver

Scripts are passed as lists of strings:

```python
from ruidadriver.ruida_driver import RdDriver

driver = RdDriver()
driver.start(udp_host="192.168.1.100")

script = [
    "HOME_XY",
    "WAIT !MACHINE_STATUS_MOVING to=30s",
    "GET_SETTING MEM_CURRENT_POSITION_X",
    "GET_SETTING MEM_CURRENT_POSITION_Y",
]

driver.run(script)

# ... later ...
driver.stop()
```

### auto_checksum Parameter

```python
# Warn on checksum mismatch instead of raising
driver.run(script, auto_checksum=True)
```

### Script Lifecycle

1. `run()` queues the script for background execution.
2. The runner thread parses and encodes each line.
3. Encoded commands are sent to the transport layer.
4. On disconnect, the script is re-queued automatically.
5. Call `cancel_script()` to abort.

---

## 9. Error Handling

| Condition                   | Behavior                                   |
| --------------------------- | ------------------------------------------ |
| Runner not started          | `RuntimeError("Call start() first")`     |
| Unknown mnemonic            | `ValueError` at parse time                 |
| Invalid parameter value     | `ValueError` at encode time                |
| Checksum mismatch           | `ValueError` raised (unless auto_checksum) |
| Duplicate `SET_FILE_SUM`    | `ValueError` raised                        |
| Transport disconnect        | Script re-queued, `DISCONNECTED` event fired |
| All other parse/encode errors | `SCRIPT_ERROR` event fired, runner continues |
| Empty script (empty list)   | Silent no-op                               |

Errors during parsing or encoding are caught by the runner, a
`SCRIPT_ERROR` event is fired to registered listeners, and the runner
continues to the next command. The script is not aborted unless the
controller disconnects.
