#!/usr/bin/env bash
# Re-run just the macOS permission step.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m wkm permit
