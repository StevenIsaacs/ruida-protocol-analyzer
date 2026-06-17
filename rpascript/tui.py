"""
CLI entry point for rpa-script.

Generates Ruida protocol binary output from .rds script files.
Output can be piped directly to rpa.py for decoding.
"""

import argparse
import sys

from rpa import __version__
from rpascript.interpreter import ScriptInterpreter, ScriptParser


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for rpa-script."""
    parser = argparse.ArgumentParser(
        prog="rpa-script",
        description="Generate Ruida protocol output from .rds scripts or launch interactive TUI.",
        epilog="Examples: rpa-script myscript.rds | python rpa.py -   |   rpa-script --tui   |   rpa-script --rpyc-host 0.0.0.0",
    )
    parser.add_argument(
        "script",
        nargs="?",
        default=None,
        help=".rds script file to process (optional with --tui)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: stdout)",
        default=None,
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        help="Parse only, show parsed commands without generating output",
        action="store_true",
    )
    parser.add_argument(
        "-t",
        "--tui",
        help="Launch interactive TUI (Textual-based terminal interface)",
        action="store_true",
    )
    parser.add_argument(
        "--rpyc-host",
        default=None,
        help="Start in RPC server mode on this host address (e.g., 0.0.0.0). "
        "When set, starts RPyC server instead of TUI.",
    )
    parser.add_argument(
        "--rpyc-port",
        type=int,
        default=18812,
        help="Port for the RPyC server (default: 18812).",
    )
    parser.add_argument(
        "--rpyc-cert",
        default=None,
        help="Path to TLS certificate file for RPyC server.",
    )
    parser.add_argument(
        "--rpyc-key",
        default=None,
        help="Path to TLS private key file for RPyC server.",
    )
    parser.add_argument(
        "--rpyc-token",
        default=None,
        help="Authentication token for RPyC connections.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> None:
    """Main entry point for rpa-script CLI."""
    parser = build_parser()
    args = parser.parse_args()

    # TUI mode: launch interactive terminal interface
    if args.tui:
        from rpascript.tui_adapter import run_tui

        if args.script:
            print(
                f'Note: script argument "{args.script}" ignored in TUI mode. '
                "Use Ctrl+L to load scripts within the TUI."
            )
        run_tui()
        return

    # RPC server mode
    if args.rpyc_host:
        from rpalib.rpyc_service import start_rpyc_server

        print(
            f"Starting RPyC server on {args.rpyc_host}:{args.rpyc_port}...",
            file=sys.stderr,
        )
        start_rpyc_server(
            host=args.rpyc_host,
            port=args.rpyc_port,
            cert_path=args.rpyc_cert,
            key_path=args.rpyc_key,
            token=args.rpyc_token,
        )
        return

    # Script argument is required when not in TUI mode
    if args.script is None:
        parser.print_help()
        sys.exit(1)

    # Parse script
    script_parser = ScriptParser()
    commands = script_parser.parse_file(args.script)

    # Dry run: show parsed commands
    if args.dry_run:
        for cmd in commands:
            if cmd["type"] == "NEW_PACKET":
                print("  NEW_PACKET")
                continue
            params_str = " ".join(cmd["params"])
            expected_str = f"= {cmd['expected']}" if cmd["expected"] else ""
            print(
                f"  {cmd['type']:8s} {cmd['mnemonic']:20s} "
                f"{params_str:30s} {expected_str}"
            )
        return

    # Generate tshark output
    out_stream = open(args.output, "w") if args.output else sys.stdout
    try:
        interpreter = ScriptInterpreter(out_stream)
        interpreter.interpret(commands)
    finally:
        if args.output:
            out_stream.close()


if __name__ == "__main__":
    main()
