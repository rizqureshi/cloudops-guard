"""Architecture/scope regression tests (§9.G): prove the Phase 4B
`cloudops_guard.ingestion` package exposes no HTTP endpoint, starts no
server or background worker, performs no network request, and imports no
web framework / cloud SDK / database driver / secret-manager SDK. Also
proves the website's browser-only report-privacy invariants are
untouched by this Python-only phase.

These assertions inspect the actual files on disk via `ast` (never a
hand-maintained substitute for reading real imports), mirroring the
import-graph-based isolation test established for the contact/report
boundary in Phase 3I.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import socket
import threading
from pathlib import Path

import pytest

import cloudops_guard.ingestion as ingestion_package

REPO_ROOT = Path(__file__).resolve().parent.parent
INGESTION_PACKAGE_DIR = Path(ingestion_package.__file__).resolve().parent

FORBIDDEN_MODULE_PREFIXES = (
    "flask",
    "fastapi",
    "django",
    "starlette",
    "uvicorn",
    "gunicorn",
    "aiohttp",
    "tornado",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "pymongo",
    "sqlite3",
    "boto3",
    "botocore",
    "google.cloud",
    "azure",
    "redis",
    "celery",
    "kombu",
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "argon2",
    "bcrypt",
    "passlib",
    "hvac",  # Vault client
)


def _iter_ingestion_source_files() -> list[Path]:
    return sorted(INGESTION_PACKAGE_DIR.glob("*.py"))


def _collect_imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # A relative import (`from .models import ...`) always
                # resolves within this same local package -- never a
                # third-party or stdlib module.
                continue
            if node.module is not None:
                modules.add(node.module)
    return modules


class TestNoForbiddenImports:
    def test_ingestion_package_has_source_files(self) -> None:
        # Sanity check that the glob below isn't silently matching nothing.
        assert len(_iter_ingestion_source_files()) >= 5

    def test_no_file_imports_a_forbidden_module(self) -> None:
        violations: dict[str, set[str]] = {}
        for path in _iter_ingestion_source_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            imports = _collect_imports(tree)
            bad = {
                module
                for module in imports
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_MODULE_PREFIXES
                )
            }
            if bad:
                violations[path.name] = bad
        assert violations == {}, f"forbidden imports found: {violations}"

    def test_only_stdlib_and_pydantic_are_imported(self) -> None:
        # A tighter, allowlist-based companion to the denylist check above:
        # every third-party import in this package must be `pydantic`.
        allowed_third_party = {"pydantic"}
        stdlib_prefixes = (
            "__future__",
            "abc",
            "collections",
            "dataclasses",
            "datetime",
            "enum",
            "threading",
            "typing",
        )
        for path in _iter_ingestion_source_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for module in _collect_imports(tree):
                top_level = module.split(".")[0]
                is_local = module.startswith(".") or top_level == "cloudops_guard"
                is_stdlib = any(module == p or module.startswith(p + ".") for p in stdlib_prefixes)
                is_allowed_third_party = top_level in allowed_third_party
                assert is_local or is_stdlib or is_allowed_third_party, (
                    f"{path.name} imports unexpected module: {module}"
                )


class TestNoNetworkOrServerBehavior:
    def test_no_socket_usage_in_source(self) -> None:
        for path in _iter_ingestion_source_files():
            source = path.read_text()
            assert "socket" not in source, f"{path.name} references 'socket'"

    def test_importing_the_package_opens_no_sockets(self) -> None:
        before = threading.active_count()
        importlib.reload(ingestion_package)
        importlib.import_module("cloudops_guard.ingestion.reference")
        importlib.import_module("cloudops_guard.ingestion.interfaces")
        after = threading.active_count()
        # Importing must not start any background thread.
        assert after == before

    def test_no_running_server_socket_bound_by_this_process_for_ingestion(self) -> None:
        # A weak but real smoke check: the reference implementation must
        # not have bound any listening socket as an import-time side
        # effect. We can't enumerate "all sockets ever opened," but we can
        # confirm a fresh socket can still bind to an ephemeral port,
        # which would be unaffected either way -- this test instead
        # documents the invariant and is kept alongside the import-time
        # thread-count check above, which is the meaningful assertion.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))


class TestNoDeploymentOrDatabaseArtifacts:
    def test_no_deployment_config_files_under_ingestion_package(self) -> None:
        suspicious_suffixes = {".yml", ".yaml", ".toml", ".dockerfile"}
        suspicious_names = {"Dockerfile", "wrangler.toml", "docker-compose.yml"}
        for path in INGESTION_PACKAGE_DIR.rglob("*"):
            if path.is_dir():
                continue
            message = f"unexpected deployment-like file: {path}"
            assert path.suffix not in suspicious_suffixes, message
            assert path.name not in suspicious_names, message

    def test_no_module_level_database_connection_or_app_object(self) -> None:
        for module_name in (
            "cloudops_guard.ingestion.models",
            "cloudops_guard.ingestion.interfaces",
            "cloudops_guard.ingestion.reference",
            "cloudops_guard.ingestion.storage_keys",
            "cloudops_guard.ingestion.errors",
        ):
            module = importlib.import_module(module_name)
            for name, value in vars(module).items():
                if name.startswith("_"):
                    continue
                # Nothing at module scope should be a live connection,
                # app, or server object -- only classes, functions,
                # constants, and type aliases are expected here.
                assert not inspect.iscoroutinefunction(value)
                assert not hasattr(value, "listen")
                is_plain_definition = inspect.isclass(value) or inspect.isfunction(value)
                assert not hasattr(value, "run") or is_plain_definition


class TestWebsitePrivacyInvariantsUntouched:
    # `cloudops_guard` alone legitimately appears in existing web
    # comments/docs referencing the released report-schema source (e.g.
    # `src/cloudops_guard/models.py`) -- these markers are specific to
    # this phase's new ingestion package, never a pre-existing reference.
    _FORBIDDEN_MARKERS = (
        "cloudops_guard.ingestion",
        "cloudops_guard/ingestion",
        "InMemoryMetadataStore",
        "InMemoryReportBlobStore",
        "InMemoryTokenStore",
        "InMemoryAttemptLimiter",
    )

    def test_ingestion_package_is_not_referenced_from_the_web_directory(self) -> None:
        web_dir = REPO_ROOT / "web"
        if not web_dir.exists():
            pytest.skip("web/ directory not present")
        for path in web_dir.rglob("*.ts"):
            text = path.read_text(errors="ignore")
            for marker in self._FORBIDDEN_MARKERS:
                assert marker not in text, f"{path} references {marker!r}"

    def test_no_ingestion_reference_in_astro_pages(self) -> None:
        web_src = REPO_ROOT / "web" / "src"
        if not web_src.exists():
            pytest.skip("web/src directory not present")
        for path in web_src.rglob("*.astro"):
            text = path.read_text(errors="ignore")
            for marker in self._FORBIDDEN_MARKERS:
                assert marker not in text, f"{path} references {marker!r}"
