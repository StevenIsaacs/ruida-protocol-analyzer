"""
Script parser for .rds (Ruida Discovery Script) files.

Parses human-readable script files into structured command tuples,
supporting comment stripping and command/expected-reply directives.
"""

import re

from protocols.ruida.ruida_protocol import CT, MT, IDXT, ACK
from rpalib.ruida_transcoder import RdEncoder


# Type group names recognized in .rds script files.
# These are purely informational labels in the script format and map to
# the underlying hex-prefix command categories in CT.
TYPE_NAMES = frozenset({
    'CORE', 'MOVE', 'LASER', 'CONFIG', 'QUERY', 'ENGRAVE', 'CUT',
    'FILE', 'SYSTEM',
})

# Decoder function name (DDEC from param spec) → RdEncoder method name.
# When a param spec has decoder 'coord', the encoder method is 'encode_coord'.
_ENCODER_MAP: dict[str, str | None] = {
    'int7':       'encode_int7',
    'uint7':      'encode_uint7',
    'int14':      'encode_int14',
    'uint14':     'encode_uint14',
    'int35':      'encode_int35',
    'uint35':     'encode_uint35',
    'coord':      'encode_coord',
    'cstring':    'encode_cstring',
    'string8':    'encode_string8',
    'power':      'encode_power',
    'frequency':  'encode_frequency',
    'speed':      'encode_speed',
    'time':       'encode_time',
    'bool':       'encode_bool',
    'on_off':     'encode_bool',
    'rapid':      'encode_uint7',
    'mt':         'encode_mt',
    'index':      'encode_index',
    'checksum':   'encode_uint35',
    'card_id':    'encode_uint35',
    'tbd':        None,
}

# Fallback: ruida type name (DTYP from param spec) → encoder method.
_RDTYPE_ENCODER_MAP: dict[str, str | None] = {
    'int_7':    'encode_int7',
    'uint_7':   'encode_uint7',
    'int_14':   'encode_int14',
    'uint_14':  'encode_uint14',
    'int_35':   'encode_int35',
    'uint_35':  'encode_uint35',
    'cstring':  'encode_cstring',
    'string8':  'encode_string8',
    'on_off':   'encode_bool',
    'bool_7':   'encode_bool',
    'mt':       'encode_mt',
    'index':    'encode_index',
}


def _strip_inline_comment(line: str) -> str:
    """Remove inline # comments, respecting quoted strings and \\# escapes."""
    in_quote = False
    quote_char = None
    i = 0
    while i < len(line):
        ch = line[i]
        # Track escaped hash: \# — skip it as a literal hash
        if ch == '\\' and i + 1 < len(line) and line[i + 1] == '#' and not in_quote:
            i += 2  # skip both \ and #
            continue
        if ch in ('"', "'") and not in_quote:
            in_quote = True
            quote_char = ch
        elif ch == quote_char and in_quote:
            in_quote = False
            quote_char = None
        elif ch == '#' and not in_quote:
            return line[:i].rstrip()
        i += 1
    return line


def _find_block_comment(text: str) -> tuple[int, int]:
    """Find the start and end of a triple-quote block comment."""
    start = text.find('"""')
    if start == -1:
        return -1, -1
    end = text.find('"""', start + 3)
    if end == -1:
        raise ValueError('Unterminated """ block comment')
    return start, end + 3


def _remove_block_comments(text: str) -> str:
    """Remove all triple-quote block comments from text."""
    result = text
    while True:
        start, end = _find_block_comment(result)
        if start == -1:
            break
        result = result[:start] + result[end:]
    return result


class ScriptParser:
    """Parser for .rds (Ruida Discovery Script) files.

    Reads human-readable script files and produces structured command dicts
    ready for interpretation / binary encoding.

    Uses the CT command table from protocols.ruida.ruida_protocol to resolve
    command mnemonics and parameter specifications.
    """

    def __init__(self) -> None:
        self._mnemonic_map: dict[str, tuple] = self._build_mnemonic_map()
        self._mt_map: dict[str, tuple[int, int]] = self._build_mt_map()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, path: str) -> list[dict]:
        """Open and parse an .rds script file.

        Returns a list of command dicts.
        """
        with open(path, 'r') as f:
            content = f.read()
        lines = content.splitlines()
        return self.parse_lines(lines)

    def parse_lines(self, lines: list[str]) -> list[dict]:
        """Parse a list of script lines (useful for testing).

        Strips block comments, then processes each non-empty line.
        """
        text = '\n'.join(lines)
        text = _remove_block_comments(text)
        stripped_lines = text.splitlines()

        commands: list[dict] = []
        for line_num, raw_line in enumerate(stripped_lines, start=1):
            trimmed = raw_line.strip()
            if not trimmed:
                continue
            cmd = self._parse_line(trimmed, line_num)
            if cmd is not None:
                commands.append(cmd)
        return commands

    # ------------------------------------------------------------------
    # Line parsing
    # ------------------------------------------------------------------

    def _parse_line(self, line: str, line_num: int) -> dict | None:
        """Parse a single non-empty script line into a command dict.

        Returns None if the line is empty after comment stripping.
        """
        # Strip inline comments
        line = _strip_inline_comment(line)
        line = line.strip()
        if not line:
            return None

        tokens = line.split()
        if not tokens:
            return None

        raw = line
        idx = 0

        # --- NEW_PACKET directive (per-packet boundary marker) ---
        if tokens[0] == 'NEW_PACKET':
            return {
                'type': 'NEW_PACKET',
                'mnemonic': 'NEW_PACKET',
                'params': [],
                'expected': None,
                'line_num': line_num,
                'raw': raw,
            }

        # --- Type / mnemonic detection ---
        type_name = None
        mnemonic = None
        first = tokens[0]

        # Try: first token is a TYPE group name
        resolved_type = self._resolve_type(first)
        if resolved_type is not None:
            type_name = resolved_type
            idx = 1
            # Skip optional 'CMD' decorative keyword after type
            # e.g. "CORE CMD NOP" — 'CMD' is not the mnemonic
            if len(tokens) > idx and tokens[idx].upper() == 'CMD':
                idx += 1
            if idx >= len(tokens):
                raise ValueError(
                    f'{line_num}: Type "{type_name}" specified but no mnemonic follows.'
                )
            mnemonic = tokens[idx]
            idx += 1
        else:
            # No type prefix: first token IS the mnemonic
            mnemonic = first
            idx = 1

        # Validate mnemonic exists in the command table
        if mnemonic not in self._mnemonic_map:
            raise ValueError(
                f'{line_num}: Unknown command mnemonic "{mnemonic}". '
                f'Not found in any CT table entry.'
            )

        # --- Parameters and expected reply ---
        params: list[str] = []
        expected: str | None = None

        remaining = tokens[idx:]
        eq_idx = None
        for i, tok in enumerate(remaining):
            if tok == '=':
                eq_idx = i
                break

        if eq_idx is not None:
            params = remaining[:eq_idx]
            expected_tokens = remaining[eq_idx + 1:]
            expected = ' '.join(expected_tokens) if expected_tokens else ''
        else:
            params = remaining

        return {
            'type': type_name or '',
            'mnemonic': mnemonic,
            'params': params,
            'expected': expected,
            'line_num': line_num,
            'raw': raw,
        }

    # ------------------------------------------------------------------
    # Type resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_type(token: str) -> str | None:
        """Check if a token is a recognized type group name."""
        if token in TYPE_NAMES:
            return token
        # Try stripping _CMD suffix (e.g., CORE_CMD → CORE)
        if token.endswith('_CMD') and token[:-4] in TYPE_NAMES:
            return token[:-4]
        return None

    # ------------------------------------------------------------------
    # Table indexing
    # ------------------------------------------------------------------

    @staticmethod
    def _build_mnemonic_map() -> dict[str, tuple]:
        """Build {mnemonic: (prefix_byte, opcode_or_None, cmd_entry_or_None)}.

        Iterates over the full CT table structure, handling:
        - Direct command tuples  (0x88: ('MOVE_ABS_XY', ...))
        - Sub-command dicts      (0x80: {0x00: ('AXIS_X_MOVE', ...)})
        - Nested sub-command     (0xCA: {0x01: {0x00: 'LAYER_END', ...}})
        - Plain string commands  (0xCE: 'ENQ')
        - External refs          (0xA7: KT) — skipped
        """
        _map: dict[str, tuple] = {}

        for prefix_byte, entry in CT.items():
            # --- Direct command tuple ---
            # e.g. 0x88: ('MOVE_ABS_XY', XABSCOORD, YABSCOORD)
            if isinstance(entry, tuple):
                if len(entry) >= 1 and isinstance(entry[0], str):
                    _map[entry[0]] = (prefix_byte, None, entry)
                continue

            # --- Plain string command ---
            # e.g. 0xCE: 'ENQ'
            if isinstance(entry, str):
                name = entry.strip()
                # Skip non-command strings like '\n ---- EOF ----'
                if name and not name.startswith('\n') and len(name) < 40:
                    _map[name] = (prefix_byte, None, None)
                continue

            # --- Dictionary: sub-commands or external reference ---
            if isinstance(entry, dict):
                # Detect external refs (KT, IDXT, RT) by checking for
                # non-standard structures (list values, etc.)
                has_list_val = any(isinstance(v, list) for v in entry.values())
                if has_list_val:
                    continue  # Skip external refs like KT

                for opcode, sub_entry in entry.items():
                    if isinstance(sub_entry, str):
                        _map[sub_entry] = (prefix_byte, opcode, None)
                    elif isinstance(sub_entry, tuple):
                        if len(sub_entry) >= 1 and isinstance(sub_entry[0], str):
                            _map[sub_entry[0]] = (prefix_byte, opcode, sub_entry)
                    elif isinstance(sub_entry, dict):
                        # Nested sub-commands (e.g., 0xCA: {0x01: {0x00: ...}})
                        for opcode2, nested in sub_entry.items():
                            if isinstance(nested, str):
                                _map[nested] = (prefix_byte, opcode, opcode2, None)
                            elif isinstance(nested, tuple):
                                if len(nested) >= 1 and isinstance(nested[0], str):
                                    _map[nested[0]] = (prefix_byte, opcode, opcode2, nested)

        return _map

    @staticmethod
    def _build_mt_map() -> dict[str, tuple[int, int]]:
        """Build {mnemonic: (msb, lsb)} mapping from MT table.

        Enables resolution of memory-address mnemonics (e.g. MEM_IO_ENABLE)
        to their MSB/LSB byte pair for encoding.
        """
        _map: dict[str, tuple[int, int]] = {}
        for msb, entries in MT.items():
            for lsb, entry in entries.items():
                if isinstance(entry, tuple) and len(entry) >= 1:
                    name = entry[0]
                    if isinstance(name, str):
                        _map[name] = (msb, lsb)
        return _map

    # ------------------------------------------------------------------
    # Utility (exposed for testing / debugging)
    # ------------------------------------------------------------------

    @property
    def mnemonic_map(self) -> dict[str, tuple]:
        """The resolved mnemonic → encoding-info map."""
        return self._mnemonic_map

    @property
    def mt_map(self) -> dict[str, tuple[int, int]]:
        """The resolved MT-mnemonic → (msb, lsb) map."""
        return self._mt_map


# ═══════════════════════════════════════════════════════════════════════
# ScriptInterpreter
# ═══════════════════════════════════════════════════════════════════════

class ScriptInterpreter:
    """Interprets parsed script commands and generates tshark-compatible output.

    Walks a list of parsed command dicts (from ScriptParser), looks up
    each command in the CT command table, encodes parameters to binary,
    and writes tshark-format lines to the output stream.
    """

    # Default UDP endpoint (controller port range)
    DEFAULT_SRC_PORT = 40200
    DEFAULT_DST_PORT = 50200

    def __init__(self, output_stream):
        self._out = output_stream
        self._parser = ScriptParser()
        self._enc = RdEncoder()
        self._timestamp = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def interpret(self, commands: list[dict]) -> None:
        """Process a list of parsed commands, writing tshark output.

        Commands between NEW_PACKET markers are combined into single UDP packets.
        Synthetic ACK replies are emitted immediately after each command packet.
        Commands with an ``expected`` value get a synthetic reply packet emitted
        after the batch packet (and ACK) that carried them.
        """
        self._timestamp = 0.0
        current_batch = bytearray()
        pending_commands: list[dict] = []
        for cmd in commands:
            if cmd.get('type') == 'NEW_PACKET':
                # Flush current batch as a single combined packet,
                # emit ACK immediately after the command packet,
                # then emit synthetic replies for any commands with expected values.
                if current_batch:
                    self._emit_packet(current_batch)
                    # Emit ACK immediately after the command packet
                    self._out.write(self._encode_ack(self._timestamp) + '\n')
                    self._timestamp += 0.0003
                    # Then emit expected replies
                    for pending in pending_commands:
                        reply_line = self._encode_reply(pending)
                        if reply_line:
                            self._out.write(reply_line + '\n')
                    current_batch = bytearray()
                    pending_commands = []
                continue

            # Accumulate raw command bytes into the current batch
            raw = self._encode_raw(cmd)
            current_batch.extend(raw)

            # Track commands that have an expected reply value
            if cmd.get('expected'):
                pending_commands.append(cmd)

        # Flush the final batch and any remaining expected replies
        if current_batch:
            self._emit_packet(current_batch)
            # Emit ACK for the final packet too
            self._out.write(self._encode_ack(self._timestamp) + '\n')
            self._timestamp += 0.0003
            for pending in pending_commands:
                reply_line = self._encode_reply(pending)
                if reply_line:
                    self._out.write(reply_line + '\n')

    # ------------------------------------------------------------------
    # Swizzle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _swizzle_byte(b: int, magic: int = 0x88) -> int:
        """Swizzle a byte using the magic number (inverse of RdPacket.un_swizzle_byte).

        The un-swizzle operation (from ruida_analyzer.py) is:
            b = (b - 1) & 0xFF
            b ^= magic
            b ^= (b >> 7) & 0xFF
            b ^= (b << 7) & 0xFF
            b ^= (b >> 7) & 0xFF

        The forward swizzle reverses the order and replaces (b - 1) with (b + 1).
        All XOR operations are self-inverse, so they remain the same.
        """
        b ^= (b >> 7) & 0xFF
        b ^= (b << 7) & 0xFF
        b ^= (b >> 7) & 0xFF
        b ^= magic
        b = (b + 1) & 0xFF
        return b

    # ------------------------------------------------------------------
    # Command encoding
    # ------------------------------------------------------------------

    def _encode_raw(self, cmd: dict) -> bytearray:
        """Encode a single command to raw bytes (unswizzled, no checksum).

        Builds the prefix byte, optional opcode, and encoded parameters
        without performing per-packet swizzling or checksumming.
        """
        mnemonic = cmd['mnemonic']
        info = self._parser.mnemonic_map.get(mnemonic)
        if info is None:
            raise ValueError(
                f'{cmd["line_num"]}: Unknown mnemonic "{mnemonic}"'
            )

        prefix_byte = info[0]
        raw = bytearray([prefix_byte])

        if len(info) == 4:
            # Nested option command: (prefix, middle_opcode, inner_opcode, cmd_entry_or_None)
            raw.append(info[1] & 0x7F)
            raw.append(info[2] & 0x7F)
        elif info[1] is not None:
            raw.append(info[1] & 0x7F)

        # cmd_entry is at index 2 for 3-tuples, index 3 for 4-tuples
        cmd_entry = info[3] if len(info) == 4 else (info[2] if len(info) > 2 else None)
        if cmd_entry is not None and len(cmd_entry) > 1 and cmd['params']:
            param_specs = cmd_entry[1:]  # Skip command name at index 0
            param_values = cmd['params']
            raw.extend(self._encode_params(param_specs, param_values, cmd))

        return raw

    def _emit_packet(self, raw: bytearray) -> None:
        """Swizzle, checksum, and emit a single packet as a tshark line.

        Takes combined raw command bytes (unswizzled, no checksum),
        applies the forward swizzle, prepends a 2-byte big-endian checksum,
        and writes a single tshark-format line.
        """
        if not raw:
            return

        # Swizzle all raw bytes
        swizzled = bytearray(
            self._swizzle_byte(b, magic=0x88) for b in raw
        )

        # Calculate checksum = sum of swizzled bytes
        chk = sum(swizzled) & 0xFFFF

        # Build final binary: checksum (2 bytes big-endian) + swizzled data
        binary = bytearray([
            (chk >> 8) & 0xFF,
            chk & 0xFF,
        ])
        binary.extend(swizzled)

        # Build tshark line
        ts = f'{self._timestamp:.6f}'
        src_dst = f'{self.DEFAULT_SRC_PORT},{self.DEFAULT_DST_PORT}'
        pkt_len = len(binary) + 8  # UDP header is 8 bytes
        hex_data = binary.hex()

        line = f'{ts}\t{src_dst}\t{pkt_len}\t{hex_data}'
        self._out.write(line + '\n')

        self._timestamp += 0.001

    def _encode_ack(self, ts: float) -> str:
        """Generate a tshark-format ACK reply line (controller→host).

        Produces a synthetic 1-byte ACK packet compatible with rpa.py's
        check_handshake() method. The raw byte is the swizzled form of
        0xCC (ACK) using magic 0x88.
        """
        src_dst = f'{self.DEFAULT_DST_PORT},{self.DEFAULT_SRC_PORT}'
        raw_ack = self._swizzle_byte(ACK, magic=0x88)
        return f'{ts:.6f}\t{src_dst}\t9\t{raw_ack:02x}'

    def _encode_reply(self, cmd: dict) -> str:
        """Generate a tshark-format reply packet for a command with expected value.

        Controller→host replies do not carry a checksum.  The data bytes are
        swizzled (same magic 0x88).  Ports are reversed (50200→40200).

        Returns the tshark line string, or an empty string if no reply can be
        generated (unknown MEM mnemonic, unknown type, TBD types, etc.).
        """
        expected = cmd.get('expected', '')
        if not expected or expected in ('?', '*'):
            return ''

        params = cmd.get('params', [])
        if not params:
            return ''

        mem_mnemonic = params[0]
        mt_map = self._parser.mt_map
        if mem_mnemonic not in mt_map:
            return ''

        msb, lsb = mt_map[mem_mnemonic]

        # Look up the MT entry to get the parameter spec tuple
        mt_entry = MT.get(msb, {}).get(lsb)
        if mt_entry is None or len(mt_entry) < 2:
            return ''

        spec = mt_entry[1]  # Type spec, e.g. CARD_ID, XABSCOORD, TBDU35
        if not isinstance(spec, tuple) or len(spec) < 2:
            return ''

        decoder_name = spec[1]
        rd_type = spec[2] if len(spec) >= 3 else None

        # Skip unknown / TBD types
        if decoder_name in ('tbd',):
            return ''

        # Parse the expected value string into a Python value
        parsed = self._parse_value(expected, decoder_name, rd_type)

        # Choose and call the appropriate encoder
        raw: bytearray
        if decoder_name == 'card_id':
            raw = self._enc.encode_card_id(parsed)
        elif decoder_name == 'coord':
            coord_nbytes = {'int_14': 2, 'uint_14': 2,
                            'int_35': 5, 'uint_35': 5}.get(rd_type, 5)
            raw = self._enc.encode_coord(parsed, coord_nbytes)
        else:
            method_name = _ENCODER_MAP.get(decoder_name)
            if method_name is None and rd_type is not None:
                method_name = _RDTYPE_ENCODER_MAP.get(rd_type)
            if method_name is None:
                return ''
            encoder = getattr(self._enc, method_name, None)
            if encoder is None:
                return ''
            raw = encoder(parsed)

        if not raw:
            return ''

        # Build reply framing + data bytes.
        # The parser's state machine expects:
        #   _st_mt_command → _st_mt_sub_command → _st_mt_address_msb
        #   → _st_mt_address_lsb → _st_mt_decode_reply
        #   Reply command byte: 0xDA (SETTING, bit 7 set → _h_is_command)
        #   Reply sub-command: 0x01 (GET_SETTING in RT, not 0x00 in CT)
        #   Address MSB/LSB: from the MT entry being queried
        framing = bytearray([0xDA, 0x01, msb & 0xFF, lsb & 0xFF])
        full_reply = framing + raw

        # Swizzle all reply bytes (replies use the same magic 0x88)
        swizzled = bytearray(self._swizzle_byte(b) for b in full_reply)

        # Controller→host: no checksum, ports reversed
        ts = f'{self._timestamp:.6f}'
        src_dst = f'{self.DEFAULT_DST_PORT},{self.DEFAULT_SRC_PORT}'
        pkt_len = len(swizzled) + 8
        hex_data = swizzled.hex()

        # Advance timestamp for the reply
        self._timestamp += 0.0002

        return f'{ts}\t{src_dst}\t{pkt_len}\t{hex_data}'

    # ------------------------------------------------------------------
    # Parameter encoding
    # ------------------------------------------------------------------

    def _encode_params(
        self,
        param_specs: tuple,
        param_values: list[str],
        cmd: dict,
    ) -> bytearray:
        """Encode script parameter values into binary using CT param specs.

        Each param spec is a tuple (format_str, decoder_fn, rd_type).
        The decoder function name determines which encoder to use.
        """
        result = bytearray()

        for i, (spec, value_token) in enumerate(zip(param_specs, param_values)):
            if not isinstance(spec, tuple) or len(spec) < 2:
                continue

            decoder_fn = spec[1]  # DDEC — e.g. 'coord', 'mt', 'int35'
            rd_type = spec[2] if len(spec) >= 3 else None

            encoded = self._encode_single_param(decoder_fn, rd_type, value_token, cmd)
            result.extend(encoded)

        return result

    def _encode_single_param(
        self,
        decoder_fn: str,
        rd_type: str | None,
        value_token: str,
        cmd: dict,
    ) -> bytearray:
        """Encode a single parameter value to binary."""
        # --- MT / Index special handling ---
        if decoder_fn in ('mt', 'index'):
            return self._encode_mt_param(value_token, cmd)

        # --- Named-param split: 'X=200.000mm' → '200.000mm' ---
        if '=' in value_token:
            _, value_token = value_token.split('=', 1)

        # --- Parse the value token to a Python value ---
        parsed = self._parse_value(value_token, decoder_fn, rd_type)

        # --- Determine the encoder method ---
        method_name = _ENCODER_MAP.get(decoder_fn)
        if method_name is None and rd_type is not None:
            method_name = _RDTYPE_ENCODER_MAP.get(rd_type)

        if method_name is None:
            return bytearray()

        # Special handling for coord which needs rd_type to select byte count
        if decoder_fn == 'coord':
            coord_nbytes = {'int_14': 2, 'uint_14': 2, 'int_35': 5, 'uint_35': 5}.get(rd_type, 5)
            return self._enc.encode_coord(parsed, coord_nbytes)

        # Resolve method on RdEncoder instance
        encoder = getattr(self._enc, method_name, None)
        if encoder is None:
            return bytearray()

        return encoder(parsed)

    def _encode_mt_param(self, value_token: str, cmd: dict) -> bytearray:
        """Encode a memory/index parameter.

        Looks up the token in the MT (or IDXT) mnemonic table and encodes
        its MSB/LSB address pair.
        """
        mt_map = self._parser.mt_map
        lookup = mt_map if cmd['type'] != 'index' else {}

        if value_token in lookup:
            msb, lsb = lookup[value_token]
        else:
            return bytearray()

        return bytearray([msb & 0x7F, lsb & 0x7F])

    # ------------------------------------------------------------------
    # Value parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_value(
        token: str,
        decoder_fn: str | None,
        rd_type: str | None,
    ):
        """Parse a script parameter token into a Python value.

        Handles numeric values, typed suffixes (mm, %, KHz, etc.),
        booleans, and plain strings.
        """
        raw = token.strip()

        # --- Unescape \# → # (from rds comment escaping) ---
        raw = raw.replace('\\#', '#')

        # --- Strip format-string label prefix ---
        # Parameters from format strings like 'Speed:{:.3f}mm/S' or 'State: {}'
        # include labels (e.g. 'Speed:', 'Power:', 'Freq:', 'CardID:') that must
        # be removed before parsing.  Skip strings with '=' which are already
        # split by _encode_single_param, and skip strings where the part before
        # ':' is not a plain label.
        if ':' in raw and '=' not in raw:
            maybe_label, after = raw.split(':', 1)
            if maybe_label.isalpha():
                raw = after.strip()

        # --- Boolean ---
        if raw.upper() in ('ON', 'TRUE', '1'):
            return True
        if raw.upper() in ('OFF', 'FALSE', '0'):
            return False

        # --- Typed suffix parsing ---
        if decoder_fn == 'coord':
            clean = raw
            if '=' in clean:
                clean = clean.split('=', 1)[1].strip()
            for suffix in ('mm', 'MM'):
                if clean.lower().endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break
            return float(clean)

        if decoder_fn == 'power':
            if raw.endswith('%'):
                return float(raw[:-1])
            return float(raw)

        if decoder_fn == 'frequency':
            clean = raw
            for suffix in ('KHz', 'khz', 'KHz', 'kHz'):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break
            return float(clean)

        if decoder_fn == 'speed':
            clean = raw
            for suffix in ('mm/S', 'mm/s', 'MM/S'):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break
            return float(clean)

        if decoder_fn == 'time':
            clean = raw
            for suffix in ('mS', 'ms', 'MS'):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break
            return float(clean)

        if decoder_fn == 'cstring':
            if (raw.startswith('"') and raw.endswith('"')) or \
               (raw.startswith("'") and raw.endswith("'")):
                return raw[1:-1]
            return raw

        # --- Numeric ---
        # Handle hex prefixed with # (e.g. Color:#FF00FFFF)
        if raw.startswith('#'):
            try:
                return int(raw[1:], 16)
            except ValueError:
                pass

        # Handle 0x/0X prefixed hex (e.g. 0x0000048216 from Sum:0x{...} format strings)
        if raw.startswith(('0x', '0X')):
            try:
                return int(raw, 0)
            except ValueError:
                pass

        try:
            return int(raw)
        except ValueError:
            pass

        try:
            return float(raw)
        except ValueError:
            pass

        # --- Fallback: return as string ---
        return raw
