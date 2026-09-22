#!/usr/bin/env bash
set -euo pipefail

# TUI client launcher: renders the Web control plane in the terminal.
# It is a *client* -- start the dashboard backend first (web/start_dashboard.sh).
CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$CODE_ROOT${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$CODE_ROOT/.venv/bin/python" ]]; then
    PYTHON="$CODE_ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

if ! "$PYTHON" -c 'import textual' 2>/dev/null; then
  echo "ERROR: 缺少 textual 依赖，请先执行： uv sync --extra tui" >&2
  exit 1
fi

exec "$PYTHON" -m apps.tui "$@"
