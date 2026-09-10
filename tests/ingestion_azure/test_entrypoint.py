"""Tests for the Phase 4G-A production entrypoint (task 7/12 item 9):
import-time side effects, and fail-closed startup behavior.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cloudops_guard.ingestion_azure.entrypoint import _open_pools
from cloudops_guard.ingestion_azure.errors import (
    DatabaseUnavailableError,
    ProductionEnvironmentError,
)

_IMPORT_SIDE_EFFECT_PROBE = """
import socket
import threading

_orig_connect = socket.socket.connect
_orig_thread_start = threading.Thread.start

def _fail_connect(*a, **k):
    raise AssertionError("socket.connect called during import")

def _fail_thread(*a, **k):
    raise AssertionError("Thread.start called during import")

socket.socket.connect = _fail_connect
threading.Thread.start = _fail_thread

import cloudops_guard.ingestion_azure.entrypoint  # noqa: F401
import cloudops_guard.ingestion_azure.production_config  # noqa: F401
import cloudops_guard.ingestion_azure.pool  # noqa: F401
import cloudops_guard.ingestion_azure.postgres_metadata_store  # noqa: F401
import cloudops_guard.ingestion_azure.blob_store  # noqa: F401

print("OK: no socket/thread activity during import")
"""


def test_importing_entrypoint_and_adapters_performs_no_io() -> None:
    """Mirrors `ingestion_api.app.create_app`'s own "constructing performs
    no I/O" discipline, and this project's established import-time
    side-effect-check pattern (`tests/test_uploader_dependency_boundary.py`-
    adjacent conventions) -- run in a real, fresh subprocess so the
    monkeypatched `socket.connect`/`Thread.start` apply to a completely
    clean interpreter state.
    """
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_SIDE_EFFECT_PROBE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"import-time side effect detected (stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "OK" in result.stdout


class TestFailClosedStartup:
    def test_missing_environment_fails_before_any_pool_is_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in list(__import__("os").environ):
            if name.startswith("COG_"):
                monkeypatch.delenv(name, raising=False)
        from cloudops_guard.ingestion_azure.entrypoint import build_production_config

        with pytest.raises(ProductionEnvironmentError):
            build_production_config()

    def test_unreachable_database_fails_closed_not_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A syntactically valid but genuinely unreachable database
        connection string must raise `DatabaseUnavailableError` from
        `pool.open()` -- this process must never proceed to construct an
        `IngestionApiConfig`, let alone serve traffic, on the strength of
        adapters it has not actually confirmed can reach their backing
        store.
        """
        # Find a local TCP port nothing is listening on.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            unused_port = probe.getsockname()[1]

        env = {
            "COG_INGESTION_REGION": "canadacentral",
            "COG_METADATA_DB_CONNINFO": f"postgresql://user:pass@127.0.0.1:{unused_port}/db",
            "COG_TOKEN_DB_CONNINFO": f"postgresql://user:pass@127.0.0.1:{unused_port}/db",
            "COG_BLOB_ACCOUNT_URL": "https://example.blob.core.windows.net",
            "COG_BLOB_CONTAINER_NAME": "reports",
            "COG_LIMITER_DB_CONNINFO": f"postgresql://user:pass@127.0.0.1:{unused_port}/db",
            "COG_LIMITER_HMAC_KEY": "x" * 32,
            "COG_RETENTION_PERIOD_SECONDS": "7776000",
            "COG_LOOKUP_LIMITER_THRESHOLD": "10",
            "COG_LOOKUP_LIMITER_WINDOW_SECONDS": "900",
            "COG_SOURCE_LIMITER_THRESHOLD": "30",
            "COG_SOURCE_LIMITER_WINDOW_SECONDS": "300",
            "COG_TOKEN_RATE_LIMITER_THRESHOLD": "60",
            "COG_TOKEN_RATE_LIMITER_WINDOW_SECONDS": "60",
            "COG_CAPABILITIES_RATE_LIMITER_THRESHOLD": "60",
            "COG_CAPABILITIES_RATE_LIMITER_WINDOW_SECONDS": "60",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        from cloudops_guard.ingestion_azure.entrypoint import build_production_config
        from cloudops_guard.ingestion_azure.pool import IngestionDatabasePool

        monkeypatch.setattr(
            IngestionDatabasePool,
            "open",
            lambda self: (_ for _ in ()).throw(DatabaseUnavailableError("simulated: unreachable")),
        )

        with pytest.raises(DatabaseUnavailableError):
            build_production_config()


class _FakePool:
    """A minimal `IngestionDatabasePool`-shaped double -- `_open_pools`
    only calls `.open()`/`.close()`, never any other method, so a full
    `IngestionDatabasePool` (which would require a real `conninfo` and
    real connectivity) is unnecessary here.
    """

    def __init__(self, name: str, *, fail_open: bool = False) -> None:
        self.name = name
        self.fail_open = fail_open
        self.open_calls = 0
        self.close_calls = 0

    def open(self) -> None:
        self.open_calls += 1
        if self.fail_open:
            raise DatabaseUnavailableError(f"simulated failure opening {self.name}")

    def close(self) -> None:
        self.close_calls += 1


class TestOpenPoolsPartialFailureCleanup:
    """Correction-pass item 9: if opening the second or third pool fails,
    every pool already opened by this same call must be closed before the
    exception propagates -- the original sequence (three unconditional
    `.open()` calls in a row) left earlier pools' connections leaked on
    exactly this failure. Covers all three failure positions plus the
    all-succeed path.
    """

    def test_metadata_pool_failure_opens_and_closes_nothing_else(self) -> None:
        metadata = _FakePool("metadata", fail_open=True)
        token = _FakePool("token")
        limiter = _FakePool("limiter")
        adapters = SimpleNamespace(metadata_pool=metadata, token_pool=token, limiter_pool=limiter)

        with pytest.raises(DatabaseUnavailableError):
            _open_pools(adapters)  # type: ignore[arg-type]

        assert (metadata.open_calls, token.open_calls, limiter.open_calls) == (1, 0, 0)
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (0, 0, 0)

    def test_token_pool_failure_closes_the_already_opened_metadata_pool(self) -> None:
        metadata = _FakePool("metadata")
        token = _FakePool("token", fail_open=True)
        limiter = _FakePool("limiter")
        adapters = SimpleNamespace(metadata_pool=metadata, token_pool=token, limiter_pool=limiter)

        with pytest.raises(DatabaseUnavailableError):
            _open_pools(adapters)  # type: ignore[arg-type]

        assert (metadata.open_calls, token.open_calls, limiter.open_calls) == (1, 1, 0)
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (1, 0, 0)

    def test_limiter_pool_failure_closes_both_already_opened_pools(self) -> None:
        metadata = _FakePool("metadata")
        token = _FakePool("token")
        limiter = _FakePool("limiter", fail_open=True)
        adapters = SimpleNamespace(metadata_pool=metadata, token_pool=token, limiter_pool=limiter)

        with pytest.raises(DatabaseUnavailableError):
            _open_pools(adapters)  # type: ignore[arg-type]

        assert (metadata.open_calls, token.open_calls, limiter.open_calls) == (1, 1, 1)
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (1, 1, 0)

    def test_all_pools_opening_successfully_closes_none_of_them(self) -> None:
        metadata = _FakePool("metadata")
        token = _FakePool("token")
        limiter = _FakePool("limiter")
        adapters = SimpleNamespace(metadata_pool=metadata, token_pool=token, limiter_pool=limiter)

        _open_pools(adapters)  # type: ignore[arg-type]

        assert (metadata.open_calls, token.open_calls, limiter.open_calls) == (1, 1, 1)
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (0, 0, 0)


class TestPostOpenStartupFailureClosesAllPools:
    """Correction pass, item 8: `_open_pools` itself was already fixed to
    close every already-opened pool if a later pool's own `.open()` call
    fails (the class above). But `build_production_config` still had a
    real, reproduced gap: once all three pools are open, a failure while
    constructing `IngestionApiConfig` or during `validate_production_
    config` propagated straight out of `build_production_config` with no
    cleanup at all -- `run()`'s own pool-closing `try/finally` only wraps
    `server.run()`, which never starts if `build_production_config`
    itself raises. Reproduced directly (a real ad hoc script, not just
    this test) before this fix: three real fake pools all opened, zero
    closed, after a simulated `validate_production_config` failure.
    """

    def _adapters(self, metadata: _FakePool, token: _FakePool, limiter: _FakePool):
        return SimpleNamespace(
            metadata_pool=metadata,
            token_pool=token,
            limiter_pool=limiter,
            metadata_store=object(),
            blob_store=object(),
            token_store=object(),
            lookup_limiter=object(),
            source_limiter=object(),
            token_rate_limiter=object(),
            capabilities_rate_limiter=object(),
            retention_period=object(),
        )

    def test_validate_production_config_failure_closes_all_three_pools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cloudops_guard.ingestion_azure.entrypoint as entrypoint_mod

        metadata, token, limiter = _FakePool("metadata"), _FakePool("token"), _FakePool("limiter")
        adapters = self._adapters(metadata, token, limiter)

        monkeypatch.setattr(entrypoint_mod, "load_settings_from_environment", lambda: object())
        monkeypatch.setattr(entrypoint_mod, "build_production_adapters", lambda settings: adapters)
        monkeypatch.setattr(
            entrypoint_mod,
            "validate_production_config",
            lambda config: (_ for _ in ()).throw(RuntimeError("simulated post-open failure")),
        )

        with pytest.raises(RuntimeError, match="simulated post-open failure"):
            entrypoint_mod.build_production_config()

        assert (metadata.open_calls, token.open_calls, limiter.open_calls) == (1, 1, 1)
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (1, 1, 1)

    def test_config_construction_failure_closes_all_three_pools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure constructing `IngestionApiConfig` itself (not just
        the later `validate_production_config` call) must be covered by
        the same cleanup -- both live inside the same `try` block.
        """
        import cloudops_guard.ingestion_azure.entrypoint as entrypoint_mod

        metadata, token, limiter = _FakePool("metadata"), _FakePool("token"), _FakePool("limiter")
        adapters = self._adapters(metadata, token, limiter)

        monkeypatch.setattr(entrypoint_mod, "load_settings_from_environment", lambda: object())
        monkeypatch.setattr(entrypoint_mod, "build_production_adapters", lambda settings: adapters)

        def _raising_config(*args, **kwargs):
            raise ValueError("simulated IngestionApiConfig construction failure")

        monkeypatch.setattr(entrypoint_mod, "IngestionApiConfig", _raising_config)

        with pytest.raises(ValueError, match="simulated IngestionApiConfig construction failure"):
            entrypoint_mod.build_production_config()

        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (1, 1, 1)

    def test_success_path_closes_no_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Companion proving the fix didn't make cleanup fire on the
        ordinary, successful path too."""
        import cloudops_guard.ingestion_azure.entrypoint as entrypoint_mod

        metadata, token, limiter = _FakePool("metadata"), _FakePool("token"), _FakePool("limiter")
        adapters = self._adapters(metadata, token, limiter)

        monkeypatch.setattr(entrypoint_mod, "load_settings_from_environment", lambda: object())
        monkeypatch.setattr(entrypoint_mod, "build_production_adapters", lambda settings: adapters)
        monkeypatch.setattr(entrypoint_mod, "validate_production_config", lambda config: None)

        config, returned_adapters = entrypoint_mod.build_production_config()

        assert returned_adapters is adapters
        assert (metadata.close_calls, token.close_calls, limiter.close_calls) == (0, 0, 0)
