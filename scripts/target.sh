#!/usr/bin/env bash
# Starts wkm on the machine being driven.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m wkm target "$@"
