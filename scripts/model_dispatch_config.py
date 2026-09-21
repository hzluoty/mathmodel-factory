#!/usr/bin/env python3
"""Read model registry and per-step model assignment config.

Absence and corruption are deliberately different outcomes.  A missing file is a
supported default (the caller falls back to built-in candidates); a file that is
*present* but unreadable, malformed or wrongly typed raises
:class:`ModelDispatchConfigError`.  Previously any read failure collapsed to
``None``, so a corrupt config was indistinguishable from "not configured" and
could silently select different models than the operator declared.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

US = "\x1f"


class ModelDispatchConfigError(RuntimeError):
    """A model configuration file exists but cannot be trusted."""


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_json_object(path: Path, what: str) -> dict:
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        raise ModelDispatchConfigError(f"{what} {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelDispatchConfigError(f"{what} {path} must be a JSON object")
    return data


def normalize_assignment(value: Any) -> tuple[str, str] | None:
    """Normalize one ``step_N`` entry.

    ``None`` (key absent, or explicitly null) means "no assignment" and is a
    supported outcome.  A value that is present but unusable raises, so
    malformed configuration cannot masquerade as an unconfigured step.
    """

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return (text, "") if text else None
    if isinstance(value, dict):
        primary = value.get("primary")
        fallback = value.get("fallback")
        if not isinstance(primary, str) or not primary.strip():
            raise ModelDispatchConfigError(
                "model assignment 'primary' must be a non-empty string"
            )
        if fallback is not None and not isinstance(fallback, str):
            raise ModelDispatchConfigError("model assignment 'fallback' must be a string")
        return primary.strip(), (fallback or "").strip()
    raise ModelDispatchConfigError(
        f"model assignment must be a string or object, got {type(value).__name__}"
    )


def get_step_model_ids(config_file: Path, base: str, step: str | int) -> tuple[str, str] | None:
    if not config_file.is_file():
        return None
    cfg = _load_json_object(config_file, "model config")
    key = f"step_{step}"

    def get(scope: str) -> tuple[str, str] | None:
        if scope not in cfg:
            return None
        scoped = cfg[scope]
        if not isinstance(scoped, dict):
            raise ModelDispatchConfigError(
                f"model config scope {scope!r} must be a JSON object"
            )
        return normalize_assignment(scoped.get(key))

    try:
        return get(base) or get("_default")
    except ModelDispatchConfigError as exc:
        raise ModelDispatchConfigError(f"{config_file}: {exc}") from exc


def get_model_entry(registry_file: Path, model_id: str) -> dict[str, str] | None:
    if not model_id or not registry_file.is_file():
        return None
    try:
        registry = read_json(registry_file)
    except (OSError, ValueError) as exc:
        raise ModelDispatchConfigError(
            f"model registry {registry_file} is unreadable: {exc}"
        ) from exc
    models = registry.get("models", []) if isinstance(registry, dict) else registry
    if not isinstance(models, list):
        raise ModelDispatchConfigError(
            f"model registry {registry_file} must contain a list of models"
        )
    for model in models:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        if model.get("enabled") is False:
            return None
        entry: dict[str, str] = {}
        for field in ("backend", "model", "effort", "base_url", "key_env"):
            value = model.get(field)
            if value is None:
                entry[field] = ""
            elif isinstance(value, str):
                entry[field] = value
            else:
                raise ModelDispatchConfigError(
                    f"model registry {registry_file}: model {model_id!r} field "
                    f"{field!r} must be a string"
                )
        return entry
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("step-ids")
    p.add_argument("config_file")
    p.add_argument("base")
    p.add_argument("step")

    p = sub.add_parser("entry")
    p.add_argument("registry_file")
    p.add_argument("model_id")

    args = parser.parse_args()
    try:
        if args.command == "step-ids":
            ids = get_step_model_ids(Path(args.config_file), args.base, args.step)
            if not ids:
                return 1
            print(f"{ids[0]}|{ids[1]}")
            return 0
        if args.command == "entry":
            entry = get_model_entry(Path(args.registry_file), args.model_id)
            if not entry:
                return 1
            print(US.join([
                entry["backend"],
                entry["model"],
                entry["effort"],
                entry["base_url"],
                entry["key_env"],
            ]))
            return 0
    except ModelDispatchConfigError as exc:
        # Exit 2 (not 1) keeps "corrupt configuration" distinguishable from
        # "no assignment" for callers that inspect the exit code.
        print(f"CONFIG_ERROR {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
