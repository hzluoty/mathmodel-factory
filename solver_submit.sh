#!/usr/bin/env bash
set -euo pipefail

FACTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$FACTORY${PYTHONPATH:+:$PYTHONPATH}"

find_engine_project() {
    local dir="$1"
    while [[ -n "$dir" && "$dir" != "/" ]]; do
        if [[ -f "$dir/.factory/state.db" ]]; then
            printf '%s\n' "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

start_dir="$PWD"
script=""
skip_next=0
for arg in "$@"; do
    if (( skip_next )); then
        skip_next=0
        continue
    fi
    case "$arg" in
        --type|--max-time|--args|--status|--wait|--input|--output|--seed)
            skip_next=1
            ;;
        --dry-run|-h|--help)
            ;;
        -*)
            ;;
        *)
            script="$arg"
            ;;
    esac
done
if [[ -n "$script" ]]; then
    if [[ "$script" = /* ]]; then
        start_dir="$(dirname "$script")"
    else
        start_dir="$(dirname "$PWD/$script")"
    fi
fi

project="$(find_engine_project "$start_dir" 2>/dev/null || true)"
if [[ -z "$project" ]]; then
    echo "ERROR: NATIVE_WORKFLOW_REQUIRED: solver submission requires a Native project with .factory/state.db" >&2
    exit 2
fi

case "${1:-}" in
    --status)
        exec python3 -m factory_core.cli solver status "$project" "${2:?missing job id}" "${@:3}"
        ;;
    --wait)
        exec python3 -m factory_core.cli solver wait "$project" "${2:?missing job id}"
        ;;
    *)
        exec python3 -m factory_core.cli solver submit "$project" "$@"
        ;;
esac
