"""Ingestion-token acquisition for `cloudops-guard upload`.

The only credential source in this phase: the `CLOUDOPS_GUARD_INGESTION_TOKEN`
environment variable -- never a CLI option, never a config file, never a
URL, query string, or request body field. This module is imported and
called only *after* confirmation succeeds (or, on the `--yes` path, only
after every local-validation step has already passed) -- see
`service.py`'s own ordering. Never called at all by `--dry-run`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from cloudops_guard.ingestion.errors import TokenFormatError
from cloudops_guard.ingestion.token_format import parse_token

from .errors import CredentialError

INGESTION_TOKEN_ENV_VAR = "CLOUDOPS_GUARD_INGESTION_TOKEN"


def load_ingestion_token(env: Mapping[str, str] | None = None) -> str:
    """Reads and structurally validates the ingestion token from
    `CLOUDOPS_GUARD_INGESTION_TOKEN`.

    `env` is injectable for deterministic tests; it defaults to the real
    process environment. Returns the token exactly as read (never
    stripped or otherwise transformed) once its `<lookup_id>.<secret>`
    structure has been confirmed valid via the existing Phase 4C
    `token_format.parse_token` -- structural validation only, never a
    network lookup. Raises `CredentialError` if the variable is unset,
    empty, or structurally malformed; never includes the presented value
    or any substring of it in the raised message (matching
    `TokenFormatError`'s own guarantee, which this function's message
    deliberately does not even repeat verbatim, to avoid depending on
    that message never changing to include more detail in the future).
    """
    environment = env if env is not None else os.environ
    value = environment.get(INGESTION_TOKEN_ENV_VAR)
    if value is None:
        raise CredentialError(
            f"{INGESTION_TOKEN_ENV_VAR} is not set. Set it to an ingestion API bearer token."
        )
    if not value.strip():
        raise CredentialError(f"{INGESTION_TOKEN_ENV_VAR} is set but empty.")
    try:
        parse_token(value)
    except TokenFormatError:
        raise CredentialError(
            f"{INGESTION_TOKEN_ENV_VAR} is not a validly structured ingestion API token."
        ) from None
    return value
