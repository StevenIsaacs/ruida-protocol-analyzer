'''A way to visualize laser head moves while parsing a log file.

This uses build123d and OCP Cad Viewer to illustrate laser head movement
and power settings. This can reveal whether a driver is handling move and
cut commands correctly.'''

from build123d import *
from ocp_vscode import *

from rpa_emitter import RpaEmitter

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
        plot        The movement sketch.
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
    def __init__(self, out: RpaEmitter):
        '''Init the plotter.

        Parameters:
            out     The message emitter to use.
        '''
        self.out = out

        self.cmd_label = 0
        # At power on the controller move to 0,0.
        self.x = 0
        self.y = 0
        self.u = 0
        self.p = 0.0 # Laser effectively off.
        self.color: Color = Color(0, 0, 0)

        self.plot = Sketch()
        self.lines = []
        self.line_n = 0
        self.last_line_n = 0
        self.bed = None

        self.bed_xy = {'X': 0, 'Y': 0} # Set by bed_xy_x and bed_xy_y.
        self.bed_z = 0      # For later.
        self.rotator_u = 0  # For later.

        self._enabled = False
        self._stepping_enabled = False
        self._stepping_cmd_id = 0

        self._bed_sized = False # True when both bed dimensions have been set.
        self._moved = False     # Indicates if the head has moved after init.
        self._last_x = 0        # For line start point.
        self._last_y = 0        # For line end point.

        self._color_lut = self._gen_color_lut()

    #++++ Display options.
    def enable(self):
        '''Enable plotting.

        This must be call to enable plotting.
        '''
        self._enabled = True

    def show(self, wait=False):
        show(self.lines)
        if wait:
            self.out.pause('Displaying plot. Press Enter to continue.')

    def step_on_cmd_id(self, cmd_id):
        '''Set the command ID at which to start stepping moves.

        This is ignored when stepping is disabled.

        Set to 0 to disable and step all commands.
        '''
        self._stepping_cmd_id = cmd_id

    def enable_stepping(self, enable: bool):
        '''Enable or disable single stepping lines.

        When stepping is enabled the plot is re-displayed when a line is
        added. Show is called with wait enabled.
        '''
        self._enable_stepping = enable

    def _stepping(self):
        return  (
            self._stepping_enabled and (
                self._stepping_cmd_id > 0 and
                self.cmd_label >= self._stepping_cmd_id
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
                self.plot += Rectangle(self.bed_xy['X'], self.bed_xy['Y'])
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
        self.last_line_n = self.line_n
        self.line_n += 0.1
        # Get the color.
        if cut:
            _c = self.color
        else:
            _c = Color(0.8, 0.8, 0.8)
        # Draw the line from the previous head position.
        # TODO: How to change line color.
        _start = Vector(self._last_x, self._last_y, self.last_line_n)
        _end = Vector(x, y, self.line_n)
        _line = Line(_start, _end)
        self.lines.append(_line)
        _line.color = _c
        _line.label = self.cmd_label
        self.plot += _line
        if self._stepping():
            show(self.lines)
        # show(self.plot)
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
        self._add_line(self.x + values[1], self.y + values[2])

    def cmd_rapid_move_xyu(self, values: list[float]):
        '''Move a  to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.'''
        '''This effectively a move with the laser off.

        Move lines are always black.

        TODO: Add U axis.
        '''
        self._add_line(self.x + values[1], self.y + values[2])

    def cmd_rapid_move_x(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(self.x + values[1], self.y)

    def cmd_rapid_move_y(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self._add_line(self.x, self.y + values[1])

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
        _rel_y = values[1]
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
            _lut.append(Color(_rgb[0] / 255, _rgb[1] / 255, _rgb[2] / 255))
        return _lut

    def cmd_power(self, values: list[float]):
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
        0xD9: {
            0x00: 'cmd_rapid_move_x',
            0x01: 'cmd_rapid_move_y',
            0x10: 'cmd_rapid_move_xy',
            0x30: 'cmd_rapid_move_xyu',
        },
    }

    def cmd_update(self, label, cmd, sub_cmd, values: list):
        '''Update the plot depending upon the command.

        Parameters:
            id      The command number. This is used as a label for a line.
            cmd     The command
            sub_cmd The sub-command
            values  A list of decoded parameter values
        '''
        if self._enabled and cmd in self._ct:
            if sub_cmd is not None:
                if sub_cmd in self._ct[cmd]:
                    self.cmd_label = label
                    getattr(self, self._ct[cmd][sub_cmd])(values)
            else:
                self._ct[cmd](values)

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
