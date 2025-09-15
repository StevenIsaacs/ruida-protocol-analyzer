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
python ruida_analyzer.py capture.log
```

#### Real-time Analysis
```bash
python ruida_analyzer.py --on-the-fly
```

#### Advanced Options
```bash
# Verbose output with raw packet data
python ruida_analyzer.py --verbose --raw capture.log

# Save decoded output to file
python ruida_analyzer.py -o decoded.txt capture.log

# Quiet mode, stop on first error
python ruida_analyzer.py --quiet --stop-on-error -o results.txt capture.log
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--on-the-fly` | Spawn tshark and process output in real time |
| `--out <file>`, `-o <file>` | Write decoded data to specified file |
| `--quiet`, `-q` | Suppress stdout output |
| `--verbose` | Generate detailed output with additional information |
| `--raw` | Include raw packet dumps with decoded output |
| `--stop-on-error` | Stop processing on first decode error |

## Output Format

The analyzer produces human-readable output showing:
- Timestamp and packet information
- Decoded command names
- Parameter values with appropriate units
- Error messages for malformed packets

### Example Output
```
Line 1: 12:34:56.789: Port 50200 (8 bytes) - 0x88 MOVE_ABS_XY(X=1000um, Y=2000um)
Line 2: 12:34:57.123: Port 50200 (3 bytes) - 0xC0 IMD_POWER_2(Power:75%)
Line 3: 12:34:57.456: Port 50200 (2 bytes) - 0xD8/0x00 START_PROCESS
```

### Verbose Output
With `--verbose`, additional details are shown:
```
Line 1: 12:34:56.789: Port 50200 (8 bytes) - 0x88 MOVE_ABS_XY(X=1000um, Y=2000um)
    Raw bytes: 88 E8 03 00 00 D0 07 00 00
    Command structure: Single command
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

### Adding New Commands

Commands are defined in the protocol tables:
```python
# In CT (Command Table)
0x88: ('MOVE_ABS_XY', ParameterType.XCOORD, ParameterType.YCOORD),
```

Parameter decoders are defined as tuples:
```python
XCOORD = ('X={}um', abscoord, 'int_35')
#         ^format   ^decoder ^raw_type
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

This tool is for educational and research purposes. The Ruida protocol is proprietary, and this analyzer is based on observation and reverse engineering. Use responsibly and respect intellectual property rights.

## Acknowledgments

- Developed for understanding CNC/laser cutter communications
- Built with insights from the embedded systems and maker communities
- Inspired by the need for open tools in the CNC/laser space

## Support

- **Issues**: Please report bugs and feature requests via GitHub issues
- **Discussions**: Use GitHub discussions for questions and protocol insights
- **Documentation**: Help improve protocol documentation through pull requests

---

**Note**: This analyzer is a work in progress. Protocol coverage is incomplete, and new command interpretations are added as they're discovered and validated.
