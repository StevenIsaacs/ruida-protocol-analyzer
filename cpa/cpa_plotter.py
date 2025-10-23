'''A way to visualize laser head moves while parsing a log file.

This uses matplotlib and mplcursors to illustrate laser head movement
and power settings. This can reveal whether a driver is handling move and
cut commands correctly.'''

import itertools

import matplotlib.pyplot as mpl
from matplotlib.patches import Rectangle
import mplcursors

from cpa.cpa_emitter import CpaEmitter
import cpa.cpa_popup as cpa_p
import cpa.cpa_line as cpa_l

class CpaPlotter():
    '''Generate a colorized plot of laser head movement.

    This is designed to be called by the ruida_parser to indicate laser
    head movement and power settings. Movement can be absolute or relative.
    Power settings are indicated using line color.

    Because plotting can impact performance, the plotter must be explicitly
    enabled.

    For readability all methods here correspond to the Ruida protocol commands
    and are named similarly.

    All dimensions are in millimeters and all power settings are expressed as
    a percentage of full power.

    This code knows the limitations of the Ruida protocol. For example, relative
    moves are limited to what can be expressed using a signed 14 bit integer
    indicating the distance in micrometers. This results in a 2^13 positive or
    negative distance.

    If the bed size is known a rectangle is drawn to indicate the bed area. All
    subsequent moves are verified to stay within the bounds of the bed area.

    All moves are validated as to whether they are reasonable values and will
    fit in the bed dimensions.

    Method name prefixes are used to indicate whether a method is to be called
    as the result of a command (cmd_) or a memory table access (mt_).

    NOTE: All parameters are expected to be the values returned by their
    corresponding decoders in ruida_parser.

    NOTE: All moves are continuous moves and are part of the plot. There are
    basically two types of moves: A repositioning of the laser head (always
    shown with a black line) and a move of laser head with the laser on (the
    line color indicates the percentage of full power).

    TODO: At this time it is unknown whether or not all Ruida based lasers
    have the home position in the far right corner. This code assumes all to
    be far right corner (i.e. coordinate 0, 0 is the home position in the
    far right corner.).

    TODO: Currently only the X and Y axis are drawn. The U axis for a rotator
    needs to be added.

    Attributes:
        out         The message emitter.
        cmd_id      The command ID to be associated with a line.
        plot_title  The label (title) for the plot.
        plot        The movement plot.
        axes        The axis array for the plot.
        lines       The number of lines in the plot. This is used for the Z
                    axis.
        bed         A rectangle showing the bed as reported by the controller.
        x           Current X position.
        y           Current Y position.
        u           Current rotator position.
        p           Current power percentage.
        color       Current color corresponding to power. This is expressed as
                    0xRRGGBB.
        step        When true single stepping lines is enabled.
    '''
    def __init__(self, out: CpaEmitter,
                 title: str, s: dict,
                 m_to_s_map: dict,
                 cmd_counters: dict,
                 mt_counters: dict):
        '''Init the plotter.

        Parameters:
            out     The message emitter to use.
            label   The label to display as the plot title.
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
        self.p = 0.0 # Laser effectively off.
        self.s = s
        self.m_to_s_map = m_to_s_map
        self.speed = 0.0

        self.color: tuple = (0, 0, 0)

        # For moves relative to a set origin
        self.origin_x = 0
        self.origin_y = 0

        self._max_win_x = 0
        self._min_win_x = 0
        self._max_win_y = 0
        self._min_win_y = 0

        mpl.set_loglevel('warning')
        self.plot_title = title
        self.plot, self.ax = mpl.subplots(figsize=(10, 8.5), linewidth=1)
        self.plot.suptitle(self.plot_title)
        self.ax.set_title(self.plot_title)
        self.ax.set_xlabel('Bed X mm')
        self.ax.set_ylabel('Bed Y mm')
        # TODO: Set limit sign based upon where home is.
        self.ax.set_xlim(-1000, 5)
        self.ax.set_ylim(-1000, 5)
        self.ax.set_aspect('equal')
        self.ax.grid(True)

        #self.ax.invert_xaxis()
        #self.ax.invert_yaxis()
        self._last_annotation = None
        mplcursors.cursor(self.ax, hover=True)

        self.plot_lines = []
        self.cpa_lines: dict[int, cpa_l.CpaLine] = {}

        # For additional pop-up plots. Only three more plots.
        self.popup_list: list(cpa_p.CpaPopUp) = [None, None, None]

        self.bed = None

        self.bed_xy = {'X': 0, 'Y': 0} # Set by bed_xy_x and bed_xy_y.
        self.bed_z = 0      # For later.
        self.rotator_u = 0  # For later.

        self.enabled = False
        self._stepping_enabled = False
        self._stepping_cmd_id = 0
        self._stepping_end = 0
        self._run_n_lines = 0

        self._x_min = -5        # For some overshoot.
        self._y_min = -5        # For some overshoot.
        self.bed_sized = False  # True when both bed dimensions have been set.
        self._moved = False     # Indicates if the head has moved after init.
        self._last_x = 0        # For line start point.
        self._last_y = 0        # For line end point.

        self._move_color = (0.6, 0.6, 0.6)
        self._color_lut = self._gen_color_lut()
        self._color_hist = [0] * len(self._color_lut)
        self._hist = None
        self._hist_ax = None

        self._max_legend_lines = 20

    # Command line interface
        self._cli_commands = {
            'help': (
                '',
                    'Display this help.',
            ),
            'stats': (
                '',
                    'Display plot statistics.',
                    ),
            'line-atts': (
                '<cmd_id>',
                    'Display line attributes for line <cmd_id>.'
            ),
            'new-plot': (
                '<cmd_id> [<n>]',
                    'Open a new plot to display <n> (default=30) lines '
                    'starting with <cmd_id>.',
                    ),
            'close-plot': (
                '<plot_id>',
                    'Close the window for <plot_id>.',
            ),
            'step': (
                '[on | off]',
                    'Enable or disable single step plotting.',
                    ),
            'run-to': (
                '<cmd_id>',
                    'Run until <cmd_id> then enter single step plotting.',
                    ),
            'run-until': (
                '<n_lines>',
                    'Run until <n_lines> have been plotted'
                    ' then enter single step plotting.',
                    ),
            'range': (
                '<cmd_id> [<n>]',
                    'Plot <n> lines starting with <cmd_id>.\n'
                    '\t<n> is optional and defaults to 20.',
                    ),
            'show-legend': (
                '<cmd_id>',
                    'Display a clickable legend for the visible lines '
                    f'limited to {self._max_legend_lines} lines.',
                    ),
            'close-legend': (
                '',
                    'Close the legend in the active plot.',
                    ),
            'show-speed': (
                '',
                    'Display the speed setting table.',
            ),
            'show-power': (
                '',
                    'Display a power setting legend for the visible lines.',
                    ),
            'close-power': (
                '',
                    'Close the power setting legend.',
                    ),
        }

    def _cli_help(self, params: list[str]):
        _help = f'\n{params[0]}'
        for _n in self._cli_commands:
            _c = self._cli_commands[_n]
            _help += f'\n{_n} {_c[0]}:\n\t{_c[1]}'
        self.out.write(_help)

    def _cli_stats(self, params: list[str]):
        '''Display plotting statistics.'''
        _s = ('\n'
            f'Total lines: {len(self.plot_lines)}'
        )
        for _c in self.cmd_counters:
            _s += ('\n'
                   f'{_c} = {self.cmd_counters[_c]}')
        for _c in self.mt_counters:
            _s += ('\n'
                   f'{_c} = {self.mt_counters[_c]}')
        self.out.write(_s)


    def _cli_line_atts(self, params: list[str]):
        '''Display line attributes for line <cmd_id>.'''
        if len(params) == 2:
            _cmd_id = int(params[1].strip())
            if _cmd_id in self.cpa_lines:
                self.out.write(str(self.cpa_lines[_cmd_id]))
            else:
                self.out.write(f'Command ID {_cmd_id} is not known.')
        else:
            self._cli_help(['A command ID is required.'])

    def _cli_new_plot(self, params: list[str]):
        '''Display a select list of lines in a new plot window.

        '''
        if len(params) < 2:
            raise IndexError('A command ID is required.')
        _use = None
        for _i in range(len(self.popup_list)):
            if (self.popup_list[_i] is None or
                not self.popup_list[_i].is_open):
                _use = _i
                break
        if _use is None:
            raise IndexError('No more plots available.')
        _cmd_id = int(params[1])
        _start = None
        for _index, _key in enumerate(self.cpa_lines):
            if _key >= _cmd_id:
                _start = _index
                break
        if _start is None:
            raise IndexError(f'Command {_cmd_id} not found.')
        if len(params) == 3:
            _end = _start + int(params[2])
        else:
            _end = _start + 50 # Just an arbitrary number.
        if _end > len(self.cpa_lines):
            _end = len(self.cpa_lines) - 1
        _cpa_lines = dict(itertools.islice(
            self.cpa_lines.items(), _start, _end))

        _popup = cpa_p.CpaPopUp(_i)
        self.popup_list[_i] = _popup
        _popup.show(_cpa_lines)

    def _cli_close_plot(self, params: list[str]):
        if len(params) < 2:
            raise IndexError('A plot ID is required.')
        _i = int(params[1])
        if _i >= len(self.popup_list):
            raise IndexError(f'Invalid plot number: {_i}')
        if self.popup_list[_i] is None:
            raise IndexError(f'Plot {_i} is not open.')
        self.popup_list[_i].close()
        self.popup_list[_i] = None
        self.out.write(f'\nPlot {_i} closed.')

    def _cli_step(self, params: list[str]):
        if params[1] == 'on':
            self.enable_stepping(True)
        elif params[1] == 'off':
            self.enable_stepping(False)
            self.out.write('\nStep mode is OFF.')
        else:
            self.out.write('Invalid option for step command.')

    def _cli_run_to(self, params: list[str]):
        if len(params) < 2:
            raise IndexError('A command ID is required.')
        self._stepping_cmd_id = int(params[1])

    def _cli_run_until(self, params: list[str]):
        if len(params) < 2:
            raise IndexError('Number of plotted lines is required.')
        self._run_n_lines = int(params[1])

    def _cli_range(self, params: list[str]):
        _cmd_id = int(params[1].strip())
        if len(params) == 3:
            _end = _cmd_id + int(params[2].strip())
        else:
            _end = 0
        self.step_on_cmd_id(_cmd_id, _end)
        self.out.write(f'Step will resume at command: {_cmd_id}')

    def _cli_show_legend(self, params: list[str]):
        self.ax.legend(
            fontsize=6,
            fancybox=True,
            shadow=True,
            draggable=True,
            )

    def _cli_close_legend(self, params: list[str]):
        self.out.write('\nTBD')

    def _cli_show_speed(self, params: list[str]):
        _str = ''
        for _m in self.m_to_s_map:
            _s = self.m_to_s_map[_m]
            _str += f'\n{_m}:{_s}={self.s[_s]:.2f}'
        self.out.write(_str)

    def _cli_show_power(self, params: list[str]):
        self._hist, self._hist_ax = mpl.subplots(figsize=(8.5,10))
        self._hist_ax.bar(
            list(range(len(self._color_lut))),
            self._color_hist,
            color=self._color_lut
        )
        self._hist_ax.set_title('Power Setting Histogram')
        self._hist_ax.set_xlabel('Power %')
        self._hist_ax.set_ylabel('Frequency')
        self._hist.show()

    def _cli_close_power(self, params: list[str]):
        self.out.write('\nTBD')

    def cli(self, label: str):
        '''Handle user commands during pause.

        Parameters:
            label   A command label formatted as:
                        <cmd_id>:<command>
        '''
        if self._stepping() or (label is None):
            while True:
                _input = self.out.pause(
                    f'{self.cmd_id}:{label} Command or Enter:')
                if _input == '':
                    break
                _params = _input.strip().split(' ')
                _cli_cmd = _params[0].strip()
                if _params[0] == '?':
                    self._cli_help(_params)
                elif _cli_cmd in self._cli_commands:
                    try:
                        _cli_method = f'_cli_{_cli_cmd}'.replace('-', '_')
                        getattr(self, _cli_method)(_params)
                    except Exception as e:
                        self.out.write(e)
                        #self._cli_help([f'Error in: {_cli_cmd}'])
                else:
                    self._cli_help([f'Unknown command: {_cli_cmd}'])

    #++++ Display options.
    def enable(self):
        '''Enable plotting.

        This must be call to enable plotting.
        '''
        self.enabled = True

    def show(self, line=None, label=None, wait=False):
        def _annotate(sel: mplcursors._pick_info.Selection):
            sel.annotation.draggable(False)
            sel.annotation.set_visible(False)
            sel.annotation.set_fontsize(6)
            _line = sel.artist
            # Ruida controllers have home far right. This effectively
            # reverses coordinates so that positive becomes negative.
            _end_x = _line.get_xdata()[-1]
            _min_x, _max_x = (self.ax.get_xlim())
            # Move the plot window if the new coord is not in the visible area.
            _len_x = _max_x - _min_x
            if _end_x < _min_x:
                _min = _end_x - _len_x / 2
                _max = _end_x + _len_x / 2
                self.ax.set_xlim(_min, _max)
                _min_x, _max_x = (self.ax.get_xlim())

            if _end_x < _min_x:
                _pos_x = _min_x
            else:
                _pos_x = _end_x

            _end_y = _line.get_ydata()[-1]
            _min_y, _max_y = self.ax.get_ylim()
            # Move the plot window if the new coord is not in the visible area.
            _len_y = _max_y - _min_y
            if _end_y < _min_y:
                _min = _end_y - _len_y / 2
                _max = _end_y + _len_y / 2
                self.ax.set_ylim(_min, _max)
                _min_y, _max_y = self.ax.get_ylim()

            if _end_y < _min_y:
                _pos_y = _min_y
            else:
                _pos_y = _end_y

            _label: str = _line.get_label()
            _cmd_id = int(_label.split(':')[0])
            _cmd = _label.split(':')[1]
            if self._last_annotation is not None:
                self._last_annotation.remove()
            _a_text = f'{_label}\nx={-_end_x:.3f}mm\ny={-_end_y:.3f}mm'
            _a_text += f'\nPower={self.cpa_lines[_cmd_id].power:.1f}%'
            # TODO: How to check for cut vrs move?
            if _cmd in self.m_to_s_map:
                _speed = self.cpa_lines[_cmd_id].speed
                _a_text += f'\nSpeed={_speed:.1f}mm/S'
            else:
                _a_text += f'\nSpeed=UNKNOWN'
            self._last_annotation = self.ax.annotate(
                _a_text,
                xy=(_pos_x, _pos_y),
                xytext=(5, 5),
                textcoords='offset points',
                bbox=dict(
                    boxstyle='round,pad=0.5',
                    fc='yellow',
                    ec='black',
                    lw=1,
                    alpha=0.6,
                    ),
                arrowprops=dict(
                    arrowstyle='->', connectionstyle='arc3,rad=.2',
                ),
                ha='center', va='bottom',
                fontsize=6,
            )
            self._last_annotation.draggable()

        if label is None:
            self.ax.set_xlim(self._min_win_x -5,
                             self._max_win_x + 5)
            self.ax.set_ylim(self._min_win_y -5,
                             self._max_win_y + 5)

        self.plot.show()
        self.plot.canvas.draw_idle()
        if line is None:
            _lines = self.plot_lines
        else:
            _lines = [line]
        if wait:
            cursor = mplcursors.cursor(_lines, hover=True, multiple=False)
            cursor.connect('add', _annotate)
            self.cli(label)

    def step_on_cmd_id(self, cmd_id, end=0):
        '''Set the command ID at which to start stepping moves.

        This is ignored when stepping is disabled.

        Set to 0 to disable and step all commands.
        '''
        self._stepping_cmd_id = cmd_id
        self._stepping_end = end
        self._stepping_enabled = True

    def enable_stepping(self, enable: bool):
        '''Enable or disable single stepping lines.

        When stepping is enabled the plot is re-displayed when a line is
        added. Show is called with wait enabled.
        '''
        self._stepping_enabled = enable

    def _stepping(self):
        return (
                (self._run_n_lines <= 0 ) and (
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

    def set_bed_dimension(self, axis: str, length: float):
        if self._moved:
            self.out.error('Bed size being set after head was moved.')
        if self.bed_xy[axis] != 0 and self.bed_xy[axis] != length:
            self.out.error(
                f'Bed size {axis} changed from {self.bed_xy[axis]} to {length}.')
        _b = self.bed_xy
        self.bed_xy[axis] = length
        if _b['X'] != self.bed_xy['X'] or _b['Y'] != self.bed_xy['Y']:
            if self.bed_xy['X'] != 0 and self.bed_xy['Y'] != 0:
                # If the bed size was set previously then a new, overlapping
                # rectangle is drawn.
                self.out.verbose(f'Drawing bed rectangle.')
                self.ax.set_xlim(self._x_min, self.bed_xy['X'])
                self.ax.set_ylim(self._y_min, self.bed_xy['Y'])
                self.bed_sized = True
            else:
                self.out.verbose(f'Bed dimension {axis} set to {length}.')
        else:
            self.out.verbose('No change in bed size.')

    def valid_coord(self, axis: str, coord: float):
        '''Validate an absolute coordinate as to whether it will fit in the
        current bed dimensions (if defined).
        '''
        if coord < 0:
            self.out.error(f'Axis {axis} coordinate ({coord}) is less than 0.')
            return False
        else:
            if self.bed_sized and coord > self.bed_xy[axis]:
                self.out.error(
                    f'Axis {axis} coordinate ({coord}) is outside bed area.')

    def _gen_color_lut(self):
        '''Intended to be called from __init__ this generates a LUT containing
        101 color entries and indexed by power percentage in the range
        0 to 100. The resulting colors range is:
            blue -> green -> yellow -> orange -> red.
        '''
        _seed_colors = [
            (0, 0, 255),    # Blue
            (0, 255, 0),    # Green
            (255, 255, 0),  # Yellow
            (255, 128, 0),  # Orange (approx)
            (255, 0, 0)     # Red
        ]
        _seeds = len(_seed_colors) - 1
        _lut = []
        _num_entries = 101
        for _i in range(_num_entries):
            _global_f = _i / (_num_entries)
            _seed = min(int(_global_f * _seeds), _seeds - 1)
            _start_f = _seed / _seeds
            _end_f = (_seed + 1) / _seeds
            if (_end_f - _start_f) > 1e-9:
                _local_f = (_global_f - _start_f) / (_end_f - _start_f)
            else:
                _local_f = 0.0
            _start_rgb = _seed_colors[_seed]
            _end_rgb = _seed_colors[_seed + 1]
            _rgb = [
                int(_start_rgb[_c] + (_end_rgb[_c] - _start_rgb[_c]) * _local_f)
                for _c in range(3)
            ]
            _lut.append((_rgb[0] / 255, _rgb[1] / 255, _rgb[2] / 255))
        return _lut

    def set_power(self, power: float):
        '''Set line power and color.'''
        self.p = power
        _i = round(power)
        if _i > 100:
            self.out.error(
                f'Power ({power} is greater than 100 percent.)')
            _i = 100
        if _i < 0:
            self.out.error(
                f'Power ({power} cannot be less than 0.)')
            _i = 0
        self.color = self._color_lut[_i]
        self._color_hist[_i] += 1

    def add_line(self, x: float, y: float, cut=False):
        '''Position the virtual head at x,y.

        If cut is True then this is a virtual move with the laser on. All
        such moves are drawn with a color corresponding to laser power.
        Otherwise the color is black.
        '''
        # Validate the coordinates. They must be within the bed area.
        self.valid_coord('X', x)
        self.valid_coord('Y', y)

        # TODO: Set X and Y sign based upon where home is.

        # Set the new coordinate -- good or bad.
        self._last_x = self.x
        self.x = x
        self._max_win_x = max(-x, self._max_win_x)
        self._min_win_x = min(-x, self._min_win_x)
        self._last_y = self.y
        self.y = y
        self._max_win_y = max(-y, self._max_win_y)
        self._min_win_y = min(-y, self._min_win_y)
        # Get the color.
        if cut:
            _lw = 1
            _c = self.color
            _ls = 'solid'
        else:
            _lw = 0.5
            _c = self._move_color
            _ls = 'dashed'
        # Draw the line from the previous head position.
        # Invert locations because controller home is far right.
        if ((self.cmd_id >= self._stepping_cmd_id) and
            ((self._stepping_end == 0) or
             (self.cmd_id <= self._stepping_end))):
            _line_label = f'{self.cmd_id}:{self.cmd_label}'
            _line, = self.ax.plot(
                [-self._last_x, -x], [-self._last_y, -y],
                label=_line_label, color=_c, lw=_lw, linestyle=_ls)
            self.cpa_lines[self.cmd_id] = cpa_l.CpaLine(
                self.cmd_id,
                self.cmd_label,
                len(self.plot_lines),
                (self._last_x, self._last_y),
                (x, y),
                self.s[self.m_to_s_map[self.cmd_label]],
                self.p,
                _lw,
                _ls,
                _c,
            )
            self.plot_lines.append(_line)
            if self._stepping():
                self.show(line=_line, label=self.cmd_label, wait=True)
        if self._run_n_lines > 0:
            self._run_n_lines -= 1
        self._moved = True

    def add_rect(self,
                 top_left: tuple[float, float],
                 bottom_right: tuple[float, float],
                 color:tuple[float, float, float],
                 alpha: float,
                 hatch: str):
        '''Add a rectangle having a color and transparency (alpha).'''
        # A matplotlib rectangle is defined as bottom left corner and
        # width and height.
        _l = min(-top_left[0], -bottom_right[0])
        _b = min(-top_left[1], -bottom_right[1])
        _bl = (_l, _b)
        _w = abs(bottom_right[0] - top_left[0])
        if _w == 0:
            self.out.warn(f'Command {self.cmd_label} area width = {_w}')
        _h = abs(top_left[1] - bottom_right[1])
        if _w == 0:
            self.out.warn(f'Command {self.cmd_label} area height = {_w}')
        _l = f'{self.cmd_id}:{self.cmd_label}'
        _rect = Rectangle(_bl, _w, _h,
                          label=_l,
                          edgecolor=(0, 0, 0),
                          facecolor=color,
                          alpha=alpha,
                          hatch=hatch)
        self.ax.add_patch(_rect)
        self._min_win_x = _bl[0]
        self._max_win_x = _bl[0] + _w
        self._min_win_y = _bl[1]
        self._max_win_y = _bl[1] + _h
        self.show()
        pass