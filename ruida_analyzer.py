'''
Protocol analyzer for Ruida data transported via a UDP connection. This
dissects Ruida commands and replies in UDP data packets to produce a human
readable log of events and their relative timing.

This version is intended to be used from the command line to process UDP
session data previously captured using tshark (Wireshark CLI). See decode
for more information.
'''
import sys
import argparse
from datetime import datetime
import typing
import os

import ruida_parser as rp
from rda_emitter import RdaEmitter

class UdpDumpReader():
    '''Parse lines from the dump file or a live stream.

    The dump file should be captured using the command:
    tshark -Y '(ip.addr == <ruida ip> && udp.payload)' -T fields \
        -e frame.time -e udp.port -e udp.length -e data.data>

    Parameters:
        input       The input stream to read capture data from. This stream
                    must support the readline method.
        output      Where to write messages to.

    Attributes:
        line        The line read from the input stream. This includes the payload.
                    None indicates the end of the file.
        line_number The number of lines read or the number for the last line
                    read.
        ts          The timestamp of when the packet was captured.
        to_port     The destination port number.
        from_port   The source port number. The receiver uses this port as the
                    destination port for replies.
        length      The length of the payload (not including the checksum)
        data        The binary swizzled data payload (not including checksum).
    '''
    def __init__(self, input, output: RdaEmitter):
        self.input = input
        self.out = output
        self.line = None
        self.line_number = 0
        self.ts = None
        self.last_ts = None
        self.to_port = None
        self.from_port = None
        self.length = 0
        self.data = []

    def next_packet(self):
        '''Read the next packet from the dump file.

        Reads and extracts the fields from the next line from the dump file.
        The attributes are set based upon the line contents.

        Returns:
            The number of bytes in the data payload.
            If the end of the file has been reached then None is returned.
        '''
        try:
            self.line = self.input.readline()
            self.out.raw(self.line)
            self.line_number += 1
            self.out.set_id(self.line_number)
            _fields:list[str] = self.line.strip().split('\t')

            _ts = _fields[0].split(' ')
            _y = _ts[2]
            _m = _ts[0]
            _d = _ts[1].split(',')[0]
            _t = _ts[3]
            _ds = f'{_y}-{_m}-{_d} {_t:.15}'
            _df = '%Y-%b-%d %H:%M:%S.%f'
            _dto = datetime.strptime(_ds, _df)
            self.ts = _dto.timestamp()
            if self.last_ts == None:
                self.last_ts = self.ts
            self.delta_time = (self.ts - self.last_ts)
            self.last_ts = self.ts
            self.out.reader(f'Interval:{self.delta_time:.6f}uS')

            _ports = _fields[1].split(',')
            self.to_port = int(_ports[0])
            self.from_port = int(_ports[1])
            self.length = int(_fields[2]) - 8 # Subtract length of UDP header.
            self.data = bytes.fromhex(_fields[3])
            _n = len(self.data)
            if _n != self.length:
                self.out.fatal(
                    f'Length MISMATCH: UDP=({self.length}) payload=({_n})')
        except EOFError:
            self.line = None
            return None
        return self.length

    def reset(self):
        '''Reset the file pointer to the beginning of the dump file.'''
        try:
            self.out.verbose('Resetting input stream.')
            self.input.seek(0)
            self.line_number = 0
        except AttributeError:
            self.out.info("An input stream from a process doesn't have a seek method.")

class RdPacket():
    '''Unswizzled packet data.

    This simulates a byte stream. The decoder only needs to call read_byte.
    Packets and replies are handled transparently. However, the caller needs to
    check to see if available data is a command or a reply.

    NOTE: This is designed such that a using this class only requires calling
    next_byte. The rest is handled internally.

        Attributes:
            reader      The packet reader of the source of incoming data.
            out         The stream messages are written to.
            magic       The magic number being used to unswizzle data.
            new_packet  When True the first byte of a new packet has not been
                        read.
            reply       When True the packet is a reply.
            handshake   When True the reply is a handshake reply.
            swizzled    When True the packet was swizzled.
            data        The unswizzled packet payload data (not including
                        checksum).
            length      The length of the packet payload not including
                        checksum.
            total_host_packets
                        The total number of packets sent from the host to the
                        controller.
            total_host_bytes
                        The total number of bytes sent from the host to the
                        controller.
            total_reply_packets
                        The total number of reply packets from the controller.
            total_reply_bytes
                        The total number of bytes received from the controller.

        Properties:
            remaining   The number of bytes not read from the data.

    '''

    MAGIC_LUT = {  # RAW ACK or NAK to swizzle magic number table.
        # Raw Magic
        0xC6: 0x88,
        # TODO: Add more magic numbers.
    }

    def __init__(self, reader: UdpDumpReader, output: RdaEmitter):
        '''
        Parameters:
        reader      The packet reader to get input data from.
        output      The stream to write messages to.

        '''
        self.reader = reader
        self.out = output
        self.magic = None
        self.new_packet = False
        self.reply = False
        self.handshake = False
        self.data = None
        self.length = 0
        self.total_host_packets = 0
        self.total_host_bytes = 0
        self.total_reply_packets = 0
        self.total_reply_bytes = 0
        self._take = 0      # Take index for reading byte by byte.

    @property
    def remaining(self):
        '''Return the number of unread bytes in the packet data buffer.'''
        return self.length - self._take

    def un_swizzle_byte(self, b):
        '''Un-swizzle a byte using the magic number.

        '''
        b = (b - 1) & 0xFF
        b ^= self.magic
        b ^= (b >> 7) & 0xFF
        b ^= (b << 7) & 0xFF
        b ^= (b >> 7) & 0xFF
        return b

    def _next_packet(self):
        '''Read and un-swizzle the next packet.

        Returns the number of bytes in the payload data. None indicates
        the end of file.'''
        _n = self.reader.next_packet()
        if _n is None:
            # The end of the file has been reached.
            return None

        self.new_packet = True  # next_byte resets this.
        self.swizzled = self.reader.to_port in [40200, 50200]
        self.reply = self.reader.from_port in [40200, 40207]

        if self.reply:
            # Replies don't carry a checksum.
            _data = self.reader.data
            self.chk_ok = True
        else:
            # Verify checksum and return only the data portion of the payload.
            # NOTE: The checksum is not swizzled.
            _chk = int.from_bytes(self.reader.data[0:2])
            _data = self.reader.data[2:]
            _chk_sum = (sum(_data) & 0xFFFF)
            self.chk_ok = (_chk == _chk_sum)
            if not self.chk_ok:
                self.out.error(
                    f'Checksum mismatch. pkt:0x{_chk:04X} sum:0x{_chk_sum:04X}')

        if self.swizzled:
            self.data = bytearray(b'')
            for b in _data:
                self.data.append(self.un_swizzle_byte(b))
        else:
            self.data = _data
        self.out.raw(self.data.hex())
        self.length = len(self.data) # Does not include any checksum.
        # Update stats.
        if self.reply:
            self.handshake = (self.length == 1)
            self.total_reply_packets += 1
            self.total_reply_bytes += self.length
        else:
            self.total_host_packets += 1
            self.total_host_bytes += self.length

        self._take = 0
        return self.length

    def set_magic(self, magic=None):
        '''Scan the input file to either an ACK or NAK from the controller
        and use it's swizzled value to determine the magic number.

        This resets the file pointer to the beginning of the file.

        Parameters:
            magic   The magic number to use. If this is None then the input
                    file is scanned to discover the magic number.
        Raises:
            LookupError
                    If the magic number cannot be discovered within a few
                    packets.
        '''
        if magic is None:
            self.reader.reset()
            _tries = 4  # Should discover magic within a few packets.
            while True:
                self.reader.next_packet()
                if self.reader.from_port == 40200 and self.reader.length == 1:
                    _r = self.reader.data[0]
                    if _r in self.MAGIC_LUT:
                        self.magic = self.MAGIC_LUT[_r]
                        self.out.verbose(
                            f'Detected magic: 0x{self.magic:02X}')
                        break
                    else:
                        if _tries:
                            _tries -= 1
                            continue
                        else:
                            self.out.shutdown('Magic number not discovered.')
            self.reader.reset()
        else:
            self.magic = magic
            self.out.verbose(f'Using magic: 0x{self.magic:02X}')

    def next_byte(self) -> int:
        '''Return the next data byte from the input file.

        If the file has been consumed then return None.

        This is the only method needed to retrieve packet data for analysis.
        The calling method needs to check reply and ack_nak status to determine
        if the packet is a reply and if the reply is an ACK or NAK.

        Returns:
            The byte as an integer. NOTE: In Ruida packets all bytes are 7 bit
            with the exception of a command byte which has the most significant
            bit set.
        '''
        if self.magic is None:
            self.set_magic()
        self.new_packet = False
        _b = None
        while _b is None:
            if self.remaining > 0:
                _b = self.data[self._take]
                self._take += 1
            else:
                # The end of the input file has been reached when _next_packet
                # returns None.
                if self._next_packet() is None:
                    break
                else:
                    self.new_packet = True
        return _b

class RuidaProtocolAnalyzer():
    '''Parse a tshark dump file.

    Each packet of the dump file is unswizzled into a buffer and then decoded
    using the RuidaParser. Technically, Ruida commands can span packet boundaries
    but should not. The ruida controller responses are checked.

    Info and swizzling from: https://edutechwiki.unige.ch/en/Ruida#Protocol

    Magic numbers:
        644XG - Magic = 0x88
        644XS - Magic = 0x88
        320 - Magic = 0x88
        633X - Magic = 0x88
        634XG - Magic = 0x11
        654XG - Magic = 0x88
        RDL9635 - Magic = 0x38

    NOTE: It is possible for the host to send several packets to the controller
    without waiting for an ACK. This means replies can be delayed and interleaved
    with command packets. Because sequence numbers are not part of the protocol,
    missing packets cannot be detected. Because of this the analyzer considers
    not waiting for an ACK to be a source of potentially difficult to diagnose
    timing errors. Therefore, sending packets without waiting for a reply to a
    previous packet is considered an error.

    Parameters:
        args        The command line arguments. The important fields are:
                    magic       The magic number to use for swizzling.
                    verbose     Emit a lot more information as the input
                                stream is being decoded.
                    raw         Emit the raw -- unprocessed data.
        input       The input stream to read from. This must be text mode and
                    have a "readline" method.
        output      The output stream to write decoded data to. This must
                    be text mode and have a "write" method.

    Attributes:
        args        The arguments parameter.
        magic       The swizzle magic number.
        new_packet  When True a new packet is being processed.
        expect_ack  When True a host packet has been received. The next packet
                    must be a reply with an ACK. This is reset when the ACK has
                    been received.
        MAGIC_LUT   A lookup table to convert a RAW ACK or NAK to a magic number
                    for un-swizzling data.
    '''
    def __init__(self, args, input, output: RdaEmitter):
        self.args = args
        self.out = output
        self.new_packet = False
        self.expect_ack = False
        self._reader = UdpDumpReader(input, output)
        self._pkt = RdPacket(self._reader, output)
        self._pkt.set_magic(args.magic)
        self._parser = rp.RdParser(output)
        self._line_number = 0

    def check_handshake(self):
        '''Verify the ack/nak handshake.

        Basically, all packets from the host require an ack/nak from the
        controller and the host must wait for the ack/nak before sending
        another packet.

        A handshake packet is a packet having a length equal to 1.

        '''
        # Getting the byte required reading another packet.
        self._line_number = self._reader.line_number
        _msg = ''
        if self._pkt.reply:
            _dir = '<--'
            if self._pkt.handshake:
                _b = self._pkt.data[0]
                if _b == rp.ACK:
                    _msg += 'ACK'
                    self.expect_ack = False
                elif _b == rp.NAK:
                    _msg += 'NAK'
                elif _b == rp.ERR:
                    _msg += 'ERR'
                elif _b == rp.ENQ:
                    _msg += 'ENQ'
                else:
                    self.out.error(f'Unexpected reply byte 0x{_b:02X}')
                if self.expect_ack:
                    self.out.error(
                        f'Received 0x{_b:02X} when ACK was expected.')
                self.expect_ack = False
            else:
                self.out.reader('Reply data')
        else:
            _dir = '-->'
            self.expect_ack = True
            _msg = 'Expecting ACK'
        self.out.reader(f'{_dir}:{_msg}')

    def decode(self):
        '''Step through each byte of the input stream and decode each packet.

        '''
        while True:
            _b = self._pkt.next_byte()
            if _b is None:
                # The end of the input stream.
                break
            if self._pkt.new_packet:
                self.check_handshake()
            # Handshake bytes are not passed to the state machine.
            if not self._pkt.handshake:
                _decoded = self._parser.step(
                    _b, is_reply=self._pkt.reply, remaining=self._pkt.remaining)
                if _decoded is not None:
                    self.out.parser(_decoded)
