#!/usr/bin/env bash
set -euo pipefail

# Native CLI launcher: FactoryEngine + SQLite + Stage, with no Legacy fallback.
CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY="${FACTORY:-$CODE_ROOT}"
export FACTORY
export PYTHONPATH="$CODE_ROOT${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m factory_core.cli compat "$@"
