#!/usr/bin/env bash
set -euo pipefail

_self=$(basename "$0")
_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$_script_dir"

usage () {
  cat <<EOF
Usage: $_self [--pip-install]

Build a standalone CPA binary using PyInstaller.

Options:
  --pip-install    Run pip install -e . before building (useful for clean builds)
EOF
  exit 1
}

# Parse args
_pip_install=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pip-install) _pip_install=true; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# Venv setup
if [[ -z "$VIRTUAL_ENV" ]]; then
  if [ -d ".venv" ]; then
    source .venv/bin/activate
  elif [ -d ".venv-bokeh" ]; then
    source .venv-bokeh/bin/activate
  else
    echo "Creating virtual environment..."
    python -m venv .venv
    source .venv/bin/activate
  fi
fi

# Optional pip install
if [ "$_pip_install" = true ]; then
  echo "Installing package..."
  pip install -e .
fi

# Ensure PyInstaller is available
if ! command -v pyinstaller &>/dev/null; then
  echo "Installing PyInstaller..."
  pip install pyinstaller
fi

# Build
echo "Building CPA standalone binary..."
pyinstaller --clean cpa.spec

echo ""
echo "Build complete! Binary at: dist/cpa"
echo ""
echo "Verify with:"
echo "  dist/cpa --help"
echo ""
