"""Tests for AuditConfig loading, validation and CLI-override merging."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cloudops_guard.config import DEFAULT_RESTART_THRESHOLD, AuditConfig, load_config


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return path


def test_no_path_returns_defaults() -> None:
    config = load_config(None)
    assert config.namespace is None
    assert config.restart_threshold == DEFAULT_RESTART_THRESHOLD


def test_missing_file_raises_file_not_found_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_empty_yaml_file_yields_defaults(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "")
    config = load_config(path)
    assert config.namespace is None
    assert config.restart_threshold == DEFAULT_RESTART_THRESHOLD


def test_non_mapping_yaml_raises_value_error(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_config(path)


@pytest.mark.parametrize("threshold", [1, 2, 100])
def test_valid_restart_thresholds_are_accepted(threshold: int) -> None:
    config = AuditConfig(restart_threshold=threshold)
    assert config.restart_threshold == threshold


def test_restart_threshold_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuditConfig(restart_threshold=0)


def test_restart_threshold_negative_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuditConfig(restart_threshold=-1)


def test_restart_threshold_non_integer_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuditConfig(restart_threshold="five")


def test_restart_threshold_fractional_float_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuditConfig(restart_threshold=2.5)


def test_unknown_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "namespace: default\nnot_a_real_key: 5\n")
    with pytest.raises(ValidationError, match="not_a_real_key"):
        load_config(path)


def test_namespace_whitespace_is_stripped() -> None:
    config = AuditConfig(namespace="  payments  ")
    assert config.namespace == "payments"


@pytest.mark.parametrize("namespace", ["", "   "])
def test_empty_or_whitespace_namespace_is_rejected(namespace: str) -> None:
    with pytest.raises(ValidationError):
        AuditConfig(namespace=namespace)


def test_yaml_restart_threshold_zero_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "restart_threshold: 0\n")
    with pytest.raises(ValidationError):
        load_config(path)


# --- CLI-over-config precedence via with_overrides ---------------------------


def test_with_overrides_leaves_values_unset_when_none_given() -> None:
    base = AuditConfig(namespace="default", restart_threshold=7)
    merged = base.with_overrides(namespace=None, restart_threshold=None)
    assert merged.namespace == "default"
    assert merged.restart_threshold == 7


def test_with_overrides_cli_namespace_takes_precedence() -> None:
    base = AuditConfig(namespace="default")
    merged = base.with_overrides(namespace="payments", restart_threshold=None)
    assert merged.namespace == "payments"


def test_with_overrides_cli_restart_threshold_takes_precedence() -> None:
    base = AuditConfig(restart_threshold=5)
    merged = base.with_overrides(namespace=None, restart_threshold=10)
    assert merged.restart_threshold == 10


def test_with_overrides_revalidates_cli_supplied_values() -> None:
    base = AuditConfig()
    with pytest.raises(ValidationError):
        base.with_overrides(namespace=None, restart_threshold=0)


def test_with_overrides_revalidates_cli_supplied_namespace() -> None:
    base = AuditConfig()
    with pytest.raises(ValidationError):
        base.with_overrides(namespace="   ", restart_threshold=None)
