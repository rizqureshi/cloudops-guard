"""Secret-hygiene regression tests for Phase 4C: no production
authentication module logs, prints, or otherwise persists a plaintext
secret or complete token; no CLI wiring exists to accept one as a
command-line argument; and no real credential is ever written to disk by
this test suite itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import cloudops_guard.ingestion as ingestion_package
from cloudops_guard.ingestion.models import TokenRecord

REPO_ROOT = Path(__file__).resolve().parent.parent
INGESTION_PACKAGE_DIR = Path(ingestion_package.__file__).resolve().parent

_AUTH_MODULE_NAMES = (
    "token_format.py",
    "token_issuance.py",
    "argon2_backend.py",
    "abuse_protection.py",
    "authenticator.py",
    "_secure_value.py",
)


def _auth_module_paths() -> list[Path]:
    return [INGESTION_PACKAGE_DIR / name for name in _AUTH_MODULE_NAMES]


class TestNoLoggingOrPrintingAnywhere:
    def test_no_print_call_in_any_auth_module(self) -> None:
        for path in _auth_module_paths():
            assert path.is_file(), f"expected file missing: {path}"
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "print", f"{path.name} calls print()"

    def test_no_logging_module_imported_by_any_auth_module(self) -> None:
        for path in _auth_module_paths():
            source = path.read_text()
            assert "import logging" not in source, f"{path.name} imports logging"

    def test_no_stdout_or_stderr_write_in_any_auth_module(self) -> None:
        for path in _auth_module_paths():
            source = path.read_text()
            assert "sys.stdout" not in source
            assert "sys.stderr" not in source


class TestNoCliArgumentWiring:
    def test_cli_module_does_not_reference_the_authentication_package(self) -> None:
        cli_path = REPO_ROOT / "src" / "cloudops_guard" / "cli.py"
        source = cli_path.read_text()
        for forbidden in (
            "provision_token",
            "AuthenticationCoordinator",
            "token_issuance",
            "authenticator",
            "Argon2SecretVerifier",
        ):
            assert forbidden not in source, f"cli.py references {forbidden!r}"

    def test_no_typer_option_or_argument_in_any_auth_module(self) -> None:
        # `typer.Option`/`typer.Argument` would be the pattern a CLI flag
        # uses in this codebase (see cli.py) -- their total absence here
        # confirms no command-line surface was added for provisioning.
        for path in _auth_module_paths():
            source = path.read_text()
            assert "typer" not in source, f"{path.name} references typer"


class TestNoRecoverableSecretInStoredState:
    def test_token_record_has_no_way_to_reconstruct_the_secret(self) -> None:
        # secret_hash is the only secret-derived field, and Argon2id
        # hashing is one-way by construction -- this is a structural
        # proof (field enumeration), not a cryptographic one; the
        # cryptographic one-wayness itself is Argon2id's own, well-
        # established property, not something this test suite re-proves.
        field_names = set(TokenRecord.model_fields)
        assert field_names == {
            "lookup_id",
            "secret_hash",
            "tenant_id",
            "scopes",
            "revoked",
            "created_at",
        }

    def test_no_module_defines_a_secret_recovery_or_decrypt_function(self) -> None:
        forbidden_name_fragments = ("decrypt", "recover_secret", "reverse_hash", "unhash")
        for path in _auth_module_paths():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lowered = node.name.lower()
                    for fragment in forbidden_name_fragments:
                        assert fragment not in lowered, (
                            f"{path.name} defines suspicious function {node.name!r}"
                        )
