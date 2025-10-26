'''
Ruida UDP communications protocol description tables and definitions.
'''
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
EOF = 0xD7      # Indicates the end of the Ruida file and the checksum will
                # follow.

# Internal and not part of the Ruida protocol.
EOD = 0xFF      # To signal end of incoming data to the decoder.

# This table defines the number of bit and corresponding number of incoming
# data bytes for each basic data type.
# Indexes into the RD_TYPES table.
# e.g. to get the number of bytes needed to decode the value:
#  n_bytes = RD_TYPES[DTYP][RDT_BYTES]
RDT_BITS = 0    # The number of bits in a decoded value.
                # These can be used to generate a mask for ignoring
                # bits in a larger variable. e.g. A
RDT_BYTES = 1   # The number of data bytes to decode.

RD_TYPES = {
#   Type            RDT_BITS    RDT_BYTES
    'bool_7':   [   7,          1], # Boolean value -- True or False.
    'int_7':    [   7,          1],
    'uint_7':   [   7,          1],
    'int_14':   [   14,         2],
    'uint_14':  [   14,         2],
    'int_35':   [   32,         5], # Top three bits are dropped.
    'uint_35':  [   32,         5], # Top three bits are dropped.
    'cstring':  [   7,          1], # The values are multiplied by the length
                                    # of the string.
    'string8':  [   50,         10],# An 8 character string.
    'on_off':   [   1,          1], # An ON or OFF switch (flag).
    'mt':       [   14,         2], # Special handling for a controller memory
                                    # access (read).
    'index':    [   14,         2], # An index into an unknown table.
    'chksum':   [   32,         5], # For file checksum calculation.
    'card_id':  [   32,         5], # Card ID reply.
    'tbd':      [   -1,        -1], # Type is unknown signal read to end of packet.
                                    # Use this for reverse engineering data.
}

# Card ID reply to model name lookup table.
CARD_IDS = {
    0x65106510: 'RDC6442S',
}

# For checking which origin mode.
ORIGIN_HOME = 0x02
LIGHT_ON = 0x01
# Rapid option table.
ROT = {
    0x00: 'RAPID_ORIGIN',
    0x01: 'RAPID_LIGHT_ORIGIN',
    0x02: 'RAPID_NONE',
    0x03: 'RAPID_LIGHT',
}

# Type format strings.
COORD_FMT = '{:.3f}mm'

# Parameter specifications. NOTE: These are command specific where possible.
# These need to be tuples because the type is used for determining next
# states in the state machine.
# Basic types:
#  Format, decoder, ruida type
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
#               DFMT                DDEC        DTYP
INT7 = (        '{}',               'int7',     'int_7')
UINT7 = (       '{}',               'uint7',    'uint_7')
HEX7 = (        '0x{:02X}',         'uint7',    'uint_7')
BOOL7 = (       '{}',               'bool',     'bool_7')
INT14 = (       '{}',               'int14',    'int_14')
UINT14 = (      '{}',               'uint14',   'uint_14')
HEX14 = (       '0x{:04X}',         'uint14',   'uint_14')
INT35 = (       '{}',               'int35',    'int_35')
UINT35 = (      '{}',               'uint35',   'uint_35')
HEX35 = (       '0x{:010X}',        'uint35',   'uint_35')
CSTRING = (     '{}',               'cstring',  'cstring')
# Parameter types:
FNAME = (       'File:{}',          'cstring',  'cstring') # File name.
STRING8 = (     'String: "{}"',     'string8',  'string8') # 8 char string from 2 uint35s.
FNUM = (        'FNum: {}',         'uint14',   'uint_14')
ENAME = (       'Elem:{}',          'cstring',  'cstring')
PART = (        'Part:{}',          'int7',     'int_7') # or layer.
LASER = (       'Laser:{}',         'int7',     'int_7') # For dual head lasers.
VALUE = (       '{}',               'int7',     'int_7')
RAPID = (       'Option:{}',        'rapid',    'int_7')
COLOR = (       'Color:#{:06X}',    'uint35',   'uint_35')
SETTING = (     'Set:{:08X}',       'uint35',   'uint_35')
ID = (          'ID:{}',            'uint14',   'uint_14')
DIRECTION = (   'Dir:{}',           'int7',     'int_7') # Table-ize the direction?
COORD = (       'POS=' + COORD_FMT, 'coord',    'int_35')
ABSCOORD = (    'ABS=' + COORD_FMT, 'coord',    'int_35')
XABSCOORD = (   'X=' + COORD_FMT,   'coord',    'int_35')
YABSCOORD = (   'Y=' + COORD_FMT,   'coord',    'int_35')
ZABSCOORD = (   'Z=' + COORD_FMT,   'coord',    'int_35')
AABSCOORD = (   'A=' + COORD_FMT,   'coord',    'int_35')
UABSCOORD = (   'U=' + COORD_FMT,   'coord',    'int_35')
RELCOORD = (    'Rel=' + COORD_FMT, 'coord',    'int_14')
XRELCOORD = (   'RelX=' + COORD_FMT,'coord',    'int_14')
YRELCOORD = (   'RelY=' + COORD_FMT,'coord',    'int_14')
POWER = (       'Power:{:.1f}%',    'power',    'uint_14')
SPEED = (       'Speed:{:.3f}mm/S', 'speed',    'int_35')
FREQUENCY = (   'Freq:{:.3f}KHz',   'frequency','int_35')
TIME = (        '{:.3f}mS',         'time',     'int_35')
SWITCH = (      'State: {}',        'on_off',   'uint_7')
CARD_ID = (     'CardID: {}',       'card_id',  'uint_35')

# A memory access triggers special processing using MT.
MEMORY = (      'Addr:{:04X}',      'mt',       'mt')
# An index into something -- unknown at this time.
INDEX = (      'Index:{:04X}',      'index',    'index')

FILE_SUM = (    'Sum:0x{0:010X} ({0})',
                                    'checksum', 'uint_35')

# For when the format and type of data is not known.
# Use this for data that needs to be reverse engineered
TBD = (         '\nTBD:{0:035b}b: 0x{0:08x}: {0}',
                                    'tbd',      'tbd')
# Use these once the size is known but needs further investigation.
TBDU7 = (       '\nTBDU7:{0:07b}b: 0x{0:02x}: {0}',
                                    'uint7',    'uint_7')
TBDU14 = (      '\nTBDU14:{0:014b}b: 0x{0:04x}: {0}',
                                    'uint14',   'uint_14')
TBDU35 = (      '\nTBDU35:{0:035b}b: 0x{0:08x}: {0}',
                                    'uint35',   'uint_35')
TBD7 = (        '\nTBD7:{0:07b}b: 0x{0:02x}: {0}',
                                    'int7',     'int_7')
TBD14 = (       '\nTBD14:{0:014b}b: 0x{0:04x}: {0}',
                                    'int14',    'int_14')
TBD35 = (       '\nTBD35:{0:035b}b: 0x{0:08x}: {0}',
                                    'int35',    'int_35')

# Reply types.
# Action markers are integers.
REPLY = -1  # An integer to indicate when a reply to a command is expected.
PAUSE = -2  # Can add this to a parameter table to act as a break during decode.
            # This is ignored when verbose is not enabled.
# Sometimes bytes appear that don't make sense and look like other
# commands. This is to skip those bytes so they don't confuse the
# parser. This works by disabling the check for commands for N bytes.
# The next entry in the tuple containing SKIP is the number of bytes
# to skip.
SKIP = -3




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
# Unknown address generic decode.
UNKNOWN_ADDRESS = ('TBD:Unknown address', TBD)  # Use when address discovered but data is
                                    # unknown.
# These are used when SETTING at an address.
UNKNOWN_MSB = 'MSB TBD'
UNKNOWN_LSB = 'LSB TBD'

# Commands which change checksum enable.
KEYPRESS = 0xA7
SETTING = 0xDA
FILE_COMMAND = 0xE5
CHK_DISABLES = (KEYPRESS, SETTING)

SETTING_READ = 0x00
SETTING_WRITE = 0x01

MT = {
    0x00: {
        0x04: ('IO Enable', TBDU35), # 0x004
        0x05: ('G0 Velocity', TBD), # 0x005
        0x0B: ('Eng Facula', TBD), # 0x00B
        0x0C: ('Home Velocity', TBD), # 0x00C
        0x0E: ('Eng Vert Velocity', TBD), # 0x00E
        0x10: ('System Control Mode', TBD), # 0x010
        0x11: ('Laser PWM Frequency 1', TBD), # 0x011
        0x12: ('Laser Min Power 1', TBD), # 0x012
        0x13: ('Laser Max Power 1', TBD), # 0x013
        0x16: ('Laser Attenuation', TBD), # 0x016
        0x17: ('Laser PWM Frequency 2', TBD), # 0x017
        0x18: ('Laser Min Power 2', TBD), # 0x018
        0x19: ('Laser Max Power 2', TBD), # 0x019
        0x1A: ('Laser Standby Frequency 1', TBD), # 0x01A
        0x1B: ('Laser Standby Pulse 1', TBD), # 0x01B
        0x1C: ('Laser Standby Frequency 2', TBD), # 0x01C
        0x1d: ('Laser Standby Pulse 2', TBD), # 0x01D
        0x1e: ('Auto Type Space', TBD35), # 0x01E
        0x20: ('Axis Control Para 1', TBD), # 0x020
        0x21: ('Axis Precision 1', TBDU35), # 0x021
        0x23: ('Axis Max Velocity 1', TBD), # 0x023
        0x24: ('Axis Start Velocity 1', TBD), # 0x024
        0x25: ('Axis Max Acc 1', TBD), # 0x025
        0x26: ('Bed Size X', XABSCOORD), # Deduced from LB
        0x27: ('Axis Btn Start Vel 1', TBD), # 0x027
        0x28: ('Axis Btn Acc 1', TBD), # 0x028
        0x29: ('Axis Estp Acc 1', TBD), # 0x029
        0x2A: ('Axis Home Offset 1', TBD), # 0x02A
        0x2B: ('Axis Backlash 1', TBD), # 0x02B
        0x30: ('Axis Control Para 2', TBD), # 0x030
        0x31: ('Axis Precision 2', TBDU35), # 0x031
        0x33: ('Axis Max Velocity 2', TBD), # 0x033
        0x34: ('Axis Start Velocity 2', TBD), # 0x034
        0x35: ('Axis Max Acc 2', TBD), # 0x035
        0x36: ('Bed Size Y', YABSCOORD), # Deduce from LB
        0x37: ('Axis Btn Start Vel 2', TBD), # 0x037
        0x38: ('Axis Btn Acc 2', TBD), # 0x038
        0x39: ('Axis Estp Acc 2', TBD), # 0x039
        0x3A: ('Axis Home Offset 2', TBD), # 0x03A
        0x3B: ('Axis Backlash 2', TBD), # 0x03B
        0x40: ('Axis Control Para 3', TBD), # 0x040
        0x41: ('Axis Precision 3', TBDU35), # 0x041
        0x43: ('Axis Max Velocity 3', TBD), # 0x043
        0x44: ('Axis Start Velocity 3', TBD), # 0x044
        0x45: ('Axis Max Acc 3', TBD), # 0x045
        0x46: ('Axis Range 3', TBD), # 0x046
        0x47: ('Axis Btn Start Vel 3', TBD), # 0x047
        0x48: ('Axis Btn Acc 3', TBD), # 0x048
        0x49: ('Axis Estp Acc 3', TBD), # 0x049
        0x4A: ('Axis Home Offset 3', TBD), # 0x04A
        0x4B: ('Axis Backlash 3', TBD), # 0x04B
        0x50: ('Axis Control Para 4', TBD), # 0x050
        0x51: ('Axis Precision 4', TBDU35), # 0x051
        0x53: ('Axis Max Velocity 4', TBD), # 0x053
        0x54: ('Axis Start Velocity 4', TBD), # 0x054
        0x55: ('Axis Max Acc 4', TBD), # 0x055
        0x56: ('Axis Range 4', TBD), # 0x056
        0x57: ('Axis Btn Start Vel 4', TBD), # 0x057
        0x58: ('Axis Btn Acc 4', TBD), # 0x058
        0x59: ('Axis Estp Acc 4', TBD), # 0x059
        0x5A: ('Axis Home Offset 4', TBD), # 0x05A
        0x5B: ('Axis Backlash 4', TBD), # 0x05B
        0x60: ('Machine Type (0x1155, 0xaa55)', TBD), # 0x060
        0x63: ('Laser Min Power 3', TBD), # 0x063
        0x64: ('Laser Max Power 3', TBD), # 0x064
        0x65: ('Laser PWM Frequency 3', TBD), # 0x065
        0x66: ('Laser Standby Frequency 3', TBD), # 0x066
        0x67: ('Laser Standby Pulse 3', TBD), # 0x067
        0x68: ('Laser Min Power 4', TBD), # 0x068
        0x69: ('Laser Max Power 4', TBD), # 0x069
        0x6a: ('Laser PWM Frequency 4', TBD), # 0x06A
        0x6B: ('Laser Standby Frequency 4', TBD), # 0x06B
        0x6C: ('Laser Standby Pulse 4', TBD), # 0x06C
    },
    0x02: {
        0x00: ('System Settings', TBD), # 0x100
        0x01: ('Turn Velocity', TBD), # 0x101
        0x02: ('Syn Acc', TBD), # 0x102
        0x03: ('G0 Delay', TBD), # 0x103
        0x07: ('Feed Delay After', TBD), # 0x107
        0x09: ('Turn Acc', TBD), # 0x109
        0x0A: ('G0 Acc', TBD), # 0x10A
        0x0B: ('Feed Delay Prior', TBD), # 0x10B
        0x0c: ('Manual Dis', TBD), # 0x10C
        0x0D: ('Shut Down Delay', TBD), # 0x10D
        0x0E: ('Focus Depth', TBD), # 0x10E
        0x0F: ('Go Scale Blank', TBD), # 0x10F
        0x1A: ('Acc Ratio', TBD), # 0x11A
        0x17: ('Array Feed Repay', TBD), # 0x117
        0x1B: ('Turn Ratio', TBD), # 0x11B
        0x1C: ('Acc G0 Ratio', TBD), # 0x11C
        0x1F: ('Rotate Pulse', TBD), # 0x11F
        0x21: ('Rotate D', TBD), # 0x121
        0x24: ('X Minimum Eng Velocity', TBD), # 0x124
        0x25: ('X Eng Acc', TBD), # 0x125
        0x26: ('User Para 1', TBDU35), # 0x126
        0x28: ('Z Home Velocity', TBD), # 0x128
        0x29: ('Z Work Velocity', TBD), # 0x129
        0x2A: ('Z G0 Velocity', TBD), # 0x12A
        0x2B: ('Z Pen Up Position', TBD), # 0x12B
        0x2C: ('U Home Velocity ', TBD), # 0x12C
        0x2D: ('U Work Velocity', TBD), # 0x12D
        0x31: ('Manual Fast Speed', TBD), # 0x131
        0x32: ('Manual Slow Speed', TBD), # 0x132
        0x34: ('Y Minimum Eng Velocity', TBD), # 0x134
        0x35: ('Y Eng Acc', TBD), # 0x135
        0x37: ('Eng Acc Ratio', TBD), # 0x137
    },
    0x03: {
        0x00: ('Card Language', TBD), # 0x180
        0x01: ('PC Lock 1', TBD), # 0x181
        0x02: ('PC Lock 2', TBD), # 0x182
        0x03: ('PC Lock 3', TBD), # 0x183
        0x04: ('PC Lock 4', TBD), # 0x184
        0x05: ('PC Lock 5', TBD), # 0x185
        0x06: ('PC Lock 6', TBD), # 0x186
        0x07: ('PC Lock 7', TBD), # 0x187
        0x11: ('Total Laser Work Time', TBD), # 0x211
    },
    0x04: {
        0x00: ('Machine Status (0b00110111 relevant bits).', TBDU35), # 0x200
        0x01: ('Total Open Time', TBD), # 0x201
        0x02: ('Total Work Time', TBD), # 0x202
        0x03: ('Total Work Number', TBD), # 0x203
        0x05: ('Total Doc Number', TBDU35), # 0x205
        0x07: ('Unknown', TBDU35), # LightBurn uses this
        0x08: ('Pre Work Time', TBD), # 0x208
        0x21: ('Axis Preferred Position 1', TBD), # 0x221
        0x23: ('Total Work Length 1', TBD), # 0x223
        0x31: ('Axis Preferred Position 2', TBD), # 0x231
        0x33: ('Total Work Length 2', TBD), # 0x233
        0x41: ('Axis Preferred Position 3', TBD), # 0x241
        0x43: ('Total Work Length 3', TBD), # 0x243
        0x51: ('Axis Preferred Position 4', TBD), # 0x251
        0x53: ('Total Work Length 4', TBD), # 0x253
    },
    0x05: {
        0x7E: ('Card ID', CARD_ID), # 0x2FE
        0x7F: ('Mainboard Version', TBD), # 0x2FF
    },
    0x06: {
        0x20: UNKNOWN_ADDRESS, #  Discovered running LB.
    },
    0x07: {
        0x10: ('Document Time', TBD), # 0x390
    },
    0x0B: {
        0x11: ('Card Lock', TBD), # 0x591
        0x12: ('Unknown', TBD35), # LightBurn uses this.
    },
}

# Index table. This is for replies which appear to index into something but
# exactly what is unknown.
IDXT = {
    0x00: {
        0x00: ('TBD',
               HEX14, HEX14, HEX14,
               HEX14, HEX14, HEX14,
               HEX14, HEX14, HEX14),
    },
}

# Reply table
RT = {
    # TBD Learn during debug.
    SETTING: {
        0x01: ('GET_SETTING', MEMORY),
        0x05: ('TBD', INDEX),
    },
}

# In the command table this indicates a reply is expected and the next entry
# is a reference to the reply in RT.
REPLY = -1

# Command table - port 50200
CT = {
    0x80: {
        0x00: ('AXIS_X_MOVE', XABSCOORD),
        # TODO: Identify the Y move.
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
    0xA7: KT, # KEYPRESS
    0xA8: ('CUT_ABS_XY', XABSCOORD, YABSCOORD),
    0xA9: ('CUT_REL_XY', XRELCOORD, YRELCOORD),
    0xAA: ('CUT_REL_X', XRELCOORD),
    0xAB: ('CUT_REL_Y', YRELCOORD),
    0xC0: ('IMD_POWER_2', POWER),
    0xC1: ('END_POWER_2', POWER),
    0xC2: ('IMD_POWER_3', POWER),
    0xC3: ('END_POWER_3', POWER), # ???
    0xC4: ('IMD_POWER_4', POWER), # ???
    0xC5: ('END_POWER_4', POWER),
    0xC6: {
        0x01: ('MIN_POWER_1', POWER),
        0x02: ('MAX_POWER_1', POWER),
        0x05: ('MIN_POWER_3', POWER),
        0x06: ('MAX_POWER_3', POWER),
        0x07: ('MIN_POWER_4', POWER),
        0x08: ('MAX_POWER_4', POWER),
        0x10: ('LASER_INTERVAL', TIME),
        0x11: ('ADD_DELAY', TIME),
        0x12: ('LASER_ON_DELAY', TIME),
        0x13: ('LASER_OFF_DELAY', TIME),
        0x15: ('LASER_ON_DELAY2', TIME),
        0x16: ('LASER_OFF_DELAY2', TIME),
        0x21: ('MIN_POWER_2', POWER), # Source: ruida-laser
        0x22: ('MAX_POWER_2', POWER), # Source: ruida-laser
        0x31: ('MIN_POWER_1_PART', PART, POWER),
        0x32: ('MAX_POWER_1_PART', PART, POWER),
        0x35: ('MIN_POWER_3_PART', PART, POWER),
        0x36: ('MAX_POWER_3_PART', PART, POWER),
        0x37: ('MIN_POWER_4_PART', PART, POWER),
        0x38: ('MAX_POWER_4_PART', PART, POWER),
        0x41: ('MIN_POWER_2_PART', PART, POWER),
        0x42: ('MAX_POWER_2_PART', PART, POWER),
        0x50: ('THROUGH_POWER_1', POWER),
        0x51: ('THROUGH_POWER_2', POWER),
        0x55: ('THROUGH_POWER_3', POWER),
        0x56: ('THROUGH_POWER_4', POWER),
        0x60: ('FREQUENCY_PART', LASER, PART, FREQUENCY),
    },
    0xC7: ('IMD_POWER_1', POWER),
    0xC8: ('END_POWER_1', POWER),
    0xC9: {
        0x02: ('SPEED_LASER_1', SPEED),
        0x03: ('SPEED_AXIS', SPEED),
        0x04: ('SPEED_LASER_1_PART', PART, SPEED),
        0x05: ('FORCE_ENG_SPEED', SPEED),
        0x06: ('SPEED_AXIS_MOVE', SPEED),
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
        0x03: ('EN_LASER_TUBE_START', SWITCH),
        0x04: ('X_SIGN_MAP', VALUE),
        0x05: ('LAYER_COLOR', COLOR),
        0x06: ('LAYER_COLOR_PART', PART, COLOR),
        0x10: ('EN_EX_IO', VALUE),
        0x22: ('MAX_LAYER_PART', PART),
        0x30: ('U_FILE_ID', ID),
        0x40: ('ZU_MAP', VALUE),
        0x41: ('LAYER_SELECT', PART, UINT7), # Source: ruida-laser
    },
    0xD0: {  # This was discovered with LightBurn
        0x29: ('Skipping 2 bytes:', SKIP, 2) # Follows with 0x89 0x89 --- wha???
    },
    0xD7: '\n ---- EOF ----',
    0xD8: {
        0x00: 'START_PROCESS',
        0x01: 'STOP_PROCESS',
        0x02: 'PAUSE_PROCESS',
        0x03: 'RESTORE_PROCESS',
        0x10: 'REF_POINT_2',
        0x11: 'REF_POINT_1',
        0x12: 'CURRENT_POSITION',   # All moves relative to current position.
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
        0x00: ('RAPID_MOVE_X', RAPID, XABSCOORD),
        0x01: ('RAPID_MOVE_Y', RAPID, YABSCOORD),
        0x02: ('RAPID_MOVE_Z', RAPID, ZABSCOORD),
        0x03: ('RAPID_MOVE_U', RAPID, UABSCOORD),
        0x0F: ('RAPID_FEED_AXIS_MOVE', RAPID),
        0x10: ('RAPID_MOVE_XY', RAPID, XABSCOORD, YABSCOORD),
        0x30: ('RAPID_MOVE_XYU', RAPID, XABSCOORD, YABSCOORD, UABSCOORD),
    },
    0xDA: { # SETTING
        0x00: ('GET_SETTING', MEMORY),  # SETTING_READ
        0x01: ('SET_SETTING', MEMORY, TBDU35, TBDU35), # SETTING_WRITE
        0x05: ('GET_UNKNOWN', INDEX, TBD),
    },
    0xE5: { # FILE
        0x00: ('DOCUMENT_FILE_UPLOAD', FNUM, UINT35, UINT35),
        0x02: 'DOCUMENT_FILE_END',
        0x05: ('SET_FILE_SUM', FILE_SUM),
    },
    0xE6: {
        0x01: 'SET_ABSOLUTE',
    },
    0xE7: {
        0x00: 'BLOCK_END',
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
        0x0A: ('FEED_INFO', TBD35), # TODO: A 35 bit value? What for?
        0x0B: ('ARRAY_EN_MIRROR_CUT', UINT7),
        0x13: ('ARRAY_TOP_LEFT', XABSCOORD, YABSCOORD),
        0x17: ('ARRAY_BOTTOM_RIGHT', XABSCOORD, YABSCOORD),
        0x23: ('ARRAY_ADD',XABSCOORD, YABSCOORD),
        0x24: ('ARRAY_MIRROR', UINT7),
        0x32: ('UNKNOWN E732', TBDU35, TBDU35), # RDWorks uses this.
        0x35: ('BLOCK_X_SIZE', XABSCOORD, YABSCOORD),
        # ? 0x35: ('BY_TEST: {:08X}', UINT35), # expect 0x11227766?
        0x36: ('SET_FILE_EMPTY', UINT7),
        0x37: ('ARRAY_EVEN_DISTANCE', TBD35, TBD35),
        0x38: ('SET_FEED_AUTO_PAUSE', SWITCH),
        0x3A: 'UNION_BLOCK_PROPERTY',
        0x50: ('DOCUMENT_TOP_LEFT', XABSCOORD, YABSCOORD),
        0x51: ('DOCUMENT_BOTTOM_RIGHT', XABSCOORD, YABSCOORD),
        0x52: ('PART_TOP_LEFT', PART, XABSCOORD, YABSCOORD),
        0x53: ('PART_BOTTOM_RIGHT', PART, XABSCOORD, YABSCOORD),
        0x54: ('PEN_OFFSET: Axis=', UINT7, ABSCOORD),
        0x55: ('LAYER_OFFSET: Axis=', UINT7, ABSCOORD),
        0x60: ('SET_CURRENT_ELEMENT_INDEX', UINT7),
        0x61: ('PART_EX_TOP_LEFT', PART, XABSCOORD, YABSCOORD),
        0x62: ('PART_EX_BOTTOM_RIGHT', PART, XABSCOORD, YABSCOORD),
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
       0x02: ('ENABLE_BLOCK_CUTTING', SWITCH),
       0x03: ('DISPLAY_OFFSET', XABSCOORD, YABSCOORD),
       0x04: ('FEED_AUTO_CALC', UINT7),
    },
    0xF2: {
        0x00: ('ELEMENT_INDEX', UINT7),
        0x01: ('ELEMENT_NAME_INDEX', UINT7),
        0x02: ('ELEMENT_NAME', STRING8),
        0x03: ('ELEMENT_ARRAY_TOP_LEFT', XABSCOORD, YABSCOORD),
        0x04: ('ELEMENT_ARRAY_BOTTOM_RIGHT', XABSCOORD, YABSCOORD),
        0x05: ('ELEMENT_ARRAY',
                INT14, INT14, INT14, INT14, INT14, INT14, INT14),
        0x06: ('ELEMENT_ARRAY_ADD', XABSCOORD, YABSCOORD),
        0x07: ('ELEMENT_ARRAY_MIRROR', UINT7),
    },
}
