'''Visualize laser head movements as defined by the Ruida protoco..\

Most of the work is done by CpaPlotter. This vectors Ruida protocol commands to
the corresponding CpaPlotter methods.
'''

from cpa.cpa_emitter import CpaEmitter
import cpa.cpa_plotter

import rpa_protocol as rdap

class RpaPlotter():
    '''Create and update a plot of Ruida movement and power setting commands.
    '''
    def __init__(self, out: CpaEmitter, title: str):
        self.out = out
        self.title = title
        self.plot = cpa.cpa_plotter.CpaPlotter(out, title)

    #++++ Memory table
    def mt_bed_size_x(self, length: float):
        '''Set the bed size X dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.set_bed_dimension('X', length)

    def mt_bed_size_y(self, length: float):
        '''Set the bed size Y dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.set_bed_dimension('Y', length)

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

    def cmd_move_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.add_line(values[0], values[1])

    def cmd_axis_x_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.add_line(self.x + values[0], self.y)

    def cmd_axis_y_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.add_line(self.x, self.y + values[1])

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
            self.add_line(self.x + values[1], self.y + values[2])
        else:
            self.add_line(self.origin_x + values[1], self.origin_y + values[2])

    def cmd_rapid_move_xyu(self, values: list[float]):
        '''Move a  to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.'''
        '''This effectively a move with the laser off.

        Move lines are always black.

        TODO: Add U axis.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.add_line(self.x + values[1], self.y + values[2])
        else:
            self.add_line(self.origin_x + values[1], self.origin_y + values[2])


    def cmd_rapid_move_x(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.add_line(self.x + values[1], self.y)
        else:
            self.add_line(self.origin_x + values[1], self.origin_y)

    def cmd_rapid_move_y(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.add_line(self.x, self.y + values[1])
        else:
            self.add_line(self.origin_x, self.origin_y + values[1])

    def cmd_move_rel_xy(self, values: list[float]):
        '''Move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self.add_line(self.x + _rel_x, self.y + _rel_y)

    def cmd_move_rel_x(self, values: list[float]):
        '''Move a distance along the X axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self.add_line(self.x + _rel_x, self.y)

    def cmd_move_rel_y(self, values: list[float]):
        '''Move a distance along the Y axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self.add_line(self.x, self.y + _rel_y)

    #++++ Cuts
    def cmd_cut_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.add_line(values[0], values[1], cut=True)

    def cmd_cut_rel_xy(self, values: list[float]):
        '''With the laser on move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self.add_line(self.x + _rel_x, self.y + _rel_y, cut=True)

    def cmd_cut_rel_x(self, values: list[float]):
        '''With the laser on move a distance along the X axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self.add_line(self.x + _rel_x, self.y, cut=True)

    def cmd_cut_rel_y(self, values: list[float]):
        '''With the laser on move a distance along the Y axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self.add_line(self.x, self.y + _rel_y, cut=True)

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
