'''Bokeh-based data collection and state management for laser head movement visualization.

Replaced cpalib.cpa_plotter.CpaPlotter with a Bokeh-compatible data model.
Maintains the same public interface for compatibility with RpaPlotter.'''

# Fail-fast import check
try:
    from bokeh.models import ColumnDataSource
except ImportError:
    raise ImportError(
        'Bokeh is required for plotting. Install with: pip install bokeh')

from cpalib.cpa_emitter import CpaEmitter
import cpalib.cpa_line as cpa_l


class BokehPlotter():
    '''Data model and state manager for laser head movement visualization.

    Maintains the same public interface as CpaPlotter (methods called by
    RpaPlotter) but does NOT create matplotlib figures. Instead it stores
    data in a ColumnDataSource-compatible format for Bokeh rendering.

    Z-axis placeholder: Future 3D capability will add z coordinate support
    for multi-layer engraving visualization.
    '''

    def __init__(self, out: CpaEmitter,
                 title: str, s: dict,
                 m_to_s_map: dict,
                 cmd_counters: dict,
                 mt_counters: dict):
        '''Init the Bokeh plotter data model.

        Parameters:
            out           The message emitter to use.
            title         The label to display as the plot title.
            s             Settings dictionary.
            m_to_s_map    Mapping from command label to settings key.
            cmd_counters  Command counters dictionary.
            mt_counters   Motion type counters dictionary.
        '''
        self.out = out

        self.cmd_label = ''
        self.cmd_id = None
        self.cmd = 0
        self.sub_cmd = 0
        self.cmd_counters = cmd_counters
        self.mt_counters = mt_counters

        # At power on the controller moves to 0,0.
        self.x = 0
        self.y = 0
        self.u = 0
        self.p = 0.0  # Laser effectively off.
        self.s = s
        self.m_to_s_map = m_to_s_map
        self.speed = 0.0

        self.color: tuple = (0, 0, 0)

        # For moves relative to a set origin.
        self.origin_x = 0
        self.origin_y = 0

        # Z-axis placeholder: Future 3D capability will add z coordinate
        # support for multi-layer engraving visualization.

        self._max_win_x = 0
        self._min_win_x = 0
        self._max_win_y = 0
        self._min_win_y = 0

        self.plot_title = title

        self.cpa_lines: dict[int, cpa_l.CpaLine] = {}

        self.bed_xy = {'X': 0, 'Y': 0}
        self.bed_sized = False
        self._moved = False

        self.enabled = False
        self._stepping_enabled = False
        self._stepping_cmd_id = 0
        self._stepping_end = 0
        self._run_n_lines = 0

        self._x_min = -5       # For some overshoot.
        self._y_min = -5       # For some overshoot.
        self._last_x = 0       # For line start point.
        self._last_y = 0       # For line end point.

        self._move_color = (0.6, 0.6, 0.6)

        # Color look-up table indexed by power percentage (0-100).
        # Stored as hex strings #RRGGBB for Bokeh consumption.
        self._color_lut = self._gen_color_lut()
        self._color_hist = [0] * len(self._color_lut)

    def enable(self):
        '''Enable plotting.'''
        self.enabled = True

    def enable_stepping(self, enable: bool):
        '''Enable or disable single stepping.

        When stepping is enabled the plot is re-displayed when a line is
        added.

        Parameters:
            enable  True to enable stepping, False to disable.
        '''
        self._stepping_enabled = enable

    @property
    def color_lut(self):
        '''Public access to the color lookup table for histogram colorization.'''
        return self._color_lut

    def step_on_cmd_id(self, cmd_id, end=0):
        '''Set the command ID at which to start stepping moves.

        This is ignored when stepping is disabled.

        Set to 0 to disable and step all commands.

        Parameters:
            cmd_id  The command ID to start stepping from.
            end     The last command ID to step through (0 = no end).
        '''
        self._stepping_cmd_id = cmd_id
        self._stepping_end = end
        self._stepping_enabled = True

    def set_power(self, power: float):
        '''Set line power and color.

        Looks up the power percentage (0-100) in the color LUT to determine
        the line color. Clamps out-of-range values.

        Parameters:
            power  Laser power percentage (0.0 - 100.0).
        '''
        self.p = power
        _i = round(power)

        # Clamp power index to valid range.
        if _i > 100:
            self.out.error(f'Power ({power} is greater than 100 percent.)')
            _i = 100
        if _i < 0:
            self.out.error(f'Power ({power} cannot be less than 0.)')
            _i = 0

        self.color = self._color_lut[_i]
        self._color_hist[_i] += 1

    def valid_coord(self, axis: str, coord: float):
        '''Validate an absolute coordinate against bed dimensions.

        Returns False if the coordinate is out of bounds, True otherwise.

        Parameters:
            axis   The axis being validated ('X' or 'Y').
            coord  The coordinate value to validate.

        Returns:
            True if the coordinate is valid, False otherwise.
        '''
        if coord < 0:
            self.out.error(f'Axis {axis} coordinate ({coord}) is less than 0.')
            return False
        if self.bed_sized and coord > self.bed_xy[axis]:
            self.out.error(
                f'Axis {axis} coordinate ({coord}) is outside bed area.')
            return False
        return True

    def set_bed_dimension(self, axis: str, length: float):
        '''Set the bed dimension for the given axis.

        Parameters:
            axis    The axis to set ('X' or 'Y').
            length  The bed dimension in mm.
        '''
        if self._moved:
            self.out.error('Bed size being set after head was moved.')
        if self.bed_xy[axis] != 0 and self.bed_xy[axis] != length:
            self.out.error(
                f'Bed size {axis} changed from {self.bed_xy[axis]} to {length}.')

        _b = self.bed_xy.copy()
        self.bed_xy[axis] = length
        if _b['X'] != self.bed_xy['X'] or _b['Y'] != self.bed_xy['Y']:
            if self.bed_xy['X'] != 0 and self.bed_xy['Y'] != 0:
                self.out.verbose('Drawing bed rectangle.')
                self.bed_sized = True
            else:
                self.out.verbose(f'Bed dimension {axis} set to {length}.')
        else:
            self.out.verbose('No change in bed size.')

    def add_line(self, x: float, y: float, cut=False):
        '''Position the virtual head at x,y. Creates a CpaLine and stores it.

        If cut is True then this is a virtual move with the laser on. All
        such moves are drawn with a color corresponding to laser power.
        Otherwise the color is the move color (gray).

        Parameters:
            x    The X coordinate to move to.
            y    The Y coordinate to move to.
            cut  True if this is a cutting (laser-on) move.
        '''
        # Validate coordinates — guard clause for out-of-bounds.
        self.valid_coord('X', x)
        self.valid_coord('Y', y)

        # Record the previous position before updating.
        self._last_x = self.x
        self.x = x
        self._max_win_x = max(-x, self._max_win_x)
        self._min_win_x = min(-x, self._min_win_x)
        self._last_y = self.y
        self.y = y
        self._max_win_y = max(-y, self._max_win_y)
        self._min_win_y = min(-y, self._min_win_y)

        # Determine line style based on move type.
        if cut:
            _lw = 1
            _c = self.color
            _ls = 'solid'
        else:
            _lw = 0.5
            _c = self._move_color
            _ls = 'dashed'

        # Store the CpaLine regardless of stepping state.
        _cpa_line = cpa_l.CpaLine(
            self.cmd_id,
            self.cmd_label,
            len(self.cpa_lines),
            (self._last_x, self._last_y),
            (x, y),
            self.s[self.m_to_s_map.get(self.cmd_label, 'speed_axis_move')],
            self.p,
            _lw,
            _ls,
            _c,
        )
        self.cpa_lines[self.cmd_id] = _cpa_line

        if self._run_n_lines > 0:
            self._run_n_lines -= 1
        self._moved = True

    def add_rect(self,
                 top_left: tuple[float, float],
                 bottom_right: tuple[float, float],
                 color: tuple[float, float, float],
                 alpha: float,
                 hatch: str):
        '''Store rectangle information for later Bokeh rendering.

        The stored rectangles can be rendered as Bokeh glyphs by a
        separate view layer (BokehView).

        Parameters:
            top_left     The top-left corner of the rectangle.
            bottom_right The bottom-right corner of the rectangle.
            color        The RGB color of the rectangle (0-1 float).
            alpha        The transparency of the rectangle.
            hatch        The hatch pattern string.
        '''
        if not hasattr(self, '_rects'):
            self._rects = []
        self._rects.append({
            'top_left': top_left,
            'bottom_right': bottom_right,
            'color': color,
            'alpha': alpha,
            'hatch': hatch,
            'cmd_label': self.cmd_label,
            'cmd_id': self.cmd_id,
        })

        _l = min(-top_left[0], -bottom_right[0])
        _b = min(-top_left[1], -bottom_right[1])
        _w = abs(bottom_right[0] - top_left[0])
        _h = abs(top_left[1] - bottom_right[1])
        self._min_win_x = _l
        self._max_win_x = _l + _w
        self._min_win_y = _b
        self._max_win_y = _b + _h

    def _gen_color_lut(self):
        '''Generate a color LUT of 101 hex color strings indexed by power.

        The resulting color range is:
            blue -> green -> yellow -> orange -> red.

        Returns:
            A list of 101 hex color strings (#RRGGBB).
        '''
        _seed_colors = [
            (0, 0, 255),    # Blue
            (0, 255, 0),    # Green
            (255, 255, 0),  # Yellow
            (255, 128, 0),  # Orange
            (255, 0, 0)     # Red
        ]
        _seeds = len(_seed_colors) - 1
        _lut = []
        _num_entries = 101
        for _i in range(_num_entries):
            _global_f = _i / _num_entries
            _seed = min(int(_global_f * _seeds), _seeds - 1)
            _start_f = _seed / _seeds
            _end_f = (_seed + 1) / _seeds
            if (_end_f - _start_f) > 1e-9:
                _local_f = (_global_f - _start_f) / (_end_f - _start_f)
            else:
                _local_f = 0.0
            _start_rgb = _seed_colors[_seed]
            _end_rgb = _seed_colors[_seed + 1]
            _r = int(_start_rgb[0] + (_end_rgb[0] - _start_rgb[0]) * _local_f)
            _g = int(_start_rgb[1] + (_end_rgb[1] - _start_rgb[1]) * _local_f)
            _b_val = int(_start_rgb[2] + (_end_rgb[2] - _start_rgb[2]) * _local_f)
            # Store as hex string for Bokeh.
            _lut.append(f'#{_r:02X}{_g:02X}{_b_val:02X}')
        return _lut

    def _stepping(self):
        '''Check if stepping is currently active.

        Returns:
            True if the plotter should pause at the current line.
        '''
        return (
            (self._run_n_lines <= 0) and (
                (self._stepping_enabled and self._stepping_cmd_id == 0) or (
                    self._stepping_enabled and (
                        (self.cmd_id >= self._stepping_cmd_id) and (
                            (self._stepping_end == 0) or
                            (self.cmd_id <= self._stepping_end)
                        )
                    )
                )
            )
        )

    def to_column_data(self):
        '''Convert all stored CpaLines to a ColumnDataSource-compatible dict.

        Coordinates are negated because Ruida home is far-right.

        Returns:
            A dict with keys suitable for Bokeh ColumnDataSource:
              cmd_id, command, index, start_x, start_y, end_x, end_y,
              length, speed, power, width, style, color, annotation.
        '''
        # Guard clause: return empty structure when no lines stored.
        if not self.cpa_lines:
            return {
                'cmd_id': [], 'command': [], 'index': [],
                'start_x': [], 'start_y': [], 'end_x': [], 'end_y': [],
                'length': [], 'speed': [], 'power': [],
                'width': [], 'style': [], 'color': [],
                'annotation': [],
            }

        _data = {k: [] for k in [
            'cmd_id', 'command', 'index',
            'start_x', 'start_y', 'end_x', 'end_y',
            'length', 'speed', 'power',
            'width', 'style', 'color',
            'annotation',
        ]}

        # Sort by cmd_id to ensure consistent ordering.
        for _cmd_id in sorted(self.cpa_lines.keys()):
            _l = self.cpa_lines[_cmd_id]
            # Negate coordinates for Ruida home-is-far-right convention.
            _data['cmd_id'].append(_l.cmd_id)
            _data['command'].append(_l.command)
            _data['index'].append(_l.index)
            _data['start_x'].append(-_l.start[0])
            _data['start_y'].append(-_l.start[1])
            _data['end_x'].append(-_l.end[0])
            _data['end_y'].append(-_l.end[1])
            _data['length'].append(_l.length)
            _data['speed'].append(_l.speed)
            _data['power'].append(_l.power)
            _data['width'].append(_l.width)
            _data['style'].append(_l.style)
            _data['color'].append(self._cpa_color_to_hex(_l.color))
            _data['annotation'].append(_l.annotation)

        return _data

    @staticmethod
    def _cpa_color_to_hex(color):
        '''Convert an RGB tuple (0-1 float) to a hex string #RRGGBB.

        If the input is already a hex string, it is returned as-is.

        Parameters:
            color  An RGB tuple (r, g, b) with values 0.0-1.0,
                   or a hex string like '#RRGGBB'.

        Returns:
            A hex color string in the format '#RRGGBB'.
        '''
        if isinstance(color, str):
            return color  # Already hex.
        _r = min(255, max(0, int(color[0] * 255)))
        _g = min(255, max(0, int(color[1] * 255)))
        _b = min(255, max(0, int(color[2] * 255)))
        return f'#{_r:02X}{_g:02X}{_b:02X}'
