"""Keyed hashing for limiter scope keys before they are ever persisted to
PostgreSQL (Phase 4G-A, task 5: "Hash limiter scope keys with a keyed
construction before persistence so raw source IP addresses are not stored
as limiter keys").

A scope key (`abuse_protection.source_scope_key(...)`, etc.) can embed a
raw client IP address or a token's `lookup_id` -- neither should sit in a
database table in plaintext for the lifetime of the row (bounded, but
still real) if avoidable. HMAC-SHA256, not a bare hash, specifically so
the mapping is not offline-guessable by an attacker who obtains a copy of
the table without the key: a bare `sha256(scope_key)` is trivially
reversible for a small, guessable input space (an IPv4 address space is
enumerable); HMAC with a secret key is not, as long as the key itself
stays secret.

**The key**: comes from process configuration (`production_config.py`),
eventually sourced from Azure Key Vault (Phase 4G-B) -- never printed,
logged, or embedded in any exception message or log line anywhere in this
package.
"""

from __future__ import annotations

import hashlib
import hmac


def hash_scope_key(raw_scope_key: str, *, hmac_key: bytes) -> str:
    """Returns a fixed-length, hex-encoded HMAC-SHA256 of `raw_scope_key`
    -- deterministic (the same input always hashes to the same output
    under the same key, which is required: a limiter must recognize
    repeated activity from the same source), but not practically
    reversible without `hmac_key`.
    """
    if not hmac_key:
        raise ValueError("hmac_key must not be empty.")
    return hmac.new(hmac_key, raw_scope_key.encode("utf-8"), hashlib.sha256).hexdigest()
