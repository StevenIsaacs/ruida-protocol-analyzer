import argparse
import sys
import subprocess

import ruida_analyzer as rpa
from rpa_emitter import RdaEmitter

def parse_arguments():
    """Parse command line arguments for Ruida protocol analyzer"""
    parser = argparse.ArgumentParser(
        description='''
Ruida Protocol Analyzer - Parse and decode Ruida CNC protocol packets.

The tshark log file must be in a specific format. Use this command to capture:

tshark -Y "(ip.addr == <ruida_ip> && udp.payload)" -T fields \
       -e frame.time -e udp.port -e udp.length -e data.data > capture.log

The decoded data is emitted to the console (stdout) which can be redirected to
a file.
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s capture.log                      # Analyze existing tshark log
  %(prog)s --on-the-fly --ip 192.168.1.100  # Real-time analysis
  %(prog)s -o output.txt capture.log        # Save decoded output to file
  %(prog)s --verbose --raw capture.log      # Detailed output with raw data
  %(prog)s --magic 0x88 capture.log         # Use specific magic number
  %(prog)s --step-decode capture.log        # Pause for each decoded output
        '''
    )

    # Input source
    parser.add_argument(
        'input_file',
        nargs='?',
        help='Tshark log file to analyze (not needed with --on-the-fly).'
    )

    # Input file encodng.
    parser.add_argument(
        '--input-encoding',
        metavar='<input_encoding>',
        default='utf-8',
        help='Input text encoding. Windows files can be encoded as utf-16.'
    )

    # Real-time processing
    parser.add_argument(
        '--on-the-fly',
        action='store_true',
        help='Spawn tshark and process the output in real time (requires --ip).'
    )

    # Ruida controller IP address
    parser.add_argument(
        '--ip',
        metavar='<ip_address>',
        help='The IP address of the Ruida controller (required when using --on-the-fly.)'
    )

    # Magic number
    parser.add_argument(
        '--magic',
        metavar='<magic_number>',
        help='Specify the swizzle magic number rather than attempt to discover it in the capture.'
    )

    # Output file
    parser.add_argument(
        '--out', '-o',
        dest='output_file',
        metavar='<file>',
        help='Write the decoded data to <file> in addition to the console.'
    )

    # Quiet mode
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Do not output to stdout -- disables --verbose, --raw, and --unswizzled.'
    )

    # Verbose output
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Generate verbose output.'
    )

    # Raw dump output
    parser.add_argument(
        '--raw',
        action='store_true',
        help='Output the raw dump lines with the decoded output.'
    )

    # Raw dump output
    parser.add_argument(
        '--unswizzled',
        action='store_true',
        help='Output the unswizzled and unprocessed data.'
    )

    # Stop on error
    parser.add_argument(
        '--stop-on-error',
        action='store_true',
        help='Stop decode when an error is detected -- do not attempt to resync.'
    )

    # Single step mode -- packets
    parser.add_argument(
        '--step-packets',
        action='store_true',
        help='Pause output after each host packet has been parsed (ignored when --on-the-fly).'
    )

    # Single step mode -- commands
    parser.add_argument(
        '--step-decode',
        action='store_true',
        help='Pause output after each decode message (disables --on-the-fly).'
    )

    # Enter interactive mode (CLI)
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Enter an interactive mode on the console (disables --on-the-fly).'
    )

    args = parser.parse_args()

    # Validation
    if not args.on_the_fly and not args.input_file:
        parser.error("Input file required unless using --on-the-fly")

    if args.on_the_fly and args.input_file:
        parser.error("Cannot specify input file with --on-the-fly")

    if args.on_the_fly and not args.ip:
        parser.error("--ip is required when using --on-the-fly")

    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose are mutually exclusive")

    if (args.step_decode or args.step_packets) and args.on_the_fly:
        args.on_the_fly = False
        parser.print('Cannot step when --on-the-fly is enabled -- disabled --on-the-fly')

    # Parse magic number if provided
    if args.magic:
        try:
            # Handle hex format (0x prefix) or decimal
            if args.magic.lower().startswith('0x'):
                args.magic = int(args.magic, 16) & 0xFF
            else:
                raise ValueError
        except ValueError:
            parser.error(f"Invalid magic number format: {args.magic}")

    return args

def open_input(args):
    '''Either open the input file or spawn tshark. Both support the
    readline method so either can be passed to the parser.'''
    _file = args.input_file
    if args.input_file:
        input = open(_file, 'r', encoding=args.input_encoding)
    else:
        # Build tshark command with the specified IP
        _tshark_cmd = [
            'tshark',
            '-Y', f'(ip.addr == {args.ip} && udp.payload)',
            '-T', 'fields',
            '-e', 'frame.time_delta',
            '-e', 'udp.port',
            '-e', 'udp.length',
            '-e', 'data.data',
            '-l'
        ]
        try:
            _in = subprocess.Popen(
                _tshark_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                'tshark not found. Please install Wireshark/tshark')
        input = _in.stdout
    return input

def main():
    """Main function with command line argument processing"""
    args = parse_arguments()
    input = open_input(args)

    # Set up output handling
    output = RdaEmitter(args)
    output.open()

    # Initialize analyzer with magic number if provided
    analyzer = rpa.RuidaProtocolAnalyzer(args, input, output)
    try:
        analyzer.decode() # Does not return until decode is complete.
        output.info('Decode complete.\n')
        output.close()
    except LookupError as e:
        output.critical(f'{e}')
        output.critical(
            'Verify incoming data is a tshark dump of a Ruida UDP session.')
        exit(1)
    except SyntaxError as e:
        output.critical(f'{e}')
        exit(1)
    except RuntimeError as e:
        output.critical(f'Shutting down: {e}')
        exit(1)
    except KeyboardInterrupt:
        output.info('Exiting at user request.\n')
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(0)
    except Exception as e:
        output.critical(f'Unhandled error:{e}')
        exit(1)

if __name__ == "__main__":
    main()
