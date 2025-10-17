'''Visualize laser head movements as defined by the Ruida protoco..\

Most of the work is done by CpaPlotter. This vectors Ruida protocol commands to
the corresponding CpaPlotter methods.
'''

from cpa.cpa_emitter import CpaEmitter
import cpa.cpa_plotter

import protocols.ruida.rpa_protocol as rdap

class RpaPlotter():
    '''Create and update a plot of Ruida movement and power setting commands.
    '''
    def __init__(self, out: CpaEmitter, title: str):
        self.out = out
        self.title = title
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
        self.plot = cpa.cpa_plotter.CpaPlotter(
            out, title, self.s, self.m_to_s_map)

    #++++ Memory table
    def mt_bed_size_x(self, length: float):
        '''Set the bed size X dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.plot.set_bed_dimension('X', length)

    def mt_bed_size_y(self, length: float):
        '''Set the bed size Y dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.plot.set_bed_dimension('Y', length)

    #++++ Moves
    def cmd_speed_laser_1(self, values: list[float]):
        '''Used for cuts?
        '''
        self.plot.s['speed_laser_1'] = values[0]

    def cmd_speed_axis(self, values: list[float]):
        '''Unknown.
        '''
        self.plot.s['speed_axis'] = values[0]

    def cmd_speed_laser_1_part(self, values: list[float]):
        '''Unknown.
        '''
        self.plot.s['speed_laser_1_part'] = values[0]

    def cmd_force_eng_speed(self, values: list[float]):
        '''Unknown.
        '''
        self.plot.s['speed_laser_1'] = values[0]

    def cmd_speed_axis_move(self, values: list[float]):
        '''Used for moves?
        '''
        self.plot.s['speed_laser_1'] = values[0]

    def cmd_move_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.plot.add_line(values[0], values[1])

    def cmd_axis_x_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.plot.add_line(self.plot.x + values[0], self.plot.y)

    def cmd_axis_y_move(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.plot.add_line(self.plot.x, self.plot.y + values[1])

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
            self.plot.add_line(self.plot.x + values[1], self.plot.y + values[2])
        else:
            self.plot.add_line(self.plot.origin_x + values[1],
                               self.plot.origin_y + values[2])

    def cmd_rapid_move_xyu(self, values: list[float]):
        '''Move a  to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.'''
        '''This effectively a move with the laser off.

        Move lines are always black.

        TODO: Add U axis.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.plot.add_line(self.plot.x + values[1], self.plot.y + values[2])
        else:
            self.plot.add_line(self.plot.origin_x + values[1],
                               self.plot.origin_y + values[2])


    def cmd_rapid_move_x(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.plot.add_line(self.plot.x + values[1], self.plot.y)
        else:
            self.plot.add_line(self.plot.origin_x + values[1], self.plot.origin_y)

    def cmd_rapid_move_y(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        if values[0] & rdap.ORIGIN_HOME:
            self.plot.add_line(self.plot.x, self.plot.y + values[1])
        else:
            self.plot.add_line(self.plot.origin_x, self.plot.origin_y + values[1])

    def cmd_move_rel_xy(self, values: list[float]):
        '''Move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self.plot.add_line(self.plot.x + _rel_x, self.plot.y + _rel_y)

    def cmd_move_rel_x(self, values: list[float]):
        '''Move a distance along the X axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self.plot.add_line(self.plot.x + _rel_x, self.plot.y)

    def cmd_move_rel_y(self, values: list[float]):
        '''Move a distance along the Y axis relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self.plot.add_line(self.plot.x, self.plot.y + _rel_y)

    #++++ Cuts
    def cmd_cut_abs_xy(self, values: list[float]):
        '''This effectively a move with the laser off.

        Move lines are always black.
        '''
        self.plot.add_line(values[0], values[1], cut=True)

    def cmd_cut_rel_xy(self, values: list[float]):
        '''With the laser on move a distance relative to the current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        _rel_y = values[1]
        self._valid_rel('X', _rel_x)
        self._valid_rel('Y', _rel_y)
        self.plot.add_line(self.plot.x + _rel_x, self.plot.y + _rel_y, cut=True)

    def cmd_cut_rel_x(self, values: list[float]):
        '''With the laser on move a distance along the X axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_x = values[0]
        self._valid_rel('X', _rel_x)
        self.plot.add_line(self.plot.x + _rel_x, self.plot.y, cut=True)

    def cmd_cut_rel_y(self, values: list[float]):
        '''With the laser on move a distance along the Y axis relative to the
        current position.

        The relative distance cannot exceed what can be expressed in 14 bits.
        '''
        _rel_y = values[0]
        self._valid_rel('Y', _rel_y)
        self.plot.add_line(self.plot.x, self.plot.y + _rel_y, cut=True)

    #++++ Power
    def cmd_min_power_1(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        self.plot.set_power(values[0])

    def cmd_max_power_1(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        self.plot.set_power(values[0])

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

    def cmd_update(self, cmd_id, label, cmd, sub_cmd, values: list):
        '''Update the plot depending upon the command.

        Parameters:
            id      The command sequence number.
            label   The command name.
            cmd     The command
            sub_cmd The sub-command
            values  A list of decoded parameter values
        '''
        if self.plot.enabled and cmd in self._ct:
            self.plot.cmd = cmd
            if sub_cmd is not None:
                if sub_cmd in self._ct[cmd]:
                    self.plot.sub_cmd = sub_cmd
                    try:
                        self.plot.cmd_id = cmd_id
                        self.plot.cmd_label = label
                        getattr(self, self._ct[cmd][sub_cmd])(values)
                    except Exception as e:
                        pass
            else:
                try:
                    self.plot.cmd_id = cmd_id
                    self.plot.cmd_label = label
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
        if self.plot.enabled and addr_msb in self._mt:
            if addr_lsb in self._mt[addr_msb]:
                getattr(self, self._mt[addr_msb][addr_lsb])(values)
