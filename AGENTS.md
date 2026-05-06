# AGENTS.md — ruida-protocol-analyzer

## Project at a glance

A tool that decodes binary UDP packets captured from Ruida CNC/laser controllers. Entry point: `cpa.py`.

## Commands

```
python cpa.py capture.log              # Decode a tshark log file
python cpa.py --on-the-fly --ip <IP>   # Live capture via tshark
./capture <ip> <file>                  # Capture tshark log (bash)
./capture.ps1 -if Ethernet -ip <IP> -out <file>  # Capture (PowerShell)
./decode <file>                        # Produces <file>.txt + <file>-vrb.txt
./link <type> <case> <app>             # Symlink a test case into discovery/
```

- `./decode` **requires a venv to be active** (checks `$VIRTUAL_ENV`). Create one from `requirements.txt` if needed.
- `requirements.txt` has `bokeh` and `numpy` (plotting). Everything else is stdlib.

## Architecture

| Path | Role |
|------|------|
| `cpa.py` | CLI entry — arg parsing, opens input, runs analyzer |
| `cpalib/` | Output emission, line parsing, plotting UI (Bokeh) |
| `protocols/ruida/` | Protocol state machine, parser, command tables, checksum logic |
| `discovery/` | **Git submodule** — test cases (`tc/`), problems (`prb/`), captured logs |

Only the Ruida UDP protocol is currently implemented. Adding a new protocol means creating a parallel `protocols/<name>/` directory with its own analyzer.

## Workflow

1. **Capture** traffic with `./capture` (or `--on-the-fly` mode) → produces `.log`
2. **Decode** with `./decode` → produces `.txt` (summary) and `-vrb.txt` (verbose)
3. **Investigate** unknown commands/parameters marked `TBD` in output

The `./link` script creates symlinks (`discovery/selected.log`, `selected.txt`, `selected-vrb.txt`) pointing to a specific test case. Apps are identified as `mk` (MeerK40t), `lb` (LightBurn), `rdw` (RDWorks).

## Key conventions

- Protocol command tables live in `protocols/ruida/ruida_parser.py` (CT dict: byte → command name + param specs).
- Parameter decoder tuples: `(format_string, decoder_fn, raw_type)` — e.g. `('X={}mm', coord, 'int_35')`.
- Checksum is a running sum of bytes in engrave/cut commands; excludes memory and jog commands. Known ~220-byte discrepancy with LightBurn captures.
- `discovery/` is a **separate git repo** (submodule). Commit test case changes there, not in the parent.

## No test/lint/CI infrastructure

There are no unit tests, no formatter, no linter, no type checker, and no CI pipeline. Verify changes manually by running `cpa.py` against existing capture logs in `discovery/`.

## Dev tips

- README states VSCode or its forks like VSCodium and Antigravity are the recommended IDEs for stepping through code alongside plots.
- `.vscode/launch.json` exists for debugging.
- `--plot-moves` opens a Bokeh server application in browser showing interactive head moves with power/speed popups, context menus, and filtering.
- Ignored dirs: `discovery/`, `testing/`, `tmp/`, `build/`, `dist/`, `__pycache__`, `.png` files.
