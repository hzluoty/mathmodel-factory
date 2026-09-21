import json
from pathlib import Path


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_step_model_ids_prefers_project_override_and_supports_default_string(tmp_path):
    config = tmp_path / "model_config.json"
    write_json(
        config,
        {
            "_default": {
                "step_13": {"primary": "default-judge", "fallback": "default-backup"},
                "step_7": "default-eval",
            },
            "demo": {
                "step_13": {"primary": "project-judge"},
            },
        },
    )

    from scripts.model_dispatch_config import get_step_model_ids

    assert get_step_model_ids(config, "demo", "13") == ("project-judge", "")
    assert get_step_model_ids(config, "other", "13") == ("default-judge", "default-backup")
    assert get_step_model_ids(config, "other", "7") == ("default-eval", "")
    assert get_step_model_ids(config, "other", "5") is None


def test_model_entry_rejects_missing_and_disabled_models(tmp_path):
    registry = tmp_path / "model_registry.json"
    write_json(
        registry,
        {
            "models": [
                {"id": "codex-fast", "backend": "codex", "model": "gpt-5.5", "effort": "xhigh"},
                {"id": "old", "backend": "claude", "model": "haiku", "enabled": False},
            ]
        },
    )

    from scripts.model_dispatch_config import get_model_entry

    assert get_model_entry(registry, "codex-fast") == {
        "backend": "codex",
        "model": "gpt-5.5",
        "effort": "xhigh",
        "base_url": "",
        "key_env": "",
    }
    assert get_model_entry(registry, "old") is None
    assert get_model_entry(registry, "missing") is None


def test_missing_config_is_a_supported_default(tmp_path):
    from scripts.model_dispatch_config import get_model_entry, get_step_model_ids

    assert get_step_model_ids(tmp_path / "absent.json", "demo", "13") is None
    assert get_model_entry(tmp_path / "absent.json", "codex-fast") is None


def test_corrupt_config_raises_instead_of_reading_as_absent(tmp_path):
    import pytest

    from scripts.model_dispatch_config import (
        ModelDispatchConfigError,
        get_model_entry,
        get_step_model_ids,
    )

    config = tmp_path / "model_config.json"
    config.write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelDispatchConfigError, match="unreadable"):
        get_step_model_ids(config, "demo", "13")

    registry = tmp_path / "model_registry.json"
    registry.write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelDispatchConfigError, match="unreadable"):
        get_model_entry(registry, "codex-fast")


def test_malformed_config_structure_raises(tmp_path):
    import pytest

    from scripts.model_dispatch_config import (
        ModelDispatchConfigError,
        get_model_entry,
        get_step_model_ids,
    )

    not_object = tmp_path / "model_config.json"
    write_json(not_object, ["not", "an", "object"])
    with pytest.raises(ModelDispatchConfigError, match="must be a JSON object"):
        get_step_model_ids(not_object, "demo", "13")

    bad_scope = tmp_path / "bad_scope.json"
    write_json(bad_scope, {"demo": "project-judge"})
    with pytest.raises(ModelDispatchConfigError, match="must be a JSON object"):
        get_step_model_ids(bad_scope, "demo", "13")

    no_primary = tmp_path / "no_primary.json"
    write_json(no_primary, {"demo": {"step_13": {"fallback": "backup"}}})
    with pytest.raises(ModelDispatchConfigError, match="primary"):
        get_step_model_ids(no_primary, "demo", "13")

    registry = tmp_path / "model_registry.json"
    write_json(registry, {"models": "codex-fast"})
    with pytest.raises(ModelDispatchConfigError, match="list of models"):
        get_model_entry(registry, "codex-fast")

    wrong_type = tmp_path / "wrong_type.json"
    write_json(
        wrong_type,
        {"models": [{"id": "codex-fast", "backend": "codex", "model": 42}]},
    )
    with pytest.raises(ModelDispatchConfigError, match="must be a string"):
        get_model_entry(wrong_type, "codex-fast")


def test_explicitly_null_assignment_is_absence_not_corruption(tmp_path):
    from scripts.model_dispatch_config import get_step_model_ids

    config = tmp_path / "model_config.json"
    write_json(config, {"demo": {"step_13": None}})

    assert get_step_model_ids(config, "demo", "13") is None
