"""A real PostgreSQL `TokenStore` (`docs/milestones/v0.4.0-ingestion-api.md`
§F, Phase 4G-A). `lookup_id` is the real, indexed, plaintext primary key
(`tokens.lookup_id`); `verify_secret` performs no storage access at all --
it delegates entirely to an injected `SecretVerifier`, exactly as
`InMemoryTokenStore` does, so real Argon2id verification
(`cloudops_guard.ingestion.argon2_backend.Argon2SecretVerifier`) is reused
unchanged rather than re-implemented against this store.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from psycopg.rows import dict_row

from cloudops_guard.ingestion.interfaces import SecretVerifier, TokenStore
from cloudops_guard.ingestion.models import TokenRecord, TokenScope

from .errors import DatabaseUnavailableError
from .pool import IngestionDatabasePool


def _row_to_token_record(row: dict) -> TokenRecord:
    return TokenRecord(
        lookup_id=row["lookup_id"],
        secret_hash=row["secret_hash"],
        tenant_id=row["tenant_id"],
        scopes=frozenset(TokenScope(value) for value in row["scopes"]),
        revoked=row["revoked"],
        created_at=row["created_at"],
    )


class PostgresTokenStore(TokenStore):
    def __init__(self, pool: IngestionDatabasePool, secret_verifier: SecretVerifier) -> None:
        self._pool = pool
        self._secret_verifier = secret_verifier

    def lookup(self, lookup_id: str) -> TokenRecord | None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT lookup_id, secret_hash, tenant_id, scopes, revoked, created_at "
                        "FROM tokens WHERE lookup_id = %s",
                        (lookup_id,),
                    )
                    row = cur.fetchone()
                    return _row_to_token_record(row) if row is not None else None
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def verify_secret(self, presented_secret: str, secret_hash: str) -> bool:
        # Pure delegation, no database access -- identical contract to
        # InMemoryTokenStore.verify_secret.
        return self._secret_verifier(presented_secret, secret_hash)

    def mark_revoked(self, lookup_id: str) -> None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tokens SET revoked = true WHERE lookup_id = %s", (lookup_id,)
                    )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc

    def provision_for_operator(
        self,
        *,
        lookup_id: str,
        secret_hash: str,
        tenant_id: str,
        scopes: frozenset[TokenScope],
        created_at: dt.datetime,
    ) -> None:
        """**Not** part of the `TokenStore` interface -- the real-store
        analogue of `InMemoryTokenStore.register_for_testing`, used by the
        existing manual, out-of-band provisioning procedure
        (`docs/manual-token-provisioning.md`) once a real operator role
        (distinct from the application's own narrower runtime role, task
        4/9's privilege-separation requirement) inserts a token this
        function's caller already generated and hashed via
        `token_issuance.provision_token`. This function never generates a
        secret, hashes anything, or reads a plaintext secret -- it only
        persists an already-complete, already-validated `TokenRecord`'s
        fields.
        """
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash=secret_hash,
            tenant_id=tenant_id,
            scopes=scopes,
            revoked=False,
            created_at=created_at,
        )
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tokens
                            (lookup_id, secret_hash, tenant_id, scopes, revoked, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            record.lookup_id,
                            record.secret_hash,
                            record.tenant_id,
                            [scope.value for scope in record.scopes],
                            record.revoked,
                            record.created_at,
                        ),
                    )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(str(exc.__class__.__name__)) from exc
