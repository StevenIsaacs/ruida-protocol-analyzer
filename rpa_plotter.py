'''A way to visualize laser head moves while parsing a log file.

This uses build123d and OCP Cad Viewer to illustrate laser head movement
and power settings. This can reveal whether a driver is handling move and
cut commands correctly.'''

import matplotlib.pyplot as plt
import mplcursors

from rpa_emitter import RpaEmitter
import rpa_protocol as rdap

class RpaPlotter():
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
    def __init__(self, out: RpaEmitter, title: str):
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
        # At power on the controller move to 0,0.
        self.x = 0
        self.y = 0
        self.u = 0
        self.p = 0.0 # Laser effectively off.
        self.s = { # Speed settings.
            'speed_laser_1': 0.0,
            'speed_axis': 0.0,
            'speed_laser_1_part': 0.0,
            'force_eng_speed': 0.0,
            'speed_axis_move': 0.0,
        } # Move speed.
        self.m_to_s_map = { # Move type to speed setting map.
            'MOVE_ABS_XY': 'speed_axis_move',
            'MOVE_REL_XY': 'speed_axis',
            'MOVE_REL_X': 'speed_axis',
            'MOVE_REL_Y': 'speed_axis',
            'CUT_ABS_XY': 'speed_laser_1',
            'CUT_REL_XY': 'speed_laser_1',
            'CUT_REL_X': 'speed_laser_1',
            'CUT_REL_Y': 'speed_laser_1',
        }
        self.speed = 0.0
        self.color: tuple = (0, 0, 0)

        # For moves relative to a set origin
        self.origin_x = 0
        self.origin_y = 0

        plt.set_loglevel('warning')
        self.plot_title = title
        self.plot, self.ax = plt.subplots(figsize=(8, 6))
        self.plot.suptitle(self.plot_title)
        self.ax.set_title(self.plot_title)
        self.ax.set_xlabel('Bed X mm')
        self.ax.set_ylabel('Bed Y mm')
        self.ax.set_xlim(-5, 50)
        self.ax.set_ylim(-5, 50)
        self.ax.set_aspect('equal')
        self.ax.grid(True)
        #self.ax.invert_xaxis()
        #self.ax.invert_yaxis()
        self._last_annotation = None
        mplcursors.cursor(self.ax, hover=True)

        self.plot_lines = []
        self.lines = {}
        self.bed = None

        self.bed_xy = {'X': 0, 'Y': 0} # Set by bed_xy_x and bed_xy_y.
        self.bed_z = 0      # For later.
        self.rotator_u = 0  # For later.

        self._enabled = False
        self._stepping_enabled = False
        self._stepping_cmd_id = 0
        self._stepping_end = 0

        self._x_min = -5        # For some overshoot.
        self._y_min = -5        # For some overshoot.
        self._bed_sized = False # True when both bed dimensions have been set.
        self._moved = False     # Indicates if the head has moved after init.
        self._last_x = 0        # For line start point.
        self._last_y = 0        # For line end point.

        self._move_color = (0.3, 0.3, 0.3)
        self._color_lut = self._gen_color_lut()

    def _help(self):
        '''Display a list of available commands.
        '''
        _help = (
            '\nhelp or ?\t\tDisplay this help.'
            '\nno-step\t\t\tTurn single step plotting off.'
            '\nrange <n> [<end>]\tRun and start single step at command <n>.'
            '\n\t\t\t<end> is optional and is the last command to plot.'
            '\nshow-legend\t\tDisplay the legend on the plot.'
            '\n'
        )
        self.out.write(_help)

    #++++ Display options.
    def enable(self):
        '''Enable plotting.

        This must be call to enable plotting.
        '''
        self._enabled = True

    def _commands(self, label: str):
        '''Handle user commands during pause.
        '''
        if ':' in label:
            _cmd_id = int(label.split(':')[0])
        else:
            _cmd_id = 0
            _pause = True
        if self._stepping() or _pause:
            while True:
                _cmd = self.out.pause(f'{label} Command or Enter:')
                if _cmd == 'help' or _cmd == '?':
                    self._help()
                elif _cmd == 'no-step':
                    self.enable_stepping(False)
                    self.out.write('\nStep mode is off.')
                elif 'range' in _cmd:
                    _fields = _cmd.split(' ')
                    _id = int(_fields[1].strip())
                    if len(_fields) == 3:
                        _end = _id + int(_fields[2])
                    else:
                        _end = 0
                    self.step_on_cmd_id(_id, _end)
                    self.out.write(f'Step will resume at command: {_id}')
                elif _cmd == 'show-legend':
                    self.ax.legend(
                        fontsize=6,
                        fancybox=True,
                        shadow=True,
                        draggable=True,
                        )
                elif _cmd == '':
                    break


    def show(self, line=None, label='Displaying plot.', wait=False):
        def _annotate(sel):
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
            _a_text = f'{_label}\nx={-_end_x}mm\ny={-_end_y}mm'
            _a_text += f'\nPower={self.lines[_cmd_id]['power']:.1f}%'
            # TODO: How to check for cut vrs move?
            if _cmd in self.m_to_s_map:
                _speed = self.lines[_cmd_id]['speed']
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

        self.plot.show()
        self.plot.canvas.draw_idle()
        if line is None:
            _lines = self.plot_lines
        else:
            _lines = [line]
        if wait:
            cursor = mplcursors.cursor(_lines, hover=True, multiple=False)
            cursor.connect('add', _annotate)
            self._commands(label)

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
                (self._stepping_enabled and self._stepping_cmd_id == 0) or (
                    self._stepping_enabled and (
                        (self.cmd_id >= self._stepping_cmd_id) and (
                            (self._stepping_end == 0) or
                            (self.cmd_id <= self._stepping_end)
                        )
                    )
                )
            )

    #++++ Memory table
    def _set_bed_dimension(self, axis: str, length: float):
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

    def mt_bed_size_x(self, length: float):
        '''Set the bed size X dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self._set_bed_dimension('X', length)

    def mt_bed_size_y(self, length: float):
        '''Set the bed size Y dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self._set_bed_dimension('Y', length)

    def _valid_coord(self, axis: str, coord: float):
        '''Validate an absolute coordinate as to whether it will fit in the
        current bed dimensions (if defined).
        '''
        if coord < 0:
            self.out.error(f'Axis {axis} coordinate ({coord}) is less than 0.')
            return False
        else:
            if self._bed_sized and coord > self.bed_xy[axis]:
                self.out.error(
                    f'Axis {axis} coordinate ({coord}) is outside bed area.')

    #++++ Moves
    def cmd_speed_laser_1(self, values: list[float]):
        '''Used for cuts?
        '''
        self.s['speed_laser_1'] = values[0]

    def cmd_speed_axis(self, values: list[float]):
        '''Unknown.
        '''
        self.s['speed_axis'] = values[0]

    def cmd_speed_laser_1_part(self, values: list[float]):
        '''Unknown.
        '''
        self.s['speed_laser_1_part'] = values[0]

    def cmd_force_eng_speed(self, values: list[float]):
        '''Unknown.
        '''
        self.s['speed_laser_1'] = values[0]

    def cmd_speed_axis_move(self, values: list[float]):
        '''Used for moves?
        '''
        self.s['speed_laser_1'] = values[0]

    def _add_line(self, x: float, y: float, cut=False):
        '''Position the virtual head at x,y.

        If cut is True then this is a virtual move with the laser on. All
        such moves are drawn with a color corresponding to laser power.
        Otherwise the color is black.
        '''
        # Validate the coordinates. They must be within the bed area.
        self._valid_coord('X', x)
        self._valid_coord('Y', y)

        # Set the new coordinate -- good or bad.
        self._last_x = self.x
        self.x = x
        self._last_y = self.y
        self.y = y
        # Get the color.
        if cut:
            _c = self.color
        else:
            _c = self._move_color
        # Draw the line from the previous head position.
        # Invert locations because controller home is far right.
        if ((self.cmd_id >= self._stepping_cmd_id) and
            ((self._stepping_end == 0) or
             (self.cmd_id <= self._stepping_end))):
            _line, = self.ax.plot(
                [-self._last_x, -x], [-self._last_y, -y],
                label=self.cmd_label, color=_c, lw=2)
            _cmd = self.cmd_label.split(':')[1]
            self.plot_lines.append(_line)
            self.lines[self.cmd_id] = {
                'command': self.cmd_label,
                'line': _line,
                'start': (self._last_x, self._last_y),
                'end': (x, y),
                'speed': self.s[self.m_to_s_map[_cmd]],
                'power': self.p,
                }
            if self._stepping():
                self.show(line=_line, label=self.cmd_label, wait=True)
        self._moved = True

    def cmd_move_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(values[0], values[1])

    def cmd_axis_x_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(self.x + values[0], self.y)

    def cmd_axis_y_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(self.x, self.y + values[1])

    def _valid_rel(self, axis: str, rel):
        if abs(rel) > 2**13 / 1000:
            self.out.error(
                f'Axis {axis} relative {rel} is greater than {2**13/1000}')

    def cmd_rapid_move_xy(self, values: list[float]):
        '''Move a  to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.'''
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self._add_line(self.x + values[1], self.y + values[2])
        else:
            self._add_line(self.origin_x + values[1], self.origin_y + values[2])

    def cmd_rapid_move_xyu(self, values: list[float]):
        '''Move a  to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.'''
        '''This effectively a move with the laser off.

        Move lines are always black.

        TODO: Add U axis.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self._add_line(self.x + values[1], self.y + values[2])
        else:
            self._add_line(self.origin_x + values[1], self.origin_y + values[2])


    def cmd_rapid_move_x(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self._add_line(self.x + values[1], self.y)
        else:
            self._add_line(self.origin_x + values[1], self.origin_y)

    def cmd_rapid_move_y(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self._add_line(self.x, self.y + values[1])
        else:
            self._add_line(self.origin_x, self.origin_y + values[1])

    def cmd_move_rel_xy(self, values: list[float]):
        '''Move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self._add_line(self.x + _rel_x, self.y + _rel_y)

    def cmd_move_rel_x(self, values: list[float]):
        '''Move a distance along the X axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self._add_line(self.x + _rel_x, self.y)

    def cmd_move_rel_y(self, values: list[float]):
        '''Move a distance along the Y axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self._add_line(self.x, self.y + _rel_y)

    #++++ Cuts
    def cmd_cut_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(values[0], values[1], cut=True)

    def cmd_cut_rel_xy(self, values: list[float]):
        '''With the laser on move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self._add_line(self.x + _rel_x, self.y + _rel_y, cut=True)

    def cmd_cut_rel_x(self, values: list[float]):
        '''With the laser on move a distance along the X axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self._add_line(self.x + _rel_x, self.y, cut=True)

    def cmd_cut_rel_y(self, values: list[float]):
        '''With the laser on move a distance along the Y axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self._add_line(self.x, self.y + _rel_y, cut=True)

    #++++ Power
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

    def _cmd_power(self, values: list[float]):
        '''Set laser power.

        A color is calculated based upon percentage.
        TODO: There are several power related commands. What they do
        specifically is currently unknown. These will have to be discovered
        by analyzing actual cut files.
        '''
        _p = values[0]
        self.p = _p
        _i = round(_p)
        if _i > 100:
            self.out.error(
                f'Power ({_p} is greater than 100 percent.)')
            _i = 100
        if _i < 0:
            self.out.error(
                f'Power ({_p} cannot be less than 0.)')
            _i = 0
        self.color = self._color_lut[round(_p)]

    def cmd_min_power_1(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        self._cmd_power(values)

    def cmd_max_power_1(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        self._cmd_power(values)

    _ct = {
        0x80: {
            0x00: 'cmd_axis_x_move',
            0x08: 'cmd_axis_y_move',
        },
        0x88: 'cmd_move_abs_xy',
        0x89: 'cmd_move_rel_xy',
        0x8A: 'cmd_move_rel_x',
        0x8B: 'cmd_move_rel_y',
        0xA8: 'cmd_cut_abs_xy',
        0xA9: 'cmd_cut_rel_xy',
        0xAA: 'cmd_cut_rel_x',
        0xAB: 'cmd_cut_rel_y',
        0xC6: {
            0x01: 'cmd_min_power_1',
            0x02: 'cmd_max_power_1'
        },
        0xC9: {
            0x02: 'cmd_speed_laser_1',
            0x03: 'cmd_speed_axis',
            0x04: 'cmd_speed_laser_1_part',
            0x05: 'cmd_force_eng_speed',
            0x06: 'cmd_axis_move',
        },
        0xD9: {
            0x00: 'cmd_rapid_move_x',
            0x01: 'cmd_rapid_move_y',
            0x10: 'cmd_rapid_move_xy',
            0x30: 'cmd_rapid_move_xyu',
        },
    }

    def cmd_update(self, id, label, cmd, sub_cmd, values: list):
        '''Update the plot depending upon the command.

        Parameters:
            id      The command number. This is used as a label for a line.
            cmd     The command
            sub_cmd The sub-command
            values  A list of decoded parameter values
        '''
        if self._enabled and cmd in self._ct:
            self.cmd = cmd
            if sub_cmd is not None:
                if sub_cmd in self._ct[cmd]:
                    self.sub_cmd = sub_cmd
                    try:
                        self.cmd_id = id
                        self.cmd_label = label
                        getattr(self, self._ct[cmd][sub_cmd])(values)
                    except Exception as e:
                        pass
            else:
                try:
                    self.cmd_id = id
                    self.cmd_label = label
                    getattr(self, self._ct[cmd])(values)
                except Exception as e:
                    pass

    _mt = {
        0x00: {
            0x26: 'mt_bed_size_x',
            0x36: 'mt_bed_size_y',
        },
    }

    def mt_update(self, addr_msb, addr_lsb, values: list):
        '''Update the plot for a memory table access.

        Parameters:
            addr    The memory table address
            values  A list of decoded parameter values
        '''
        if self._enabled and addr_msb in self._mt:
            if addr_lsb in self._mt[addr_msb]:
                getattr(self, self._mt[addr_msb][addr_lsb])(values)
