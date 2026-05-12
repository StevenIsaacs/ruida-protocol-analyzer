---
status: complete
phase: 8
updated: 2026-05-12
---

# Implementation Plan: Matplotlib to Bokeh Migration

## Goal
Replace all matplotlib-based plotting in the Ruida Protocol Analyzer with a Bokeh-based web application that provides interactive, tabbed, 2D vector visualization with tooltips, filtering, and future 3D capability.

## Context & Decisions
| Decision | Rationale | Source |
|----------|-----------|--------|
| Bokeh over other alternatives | Bokeh excels at interactive web-based plots, supports streaming data for `--on-the-fly`, has built-in server for real-time updates, and supports hover tooltips natively | `ref:codebase-analysis` (internal codebase audit: cpa_plotter.py 742 lines, cpa_popup.py 182 lines, cpa.py lines 155-192) |
| Virtual environment `.venv-bokeh` | Isolated environment prevents dependency conflicts with existing `.venv`; per prompt requirement for long-running development task | `ref:prompt-requirements` (prompt lines 13-14, 55-56) |
| Tab-based multi-view architecture | Each view (tab) shows XY plot, power histogram, speed histogram; Bokeh's `Panel`/`Tabs` layout supports this natively | `ref:codebase-analysis` (prompt lines 18-26, AGENTS.md line 55) |
| Bokeh server mode | Required for real-time data updates during `--on-the-fly`, interactive filtering, and context menus. Bokeh's `curdoc().add_next_tick_callback` enables thread-safe updates from decoder thread | `ref:codebase-analysis` (cpa.py lines 185-192 show on-the-fly restrictions to be removed) |
| Keep `CpaLine` data class | Decouples data collection from rendering; existing parser integration unchanged (ruida_parser.py line 428, rpa_plotter.py lines 664-695); only the visualization layer changes | `ref:codebase-analysis` (cpa_line.py, rpa_plotter.py method signatures) |
| Remove numpy entirely | Codebase audit confirms numpy is only used for `np.sqrt` in `CpaLine.to_length()` (cpa_line.py line 12) — replaceable with `math.hypot`. Zero non-plotting numpy usage across `protocols/`, `cpa.py`, or `cpalib/` after removing matplotlib files. | `ref:codebase-analysis` (grep of all .py files for numpy imports — only cpa_line.py, cpa_plotter.py, cpa_popup.py) |
| Remove mplcursors dependency | Bokeh has built-in HoverTool — no need for external cursor library | `ref:codebase-analysis` (cpa_plotter.py lines 11, 137, 532-533) |
| Testing strategy: manual against discovery/ test cases | Per project convention (AGENTS.md line 49: "No test/lint/CI infrastructure... verify manually by running cpa.py against existing capture logs"). Phase 7 restructured around structured manual test procedures, not automated unit tests | `ref:codebase-analysis` (AGENTS.md lines 47-49, empty tests/ directory) |
| Queue + document callback for thread safety | Use Python `queue.Queue` to push vector data from decoder thread, and Bokeh's `document.add_next_tick_callback` for thread-safe ColumnDataSource updates — Bokeh's documented pattern for `--on-the-fly` mode | `ref:codebase-analysis` (cpa.py line 251-254 shows current blocking single-thread plot flow) |
| Default Bokeh port 5006 with auto-increment fallback | Standard Bokeh default port; on `OSError: Address already in use`, increment port number up to 5 attempts before failing gracefully | `ref:codebase-analysis` |
| Selenium for PNG export (optional) | Bokeh's `export_png` uses Selenium under the hood; PhantomJS is deprecated since 2017. Document as optional dependency with headless Chrome requirement | `ref:codebase-analysis` |
| Single venv approach for users | Users should rebuild `.venv` from scratch using `requirements.txt` once migration is complete (Phase 6.4 updates requirements.txt). `.venv-bokeh` is only for development isolation during migration. After Phase 6, users can delete `.venv`, recreate from updated `requirements.txt`, and get a clean environment with Bokeh only. | `ref:codebase-analysis` |
| Phase 5 split into three sub-phases | Mouse interaction (5a), menu systems (5b), advanced features (5c) — each independently testable | `ref:plan-review-feedback` |

## Phase 1: Environment Setup & Dependency Management [COMPLETE]
**Depends on:** None (prerequisite for all other phases)
- [x] **1.1 Create setup script `setup_bokeh_env.sh`**
  - Script creates `.venv-bokeh` virtual environment in project root
  - Installs `bokeh` only (numpy is not needed — see Context & Decisions: "Remove numpy entirely")
  - Installs `selenium` (optional, for Bokeh's `export_png` — requires headless Chrome/Firefox; skip if not available)
  - Detects existing `.venv` and prints warning: "A `.venv` exists with matplotlib. This venv will be replaced during migration (Phase 6). For development during migration, activate `.venv-bokeh`."
  - Prints success/failure status
  - Is re-runnable (idempotent — recreates if needed)
- [x] 1.2 Create `requirements-bokeh.txt`
  - Contents: `bokeh` (and any Bokeh dependencies not auto-installed)
  - `selenium` listed as optional (commented out with note)
  - Note: "numpy is NOT needed — removed entirely during migration"
- [x] 1.3 Update `requirements.txt` (temporary, will be finalized in Phase 6.4)
  - Add note: "For development during migration, use `.venv-bokeh` — see `requirements-bokeh.txt`. When migration is complete, delete `.venv` and recreate from this file."
  - Keep matplotlib/mplcursors for now (removed in Phase 6.4 to avoid breaking existing users mid-migration)
- [x] 1.4 Verify environment isolation
  - Confirm `.venv-bokeh` can be activated and `bokeh` imports successfully
  - Confirm `matplotlib` cannot be imported from `.venv-bokeh`

## Phase 2: Core Bokeh Plotting Infrastructure [COMPLETE]
**Depends on:** Phase 1
- [x] 2.1 Create `cpalib/bokeh_plotter.py` — Main Bokeh application class
  - Bokeh Document + Server setup via `curdoc()` pattern
  - ColumnDataSource for vector data (one per view)
  - XY scatter/line plot with:
    - Zoom (scroll wheel centered on mouse position) via Bokeh's `WheelZoomTool`
    - Pan (middle mouse button) via Bokeh's `PanTool` with button override
    - Box select (left mouse button drag) — zoom constrains to maintain current axis aspect ratio (preserves data aspect, does not force square). Implemented via Bokeh's `BoxSelectTool` and range callbacks.
    - Dashed selection rectangle via `BoxSelectTool` overlay styling
  - HoverTool showing vector attributes in the same `CpaLine.annotation` format
  - Mouse button remapping:
    - Left button → BoxSelectTool (zoom-to-rect)
    - Middle button → PanTool (with JavaScript `button` override via `CustomJS`)
    - Right button → Custom context menu (via `CustomJS` or HTML overlay)
  - Power histogram chart (separate Bokeh `figure`)
  - Speed histogram chart (separate Bokeh `figure`)
  - Z-axis placeholder comment for future 3D capability
  - Line coloring based on power percentage (reuse `_gen_color_lut` logic, convert RGB tuples to hex `#RRGGBB`)
  - **Fail-fast checks:** Verify Bokeh import at module load; wrap server start in try/except with graceful fallback
- [x] 2.2 Create `cpalib/bokeh_view.py` — View/Tab management
  - `BokehView` class representing a single tab
  - Contains layout: XY plot (top, large), power histogram (bottom-left), speed histogram (bottom-right)
  - Initial state backup for "reset view" functionality
  - Aspect ratio preset: maintain 1:1 axis aspect ratio for XY plot (prevents visual distortion of CNC toolpaths)
- [x] 2.3 Create `cpalib/bokeh_app.py` — Top-level Bokeh application
  - Bokeh server launcher with port auto-selection (start at 5006, increment on conflict, max 5 attempts)
  - Tab container using Bokeh's `Tabs` widget managing multiple `BokehView` instances
  - Graceful startup failure: if Bokeh server fails to start, fall back to CLI-only mode with warning message
  - First tab displays all vectors; subsequent tabs display subsets
  - "Now plotting moves. Close browser window to exit." message displayed via Bokeh `Div` widget
  - CLI interactive mode entry after decode completes
- [x] 2.4 Implement thread-safe data update mechanism
  - Use Python `queue.Queue` for thread-safe push of vector data from decoder thread
  - Use Bokeh's `document.add_next_tick_callback()` to drain queue and update ColumnDataSource on the main Bokeh server thread
  - Apply Bokeh's document lock pattern (`document.add_next_tick_callback` is already document-lock-aware)
  - Implement server health monitoring: periodic check that Bokeh server is running; if detected dead, surface error to CLI
  - Handle graceful shutdown: signal queue with sentinel value to stop server loop

## Phase 3: Data Integration Layer [COMPLETE]
**Depends on:** Phase 2
- [x] 3.1 Update `cpalib/cpa_line.py`
  - Replace `from numpy import sqrt` with `from math import hypot` (same result, removes sole numpy dependency)
  - `annotation` property remains unchanged (tooltip format preserved exactly)
  - No other structural changes needed — data class is renderer-agnostic
- [x] 3.2 Create adapter between `CpaLine` data and Bokeh `ColumnDataSource`
  - Convert `CpaLine` objects to flat dictionaries for Bokeh ColumnDataSource
  - Map color tuples `(R, G, B)` (0-1 float) to hex strings `#RRGGBB` for Bokeh
  - Handle coordinate sign inversion (Ruida home-is-far-right convention: negate X and Y)
  - Preserve `CpaLine` objects in parallel list for tooltip lookup (annotation strings)
- [x] 3.3 Update `protocols/ruida/rpa_plotter.py`
  - RpaPlotter stays as command router (routes decoded commands to plotter)
  - Internal methods call new Bokeh plotter API instead of `cpalib.cpa_plotter.CpaPlotter`
  - Method signatures preserved: `cmd_update`, `mt_update`, `add_line`, `set_power`, `set_bed_dimension`, `add_rect`
  - Update import from `cpalib.cpa_plotter` to `cpalib.bokeh_plotter`

## Phase 4: CLI & Entry Point Updates [COMPLETE]
**Depends on:** Phase 2, Phase 3
- [x] 4.1 Update `cpa.py` — Entry point modifications
  - `--plot-moves`: No longer ignored when `--on-the-fly`. Launches Bokeh server in background thread.
  - `--on-the-fly`: Remove restrictions — plot is updated in real-time as vectors are decoded via thread-safe queue
  - Remove `args.plot_moves = False` override (cpa.py lines 185-192)
  - Remove stepping-override lines that disable `--plot-moves` with `--on-the-fly`
  - `--step-moves`: Still functional, works in CLI alongside live Bokeh plot
  - Post-decode: Launch Bokeh server (if `--plot-moves`) and enter CLI interactive mode
  - Update help text for all affected arguments
  - Add new argument `--bokeh-port` (default: 5006)
- [x] 4.2 Update CLI behavior
  - After decode completes, display: "Now plotting moves. Close browser window to exit."
  - CLI remains interactive (step, stats, line-atts, etc.) while Bokeh server runs
  - CLI commands push data updates to Bokeh in thread-safe manner via queue
  - CTRL+C or "quit" command: signal sentinel to queue, wait for Bokeh server to stop, then exit cleanly
  - **Fail-safe:** If Bokeh server fails to start, continue CLI-only with warning
- [x] 4.3 Update `./decode` script
  - Venv precedence logic: check for `.venv-bokeh` first (dev venv for migration), fall back to `.venv` (original user venv; will be replaced in Phase 6). If both exist, prefer `.venv-bokeh` without warning (ambient coexistence). If neither exists, error.
  - Update script's usage text (lines 11-12 in ./decode) to reflect `--plot-moves` now works with `--on-the-fly`
  - Update NOTE block (lines 13-15) about matplotlib requirement to reference Bokeh
  - Update all path references if any hardcode `.venv` (verify `VIRTUAL_ENV` env var is used, not hardcoded paths)

## Phase 5a: Core Interaction — Tooltips & Mouse Remapping [COMPLETE]
**Depends on:** Phase 2, Phase 3
- [x] 5a.1 Implement hover tooltips
  - Bokeh `HoverTool` with tooltip formatted identically to `CpaLine.annotation`
  - Tooltip fields: cmd_id, command name, start/end coordinates (3 decimal mm), length, power (1 decimal %), speed (1 decimal mm/S)
  - Format matches current output exactly:
    ```
    @{cmd_id}:@{command}
    start=(@{start_x}, @{start_y})
    end=(@{end_x}, @{end_y})
    Length: @{length}mm
    Power=@{power}%
    Speed=@{speed}mm/S
    ```
  - Ensure tooltip displays immediately on hover (not delayed)
- [x] 5a.2 Implement mouse button remapping
  - Left button: BoxSelectTool — dashed overlay rectangle, on release zoom to selection maintaining current axis aspect ratio (preserves data aspect, prevents distortion of CNC toolpaths)
  - Middle button: PanTool — default Bokeh pan behavior
  - Scroll wheel: WheelZoomTool — zoom centered on mouse pointer (Bokeh default behavior)
  - Button mappings enforced via Bokeh's tool activation policy and JavaScript CustomJS where needed
- [x] 5a.3 Implement tab management
  - Bokeh `Tabs` widget with dynamic add/remove
  - Tab title reflects view parameters (e.g., "All Vectors", "View from Cmd #1450")
  - Right-click handlers attached to plot canvas for context menu triggering

## Phase 5b: Menu Systems — Menu Bar & Context Menus [COMPLETE]
**Depends on:** Phase 5a
- [x] 5b.1 Implement menu bar per view
  - File menu: Save as PNG (`export_png`), SVG (`export_svgs`), standalone HTML (`file_html`)
  - Settings menu: Axis range presets (fit-to-data, 1:1 aspect, square), power color mapping toggle (none/gradient/discrete), grid line toggle
  - Reset button: Restore original view settings from backup captured at view creation
  - Vector range slider: Two-input integer range slider (start index, count), updates all plots on change via callback
- [x] 5b.2 Implement right-click context menu (on vector)
  - "Open new tab with this vector as start" — creates new `BokehView` with the selected vector at cmd_id as first entry
  - New tab opens with XY plot, histograms, and menu bar identical to source view
  - Implemented via Bokeh `CustomJS` or HTML overlay `Div`
- [x] 5b.3 Implement right-click context menu (on empty space)
  - "Duplicate current view in new tab" — creates new `BokehView` with same vector set as source
  - Same layout and settings as source view

## Phase 5c: Advanced Features — Filtering & Searchable Pull-down [COMPLETE]
**Depends on:** Phase 5b
- [x] 5c.1 Implement searchable command pull-down
  - Bokeh `Select` or `AutocompleteInput` widget listing decoded commands by `cmd_id:command_name`
  - Populated in real-time as commands are decoded via data push queue
  - Mouse scroll wheel scrolls through pull-down list (native browser behavior)
  - Hover over command entry shows summary in adjacent `Div` tooltip area
  - Hover over summary item highlights corresponding vector in plot(s) via opacity/color change
  - Right-click on summary item opens new tab with that command as start
- [x] 5c.2 Implement advanced filtering
  - Filter controls in menu bar: vector type (move vs cut toggle), power range slider, speed range slider
  - Filter callback updates ColumnDataSource with filtered subset
  - Unselected vectors shown at configurable lower opacity (default 0.2, adjustable from settings)
  - Filter does not remove data from ColumnDataSource — uses separate visible/invisible column or alpha mapping

## Phase 6: Removal of matplotlib [COMPLETE]
**Depends on:** Phase 5c (all Bokeh features operational)
- [x] 6.1 Deprecation & user migration guidance
  - `setup_bokeh_env.sh` detects existing `.venv` and prints: "Migration complete! To get a clean environment, delete your `.venv` and recreate from `requirements.txt`."
  - Documentation section explaining migration: use `.venv-bokeh` during transition; after Phase 6, delete old `.venv` and recreate from updated `requirements.txt`
  - Reasoning: `.venv-bokeh` is a development-isolation venv for the migration period. After Phase 6, `requirements.txt` contains only `bokeh`, and users should rebuild their main `.venv`. No coexistence layer needed.
- [x] 6.2 Remove all matplotlib code from source
  - Delete `cpalib/cpa_plotter.py`
  - Delete `cpalib/cpa_popup.py`
  - Remove any residual `import matplotlib` or `import mplcursors` from remaining files
  - Scan `protocols/ruida/` and `cpalib/` for any remaining matplotlib references
- [x] 6.3 Update all documentation
  - `README.md`: Replace matplotlib references with Bokeh; update installation instructions (single `.venv` from `requirements.txt`); update `--on-the-fly` and `--plot-moves` option descriptions; update example output (screenshots)
  - `AGENTS.md`: Update to accurately describe Bokeh implementation (currently claims Bokeh on line 55 but matplotlib is still used)
  - `decode` script comments updated
- [x] 6.4 Finalize `requirements.txt`
  - Remove `matplotlib` and `mplcursors` lines
  - Ensure `bokeh` is listed
  - This is the single source of truth for user venv recreation (delete old `.venv`, recreate)

## Phase 7: Testing & Verification [COMPLETE]
**Depends on:** Phase 6 (all Bokeh code in place, matplotlib removed)
- [x] 7.1 Create structured manual test procedures
  - Per project convention (AGENTS.md: "no unit tests... verify manually by running against existing capture logs")
  - Test procedure document: `tests/bokeh-plotting/TEST_PROCEDURE.md`
  - Each test case references specific `discovery/tc/` or `discovery/prb/` log files
- [x] 7.2 Manual regression tests
  - Run `--plot-moves` against each test case in `discovery/tc/` (3-5 test cases)
  - Verify decoded output matches expected (compare `.txt` and `-vrb.txt` output)
  - Verify tooltip format matches: `cmd_id:command\nstart=(x, y)\nend=(x, y)\nLength: N.NNNmm\nPower=N.N%\nSpeed=N.Nmm/S`
  - Verify all vectors rendered (count matches decoded command count)
- [x] 7.3 Interactive feature verification
  - Zoom with scroll wheel — verify centering on mouse pointer
  - Pan with middle mouse button — verify smooth panning
  - Box select with left mouse button — verify dashed rectangle, aspect-ratio-preserving zoom
  - Right-click on vector → "Open in new tab" — verify new tab opens with correct vector as start
  - Right-click on empty space → "Duplicate view" — verify identical copy in new tab
- [x] 7.4 `--on-the-fly` verification
  - Run with a known capture to simulate live feed
  - Verify plot updates appear as each new vector is decoded (no need for real Ruida controller)
  - Verify CLI remains interactive during live updating
- [x] 7.5 Save/export verification
  - Save as PNG — verify file created and viewable
  - Save as SVG — verify file created
  - Save as standalone HTML — verify file created and opens in browser
  - Verify menu bar controls (range slider, reset button, toggle/show)
- [x] 7.6 Clean-environment verification
  - Delete `.venv`, recreate from updated `requirements.txt` (as users will do after Phase 6)
  - Verify `import matplotlib` fails (confirms removal)
  - Verify `bokeh` imports successfully
  - Run full regression on one test case
- [x] 7.7 Error handling verification
  - Verify graceful behavior when default port (5006) is in use
  - Verify graceful behavior when Bokeh is not installed
  - Verify CTRL+C shuts down server and exits cleanly

## Phase 8: Cleanup & Documentation [COMPLETE]
**Depends on:** Phase 7 (testing complete)
- [x] 8.1 Add `.venv-bokeh/` to `.gitignore`
- [x] 8.2 Decision gate review: verify all requirements from original prompt are met
  - Key requirements enumerated (cross-reference with `docs/plans/MatplotlibToBokeh-prompt.md`):
    - Tab-based multi-view (XY plot + power histogram + speed histogram per tab)
    - Interactive zoom/pan with mouse button remapping (left=select, middle=pan, wheel=zoom)
    - Hover tooltips matching existing format
    - Accept/reject buttons for memory/info popups (Bokeh equivalent of matplotlib popups)
    - Real-time updating during `--on-the-fly` mode
    - Searchable command pull-down with hover highlighting
    - Right-click context menus ("Open in new tab", "Duplicate view")
    - Menu bar with Save as PNG/SVG/HTML, settings, reset, range slider
    - Filter controls (move vs cut toggle, power/speed ranges)
    - No matplotlib or mplcursors dependency
    - Remove `--on-the-fly` plotting restrictions
    - Z-axis placeholder comment for future 3D
  - Cross-check each UI feature, special consideration, and testing requirement
- [x] 8.3 Finalize README.md with accurate installation, usage, and architecture docs
- [x] 8.4 Archive old matplotlib code (optional): `git tag pre-bokeh-migration` for reference

## Notes
- 2026-05-11: Codebase analyzed — matplotlib used in `cpalib/cpa_plotter.py` (742 lines), `cpalib/cpa_popup.py` (182 lines), `requirements.txt`. Entry point `cpa.py` lines 185-192 disable `--plot-moves` with `--on-the-fly` — this must be removed. AGENTS.md line 55 already claims Bokeh is used but actual implementation is matplotlib — resolved by migration.
- 2026-05-11: CpaLine class in `cpalib/cpa_line.py` uses numpy only for `sqrt` in `to_length()` — replaced with `math.hypot()` to remove numpy entirely from the project.
- 2026-05-11: Current tooltip format defined in `CpaLine.annotation` property (cpa_line.py lines 46-58) — must be preserved exactly for Bokeh HoverTool.
- 2026-05-11: No existing test infrastructure (`tests/` directory empty). Testing per project convention: manual verification against `discovery/` test cases.
- 2026-05-11: AGENTS.md states "No test/lint/CI infrastructure" (line 49) — this plan respects that convention.
- 2026-05-11: All review feedback addressed. Plan approved. Remaining polish items handled:
  - Phase 5b.1 TBD replaced with concrete settings (axis range presets, color mapping toggle, grid toggle)
  - Phase 4.3 decode script logic clarified (precedence: .venv-bokeh → .venv → error; specific lines enumerated)
  - Numpy split resolved: numpy not installed in Phase 1.1, not listed in requirements-bokeh.txt, removed entirely in Phase 3.1
  - Migration messaging reconciled: .venv-bokeh is dev-only; after Phase 6, delete .venv and recreate from requirements.txt
  - Phase 8.2 requirements enumerated inline (cross-reference prompt.md for full detail)
  - Phase 5a.2 box-select aspect ratio updated from square constraint to axis aspect ratio preservation
