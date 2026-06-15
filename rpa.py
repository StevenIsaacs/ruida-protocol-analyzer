import argparse
import subprocess
import sys
import time

import protocols.ruida.ruida_analyzer as rpa
from rpalib.rpa_emitter import RpaEmitter

# Graceful Bokeh import: BokehApp is None if Bokeh not installed.
try:
    from rpalib.bokeh_app import BokehApp
except ImportError:
    BokehApp = None

# --- Version detection ---
# Prefer importlib.metadata (works when package is pip-installed or built with PyInstaller).
# Falls back to a dev version when running rpa.py directly from source.
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("ruida-protocol-analyzer")
    except PackageNotFoundError:
        __version__ = "0.8.0-dev"
except ImportError:
    # Python < 3.8 fallback
    __version__ = "0.8.0-dev"


def parse_arguments():
    """Parse command line arguments for Ruida protocol analyzer"""
    parser = argparse.ArgumentParser(
        description="""
Ruida Protocol Analyzer - Parse and decode Ruida protocol packets.

The tshark log file must be in a specific format. Use this command to capture:

tshark -Y "(ip.addr == <ruida_ip> && udp.payload)" -T fields \
       -e frame.time -e udp.port -e udp.length -e data.data > capture.log

The decoded data is emitted to the console (stdout) which can be redirected to
a file.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s capture.log                      # Analyze existing tshark log
  %(prog)s --on-the-fly --ip 192.168.1.100  # Real-time analysis
  %(prog)s -o output.txt capture.log        # Save decoded output to file
  %(prog)s --verbose --raw capture.log      # Detailed output with raw data
  %(prog)s --magic 0x88 capture.log         # Use specific magic number
        """,
    )

    # Input source
    parser.add_argument(
        "input_file",
        nargs="?",
        help="Tshark log file to analyze (not needed with --on-the-fly).",
    )

    # Input file encodng.
    parser.add_argument(
        "--input-encoding",
        metavar="<input_encoding>",
        default="utf-8",
        help="Input text encoding. Windows files can be encoded as utf-16.",
    )

    # Real-time processing
    parser.add_argument(
        "--on-the-fly",
        action="store_true",
        help="Spawn tshark and process the output in real time (requires --ip).",
    )

    # Controller IP address
    parser.add_argument(
        "--ip",
        metavar="<ip_address>",
        help="The IP address of the controller (required when using --on-the-fly.)",
    )

    # Protocol
    parser.add_argument(
        "--protocol",
        metavar="<protocol>",
        default="ruida",
        help="Specify the protocol to use for decoding the raw data. "
        "Currently only the ruida protocol is available.",
    )

    # Magic number
    parser.add_argument(
        "--magic",
        metavar="<magic_number>",
        default="0x88",
        help="Specify the swizzle magic number rather than attempt "
        "to discover it in the capture. "
        "Available only with the ruida protocol.",
    )

    # Output file
    parser.add_argument(
        "--out",
        "-o",
        dest="output_file",
        metavar="<file>",
        help="Write the decoded data to <file> in addition to the console.",
    )

    # Quiet mode
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Do not output to stdout -- disables --verbose, --raw, and --unswizzled.",
    )

    # Verbose output
    parser.add_argument(
        "--verbose", action="store_true", help="Generate verbose output."
    )

    # Raw dump output
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output the raw dump lines with the decoded output.",
    )

    # Raw dump output
    parser.add_argument(
        "--unswizzled",
        action="store_true",
        help="Output the unswizzled and unprocessed data.",
    )

    # Stop on error
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop decode when an error is detected -- do not attempt to resync.",
    )

    # Plot moves.
    parser.add_argument(
        "--plot-moves",
        action="store_true",
        help="Plot all moves and cuts in an interactive Bokeh visualization.",
    )

    # Bokeh server port
    parser.add_argument(
        "--bokeh-port",
        type=int,
        default=5006,
        metavar="<port>",
        help="Port for the Bokeh visualization server (default: 5006).",
    )

    # Enter interactive mode (CLI)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enter an interactive mode on the console after decode completes.",
    )

    # Generate .rds script file
    parser.add_argument(
        "--generate-script",
        action="store_true",
        help="Generate a .rds script file alongside the standard output for round-trip verification.",
    )

    # Version
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
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

    if args.magic is not None and args.protocol != "ruida":
        parser.error("--magic can only be used with the ruida protocol.")

    # Parse magic number if provided
    if args.magic:
        try:
            # Handle hex format (0x prefix) or decimal
            if args.magic.lower().startswith("0x"):
                args.magic = int(args.magic, 16) & 0xFF
            else:
                raise ValueError
        except ValueError:
            parser.error(f"Invalid magic number format: {args.magic}")

    return args


def open_input(args):
    """Either open the input file or spawn tshark. Both support the
    readline method so either can be passed to the parser."""
    _file = args.input_file
    if args.input_file:
        input = open(_file, "r", encoding=args.input_encoding)
    else:
        # Build tshark command with the specified IP
        _tshark_cmd = [
            "tshark",
            "-Y",
            f"(ip.addr == {args.ip} && udp.payload)",
            "-T",
            "fields",
            "-e",
            "frame.time_delta",
            "-e",
            "udp.port",
            "-e",
            "udp.length",
            "-e",
            "data.data",
            "-l",
        ]
        try:
            _in = subprocess.Popen(
                _tshark_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            raise FileNotFoundError("tshark not found. Please install Wireshark/tshark")
        input = _in.stdout
    return input


def main():
    """Main function with command line argument processing"""
    args = parse_arguments()
    input_stream = open_input(args)

    # Set up output handling
    output = RpaEmitter(args)
    output.open()

    # Initialize analyzer with magic number if provided
    analyzer = rpa.RuidaProtocolAnalyzer(args, input_stream, output)

    # Set up script generator if --generate-script is active
    script_gen = None
    if getattr(args, "generate_script", False):
        from pathlib import Path

        from rpascript.generator import ScriptGenerator

        # Derive .rds path from output file or input file
        base = args.output_file or args.input_file or "capture"
        script_path = str(Path(base).with_suffix(".rds"))
        script_gen = ScriptGenerator(script_path, source_file=args.input_file)
        analyzer.parser.on_command = script_gen.write_command
        analyzer.on_new_packet = script_gen.on_new_packet
        output.info(f"Generating script: {script_path}")

    bokeh_app = None

    if args.plot_moves:
        analyzer.parser.plot.plot.enable()

        if BokehApp is None:
            output.warning("Bokeh is not installed. Install with: pip install bokeh")
        elif args.on_the_fly:
            # For on-the-fly, start Bokeh server before decode.
            try:
                bokeh_app = BokehApp(args, analyzer.parser.plot.plot)
                if bokeh_app.start(port=args.bokeh_port):
                    output.info(
                        "Bokeh visualization server started at "
                        f"http://localhost:{bokeh_app.port}"
                    )
                else:
                    output.warning(
                        "Failed to start Bokeh server. Continuing without plot."
                    )
                    bokeh_app = None
            except Exception as e:
                output.warning(
                    f"Failed to start Bokeh server: {e}. Continuing without plot."
                )
                bokeh_app = None

    try:
        analyzer.decode()  # Does not return until decode is complete.

        output.info("Decode complete.\n")
        output.close()

        if script_gen is not None:
            script_gen.close()

        if args.plot_moves and not args.on_the_fly and BokehApp is not None:
            # File mode: start Bokeh server after output file is written.
            try:
                bokeh_app = BokehApp(args, analyzer.parser.plot.plot)
                if bokeh_app.start(port=args.bokeh_port):
                    print(
                        "Now plotting moves. Press Ctrl+C in the terminal to exit.",
                        file=sys.stderr,
                    )
                    # Block until user presses Ctrl+C.
                    try:
                        while bokeh_app._running:
                            time.sleep(0.1)
                    except KeyboardInterrupt:
                        pass
                else:
                    print("Failed to start Bokeh server.", file=sys.stderr)
                    bokeh_app = None
            except Exception as e:
                print(f"Failed to start Bokeh server: {e}.", file=sys.stderr)
                bokeh_app = None

    except LookupError as e:
        output.critical(f"{e}")
        output.critical("Verify incoming data is a tshark dump of a Ruida UDP session.")
    except SyntaxError as e:
        output.critical(f"{e}")
    except RuntimeError as e:
        output.critical(f"Shutting down: {e}")
    except KeyboardInterrupt:
        output.info("Exiting at user request.\n")
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as e:
        if isinstance(e, str):
            output.verbose(f"Unhandled error: {e}")
        else:
            output.verbose(f"Unhandled exception {e.__str__()}")
        exit(1)
    finally:
        if bokeh_app is not None:
            bokeh_app.shutdown()
        sys.stdout.flush()


if __name__ == "__main__":
    main()
