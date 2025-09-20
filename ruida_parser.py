'''A state machine for parsing an input stream byte by byte.

This state machine must be driven by repeatedly calling "step" with a single
byte and whether the current byte is part of a reply or not.

NOTE: This does not verify the host/controller packet handshake.
'''
from rda_emitter import RdaEmitter

# Single byte handshaking.
ACK = 0xCC  # Controller received data and is valid.
ERR = 0xCD  # Controller detected a problem with the message.
ENQ = 0xCE  # Keep alive. This should be replied to with a corresponding
            # ENQ.
NAK = 0xCF  # Message checksum mismatch. Resend packet.

CMD_MASK = 0x80 # Only the first byte of a command has the top bit set.
                # This, I'm guessing, is the primary reason a 7 bit
                # protocol is used.
                # This can be used to re-sync or to check the length of
                # data associated with a command for validity.

# This table defines the number of bit and corresponding number of incoming
# data bytes for each basic data type.
RD_TYPES = {
#   Type            bits    bytes
    'bool_7':   [   7,      1], # Boolean value -- True or False.
    'int_7':    [   7,      1],
    'uint_7':   [   7,      1],
    'int_14':   [   14,     2],
    'uint_14':  [   14,     2],
    'int_35':   [   32,     5], # Top three bits are dropped.
    'uint_35':  [   32,     5], # Top three bits are dropped.
    'cstring':  [   7,      1], # The values are multiplied by the length
                                # of the string.
    'tbd':      [   -1,    -1]  # Type is unknown signal read to end of packet.
                                # Use this for reverse engineering data.
}
# Indexes into the RD_TYPES table.
# e.g. to get the number of bytes needed to decode the value:
#  n_bytes = RD_TYPES[DTYP][RDT_BYTES]
RDT_BITS = 0    # The number of bits in a decoded value.
                # These can be used to generate a mask for ignoring
                # bits in a larger variable. e.g. A
RDT_BYTES = 1   # The number of data bytes to decode.

# Added decode methods. These were added for consistency.
# TODO: Move these to rdjob.py.
def decode7(data):
    return data[0] & 0x7F

def decodeu7(data):
    return decode7(data)

# Rapid option table.
ROT = {
    0x00: 'RAPID_OPTION_ORIGIN',
    0x01: 'RAPID_OPTION_LIGHT_ORIGIN',
    0x02: 'RAPID_OPTION_NONE',
    0x03: 'RAPID_OPTION_LIGHT',
}

def rapid_option(data):
    return ROT[data[0]]

# Parameter specifications. NOTE: These are command specific where possible.
# These need to be tuples because the type is used for determining next
# states in the state machine.
# Basic types:
INT7 = ('{}', 'int7', 'int_7')
UINT7 = ('{}', 'uint7', 'uint_7')
HEX7 = ('{:02X}', 'uint7', 'uint_7')
BOOL7 = ('{}', 'bool', 'bool_7')
INT14 = ('{}', 'int14', 'int_14')
UINT14 = ('{}', 'uint14', 'uint_14')
HEX14 = ('{:04X}', 'uint14', 'uint_14')
INT35 = ('{}', 'int35', 'int_35')
UINT35 = ('{}', 'uint35', 'uint_35')
HEX35 = ('{:08X}', 'uint35', 'uint_35')
CSTRING = ('{}', 'cstring', 'cstring')
# Paremeter types:
FNAME = ('File:{}', 'cstring','cstring') # File name.
FNUM = ('FNum: {}', 'uint14', 'uint_14')
ENAME = ('Elem:{}', 'cstring', 'cstring')
PART = ('Part:{}', 'int7', 'int_7') # or layer.
LASER = ('Laser:{}', 'int7', 'int_7') # For dual head lasers.
VALUE = ('{}', 'int7', 'int_7')
RAPID_OPTION = ('Option:{}', rapid_option, 'int_7')
COLOR = ('Color:#{:06X}', 'uint35', 'uint_35')
MEMORY = ('Addr:{:04X}', 'uint14', 'uint_14')
SETTING = ('Set:{:08X}', 'uint35', 'uint_35')
ID = ('ID:{}', 'uint14', 'uint_14')
DIRECTION = ('Dir:{}', 'int7', 'int_7') # Table-ize the direction?
ABSCOORD = ('{}um', 'int35', 'int_35')
XABSCOORD = ('X={}um', 'int35', 'int_35')
YABSCOORD = ('Y={}um', 'int35', 'int_35')
ZABSCOORD = ('Z={}um', 'int35', 'int_35')
AABSCOORD = ('A={}um', 'int35', 'int_35')
UABSCOORD = ('U={}um', 'int35', 'int_35')
RELCOORD = ('{}um', 'int35', 'int_35')
XRELCOORD = ('RelX={}um', 'int35', 'int_35')
YRELCOORD = ('RelY={}um', 'int35', 'int_35')
PARSE_POWER = ('Power:{:1f}%', 'power', 'uint_14')
PARSE_SPEED = ('Speed:{:3f}mm/S', 'speed', 'int_35')
PARSE_FREQUENCY = ('Freq:{:3f}KHz', 'frequency', 'int_35')
PARSE_TIME = ('{:3f}mS', 'time', 'int_35')
# Reply types.
REPLY = -1  # An integer to indicate when a reply to a command is expected.
TBD = ('TBD:{}', 'tbd', 'tbd')  # Use this for data that needs to be reverse
                                # engineered.

# Decoder list indexes.
# e.g. To retrieve the print format for a decoder:
#  format = INT7[DFMT]
# To call the decoding function:
#  r = INT7[DDEC](data)
# To determine the number of bytes decoded:
#  n = INT7[DTYP][RDT_BYTES]
DFMT = 0 # Print format string.
DDEC = 1 # Decoder function to call.
DTYP = 2 # Basic type (used to determine how many bytes to process.)

# Buttons (keys) found on a Ruida control panel.
KT_KEYS = {
    0x01: 'X_MINUS',
    0x02: 'X_PLUS',
    0x03: 'Y_PLUS',
    0x04: 'Y_MINUS',
    0x05: 'PULSE',
    0x06: 'PAUSE',
    0x07: 'ESCAPE',
    0x08: 'ORIGIN',
    0x09: 'STOP',
    0x0A: 'Z_PLUS',
    0x0B: 'Z_MINUS',
    0x0C: 'U_PLUS',
    0x0D: 'U_MINUS',
    0x0E: '?',
    0x0F: 'TRACE',
    0x10: '?',
    0x11: 'SPEED',
    0x12: 'LASER_GATE',
}

# Keypad table - port 50207
KT = {
    0x50: ['Press:  ', KT_KEYS],
    0x51: ['Release:', KT_KEYS],
    0x53: {0x00: 'INTERFACE_FRAME'},
}

# Memory internal to the controller and readable using the 0xDA command.
# These return 32 bit values. Actual meanings to be defined.
# NOTE: Because only command bytes have the top bit set, the codes
# are limited to 128 for the lower order byte. The upper order byte therefore
# is a multiple of 128.
MT = {
    0x00: {
        0x04: ('IO Enable', REPLY, TBD), # 0x004
        0x05: ('G0 Velocity', REPLY, TBD), # 0x005
        0x0B: ('Eng Facula', REPLY, TBD), # 0x00B
        0x0C: ('Home Velocity', REPLY, TBD), # 0x00C
        0x0E: ('Eng Vert Velocity', REPLY, TBD), # 0x00E
        0x10: ('System Control Mode', REPLY, TBD), # 0x010
        0x11: ('Laser PWM Frequency 1', REPLY, TBD), # 0x011
        0x12: ('Laser Min Power 1', REPLY, TBD), # 0x012
        0x13: ('Laser Max Power 1', REPLY, TBD), # 0x013
        0x16: ('Laser Attenuation', REPLY, TBD), # 0x016
        0x17: ('Laser PWM Frequency 2', REPLY, TBD), # 0x017
        0x18: ('Laser Min Power 2', REPLY, TBD), # 0x018
        0x19: ('Laser Max Power 2', REPLY, TBD), # 0x019
        0x1A: ('Laser Standby Frequency 1', REPLY, TBD), # 0x01A
        0x1B: ('Laser Standby Pulse 1', REPLY, TBD), # 0x01B
        0x1C: ('Laser Standby Frequency 2', REPLY, TBD), # 0x01C
        0x1d: ('Laser Standby Pulse 2', REPLY, TBD), # 0x01D
        0x1e: ('Auto Type Space', REPLY, TBD), # 0x01E
        0x20: ('Axis Control Para 1', REPLY, TBD), # 0x020
        0x21: ('Axis Precision 1', REPLY, TBD), # 0x021
        0x23: ('Axis Max Velocity 1', REPLY, TBD), # 0x023
        0x24: ('Axis Start Velocity 1', REPLY, TBD), # 0x024
        0x25: ('Axis Max Acc 1', REPLY, TBD), # 0x025
        0x26: ('Axis Range 1', REPLY, TBD), # 0x026
        0x27: ('Axis Btn Start Vel 1', REPLY, TBD), # 0x027
        0x28: ('Axis Btn Acc 1', REPLY, TBD), # 0x028
        0x29: ('Axis Estp Acc 1', REPLY, TBD), # 0x029
        0x2A: ('Axis Home Offset 1', REPLY, TBD), # 0x02A
        0x2B: ('Axis Backlash 1', REPLY, TBD), # 0x02B
        0x30: ('Axis Control Para 2', REPLY, TBD), # 0x030
        0x31: ('Axis Precision 2', REPLY, TBD), # 0x031
        0x33: ('Axis Max Velocity 2', REPLY, TBD), # 0x033
        0x34: ('Axis Start Velocity 2', REPLY, TBD), # 0x034
        0x35: ('Axis Max Acc 2', REPLY, TBD), # 0x035
        0x36: ('Axis Range 2', REPLY, TBD), # 0x036
        0x37: ('Axis Btn Start Vel 2', REPLY, TBD), # 0x037
        0x38: ('Axis Btn Acc 2', REPLY, TBD), # 0x038
        0x39: ('Axis Estp Acc 2', REPLY, TBD), # 0x039
        0x3A: ('Axis Home Offset 2', REPLY, TBD), # 0x03A
        0x3B: ('Axis Backlash 2', REPLY, TBD), # 0x03B
        0x40: ('Axis Control Para 3', REPLY, TBD), # 0x040
        0x41: ('Axis Precision 3', REPLY, TBD), # 0x041
        0x43: ('Axis Max Velocity 3', REPLY, TBD), # 0x043
        0x44: ('Axis Start Velocity 3', REPLY, TBD), # 0x044
        0x45: ('Axis Max Acc 3', REPLY, TBD), # 0x045
        0x46: ('Axis Range 3', REPLY, TBD), # 0x046
        0x47: ('Axis Btn Start Vel 3', REPLY, TBD), # 0x047
        0x48: ('Axis Btn Acc 3', REPLY, TBD), # 0x048
        0x49: ('Axis Estp Acc 3', REPLY, TBD), # 0x049
        0x4A: ('Axis Home Offset 3', REPLY, TBD), # 0x04A
        0x4B: ('Axis Backlash 3', REPLY, TBD), # 0x04B
        0x50: ('Axis Control Para 4', REPLY, TBD), # 0x050
        0x51: ('Axis Precision 4', REPLY, TBD), # 0x051
        0x53: ('Axis Max Velocity 4', REPLY, TBD), # 0x053
        0x54: ('Axis Start Velocity 4', REPLY, TBD), # 0x054
        0x55: ('Axis Max Acc 4', REPLY, TBD), # 0x055
        0x56: ('Axis Range 4', REPLY, TBD), # 0x056
        0x57: ('Axis Btn Start Vel 4', REPLY, TBD), # 0x057
        0x58: ('Axis Btn Acc 4', REPLY, TBD), # 0x058
        0x59: ('Axis Estp Acc 4', REPLY, TBD), # 0x059
        0x5A: ('Axis Home Offset 4', REPLY, TBD), # 0x05A
        0x5B: ('Axis Backlash 4', REPLY, TBD), # 0x05B
        0x60: ('Machine Type (0x1155, 0xaa55)', REPLY, TBD), # 0x060
        0x63: ('Laser Min Power 3', REPLY, TBD), # 0x063
        0x64: ('Laser Max Power 3', REPLY, TBD), # 0x064
        0x65: ('Laser PWM Frequency 3', REPLY, TBD), # 0x065
        0x66: ('Laser Standby Frequency 3', REPLY, TBD), # 0x066
        0x67: ('Laser Standby Pulse 3', REPLY, TBD), # 0x067
        0x68: ('Laser Min Power 4', REPLY, TBD), # 0x068
        0x69: ('Laser Max Power 4', REPLY, TBD), # 0x069
        0x6a: ('Laser PWM Frequency 4', REPLY, TBD), # 0x06A
        0x6B: ('Laser Standby Frequency 4', REPLY, TBD), # 0x06B
        0x6C: ('Laser Standby Pulse 4', REPLY, TBD), # 0x06C
    },
    0x02: {
        0x00: ('System Settings', REPLY, TBD), # 0x100
        0x01: ('Turn Velocity', REPLY, TBD), # 0x101
        0x02: ('Syn Acc', REPLY, TBD), # 0x102
        0x03: ('G0 Delay', REPLY, TBD), # 0x103
        0x07: ('Feed Delay After', REPLY, TBD), # 0x107
        0x09: ('Turn Acc', REPLY, TBD), # 0x109
        0x0A: ('G0 Acc', REPLY, TBD), # 0x10A
        0x0B: ('Feed Delay Prior', REPLY, TBD), # 0x10B
        0x0c: ('Manual Dis', REPLY, TBD), # 0x10C
        0x0D: ('Shut Down Delay', REPLY, TBD), # 0x10D
        0x0E: ('Focus Depth', REPLY, TBD), # 0x10E
        0x0F: ('Go Scale Blank', REPLY, TBD), # 0x10F
        0x1A: ('Acc Ratio', REPLY, TBD), # 0x11A
        0x17: ('Array Feed Repay', REPLY, TBD), # 0x117
        0x1B: ('Turn Ratio', REPLY, TBD), # 0x11B
        0x1C: ('Acc G0 Ratio', REPLY, TBD), # 0x11C
        0x1F: ('Rotate Pulse', REPLY, TBD), # 0x11F
        0x21: ('Rotate D', REPLY, TBD), # 0x121
        0x24: ('X Minimum Eng Velocity', REPLY, TBD), # 0x124
        0x25: ('X Eng Acc', REPLY, TBD), # 0x125
        0x26: ('User Para 1', REPLY, TBD), # 0x126
        0x28: ('Z Home Velocity', REPLY, TBD), # 0x128
        0x29: ('Z Work Velocity', REPLY, TBD), # 0x129
        0x2A: ('Z G0 Velocity', REPLY, TBD), # 0x12A
        0x2B: ('Z Pen Up Position', REPLY, TBD), # 0x12B
        0x2C: ('U Home Velocity ', REPLY, TBD), # 0x12C
        0x2D: ('U Work Velocity', REPLY, TBD), # 0x12D
        0x31: ('Manual Fast Speed', REPLY, TBD), # 0x131
        0x32: ('Manual Slow Speed', REPLY, TBD), # 0x132
        0x34: ('Y Minimum Eng Velocity', REPLY, TBD), # 0x134
        0x35: ('Y Eng Acc', REPLY, TBD), # 0x135
        0x37: ('Eng Acc Ratio', REPLY, TBD), # 0x137
    },
    0x03: {
        0x00: ('Card Language', REPLY, TBD), # 0x180
        0x01: ('PC Lock 1', REPLY, TBD), # 0x181
        0x02: ('PC Lock 2', REPLY, TBD), # 0x182
        0x03: ('PC Lock 3', REPLY, TBD), # 0x183
        0x04: ('PC Lock 4', REPLY, TBD), # 0x184
        0x05: ('PC Lock 5', REPLY, TBD), # 0x185
        0x06: ('PC Lock 6', REPLY, TBD), # 0x186
        0x07: ('PC Lock 7', REPLY, TBD), # 0x187
        0x11: ('Total Laser Work Time', REPLY, TBD), # 0x211
    },
    0x04: {
        0x00: ('Machine Status (0b00110111 relevant bits).', REPLY, TBD), # 0x200
        0x01: ('Total Open Time', REPLY, TBD), # 0x201
        0x02: ('Total Work Time', REPLY, TBD), # 0x202
        0x03: ('Total Work Number', REPLY, TBD), # 0x203
        0x05: ('Total Doc Number', REPLY, TBD), # 0x205
        0x08: ('Pre Work Time', REPLY, TBD), # 0x208
        0x21: ('Axis Preferred Position 1', REPLY, TBD), # 0x221
        0x23: ('Total Work Length 1', REPLY, TBD), # 0x223
        0x31: ('Axis Preferred Position 2', REPLY, TBD), # 0x231
        0x33: ('Total Work Length 2', REPLY, TBD), # 0x233
        0x41: ('Axis Preferred Position 3', REPLY, TBD), # 0x241
        0x43: ('Total Work Length 3', REPLY, TBD), # 0x243
        0x51: ('Axis Preferred Position 4', REPLY, TBD), # 0x251
        0x53: ('Total Work Length 4', REPLY, TBD), # 0x253
    },
    0x05: {
        0x7E: ('Card ID', REPLY, TBD), # 0x2FE
        0x7F: ('Mainboard Version', REPLY, TBD), # 0x2FF
    },
    0x07: {
        0x10: ('Document Time', REPLY, TBD), # 0x390
    },
    0x0B: {
        0x11: ('Card Lock', REPLY, TBD), # 0x591
    },
}

# Reply table
RT = {
    # TBD Learn during debug.
    }
# In the command table this indicates a reply is expected and the next entry
# is a reference to the reply in RT.
REPLY = -1

# Command table - port 50200
CT = {
    0x80: {
        0x00: ('AXIS_X_MOVE', XABSCOORD),
        0x08: ('AXIS_Z_MOVE', YABSCOORD),
    },
    0x88: ('MOVE_ABS_XY', XABSCOORD, YABSCOORD),
    0x89: ('MOVE_REL_XY', XRELCOORD, YRELCOORD),
    0x8A: ('MOVE_REL_X', XRELCOORD),
    0x8B: ('MOVE_REL_Y', YRELCOORD),
    0xA0: {
        0x00: ('AXIS_A_MOVE', AABSCOORD),
        0x08: ('AXIS_U_MOVE', UABSCOORD),
    },
    0xA5: KT, # Keypad presses.
    0xA8: ('CUT_ABS_XY', XABSCOORD, YABSCOORD),
    0xA9: ('CUT_REL_XY', XRELCOORD, YRELCOORD),
    0xAA: ('CUT_REL_X', XRELCOORD),
    0xAB: ('CUT_REL_Y', YRELCOORD),
    0xC0: ('IMD_POWER_2', PARSE_POWER),
    0xC1: ('END_POWER_2', PARSE_POWER),
    0xC2: ('IMD_POWER_3', PARSE_POWER),
    0xC3: ('IMD_POWER_4', PARSE_POWER),
    0xC4: ('END_POWER_3', PARSE_POWER),
    0xC5: ('END_POWER_4', PARSE_POWER),
    0xC6: {
        0x01: ('MIN_POWER_1', PARSE_POWER),
        0x02: ('MAX_POWER_1', PARSE_POWER),
        0x05: ('MIN_POWER_3', PARSE_POWER),
        0x06: ('MAX_POWER_3', PARSE_POWER),
        0x07: ('MIN_POWER_4', PARSE_POWER),
        0x08: ('MAX_POWER_4', PARSE_POWER),
        0x10: ('LASER_INTERVAL', PARSE_TIME),
        0x11: ('ADD_DELAY', PARSE_TIME),
        0x12: ('LASER_ON_DELAY', PARSE_TIME),
        0x13: ('LASER_OFF_DELAY', PARSE_TIME),
        0x15: ('LASER_ON_DELAY2', PARSE_TIME),
        0x16: ('LASER_OFF_DELAY2', PARSE_TIME),
        0x31: ('MIN_POWER_1_PART', PART, PARSE_POWER),
        0x32: ('MAX_POWER_1_PART', PART, PARSE_POWER),
        0x35: ('MIN_POWER_3_PART', PART, PARSE_POWER),
        0x36: ('MAX_POWER_3_PART', PART, PARSE_POWER),
        0x37: ('MIN_POWER_4_PART', PART, PARSE_POWER),
        0x38: ('MAX_POWER_4_PART', PART, PARSE_POWER),
        0x41: ('MIN_POWER_2_PART', PART, PARSE_POWER),
        0x42: ('MAX_POWER_2_PART', PART, PARSE_POWER),
        0x50: ('THROUGH_POWER_1', PARSE_POWER),
        0x51: ('THROUGH_POWER_2', PARSE_POWER),
        0x55: ('THROUGH_POWER_3', PARSE_POWER),
        0x56: ('THROUGH_POWER_4', PARSE_POWER),
        0x60: ('FREQUENCY_PART', LASER, PART, PARSE_FREQUENCY),
    },
    0xC7: ('IMD_POWER_1', PARSE_POWER),
    0xC8: ('END_POWER_1', PARSE_POWER),
    0xC9: {
        0x02: ('SPEED_LASER_1', PARSE_SPEED),
        0x03: ('SPEED_AXIS', PARSE_SPEED),
        0x04: ('SPEED_LASER_1_PART', PART, PARSE_SPEED),
        0x05: ('FORCE_ENG_SPEED', PARSE_SPEED),
        0x06: ('SPEED_AXIS_MOVE', PARSE_SPEED),
    },
    0xCA: {
        0x01: {
            0x00: 'LAYER_END',
            0x01: 'WORK_MODE_1',
            0x02: 'WORK_MODE_2',
            0x03: 'WORK_MODE_3',
            0x04: 'WORK_MODE_4',
            0x05: 'WORK_MODE_6',
            0x10: 'LASER_DEVICE_0',
            0x11: 'LASER_DEVICE_1',
            0x12: 'AIR_ASSIST_OFF',
            0x13: 'AIR_ASSIST_ON',
            0x14: 'DB_HEAD',
            0x30: 'EN_LASER_2_OFFSET_0',
            0x31: 'EN_LASER_2_OFFSET_1',
            0x55: 'WORK_MODE_5',
        },
        0x02: ('LAYER_NUMBER_PART', PART),
        0x03: ('EN_LASER_TUBE_START', PART),
        0x04: ('X_SIGN_MAP', VALUE),
        0x05: ('LAYER_COLOR', COLOR),
        0x06: ('LAYER_COLOR_PART', PART, COLOR),
        0x10: ('EN_EX_IO', VALUE),
        0x22: ('MAX_LAYER_PART', PART),
        0x30: ('U_FILE_ID', ID),
        0x40: ('ZU_MAP', VALUE),
    },
    0xD7: 'EOF',
    0xD8: {
        0x00: 'START_PROCESS',
        0x01: 'STOP_PROCESS',
        0x02: 'PAUSE_PROCESS',
        0x03: 'RESTORE_PROCESS',
        0x10: 'REF_POINT_2',
        0x11: 'REF_POINT_1',
        0x12: 'CURRENT_POSITION',
        0x20: 'KEYDOWN_X_LEFT',
        0x21: 'KEYDOWN_X_RIGHT',
        0x22: 'KEYDOWN_Y_TOP',
        0x23: 'KEYDOWN_Y_BOTTOM',
        0x24: 'KEYDOWN_Z_UP',
        0x25: 'KEYDOWN_Z_DOWN',
        0x26: 'KEYDOWN_U_FORWARD',
        0x27: 'KEYDOWN_U_BACKWARDS',
        0x2A: 'HOME_XY',
        0x2C: 'HOME_Z',
        0x2D: 'HOME_U',
        0x2E: 'FOCUS_Z',
        0x30: 'KEYUP_LEFT',
        0x31: 'KEYUP_RIGHT',
        0x32: 'KEYUP_Y_TOP',
        0x33: 'KEYUP_Y_BOTTOM',
        0x34: 'KEYUP_Z_UP',
        0x35: 'KEYUP_Z_DOWN',
        0x36: 'KEYUP_U_FORWARD',
        0x37: 'KEYUP_U_BACKWARDS',
    },
    0xD9: {
        0x00: ('RAPID_MOVE_X', RAPID_OPTION, XABSCOORD),
        0x01: ('RAPID_MOVE_Y', RAPID_OPTION, YABSCOORD),
        0x02: ('RAPID_MOVE_Z', RAPID_OPTION, ZABSCOORD),
        0x03: ('RAPID_MOVE_U', RAPID_OPTION, UABSCOORD),
        0x0F: ('RAPID_FEED_AXIS_MOVE', RAPID_OPTION),
        0x10: ('RAPID_MOVE_XY', RAPID_OPTION, XABSCOORD, YABSCOORD),
        0x30: ('RAPID_MOVE_XYU', RAPID_OPTION, XABSCOORD, YABSCOORD, UABSCOORD),
    },
    0xDA: {
        0x00: ('GET_SETTING', MEMORY, REPLY, UINT7),  # TODO: Reply?
        0x01: ('SET_SETTING', MEMORY, SETTING, SETTING),
    },
    0xE5: {
        0x00: ('DOCUMENT_FILE_UPLOAD', FNUM, UINT35, UINT35),
        0x02: 'DOCUMENT_FILE_END',
        0x05: 'SET_FILE_SUM',
    },
    0xE7: {
        0x00: 'DOCUMENT_FILE_UPLOAD', # TODO: Reply?
        0x01: ('SET_FILE_NAME', FNAME),
        0x03: ('PROCESS_TOP_LEFT', XABSCOORD, YABSCOORD),
        0x04: ('PROCESS_REPEAT',
                INT14, INT14, INT14, INT14, INT14, INT14, INT14),
        0x05: ('ARRAY_DIRECTION', DIRECTION),
        0x06: ('FEED_REPEAT', UINT35, UINT35),
        0x07: ('PROCESS_BOTTOM_RIGHT', XABSCOORD, YABSCOORD),
        0x08: ('ARRAY_REPEAT',
                INT14, INT14, INT14, INT14, INT14, INT14, INT14),
        0x09: ('FEED_LENGTH', INT35),
        0x0A: 'FEED_INFO', # TODO: Reply?
        0x0B: ('ARRAY_EN_MIRROR_CUT', UINT7),
        0x13: ('ARRAY_MIN_POINT', XABSCOORD, YABSCOORD),
        0x17: ('ARRAY_MAX_POINT', XABSCOORD, YABSCOORD),
        0x23: ('ARRAY_ADD',XABSCOORD, YABSCOORD),
        0x24: ('ARRAY_MIRROR', UINT7),
        0x35: ('BLOCK_X_SIZE', XABSCOORD, YABSCOORD),
        # ? 0x35: ('BY_TEST: {:08X}', UINT35), # expect 0x11227766?
        0x36: ('SET_FILE_EMPTY', UINT7),
        0x37: 'ARRAY_EVEN_DISTANCE',
        0x38: 'SET_FEED_AUTO_PAUSE',
        0x3A: 'UNION_BLOCK_PROPERTY',
        0x50: ('DOCUMENT_MIN_POINT', XABSCOORD, YABSCOORD),
        0x51: ('DOCUMENT_MAX_POINT', XABSCOORD, YABSCOORD),
        0x52: ('PART_MIN_POINT', PART, XABSCOORD, YABSCOORD),
        0x53: ('PART_MAX_POINT', PART, XABSCOORD, YABSCOORD),
        0x54: ('PEN_OFFSET: Axis=', UINT7, RELCOORD),
        0x55: ('LAYER_OFFSET: Axis=', UINT7, RELCOORD),
        0x60: ('SET_CURRENT_ELEMENT_INDEX', UINT7),
        0x61: ('PART_MIN_POINT_EX', PART, XABSCOORD, YABSCOORD),
        0x62: ('PART_MAX_POINT_EX', PART, XABSCOORD, XABSCOORD),
    },
    0xE8: {
        0x00: ('DELETE_DOCUMENT', UINT35, UINT35), # Values are what?
        0x01: ('DOCUMENT_NUMBER', UINT14),
        0x02: 'FILE_TRANSFER',
        0x03: ('SELECT_DOCUMENT', UINT7),
        0x04: 'CALCULATE_DOCUMENT_TIME', # TODO: Reply?
    },
    0xEA: ('ARRAY_START', UINT7),
    0xEB: 'ARRAY_END',
    0xF0: 'REF_POINT_SET',
    0xF1: {
       0x00: ('ELEMENT_MAX_INDEX', UINT7),
       0x01: ('ELEMENT_NAME_MAX_INDEX', UINT7),
       0x02: ('ENABLE_BLOCK_CUTTING', UINT7),
       0x03: ('DISPLAY_OFFSET', XABSCOORD, YABSCOORD),
       0x04: ('FEED_AUTO_CALC', UINT7),
    },
    0xF2: {
        0x00: ('ELEMENT_INDEX', UINT7),
        0x01: ('ELEMENT_NAME_INDEX', UINT7),
        0x02: ('ELEMENT_NAME', ENAME),
        0x03: ('ELEMENT_ARRAY_MIN_POINT', XABSCOORD, YABSCOORD),
        0x04: ('ELEMENT_ARRAY_MAX_POINT', XABSCOORD, YABSCOORD),
        0x05: ('ELEMENT_ARRAY',
                INT14, INT14, INT14, INT14, INT14, INT14, INT14),
        0x06: ('ELEMENT_ARRAY_ADD', XABSCOORD, YABSCOORD),
        0x07: ('ELEMENT_ARRAY_MIRROR', UINT7),
    },
}

class RdDecoder():
    '''A parameter or reply decoder.

    Data representing a command parameter or reply. This also includes a
    state machine for accumulating and decoding a parameter. The step machine
    is a single state and is intended to run as a sub-state machine from the
    command parser state machine.

    NOTE: All incoming data bytes are expected to be 7 bit.

    To use prime the decoder with a parameter spec then step with incoming
    bytes until a value is returned or an error occurs.

    Attributes:
        format  The format string used to output the parameter data.
        rd_type The Ruida basic type for the parameter.
        decoder The decoder to call to convert the parameter bytes to
                a Python variable.
        data    The parameter data byte array to be converted.
        value   The resulting parameter value after conversion.
    '''
    def __init__(self, output: RdaEmitter):
        self.out = output
        self.accumulating = False
        self.format: str = ''
        self.rd_type:str = ''
        self.data: bytearray = bytearray([])
        self.value = None   # The actual type is not known until after decode.
        self.datum = None
        self._decoder = None
        self._length = 0
        self._remaining = 0

    @property
    def formatted(self) -> str:
        return self.format.format(self.value)

    @property
    def raw(self) -> bytearray:
        return self.data

    #++++++++++++++
    # Decoders
    # Basic Types
    def rd_int7(self, data: bytearray):
        self.value = data[0]
        if self.value & 0x40:
            self.value = -self.value
        return self.formatted

    def rd_uint7(self, data: bytearray):
        self.value = data[0]
        return self.formatted

    def rd_bool(self, data: bytearray):
        self.value = data[0] != 0
        return self.formatted

    def to_int(self, data: bytearray, n_bytes=0):
        if not n_bytes:
            _n = self._length
        else:
            _n = n_bytes
        _v = 0
        for _i in range(_n):
            _b = data[_i]
            if _i == 0:
                _b &= 0x3F
            _v += (_v << 7) + _b
        if data[0] & 0x40:
            _v *= -1
        return _v

    def to_uint(self, data: bytearray, n_bytes=0):
        if not n_bytes:
            _n = self._length
        else:
            _n = n_bytes
        _v = 0
        for _i in range(_n):
            _v += (_v << 7) + data[_i]
        return _v

    def rd_int14(self, data: bytearray):
        self.value = self.to_int(data)
        return self.formatted

    def rd_uint14(self, data: bytearray):
        self.value = self.to_uint(data)
        return self.formatted

    def rd_int35(self, data: bytearray):
        self.value = self.to_int(data)
        return self.formatted

    def rd_uint35(self, data: bytearray):
        self.value = self.to_uint(data)
        return self.formatted

    def cstring(self, data: bytearray):
        _i = 0
        _s = ''
        while True:
            _c = data[_i]
            if _c == 0:
                break
            _s += chr(_c)
            _i += 1
        self.value = _s
        return self.formatted

    # Ruida Parameter Types
    def power(self, data: bytearray):
        self.value = self.to_int(data) / (0x4000 / 100)
        return self.formatted

    def frequency(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    def speed(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    def time(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    # Ruida Reply Types
    def rd_tbd(self, data: bytearray):
        '''Convert all data to the end of the buffer to a hex string.

        This is intended to be used for data discovery.
        '''
        self.value = data.hex()
        return self.formatted

    #--------------

    def prime(self, spec: tuple, length=None):
        '''Setup to start a data decode using a data spec.

        A data spec must be a tuple having the following elements:
            0   The format to use when printing the decoded value.
            1   The decoder to use to decode the value.
            2   The Ruida defined type for the incoming data.

        Parameters:
            spec    The data format specification.
            length  Optional length parameter. This overrides the length
                    in the spec.
        '''
        self.out.verbose(f'Priming: {spec}')
        self.format: str = spec[0]
        self.rd_type:str = spec[2]
        self.data: bytearray = bytearray([])
        self.value = None   # The actual type is not known until after decode.
        self.datum = None
        # An error with getattr indicates a problem with the type table -- not
        # the incoming data.
        self._decoder = getattr(self, f'rd_{spec[1]}')
        if length is not None:
            self._length = length
        else:
            self._length = RD_TYPES[self.rd_type][1]
        self._remaining = self._length

    def step(self, datum, remaining=None):
        '''Step the decoder.

        This is a single state state machine. The transistion from this state
        produces the decoded and formatted string which can be part of the
        command or reply docode message.

        Parameters:
            datum       A single byte to accumulate for the decoder.
                        NOTE: It is an error if the most significant bit is set.
                        Only command bytes can have the most significant bit set.
            remaining   Optional number of bytes remaining in the packet. If
                        this is not None then this is used to determine when
                        capture is complete rather than use self._remaining.

        Returns:
            A formatted string containing the decoded data or None if still
            accumulating.
        '''
        if datum & CMD_MASK:
            # A possible error in the input stream. Not enough data for the
            # indicated type. Instead, a command byte has been detected. Or,
            # the parameter is incorrectly defined in the tuple passed to
            # prime.
            self.out.protocol(
                f'datum={datum:02X}: Should not have bit 7 set.')
        if datum > (CMD_MASK - 1):
            # This is likely an internal error. The datum may not be a byte.
            self.out.protocol(
                f'datum={datum:02X}: Should not be greater than 128.')
        if not self.accumulating:
            self.accumulating = True
        self.datum = datum
        self.data.append(datum)
        if remaining is not None:
            self._remaining = remaining
        else:
            self._remaining -= 1
        if self._remaining > 0:
            return None
        else:
            self.accumulating = False
            return self._decoder(self.data)

class RdParser():
    '''This is a state machine for parsing and decoding an Ruida protocol
    input stream.

    The parser is driven by repeated calls to "step" with a single byte.
    When a decode is complete step returns decoded data.

    NOTE: These tables were constructed using the information provided by
    tatarize here: https://edutechwiki.unige.ch/en/Ruida
    The command labels are defined in rdjob.py.

    Attributes:
        datum           The data byte being processed.
        remaining       The number of bytes remaining in the current packet.
                        0 indicate the end of the packet.
        state           The name of the current state.
        last            The data byte processed in the previous step.
        last_is_reply   When True the last byte was from a reply.
        data            The data accumulated since the last decoded.
        command         The current command being parsed.
        sub_command     The current sub-command being parsed.
        parameters      The list of decoded parameter values.
        command_bytes   The accumulated command bytes -- including sub-command
                        and parameters.
        param_bytes     The accumulated parameter bytes for the current parameter.
        reply_bytes     The accumulated reply bytes.
        decoded         The decoded command string. This string grows as a
                        command is parsed and decoded.
        verbose         The method to call when emitting verbose messages.
    '''
    def __init__(self, output: RdaEmitter):
        '''Initialize the parsing state machine.

        Parameters:
            output      The output stream for emitting verbose messages.
        '''
        self.out = output
        self.state = None
        self.datum = None
        self.remaining = None
        self.last = None
        self.is_reply = False
        self.last_is_reply = False
        self.data = bytearray([])
        self.command = None
        self.last_command = None
        self.sub_command = None
        self.last_sub_command = None
        self.param_list = None
        self.which_param = None
        self.parameters = []
        self.command_bytes = []
        self.param_bytes = []
        self.decoder = RdDecoder(output)
        self.decoded = ''

        self._ct = CT   # The command table to use for parsing. This changes
                        # for sub-commands.
        self._stepper = None        # For commands.
        self._sub_stepper = None    # For parameters.
        self._enter_state('sync')   # Setup the sync state.

    @property
    def is_command(self):
        return self.datum & CMD_MASK

    def _format_decoded(self, message: str, param=None):
        '''Accumulate decoded messages one by one.

        The sections of messages are accumulated by appending strings to
        the decoded string.'''
        if param is not None:
            self.decoded += message.format(param)
        else:
            self.decoded += message

    #+++++++++++++++ State Machine
    # Internal states. Every state is required to have two handlers identified
    # by the following prefixes:
    #  _tr_... State transition handler.
    #           A transition handler prepares for entry to the next state. It
    #           sets the next state. All transitions receive the current datum
    #           because a transition can make decisions based upon its value.
    #  _st_... State handler.
    #           A state handler returns the decode message once a message has
    #           been decoded. Otherwise, None is returned.
    #           A state handler calls transition methods when a transition to
    #           another state is required.
    #  _h_...   A state or transition helper. This handles logic that is common
    #           across commands or transitions.
    #+++++++++++++++ Helpers

    def _enter_state(self, state: str):
        '''Enter a state.

        This uses the state name to derive the names of the state transition
        and stepper methods to set the state reference and call its corresponding
        transition method.


        Parameter:
            state   The name of the state.
        '''
        if self.state is not None:
            self.out.verbose(f'Exiting state: {self.state}')
        self.out.verbose(f'Entering state: {state}')
        _tr = getattr(self, f'_tr_{state}')
        _st = getattr(self, f'_st_{state}')
        self._stepper = _st
        self.state = state
        _tr()

    #++++ Helpers
    def _h_is_command(self, datum):
        '''Return True if the datum is a command byte.'''
        return ((datum & CMD_MASK) == CMD_MASK)

    def _h_is_known_command(self, datum):
        '''Check the datum to see if it is a member of the current command
        table. This works for normal commands and sub-commands.'''
        return (datum in self._ct)

    def _h_prepare_for_command(self):
        self.data = []
        self.last_command = self.command
        self.command = None
        self.last_sub_command = self.sub_command
        self.sub_command = None
        self.parameters = []
        self.command_bytes = []
        self.param_bytes = []
        self.decoded = None
        self._ct = CT

    def _h_check_for_reply(self):
        _param = self.param_list[self.which_param]
        _t = type(_param)
        if _t is tuple:
            # A reply is expected to be atomic. Therefore all reamining
            # byte in the reply packet are captured for decode.
            self.decoder.prime(
                self.param_list[self.which_param])
            self.out.verbose(f'Decoding parameter {_next}.')
        elif _t is int:
            if _param == REPLY:
                _next = self.which_param + 1
                if _next > len(self.param_list):
                    self.out.protocol(
                        'No reply type following reply marker.')
                    self._enter_state('sync')
                else:
                    self._enter_state('expect_reply')
            else:
                self.out.protocol(
                    'Invalid reply marker in parameter list.')
        else:
            self.out.protocol('Unexpected type in parameter list.')
    #---- Helpers

    #++++
    def _st_expect_reply(self, datum):
        '''Decode the reply data from the controller.

        Reply packets are atomic responses meaning one command one reply.

        The reply data is appended to the parameter list.'''
        if self._h_is_command(datum):
            self.out.error(
                f'Datum 0x{datum:02X} is a command -- expected data.')
            self._enter_state('sync')
        else:
            _r = self.decoder.step(datum, self.remaining)
            if _r is not None:
                # Parameter has been decoded.
                self.out.verbose(f'Decoded reply.')
                self.decoded += ('Reply=' + _r)
                return self.decoded
        return None

    def _tr_expect_reply(self):
        if self.decoded is None:
            self.decoded = ''
        else:
            self.decoded += '\n'
        self.decoder.prime(self.param_list[self.which_param])
    #----

    #++++
    def _st_decode_parameters(self, datum):
        if self._h_is_command(datum):
            self.out.error(
                f'Datum 0x{datum:02X} is a command -- expected data.')
            self._enter_state('sync')
        else:
            _r = self.decoder.step(datum)
            if _r is not None:
                # Parameter has been decoded.
                self.out.verbose(f'Decoded parameter {self.which_param}.')
                self.decoded += (' ' + _r)
                # Advance to the next parameter.
                _next = self.which_param + 1
                if _next > len(self.param_list):
                    self.out.verbose('Parameters decoded.')
                    self._enter_state('expect_command')
                    return self.decoded
                else:
                    self.which_param = _next
                    self._h_check_for_reply()
        return None

    def _tr_decode_parameters(self):
        '''Prepare to parse a parameter. Prime the parameter decoder
        state machine.'''
        self.decoded = self.param_list[0]
        self.which_param = 1
        self._h_check_for_reply()
    #----

    #++++
    def _st_expect_sub_command(self, datum):
        '''A command has been received which has a sub-command list.'''
        if self.is_command:
            # Is it a known command for this state?
            if self._h_is_known_command(datum):
                _t = type(self._ct[datum])
                if _t is str:
                    _msg = self._format_decoded(self._ct[datum])
                    self._enter_state('expect_command')
                    return _msg
                elif _t is dict:
                    self.out.protocol(
                        f'Too many sub-levels for sub-command 0x{datum:02X}')
                    self._tr_sync(datum)
                    return None
                elif _t is tuple:
                    self.param_list = self._ct[datum]
                    self._enter_state('decode_parameters')
                    return None
                else:
                    # This is a problem with the protocol table -- not the
                    # incoming data.
                    self.out.protocol(
                        f'Unsupprted or unexpected type ({_t}) in command.')
            else:
                self.out.critical(
                    f'Datum 0x{datum:02X} is not a known command.')
                self._tr_sync()
        else:
            self._enter_state('sync')


    def _tr_expect_sub_command(self,datum):
        '''Setup for a sub-command.

        NOTE: The data type MUST be a dict.'''
        _t = type(self._ct[datum])
        if _t is dict:
            self.out.verbose('Entering: expect_sub_command')
            self._ct = self._ct[datum]
            self.sub_command = datum
        else:
            # This is a problem with the protocol table -- not the incoming
            # data.
            self.out.fatal(
                f'Command table at 0x{datum:02X} incorrect type {_t}.')
    #----

    #++++
    def _st_expect_command(self, datum):
        '''Expect the incoming byte to be a command byte. If it is not then
        generate a protocol error and return to scanning for a command byte.'''
        if self.is_command:
            # Is it a known command for this state?
            if self._h_is_known_command(datum):
                _t = type(self._ct[datum])
                if _t is str:
                    _msg = self._format_decoded(self._ct[datum])
                    self._enter_state('expect_command')
                    return _msg
                elif _t is dict:
                    self._enter_state('expect_sub_command')
                    return None
                elif _t is tuple:
                    self.param_list = self._ct[datum]
                    self._enter_state('decode_parameters')
                    return None
                else:
                    # This is a problem with the protocol table -- not the
                    # incoming data.
                    self.out.error(
                        f'Unsupprted or unexpected type ({_t}) in command.')
                    self._enter_state('sync')
            else:
                self.out.critical(
                    f'Datum 0x{datum:02X} is not a known command.')
                self._enter_state('sync')
        else:
            # Did not receive the expected command. This is either a problem
            # with the incoming stream or the protocol definition.
            self._tr_sync()

    def _tr_expect_command(self):
        self.out.verbose('Entering: expect_command')
        self._h_prepare_for_command()
    #----

    #++++
    def _st_sync(self, datum):
        '''Scan for a command byte to synchronize the parser with the input
        data.

        Once a command byte has been found normal command/reply processing
        begins.

        A command byte is the only byte which will have the most significant
        bit set.'''
        if self._h_is_command(datum):
            if self._h_is_known_command(datum):
                _t = type(self._ct[datum])
                if _t is str:
                    _msg = self._format_decoded(self._ct[datum])
                    self._enter_state('expect_command')
                    return _msg
                elif _t is dict:
                    self._enter_state('expect_sub_command')
                    return None
                elif _t is tuple:
                    self._enter_state('expect_parameter')
                    return None
                else:
                    # This is a problem with the protocol table -- not the
                    # incoming data.
                    self.out.protocol(
                        f'Unsupprted or unexpected type ({_t}) in command.')
                    self._enter_state('sync')

    def _tr_sync(self):
        self.out.verbose('Entering: sync')
        self._h_prepare_for_command()
    #----

    #---------------

    def step(self, datum: int, is_reply=False, remaining=0):
        """Step the state machine for the latest byte.

        Parameter:
            datum       The byte to step with.
            new_packet  When True the datum is the first byte of a new packet.
            is_reply    True when the byte is from a reply whether that be an
                        ACK/NAK or reply data.
            remaining   The number of bytes remaining in the current packet.

        Returns:
            The decoded command or reply.
            None if more data is requires for current command or reply.
        """
        self.last = self.datum
        self.datum = datum
        self.last_is_reply = self.is_reply
        self.is_reply = is_reply
        self.remaining = remaining
        # Step the machine.
        return self._stepper(datum)
