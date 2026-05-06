# Bokeh Migration Guide

## Overview

This document describes the migration from the original matplotlib-based plotting system to the new Bokeh-based interactive plotting system in the CNC Protocol Analyzer (CPA).

## Why Migrate to Bokeh?

The original plotting system used matplotlib with build123d for 3D visualization. While functional, it had several limitations:

1. **Static Output**: Matplotlib produces static images that require regenerating to change views
2. **Limited Interactivity**: Zooming and panning require code changes or complex event handling
3. **No Web Integration**: Matplotlib windows don't integrate well with web-based workflows
4. **Memory Intensive**: Large datasets can cause memory issues with matplotlib

Bokeh provides:
- **Interactive Plotting**: Pan, zoom, hover tooltips out of the box
- **Web-Based**: Runs in a browser, accessible from any device
- **Server Architecture**: Stateful applications with callbacks and widgets
- **Better Performance**: Handles large datasets more efficiently

## Migration Steps

### 1. Dependencies

**Before (requirements.txt):**
```
matplotlib
mplcursors
build123d
```

**After (requirements.txt):**
```
bokeh
numpy
```

### 2. Code Changes

#### Old Approach (matplotlib)
```python
import matplotlib.pyplot as plt

# Create figure
fig, ax = plt.subplots()

# Plot lines
for line in lines:
    ax.plot(line['x'], line['y'])

# Show
plt.show()
```

#### New Approach (Bokeh)
```python
from bokeh.plotting import figure, show
from bokeh.models import ColumnDataSource

# Create figure
p = figure(width=800, height=600, tools="pan,wheel_zoom,hover")

# Create data source
source = ColumnDataSource(data={'x': x_data, 'y': y_data})

# Plot lines
p.multi_line('x', 'y', source=source)

# Show (opens in browser)
show(p)
```

### 3. File Changes

| Old File | New File | Description |
|----------|----------|-------------|
| `cpalib/cpa_plotter.py` | `cpalib/cpa_bokeh_plotter.py` | Main plotter implementation |
| `cpalib/archive/` | `cpalib/archive/` | Archived matplotlib code |

### 4. Command Line Changes

**Before:**
```bash
python cpa.py --plot-moves capture.log
# Opens matplotlib window
```

**After:**
```bash
python cpa.py --plot-moves capture.log
# Opens Bokeh server application in browser
```

### 5. New Features in Bokeh Implementation

The Bokeh migration added several new features not available in the matplotlib version:

1. **Interactive Widgets**:
   - Command type filter dropdown
   - Power and speed range sliders
   - Show/hide checkboxes for cuts and moves
   - Opacity slider for dimmed vectors

2. **Context Menu**:
   - Right-click on any vector to open context menu
   - "Open in New Tab" option to open focused view
   - Vector highlighting on selection

3. **Multiple Tabs**:
   - Create multiple tabs for different views
   - Each tab maintains its own zoom/pan state

4. **Save/Load View State**:
   - Save current view (zoom, filters, etc.) to browser localStorage
   - Load previously saved views from dropdown
   - Persistent across browser sessions

5. **Command Dropdown**:
   - Dropdown menu with all commands
   - Hover highlighting: hovering over command highlights corresponding vector
   - Click to select and zoom to vector

6. **Reset Button**:
   - One-click reset of all filters
   - Reset zoom to default bounds
   - Reset all sliders to default values

### 6. Testing Changes

**New Test File**: `tests/test_bokeh_plotter.py`

This file contains comprehensive tests for the Bokeh implementation:
- Context menu functionality
- Dropdown menu behavior
- Save/Load view state
- Reset button functionality
- Hover highlighting

Run tests with:
```bash
pytest tests/test_bokeh_plotter.py -v
```

## Architecture Comparison

### Matplotlib Architecture (Old)
```
cpa.py → cpa_plotter.py → matplotlib → Static Image
```

### Bokeh Architecture (New)
```
cpa.py → cpa_bokeh_plotter.py → Bokeh Server → Browser (Interactive)
```

## Rollback Plan

If you need to rollback to matplotlib:

1. Restore old files from `cpalib/archive/`
2. Revert requirements.txt to include matplotlib
3. Update cpa.py to import old plotter

However, note that the Bokeh implementation is now the primary plotting system and the matplotlib code is archived.

## Troubleshooting

### Bokeh Server Won't Start
- Check that port 5006 is not in use
- Verify Bokeh is installed: `pip show bokeh`
- Check firewall settings if accessing remotely

### Plots Not Showing
- Check browser console for JavaScript errors
- Verify Bokeh server is running (check terminal output)
- Try accessing directly: http://localhost:5006

### Performance Issues
- Reduce number of vectors plotted
- Use filtering to show only relevant commands
- Consider upgrading server hardware for very large datasets

## References

- [Bokeh Documentation](https://docs.bokeh.org/)
- [Bokeh Server Guide](https://docs.bokeh.org/en/latest/docs/user_guide/server.html)
- [Bokeh Models Reference](https://docs.bokeh.org/en/latest/docs/reference/models.html)
