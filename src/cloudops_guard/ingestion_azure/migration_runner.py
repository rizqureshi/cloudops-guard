"""Forward-only, versioned PostgreSQL schema migrations (Phase 4G-A, task
8). Migration *files* live under `migrations/` (packaged into the wheel
via `pyproject.toml`'s hatchling package-data config, verified by
`tests/ingestion_azure/test_wheel_packaging.py`); this module is the
runner that applies them.

**Serialized with an advisory lock**: `pg_advisory_lock` (a
session-level, PostgreSQL-native lock, released automatically if the
holding session disconnects) around the whole run, so two replicas
starting up concurrently -- or a manual `migrate` invocation racing an
application startup -- never apply migrations concurrently. A second
caller blocks on the lock rather than racing; once it acquires the lock
in turn, every migration is already applied, and it correctly does
nothing.

**Safe to run more than once**: `schema_migrations` records which
versions have already been applied; an already-applied version is
skipped (with its recorded checksum re-verified against the current file
content, so a Phase 4G-B human error -- editing an already-shipped
migration file in place instead of adding a new one -- is caught loudly
rather than silently ignored).

**Forward-only**: there is no `down`/rollback migration mechanism. A
schema mistake is fixed by a new, additive migration, never by editing or
removing a previously-applied one -- consistent with §9's rollback
requirement that an application-tier rollback never require a
coordinated data-tier rollback.

**Privilege separation (task 4's requirement)**: this module is intended
to be run under a distinct, migration-only database role (documented in
`docs/deployment/azure-ingestion-production.md`), with `CREATE`/`ALTER`
privilege on the application schema -- never the same, narrower runtime
role the production entrypoint's own connection pool uses (which needs
only `SELECT`/`INSERT`/`UPDATE`/`DELETE` on the tables these migrations
create, never `CREATE TABLE`). This module does not itself enforce that
separation (PostgreSQL's own role/grant system does); it exists here as a
documented operational requirement for whoever configures the connection
string this module is run with.
"""

from __future__ import annotations

import hashlib
import importlib.resources
from dataclasses import dataclass

import psycopg

from .errors import MigrationError

#: A fixed, arbitrary key identifying this application's migration lock,
#: never reused for any other purpose -- `pg_advisory_lock` keys are a
#: shared namespace across the whole database, so a second, unrelated
#: application sharing this database (not expected in this pilot's
#: design, §8: "its own schema/credential") would need a different key.
#: Must fit in a signed 64-bit integer (PostgreSQL's `bigint`).
_MIGRATION_LOCK_KEY = 0x434F475F494E  # fits comfortably within int8's range

_ANCHOR_PACKAGE = "cloudops_guard.ingestion_azure"
_MIGRATIONS_SUBDIR = "migrations"

_CREATE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class MigrationFile:
    version: int
    name: str
    sql: str
    checksum: str


def _parse_migration_filename(filename: str) -> tuple[int, str] | None:
    """Returns `(version, name)` for a filename like
    `0001_metadata_store.sql`, or `None` for anything that doesn't match
    this package's own naming convention (e.g. `__init__.py`, if one is
    ever added, or an editor swap file).
    """
    if not filename.endswith(".sql"):
        return None
    stem = filename[: -len(".sql")]
    prefix, _, name = stem.partition("_")
    if not prefix.isdigit() or not name:
        return None
    return int(prefix), name


def load_migration_files() -> list[MigrationFile]:
    """Loads every migration file packaged with this application, sorted
    by version, ascending. Reads from the package's own installed
    location (`importlib.resources`) -- never a filesystem path relative
    to the current working directory -- so this works identically whether
    running from a source checkout or an installed wheel (verified by
    `tests/ingestion_azure/test_wheel_packaging.py`).
    """
    package_files = importlib.resources.files(_ANCHOR_PACKAGE) / _MIGRATIONS_SUBDIR
    migrations: list[MigrationFile] = []
    for entry in package_files.iterdir():
        if not entry.is_file():
            continue
        parsed = _parse_migration_filename(entry.name)
        if parsed is None:
            continue
        version, name = parsed
        sql = entry.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(MigrationFile(version=version, name=name, sql=sql, checksum=checksum))

    migrations.sort(key=lambda m: m.version)
    seen_versions = set()
    for migration in migrations:
        if migration.version in seen_versions:
            raise MigrationError(f"duplicate migration version {migration.version}.")
        seen_versions.add(migration.version)
    return migrations


def run_migrations(conninfo: str) -> list[int]:
    """Applies every not-yet-applied migration, in version order, under
    the advisory lock. Returns the list of versions actually applied by
    *this* call (empty if the schema was already current). Raises
    `MigrationError` if an already-applied migration's file content no
    longer matches its recorded checksum, or if a migration fails to
    apply (the failing migration's own transaction is rolled back;
    earlier, already-committed migrations from this same call are not
    undone -- consistent with "forward-only," this is not a rollback
    mechanism).
    """
    migrations = load_migration_files()

    with psycopg.connect(conninfo, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Session-level advisory lock: held for the connection's
            # lifetime, released automatically on disconnect even if this
            # process crashes mid-migration -- never left dangling.
            cur.execute("SELECT pg_advisory_lock(%s::bigint)", (_MIGRATION_LOCK_KEY,))
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TRACKING_TABLE_SQL)
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT version, checksum FROM schema_migrations")
                applied: dict[int, str] = dict(cur.fetchall())

            newly_applied: list[int] = []
            for migration in migrations:
                if migration.version in applied:
                    if applied[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version} ({migration.name}) has already "
                            "been applied with different content than what is currently "
                            "packaged -- an already-shipped migration must never be edited "
                            "in place; add a new migration instead."
                        )
                    continue

                with conn.cursor() as cur:
                    cur.execute(migration.sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                conn.commit()
                newly_applied.append(migration.version)

            return newly_applied
        except Exception:
            conn.rollback()
            raise
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s::bigint)", (_MIGRATION_LOCK_KEY,))
            conn.commit()


def migration_status(conninfo: str) -> dict[str, object]:
    """Read-only status report (task 11's "migration status/check"
    offline command): which versions are packaged, which are applied, and
    whether they match. Never mutates anything, never takes the advisory
    lock (a status check must never block on, or be blocked by, a real
    migration run).

    **Correction pass (rollback safety)**: also reports
    `unexpected_applied_versions` -- versions recorded as applied in the
    database's own `schema_migrations` table that are **not present at
    all** in this candidate's packaged migration files. This is the
    exact signal a rollback to an older image needs: forward-only
    migrations mean an older candidate's packaged set is a *subset* of
    what a newer, already-deployed image applied, so rolling back to it
    while the database still carries a later version's schema is
    genuinely incompatible -- the older code was never written to
    understand that schema. `up_to_date` is `False` whenever this list is
    non-empty, exactly like `pending_versions`/`drifted_versions`.
    """
    migrations = load_migration_files()
    with psycopg.connect(conninfo, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('schema_migrations') IS NOT NULL",
            )
            row = cur.fetchone()
            table_exists = bool(row[0]) if row is not None else False
            applied: dict[int, str] = {}
            if table_exists:
                cur.execute("SELECT version, checksum FROM schema_migrations")
                applied = dict(cur.fetchall())

    packaged_version_set = {m.version for m in migrations}
    pending = [m.version for m in migrations if m.version not in applied]
    drifted = [
        m.version for m in migrations if m.version in applied and applied[m.version] != m.checksum
    ]
    unexpected_applied = sorted(v for v in applied if v not in packaged_version_set)
    return {
        "packaged_versions": [m.version for m in migrations],
        "applied_versions": sorted(applied),
        "pending_versions": pending,
        "drifted_versions": drifted,
        "unexpected_applied_versions": unexpected_applied,
        "up_to_date": not pending and not drifted and not unexpected_applied,
    }
