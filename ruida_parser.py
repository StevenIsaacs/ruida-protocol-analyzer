'''A state machine for parsing an input stream byte by byte.

This state machine must be driven by repeatedly calling "step" with a single
byte and whether the current byte is part of a reply or not.

NOTE: This does not verify the host/controller packet handshake.
'''
from rpa_emitter import RdaEmitter
import rpa_protocol as rdap

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
        self.decoder: str = ''
        self.rd_type:str = ''
        self.data: bytearray = bytearray([])
        self.value = None   # The actual type is not known until after decode.
        self.datum = None
        self._rd_decoder = None
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
            _v = (_v << 7) + data[_i]
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
    def rd_power(self, data: bytearray):
        self.value = self.to_int(data) / (0x4000 / 100)
        return self.formatted

    def rd_frequency(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    def rd_speed(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    def rd_time(self, data: bytearray):
        self.value = self.to_int(data) / 1000.0
        return self.formatted

    def rd_rapid(data):
        return rdap.ROT[data[0]]

    def rd_on_off(self, data: bytearray):
        if data[0]:
            self.value = ' ON'
        else:
            self.value = 'OFF'
        return self.formatted

    def rd_mt(self, data: bytearray):
        # This is a special case where the data is a reference to an entry
        # in the memory table (rdap.MT). This is used to setup the reply or
        # setting spec.
        self.value = (data[0] << 8) + data[1]
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
        self.format: str = spec[rdap.DFMT]
        self.decoder: str = spec[rdap.DDEC]
        self.rd_type:str = spec[rdap.DTYP]
        self.data: bytearray = bytearray([])
        self.value = None   # The actual type is not known until after decode.
        self.datum = None
        # An error with getattr indicates a problem with the type table -- not
        # the incoming data.
        self._rd_decoder = getattr(self, f'rd_{spec[1]}')
        if length is not None:
            self._length = length
        else:
            self._length = rdap.RD_TYPES[self.rd_type][rdap.RDT_BYTES]
        self._remaining = self._length

    def step(self, datum, remaining=None):
        '''Step the decoder.

        This is a single state state machine. The transition from this state
        produces the decoded and formatted string which can be part of the
        command or reply decode message.

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
        if datum & rdap.CMD_MASK:
            # A possible error in the input stream. Not enough data for the
            # indicated type. Instead, a command byte has been detected. Or,
            # the parameter is incorrectly defined in the tuple passed to
            # prime.
            self.out.protocol(
                f'datum={datum:02X}: Should not have bit 7 set.')
        if datum > (rdap.CMD_MASK - 1):
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
            return self._rd_decoder(self.data)

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
        reply_command   The command byte from a reply from the controller.
        reply_sub_command
                        The sub-command byte from a reply from the controller.
        command_bytes   The accumulated command bytes -- including sub-command
                        and parameters.
        host_bytes      The bytes from the host since the last parser output.
                        These are displayed when verbose is enabled.
        controller_byres The bytes from the controller since the last parser
                        output. These are displayed when verbose is enabled.
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
        self.reply_command = None
        self.reply_sub_command = None
        self.mt_address_msb = None
        self.mt_address_lsb = None
        self.param_list = None
        self.which_param = None
        self.parameters = []
        self.command_bytes = []
        self.param_bytes = []
        self.host_bytes: bytearray = bytearray([])
        self.controller_bytes: bytearray = bytearray([])
        self.decoder = RdDecoder(output)
        self.decoded = ''

        self._ct = rdap.CT   # The command table to use for parsing. This changes
                            # for sub-commands and for expected replies.
        self._stepper = None        # For commands.
        self._sub_stepper = None    # For parameters.
        self._enter_state('sync')   # Setup the sync state.

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

    def _forward_to_state(self, state: str):
        '''Enter the state and pass the current datum to the state for
        immediate parsing.

        This calls the state after entering it and returns the result of
        parsing the current datum.
        '''
        self._enter_state(state)
        return self._stepper(self.datum)

    #++++ Helpers
    def _h_is_command(self, datum):
        '''Return True if the datum is a command byte.'''
        return ((datum & rdap.CMD_MASK) == rdap.CMD_MASK)

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
        self._ct = rdap.CT

    def _h_check_for_reply(self):
        _param = self.param_list[self.which_param]
        _t = type(_param)
        if _t is tuple:
            # A reply is expected to be atomic. Therefore all remaining
            # bytes in the reply packet are captured for decode.
            self.decoder.prime(self.param_list[self.which_param])
            self.out.verbose(f'Decoding parameter {self.which_param}.')
        elif _t is int:
            # Action marker.
            if _param == rdap.REPLY:
                # Advance to the next parameter -- skip the rdap.REPLY marker.
                _next = self.which_param + 1
                if _next > len(self.param_list):
                    self.out.protocol(
                        'No reply type following reply marker.')
                    self._enter_state('sync')
                else:
                    self.which_param = _next
                    self._enter_state('expect_reply')
            else:
                self.out.protocol(
                    'Invalid action marker in parameter list.')
        else:
            self.out.protocol('Unexpected type in parameter list.')
    #---- Helpers

    #++++ MEMORY reply
    #++++
    def _st_mt_decode_reply(self, datum):
        if self.is_reply:
            _r = self.decoder.step(datum)
            if _r is not None:
                # Parameter has been decoded.
                self.out.verbose(
                    f'Decoded reply parameter {self.which_param}={_r}.')
                self.decoded += (':Reply:' + _r)
                # Advance to the next parameter.
                _next = self.which_param + 1
                if _next >= len(self.param_list):
                    self.out.verbose('Reply decoded.')
                    self._enter_state('expect_command')
                    return self.decoded
                else:
                    self.which_param = _next
                    self.decoder.prime(self.param_list[self.which_param])
        else:
            self.out.error('Packet from host when decoding reply data.')
            self._forward_to_state('sync')

    def _tr_mt_decode_reply(self):
        if self.mt_address_msb not in rdap.MT:
            # Setup a generic decode for an unknown address.
            _reply = rdap.UNKNOWN_ADDRESS
            self.out.verbose('Setting up for unknown memory address.')
        else:
            _msb = self.mt_address_msb
            _lsb = self.mt_address_lsb
            self.out.verbose(f'Memory reference: {_msb:02X}{_lsb:02X}')
            _reply = rdap.MT[_msb][_lsb]
        self.param_list = _reply
        self.decoded += ':' + _reply[0]
        self.which_param = 1
        if 'tbd' in _reply[1]:
            self.decoder.prime(_reply[1], length=self.remaining)
        else:
            self.decoder.prime(_reply[1])
    #----

    #++++
    def _st_mt_address_lsb(self, datum):
        if self.is_reply:
            if self.mt_address_msb not in rdap.MT:
                # Setup a generic decode for an unknown address.
                self.decoded += ':' + rdap.UNKNOWN_ADDRESS[0]
            else:
                if datum not in rdap.MT[self.mt_address_msb]:
                    self.out.protocol(
                        f'Unknown MT address LSB (0x{datum:02X}.)')
            self.mt_address_lsb = datum
            self.decoded += f'{datum:02X}'
            self._enter_state('mt_decode_reply')
        else:
            self.out.error(
                'Packet from host when expecting reply memory address.')
            self._forward_to_state('sync')

    def _tr_mt_address_lsb(self):
        self.mt_address_lsb = None

    #----
    #++++
    def _st_mt_address_msb(self, datum):
        if self.is_reply:
            if datum not in rdap.MT:
                self.out.protocol(
                    f'Unknown MT address MSB (0x{datum:02X}.)')
            self.mt_address_msb = datum
            self.decoded += f' Addr:{datum:02X}'
            self._enter_state('mt_address_lsb')
        else:
            self.out.error(
                'Packet from host when expecting reply memory address.')
            self._forward_to_state('sync')

    def _tr_mt_address_msb(self):
        self.mt_address_msb = None

    #----
    #++++
    def _st_mt_sub_command(self, datum):
        if self.is_reply:
            # A reply to a memory access always has a sub-command.
            if self._h_is_known_command(datum):
                if type(self._ct[datum]) is tuple:
                    self.reply_sub_command = datum
                    self.decoded = self._ct[datum][0]
                    self._enter_state('mt_address_msb')
                else:
                    self.out.protocol(
                        f'A reply data type should be a tuple.')
                    self._enter_state('sync')
            else:
                self.out.error(
                    f'Datum (0x{datum:02X} is not a known reply sub_command)')
                self._enter_state('sync')
        else:
            self.out.error('Packet from host when expecting reply sub_command.')
            self._forward_to_state('sync')

    def _tr_mt_sub_command(self):
        self._ct = rdap.RT[self.reply_command]
    #----

    #++++
    def _st_mt_command(self, datum):
        if self.is_reply:
            if self._h_is_command(datum):
                # A reply to a memory access always has a sub-command.
                if self._h_is_known_command(datum):
                    if type(self._ct[datum]) is dict:
                        self.reply_command = datum
                        self._enter_state('mt_sub_command')
                    else:
                        self.out.protocol(
                            f'A reply sub-command type should be a dictionary.')
                        self._enter_state('sync')
                else:
                    self.out.error(
                        f'Datum (0x{datum:02X} is not a known reply command)')
                    self._enter_state('sync')
            else:
                self.out.error(
                    f'Datum (0x{datum:02X} is not a reply command byte.)')
                self._enter_state('sync')
        else:
            self.out.error('Current packet is NOT a reply packet.')
            self._forward_to_state('sync')

    def _tr_mt_command(self):
        '''Setup to parse a reply to a memory read command.

        This state is triggered when the command parameter list contains
        a MEMORY spec and the memory command has been decoded.'''
        if self.command == 0xDA: # Reading from controller.
            self.reply_command = None
            self._ct = rdap.RT
        else:
            self.out.protocol(
                f'Memory reference with wrong command: 0x{self.command:02X}')
    #----

    #---- MEMORY reply states
    #++++
    def _st_expect_reply(self, datum):
        '''Expect and decode reply data from the controller.

        Reply packets are atomic responses meaning: one command, one reply.

        The reply data is appended to the parameter list.'''
        if not self.is_reply:
            self.out.error('Packet from host when expecting reply.')
            self._forward_to_state('sync')
        else:
            if self._h_is_command(datum):
                self.out.error(
                    f'Datum 0x{datum:02X} is a command -- expected data.')
                self._forward_to_state('sync')
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
        if self.is_reply:
            self.out.error('Reply packet when expecting parameters.')
            self._enter_state('sync')
        else:
            if self._h_is_command(datum):
                # This can either be a problem with the incoming data or
                # the definition in the protocol table.
                self.out.error(
                    f'Datum 0x{datum:02X} is a command -- expected data.')
                return self._forward_to_state('sync')
            else:
                _r = self.decoder.step(datum)
                if _r is not None:
                    # Parameter has been decoded.
                    self.out.verbose(
                        f'Decoded parameter {self.which_param}={_r}.')
                    self.decoded += (' ' + _r)
                    # A controller memory reference requires special handling.
                    if 'mt' in self.param_list[self.which_param]:
                        self._enter_state('mt_command')
                        return self.decoded
                    else:
                        # Advance to the next parameter.
                        _next = self.which_param + 1
                        if _next >= len(self.param_list):
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
        self.which_param = 1
        if 'mt' in self.param_list:
            self._enter_state('mt_command')
        else:
            self._h_check_for_reply()
    #----

    #++++
    def _st_expect_sub_command(self, datum):
        '''A command has been received which has a sub-command list.'''
        if self.is_reply:
            self.out.error('Reply packet when expecting sub_command.')
            self._enter_state('sync')
        else:
            if self._h_is_command(datum):
                self.out.error('Datum is command when should be sub_command.')
                self._forward_to_state('sync')
            else:
                # Is it a known command for this state?
                if self._h_is_known_command(datum):
                    self.sub_command = datum
                    _t = type(self._ct[datum])
                    if _t is str:
                        self.decoded = self._ct[datum]
                        self._enter_state('expect_command')
                        return self.decoded
                    elif _t is dict:
                        self.out.protocol(
                            f'Too many sub-levels for sub-command 0x{datum:02X}')
                        self._enter_state('sync')
                    elif _t is tuple:
                        self.param_list = self._ct[datum]
                        self.decoded = self.param_list[0]
                        self._enter_state('decode_parameters')
                    else:
                        # This is a problem with the protocol table -- not the
                        # incoming data.
                        self.out.protocol(
                            f'Unsupprted or unexpected type ({_t}) in command.')
                else:
                    self.out.critical(
                        f'Datum 0x{datum:02X} is not a known command.')
                    self._forward_to_state('sync')
        return None


    def _tr_expect_sub_command(self):
        '''Setup for a sub-command.

        NOTE: The data type MUST be a dict.'''
        _t = type(self._ct[self.command])
        if _t is dict:
            self._ct = self._ct[self.command]
        else:
            # This is a problem with the protocol table -- not the incoming
            # data.
            self.out.protocol(
                f'Command table at 0x{self.command:02X} incorrect type {_t}.')
            self._enter_state('sync')
    #----

    #++++
    def _st_expect_command(self, datum):
        '''Expect the incoming byte to be a command byte. If it is not then
        generate a protocol error and return to scanning for a command byte.'''
        if self.is_reply:
            self.out.error('Reply packet when expecting command.')
            self._enter_state('sync')
        else:
            if self._h_is_command(datum):
                # Is it a known command for this state?
                if self._h_is_known_command(datum):
                    self.command = datum
                    _t = type(self._ct[datum])
                    if _t is str:
                        self.decoded = self._ct[datum]
                        self._enter_state('expect_command')
                        return self.decoded
                    elif _t is dict:
                        self._enter_state('expect_sub_command')
                    elif _t is tuple:
                        self.param_list = self._ct[datum]
                        self.decoded = self.param_list[0]
                        self._enter_state('decode_parameters')
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
                self._enter_state('sync')

        return None

    def _tr_expect_command(self):
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
        if not self.is_reply:
            if self._h_is_command(datum):
                if self._h_is_known_command(datum):
                    self.command = datum
                    _t = type(self._ct[datum])
                    if _t is str:
                        self._format_decoded(self._ct[datum])
                        self._enter_state('expect_command')
                        return self.decoded
                    elif _t is dict:
                        self._enter_state('expect_sub_command')
                    elif _t is tuple:
                        self._enter_state('expect_parameter')
                    else:
                        # This is a problem with the protocol table -- not the
                        # incoming data.
                        self.out.protocol(
                            f'Unsupprted or unexpected type ({_t}) in command.')
        return None

    def _tr_sync(self):
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
        # Accumulate bytes.
        if self.is_reply:
            self.controller_bytes.append(datum)
        else:
            self.host_bytes.append(datum)
        # Step the machine.
        _r = self._stepper(datum)
        if _r is not None:
            self.out.verbose(f'-->:{self.host_bytes.hex()}')
            self.out.verbose(f'<--:{self.controller_bytes.hex()}')
            self.controller_bytes = bytearray([])
            self.host_bytes = bytearray([])
        return _r