"""Loading and validation of the optional YAML audit configuration file."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_RESTART_THRESHOLD = 5


class AuditConfig(BaseModel):
    """Optional settings loaded from a YAML file.

    Command-line flags always take precedence over values loaded here.
    """

    namespace: str | None = None
    restart_threshold: int = DEFAULT_RESTART_THRESHOLD


def load_config(path: Path | None) -> AuditConfig:
    """Load an AuditConfig from a YAML file, or return defaults if no path is given."""
    if path is None:
        return AuditConfig()

    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration file must contain a YAML mapping: {path}")

    return AuditConfig.model_validate(raw)
