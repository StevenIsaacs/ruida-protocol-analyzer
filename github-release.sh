#!/usr/bin/env bash
set -euo pipefail

_self=$(basename "$0")
_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$_script_dir"

usage () {
  cat <<EOF
Usage: $_self [--tag <tag>] [--draft]

Create a GitHub release with RPA artifacts.

Options:
  --tag <tag>    Git tag for the release (default: reads version from pyproject.toml)
  --draft        Create as a draft release
  -h, --help     Show this help
EOF
  exit 1
}

_tag=""
_draft=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) _tag="$2"; shift 2 ;;
    --draft) _draft=true; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# Determine version
if [ -z "$_tag" ]; then
  _version=$(python -c "
try:
    import tomllib
except ImportError:
    import tomli as tomllib
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
print(data['project']['version'])
")
  _tag="v$_version"
fi

# Check gh CLI
command -v gh >/dev/null 2>&1 || { echo "Error: gh CLI not found. Install from https://cli.github.com/"; exit 1; }

# Verify artifacts exist
_release_dir="release/rpa-${_tag#v}"
if [ ! -d "$_release_dir" ]; then
  echo "Error: $_release_dir not found. Run release.sh first."
  exit 1
fi

echo "Creating GitHub release $_tag..."
_notes=$(mktemp)
cat > "$_notes" <<EOF
## RPA ${_tag#v}

### Artifacts
EOF

_gh_args=(release create "$_tag" --title "RPA ${_tag#v}" --notes-file "$_notes")
[ "$_draft" = true ] && _gh_args+=(--draft)

for file in "$_release_dir"/*; do
  _gh_args+=("$file")
done

gh "${_gh_args[@]}"

rm "$_notes"
echo "Release $_tag created successfully!"
