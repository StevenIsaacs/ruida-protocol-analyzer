# Ruida Protocol Analyzer

A comprehensive Python-based protocol analyzer for reverse engineering and analyzing Ruida CNC controller communications. This tool parses network packet captures from tshark/Wireshark to decode and interpret the binary Ruida protocol used in laser cutters, engravers, and CNC machines.

## Features

- **Real-time Analysis**: Spawn tshark and analyze packets as they're captured
- **File-based Analysis**: Process existing tshark capture files
- **State Machine Parser**: Robust parsing using a finite state machine architecture
- **Hierarchical Commands**: Handles nested command structures (command/subcommand)
- **Type-aware Parameters**: Decodes coordinates, power levels, speeds, and other data types
- **Flexible Output**: Console output, file output, verbose modes, and raw packet display
- **Error Handling**: Configurable error handling with resync capabilities

## Background

The Ruida protocol is a proprietary binary communication protocol used by Ruida CNC controllers, commonly found in:
- CO2 laser cutters and engravers
- Fiber laser systems
- CNC routers with Ruida controllers
- Industrial cutting and marking systems

This analyzer was developed to understand and document the protocol for research, debugging, and integration purposes.

## Requirements

- Python 3.7+
- Wireshark/tshark installed and accessible in PATH
- Network access to capture Ruida controller communications

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/ruida-protocol-analyzer.git
cd ruida-protocol-analyzer
```

2. Install dependencies (if any):
```bash
pip install -r requirements.txt  # Currently no external dependencies
```

3. Make sure tshark is installed:
```bash
# Ubuntu/Debian
sudo apt-get install tshark

# macOS
brew install wireshark

# Windows - Download from https://www.wireshark.org/
```

## Usage

### Capture Traffic with tshark

First, capture Ruida protocol traffic using tshark. Replace `<ruida_ip>` with your controller's IP address:

```bash
tshark -Y "(ip.addr == <ruida_ip> && udp.payload)" -T fields \
       -e frame.time -e udp.port -e udp.length -e data.data > capture.log
```

### Analyze Captured Data

#### Basic Analysis
```bash
python rda.py capture.log
```

#### Real-time Analysis
```bash
python rda.py --on-the-fly
```

#### Advanced Options
```bash
# Verbose output with raw packet data
python rda.py --verbose --raw capture.log

# Save decoded output to file
python rda.py -o decoded.txt capture.log

# Quiet mode, stop on first error
python rda.py --quiet --stop-on-error -o results.txt capture.log
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--on-the-fly` | Spawn tshark and process output in real time |
| `--ip` | The Ruida controller IP address. Required when --on-the-fly is used |
| `--magic <magic_number>` | Specify the swizzle magic number rather than attempt to discover it in the capture. |
| `--out <file>`, `-o <file>` | Write decoded data to specified file |
| `--quiet`, `-q` | Suppress stdout output |
| `--verbose` | Generate detailed output with additional information |
| `--raw` | Include raw packet dumps with decoded output |
| `--unswizzled` | Output the unswizzled and unprocessed data. |
  | `--stop-on-error` | Stop processing on first decode error |
| `--step-packets` | Pause output after each host packet has been parsed (ignored when --on-the-fly) |
| `--step-decode` | Pause output after each decode message (disables --on-the-fly) |
| `--interactive` | (Future) Enter an interactive mode on the console (disables --on-the-fly) |


## Output Format

The analyzer produces human-readable output showing:
- Timestamp and packet information
- Decoded command names
- Parameter values with appropriate units
- Error messages for malformed packets

Where (see example):
- pkt_n = Current packet number.
- msg_n = Message number in the current packet.
- msg_class = Message classes can be either:
  - PRT = Protocol related
  - INT = Internal engine related
- msg_type = The message type.
  - For protocol messages:
    - RDR = Packet reader
    - PRS = Data parser
    - ERR = Errors with parsing or incoming data
    - FTL = Fatal errors (will trigger an exit)
    - vrb = Verbose message (when --verbose is used)
    - raw = Raw tshark and unswizzled packets or other raw data.
    - --> = Packets from the host
    - <-- = Packets from the controller
  - For internal messages:
    - PRT = An error caused by a protocol specification
    - INF = Informaton only
    - WRN = A warning about a correctable error
    - CRT = A critical error -- will continue to run
    - FTL = A fatal error which triggers an exit


### Example Output
```
# <pkt_n>:<msg_n>:<msg_class>:<msg_type>:<offset>:<decode>
0001:016:PRT:raw:-->:
Sep 10, 2025 13:08:54.507278575 PDT	50200,40200	9	c6

0002:001:PRT:RDR:-->:Interval:0.000127S
0002:002:PRT:raw:-->:
cc
0002:003:PRT:RDR:<--:ACK
....
0003:021:PRT:PRS:<--:0009:GET_SETTING Addr:057E:Card ID:Reply:65106510
```
### Verbose Output
With `--verbose`, additional details are shown:
```
0003:010:vrb:Entering state: mt_address_lsb
0003:011:vrb:Exiting state: mt_address_lsb
0003:012:vrb:Entering state: mt_decode_reply
0003:013:vrb:Memory reference: 057E
0003:014:vrb:Priming: ('{:08X}', 'uint35', 'uint_35')
0003:015:vrb:Decoded reply parameter 1=65106510.
0003:016:vrb:Reply decoded.
0003:017:vrb:Exiting state: mt_decode_reply
0003:018:vrb:Entering state: expect_command
0003:019:vrb:-->:
0003:020:vrb:<--:da01057e0628414a10

```

### Unknown Data Output
All unknowns are marked with "TBD". These can be either newly discovered commands
or addresses or unknown data formats for previously discovered commands or
addresses. This indicates data which requires further investigation.

Unknown parameter values are output in binary, hex, and decimal.
```
0116:2643:vrb:Decoded parameter 1=Addr:0620:TBD:Unknown address.
0116:2644:vrb:Priming: ('\nTBDU35:{0:035b}b: 0x{0:08x}: {0}', 'uint35', 'uint_35')
0116:2645:vrb:Decoding parameter 2.
0116:2646:vrb:Decoded parameter 2=
TBDU35:00000000000000000000000010111011000b: 0x000005d8: 1496.
0116:2647:vrb:Priming: ('\nTBDU35:{0:035b}b: 0x{0:08x}: {0}', 'uint35', 'uint_35')
0116:2648:vrb:Decoding parameter 3.
0116:2649:vrb:Decoded parameter 3=
TBDU35:00000000000000000000000010111011000b: 0x000005d8: 1496.
0116:2650:vrb:Parameters decoded.
0116:2651:vrb:Exiting state: decode_parameters
0116:2652:vrb:Entering state: expect_command
0116:2653:vrb:-->:da0106200000000b580000000b58
0116:2654:vrb:<--:
0116:2655:PRT:PRS:-->:0986:SET_SETTING Addr:0620:TBD:Unknown address
TBDU35:00000000000000000000000010111011000b: 0x000005d8: 1496
TBDU35:00000000000000000000000010111011000b: 0x000005d8: 1496

```

## Protocol Structure

The Ruida protocol uses a hierarchical binary command structure:

- **Single Commands**: Direct command byte followed by parameters
- **Hierarchical Commands**: Command byte + subcommand byte + parameters
- **Parameters**: Type-specific encoding (coordinates, power, speed, etc.)

### Supported Parameter Types

- **Coordinates**: Absolute and relative positioning in micrometers
- **Power Values**: Laser power percentages
- **Speed Values**: Movement speeds in micrometers/second
- **Time Values**: Delays and timing in microseconds
- **Control Values**: Various machine control parameters

## Architecture

The analyzer uses a finite state machine with the following states:
- `IDLE`: Ready for new packet
- `COMMAND_BYTE`: Processing main command
- `SUBCOMMAND_BYTE`: Processing hierarchical subcommands
- `PARAMETER_PARSING`: Extracting typed parameters
- `ERROR`: Handling parse failures

## Contributing

Contributions are welcome! This is an ongoing reverse engineering project. Areas where help is needed:

- **Protocol Documentation**: Adding new command interpretations
- **Parameter Types**: Implementing additional data type decoders
- **Testing**: Validating against different Ruida controller models
- **Features**: Additional analysis and export capabilities

### Adding New Protocol Specificatons

Protocol specifications are defined in the protocol tables. For example:
```python
# In CT (Command Table)
0x88: ('MOVE_ABS_XY', XCOORD, YCOORD),
```

Parameter decoders are defined as tuples:
```python
XCOORD = ('X={}mm', coord, 'int_35')
#         ^format   ^decoder ^raw_type
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

This tool is for educational and research purposes. The Ruida protocol is proprietary, and this analyzer is based on observation and reverse engineering. Use responsibly and respect intellectual property rights.

## Acknowledgments

- Developed for understanding CNC/laser cutter communications
- Inspired by the need for open tools in the CNC/laser space
- Built with insights from the embedded systems and maker communities

### Sources

 - MeerK40T: https://github.com/meerk40t/meerk40t/tree/main/meerk40t/ruida
 - Ruida protocol: https://edutechwiki.unige.ch/en/Ruida

## Support

- **Issues**: Please report bugs and feature requests via GitHub issues
- **Discussions**: Use Discord or GitHub discussions for questions and protocol insights
- **Documentation**: Help improve protocol documentation through pull requests

---

**Note**: This analyzer is a work in progress. Protocol coverage is incomplete, and new command interpretations are added as they're discovered and validated.
