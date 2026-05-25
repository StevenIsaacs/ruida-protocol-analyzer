"""
CLI entry point for rpa-script.

Generates Ruida protocol binary output from .rds script files.
Output can be piped directly to rpa.py for decoding.
"""

import argparse
import sys

from rpascript.interpreter import ScriptParser, ScriptInterpreter


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for rpa-script."""
    parser = argparse.ArgumentParser(
        prog='rpa-script',
        description='Generate tshark-format Ruida protocol output from .rds scripts.',
        epilog='Example: rpa-script myscript.rds | python rpa.py -',
    )
    parser.add_argument(
        'script',
        help='.rds script file to process',
    )
    parser.add_argument(
        '-o', '--output',
        help='Output file (default: stdout)',
        default=None,
    )
    parser.add_argument(
        '-n', '--dry-run',
        help='Parse only, show parsed commands without generating output',
        action='store_true',
    )
    return parser


def main() -> None:
    """Main entry point for rpa-script CLI."""
    parser = build_parser()
    args = parser.parse_args()

    # Parse script
    script_parser = ScriptParser()
    commands = script_parser.parse_file(args.script)

    # Dry run: show parsed commands
    if args.dry_run:
        for cmd in commands:
            if cmd['type'] == 'NEW_PACKET':
                print('  NEW_PACKET')
                continue
            params_str = ' '.join(cmd['params'])
            expected_str = f'= {cmd["expected"]}' if cmd['expected'] else ''
            print(f"  {cmd['type']:8s} {cmd['mnemonic']:20s} "
                  f"{params_str:30s} {expected_str}")
        return

    # Generate tshark output
    out_stream = open(args.output, 'w') if args.output else sys.stdout
    try:
        interpreter = ScriptInterpreter(out_stream)
        interpreter.interpret(commands)
    finally:
        if args.output:
            out_stream.close()


if __name__ == '__main__':
    main()
