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
        self.cmd_counters = {
            'cmd_axis_x_move': 0,
            'cmd_axis_y_move': 0,
            'cmd_move_abs_xy': 0,
            'cmd_move_rel_xy': 0,
            'cmd_move_rel_x': 0,
            'cmd_move_rel_y': 0,
            'cmd_cut_abs_xy': 0,
            'cmd_cut_rel_xy': 0,
            'cmd_cut_rel_x': 0,
            'cmd_imd_power_1': 0,
            'cmd_end_power_1': 0,
            'cmd_imd_power_2': 0,
            'cmd_end_power_2': 0,
            'cmd_imd_power_3': 0,
            'cmd_end_power_3': 0,
            'cmd_imd_power_4': 0,
            'cmd_end_power_4': 0,
            'cmd_cut_rel_y': 0,
            'cmd_min_power_1': 0,
            'cmd_max_power_1': 0,
            'cmd_min_power_2': 0,
            'cmd_max_power_2': 0,
            'cmd_min_power_3': 0,
            'cmd_max_power_3': 0,
            'cmd_min_power_4': 0,
            'cmd_max_power_4': 0,
            'cmd_min_power_1_part': 0,
            'cmd_max_power_1_part': 0,
            'cmd_min_power_2_part': 0,
            'cmd_max_power_2_part': 0,
            'cmd_min_power_3_part': 0,
            'cmd_max_power_3_part': 0,
            'cmd_min_power_4_part': 0,
            'cmd_max_power_4_part': 0,
            'cmd_speed_laser_1': 0,
            'cmd_speed_axis': 0,
            'cmd_speed_laser_1_part': 0,
            'cmd_force_eng_speed': 0,
            'cmd_axis_move': 0,
            'cmd_rapid_move_x': 0,
            'cmd_rapid_move_y': 0,
            'cmd_rapid_move_xy': 0,
            'cmd_rapid_move_xyu': 0,
        }
        self.mt_counters = {
            'mt_bed_size_x': 0,
            'mt_bed_size_y': 0,
            'mt_card_id': 0,
        }

        self.plot = cpa.cpa_plotter.CpaPlotter(
            out, title, self.s, self.m_to_s_map,
            self.cmd_counters, self.mt_counters)

    #++++ Memory table
    def mt_bed_size_x(self, values: list[float]):
        '''Set the bed size X dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.plot.set_bed_dimension('X', values[0])

    def mt_bed_size_y(self, values: list[float]):
        '''Set the bed size Y dimension.

        If both X and Y are set then draw a rectangle to indicate the bed area.
        '''
        self.plot.set_bed_dimension('Y', values[0])

    def mt_card_id(self, values: list[float]):
        self.out.write(f'Card ID: {values[0]}')

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
    def cmd_imd_power_1(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_end_power_1(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_imd_power_2(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_end_power_2(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_imd_power_3(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_end_power_3(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_imd_power_4(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_end_power_4(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

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

    def cmd_min_power_2(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_2(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_3(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_3(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_4(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_4(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_1_part(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_1_part(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_2_part(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_2_part(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_3_part(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_3_part(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_min_power_4_part(self, values: list[float]):
        '''Set min power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

    def cmd_max_power_4_part(self, values: list[float]):
        '''Set max power.

        TODO: Currently it is unknown what effect min and max power have.
        '''
        pass # TBD

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
        0xC0: 'cmd_imd_power_2',
        0xC1: 'cmd_end_power_2',
        0xC2: 'cmd_imd_power_3',
        0xC3: 'cmd_end_power_3',
        0xC4: 'cmd_imd_power_4',
        0xC5: 'cmd_end_power_4',
        0xC6: {
            0x01: 'cmd_min_power_1',
            0x02: 'cmd_max_power_1',
            0x21: 'cmd_min_power_2',
            0x22: 'cmd_max_power_2',
            0x05: 'cmd_min_power_3',
            0x06: 'cmd_max_power_3',
            0x07: 'cmd_min_power_4',
            0x08: 'cmd_max_power_4',
            0x31: 'cmd_min_power_1_part',
            0x32: 'cmd_max_power_1_part',
            0x41: 'cmd_min_power_2_part',
            0x42: 'cmd_max_power_2_part',
            0x35: 'cmd_min_power_3_part',
            0x36: 'cmd_max_power_3_part',
            0x37: 'cmd_min_power_4_part',
            0x38: 'cmd_max_power_4_part',
        },
        0xC7: 'cmd_imd_power_1',
        0xC8: 'cmd_end_power_1',
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
                        _method = self._ct[cmd][sub_cmd]
                        self.cmd_counters[_method] += 1
                        getattr(self, _method)(values)
                    except Exception as e:
                        pass
            else:
                try:
                    self.plot.cmd_id = cmd_id
                    self.plot.cmd_label = label
                    _method = self._ct[cmd]
                    self.cmd_counters[_method] += 1
                    getattr(self, _method)(values)
                except Exception as e:
                    pass

    _mt = {
        0x00: {
            0x26: 'mt_bed_size_x',
            0x36: 'mt_bed_size_y',
        },
        0x05: {
            0x7E: 'mt_card_id',
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
                _mem = self._mt[addr_msb][addr_lsb]
                self.mt_counters[_mem] += 1
                getattr(self, _mem)(values)
