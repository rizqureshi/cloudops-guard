"""Loading and validation of the optional YAML audit configuration file."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_RESTART_THRESHOLD = 5


class AuditConfig(BaseModel):
    """Validated settings, whether loaded from YAML or supplied on the CLI.

    Command-line flags always take precedence over values loaded from a
    `--config` file: callers merge the two via `with_overrides`, which
    re-validates the merged result through this same model so both sources
    are held to identical rules.
    """

    model_config = ConfigDict(extra="forbid")

    namespace: str | None = None
    restart_threshold: int = Field(default=DEFAULT_RESTART_THRESHOLD, ge=1)

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("namespace must not be empty or whitespace-only")
        return stripped

    def with_overrides(
        self, *, namespace: str | None = None, restart_threshold: int | None = None
    ) -> AuditConfig:
        """Return a new, re-validated AuditConfig with CLI-supplied values applied.

        A None override leaves the corresponding value from this config unchanged.
        """
        return AuditConfig(
            namespace=namespace if namespace is not None else self.namespace,
            restart_threshold=(
                restart_threshold if restart_threshold is not None else self.restart_threshold
            ),
        )


def load_config(path: Path | None) -> AuditConfig:
    """Load an AuditConfig from a YAML file, or return defaults if no path is given.

    Raises FileNotFoundError if path is given but does not exist, ValueError if
    the file's content is not a YAML mapping, or pydantic.ValidationError if the
    mapping fails validation (unknown keys, invalid types, out-of-range values).
    """
    if path is None:
        return AuditConfig()

    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration file must contain a YAML mapping: {path}")

    return AuditConfig.model_validate(raw)
