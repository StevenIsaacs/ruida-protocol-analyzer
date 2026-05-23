#!/usr/bin/env bash
set -euo pipefail

_self=$(basename "$0")
_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$_script_dir"

usage () {
  cat <<EOF
Usage: $_self [--dist-only] [--wheel-only]

Build a versioned release of RPA in release/

Options:
  --dist-only    Only build PyInstaller binary (skip wheel)
  --wheel-only   Only build pip wheel (skip PyInstaller)
  -h, --help     Show this help
EOF
  exit 1
}

# Parse args
_dist_only=false
_wheel_only=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dist-only) _dist_only=true; shift ;;
    --wheel-only) _wheel_only=true; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# Validate flags
if [ "$_dist_only" = true ] && [ "$_wheel_only" = true ]; then
  echo "Error: --dist-only and --wheel-only are mutually exclusive."
  exit 1
fi

# --- Determine version ---
_version=$(python3 -c "
try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # pip install tomli
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
print(data['project']['version'])
")

echo "Building RPA release v$_version"
echo ""

# --- Create release directory ---
_release_dir="release/rpa-$_version"
mkdir -p "$_release_dir"

# --- Build pip wheel ---
if [ "$_dist_only" = false ]; then
  echo "--- Building pip wheel ---"
  pip wheel --no-deps -w "$_release_dir" .
  echo "Wheel built."
  echo ""
fi

# --- Build PyInstaller binary ---
if [ "$_wheel_only" = false ]; then
  echo "--- Building standalone binary ---"
  if ! command -v pyinstaller &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
  fi
  pyinstaller --clean rpa.spec
  cp dist/rpa "$_release_dir/rpa-v$_version"
  echo "Binary built."
  echo ""
fi

# --- Generate checksums ---
echo "--- Generating checksums ---"
cd "$_release_dir"
sha256sum * > SHA256SUMS
cd "$_script_dir"
echo ""

# --- Summary ---
echo "============================================"
echo "Release v$_version complete!"
echo "Artifacts in: $_release_dir"
ls -lh "$_release_dir" | tail -n +2
echo "============================================"
