#!/usr/bin/env bash
# Lensix Inventory — one-command runner.
#
# Usage:
#   ./run.sh aws
#   ./run.sh azure
#   ./run.sh gcp
#   ./run.sh aws --regions us-east-1,us-west-2
#
# What this does:
#   1. Creates a private Python virtual environment in .venv/ (first run
#      only — later runs reuse it, so they're much faster).
#   2. Installs only the dependencies needed for the provider you picked.
#   3. Runs the gather and writes a timestamped output file.
#
# You need to already be logged in / have credentials configured for
# whichever provider you're scanning (aws configure / az login / gcloud
# auth application-default login) — see README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROVIDER="${1:-}"
if [[ -n "$PROVIDER" ]]; then shift; fi

if [[ -z "$PROVIDER" ]]; then
  echo "Which cloud provider do you want to gather an inventory from?"
  select choice in aws azure gcp; do
    case "$choice" in
      aws|azure|gcp) PROVIDER="$choice"; break ;;
      *) echo "Please choose 1, 2, or 3." ;;
    esac
  done
fi

if [[ "$PROVIDER" != "aws" && "$PROVIDER" != "azure" && "$PROVIDER" != "gcp" ]]; then
  echo "Usage: $0 <aws|azure|gcp> [--regions us-east-1,us-west-2]" >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.9+ is required but wasn't found on your PATH." >&2
  echo "Install it from https://www.python.org/downloads/ and try again." >&2
  exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_MARKER="$VENV_DIR/.lensix-$PROVIDER-installed"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> First run: setting up a private Python environment in .venv/ ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if [[ ! -f "$VENV_MARKER" ]]; then
  echo "==> Installing dependencies for $PROVIDER (only needs to happen once) ..."
  pip install --quiet --upgrade pip
  pip install --quiet -r "$SCRIPT_DIR/requirements-$PROVIDER.txt"
  touch "$VENV_MARKER"
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT="$SCRIPT_DIR/lensix-inventory-${PROVIDER}-${TIMESTAMP}.ndjson.gz"

echo "==> Gathering $PROVIDER inventory — this can take a few minutes for large accounts ..."
echo ""
python -m lensix_inventory --provider "$PROVIDER" --output "$OUTPUT" "$@"
