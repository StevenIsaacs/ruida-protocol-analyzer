# Bokeh Visualization Manual Test Procedures

**Project:** Ruida Protocol Analyzer
**Migration:** matplotlib → Bokeh (Phases 1-7)
**Date:** 2026-05-12

## Prerequisites
- Python virtual environment with `bokeh` installed (from `requirements.txt`)
- Test case log files in `discovery/tc/` and `discovery/`

## Test Cases

### Available test case logs

| Log File | Source App | Description |
|----------|-----------|-------------|
| `discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-mk.log` | MeerK40t | Simple rectangle at known position (10x20mm, centered at 115,100). |
| `discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-lb.log` | LightBurn | Same rectangle test from LightBurn for comparison. |
| `discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-rdw.log` | RDWorks | Same rectangle test from RDWorks for comparison. |
| `discovery/tc/tc-2025-10-12-1/tc-2025-10-12-1-lb.log` | LightBurn | Get Position command capture: move to 150,75 and 150,175, press Get Position. |
| `discovery/tc/tc-2025-10-12-1/tc-2025-10-12-1-rdw.log` | RDWorks | Same Get Position test from RDWorks. |
| `discovery/tc/tc-2025-10-21-1/tc-2025-10-21-1-mk.log` | MeerK40t | Power test grid (5x5 squares), varying power/speed settings. |
| `discovery/tc/tc-2025-10-21-1/tc-2025-10-21-1-lb.log` | LightBurn | Same power test grid from LightBurn. |
| `discovery/tc/tc-2025-10-21-1/tc-2025-10-21-1-rdw.log` | RDWorks | Same power test grid from RDWorks (20 layers only). |

### Test 1: Basic Decode (no plotting)
**Command:**
```bash
python rpa.py -o tmp/tc1.txt discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-mk.log
```
**Verify:**
- Exit code 0
- Output created at tmp/tc1.txt
- File contains decoded commands (not empty)
- No ERROR or CRITICAL messages (WARN is OK)

### Test 2: Verbose Decode (no plotting)
**Command:**
```bash
python rpa.py --verbose --raw -o tmp/tc2.txt discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-mk.log
```
**Verify:**
- Exit code 0
- Output created
- File contains verbose output (vrb: lines)

### Test 3: Basic Plot Mode (--plot-moves)
**Command:**
```bash
python rpa.py --plot-moves -o tmp/tc3.txt discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-mk.log
```
**Verify:**
- Exit code 0
- Bokeh server starts (displays URL http://localhost:5006)
- Browser window opens showing XY plot
- All vectors rendered
- Close browser/CTRL+C exits cleanly

### Test 4: Plot Mode with Different Cases
**Commands:**
```bash
python rpa.py --plot-moves -o tmp/tc4a.txt discovery/tc/tc-2025-10-12-1/tc-2025-10-12-1-lb.log
python rpa.py --plot-moves -o tmp/tc4b.txt discovery/tc/tc-2025-10-21-1/tc-2025-10-21-1-mk.log
```
**Verify:** Same as Test 3 for each case

### Test 5: Interactive Features (manual browser verification)
With a Bokeh plot open (from Test 3 or 4):
- [ ] Hover over a vector — tooltip shows: cmd_id:command, start=(x,y), end=(x,y), Length, Power, Speed
- [ ] Scroll wheel — zooms centered on mouse pointer
- [ ] Middle mouse button drag — pans the view
- [ ] Left mouse button drag — box select zoom
- [ ] Right-click on vector → "Open new tab with this vector as start" — new tab opens, filtered
- [ ] Right-click on empty space → "Duplicate current view" — identical copy in new tab

### Test 6: Menu Bar
- [ ] File → Save as PNG — file created
- [ ] File → Save as SVG — file created
- [ ] File → Save as HTML — file created, opens in browser
- [ ] Settings → Fit to Data — axes rescale to data bounds
- [ ] Settings → 1:1 Aspect — aspect ratio toggles
- [ ] Settings → Show Grid — grid toggles on/off
- [ ] ↺ Reset View — axes return to initial state
- [ ] Range sliders (Start/Count) — subset of vectors displayed

### Test 7: Filters
- [ ] Toggle "Moves" off — only cut lines shown (solid), move lines hidden
- [ ] Toggle "Cuts" off — only move lines shown (dashed), cut lines hidden
- [ ] Power range slider — vectors outside range dim
- [ ] Speed range slider — vectors outside range dim

### Test 8: Command Search
- [ ] Type in command search box — autocomplete suggestions appear
- [ ] Select a command — summary displayed below menu bar
- [ ] Vector highlighted with red overlay
- [ ] Click "Open Tab" — new tab opens from that command

### Test 9: Custom Port
**Command:**
```bash
python rpa.py --plot-moves --bokeh-port 6000 -o tmp/tc9.txt discovery/tc/tc-2025-10-11-1/tc-2025-10-11-1-mk.log
```
**Verify:**
- Server starts on port 6000
- Accessible at http://localhost:6000

### Test 10: Error Handling
- [ ] Run with `--plot-moves` without Bokeh installed → warning message, continues without plot
- [ ] Run with `--bokeh-port 5006` when port is in use → error message, continues without plot
- [ ] CTRL+C during plot → clean shutdown (no traceback)

## Expected Reference Output
Reference verbose text outputs exist alongside each .log file in discovery/ (e.g., `tc-2025-10-11-1-mk-vrb.txt`). These were generated before the Bokeh migration and should match current output for the non-plotting parts.
