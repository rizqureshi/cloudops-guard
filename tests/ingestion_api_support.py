"""Shared test-only helpers for the `cloudops_guard.ingestion_api` test
suite -- not a test module itself (pytest's default collection pattern
`test_*.py`/`*_test.py` does not match this filename).

Uses `httpx.AsyncClient` with `httpx.ASGITransport` to drive the real ASGI
application in-process (no socket) for most tests, wrapped in
`run_async` so ordinary synchronous `def test_...()` functions can use it
without adding a pytest-asyncio/anyio plugin dependency. The dedicated
real-loopback-server concurrency suite (`test_ingestion_api_concurrency.py`)
uses `uvicorn` directly instead, since §13 requires genuine concurrent
HTTP requests against a real socket, not merely concurrent in-process
coroutines sharing one event loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import httpx
import uvicorn

from cloudops_guard.ingestion.models import TokenRecord, TokenScope
from cloudops_guard.ingestion.reference import (
    InMemoryAttemptLimiter,
    InMemoryMetadataStore,
    InMemoryReportBlobStore,
    InMemoryRequestRateLimiter,
    InMemoryTokenStore,
)
from cloudops_guard.ingestion.token_format import TOKEN_DELIMITER
from cloudops_guard.ingestion.token_issuance import generate_lookup_id, generate_secret
from cloudops_guard.ingestion_api.app import create_app
from cloudops_guard.ingestion_api.config import IngestionApiConfig

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def run_async[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


class FakeSecretVerifier:
    """A fast, deterministic, non-Argon2id `SecretVerifier` fake -- real
    Argon2id is exhaustively covered elsewhere
    (`test_ingestion_argon2_backend.py`); these HTTP-layer tests only need
    a fast, predictable pass/fail.
    """

    def __init__(self) -> None:
        self._expected_secret_by_hash: dict[str, str] = {}

    def register(self, secret_hash: str, expected_secret: str) -> None:
        self._expected_secret_by_hash[secret_hash] = expected_secret

    def __call__(self, presented_secret: str, secret_hash: str) -> bool:
        expected = self._expected_secret_by_hash.get(secret_hash)
        return expected is not None and presented_secret == expected


class MutableClock:
    """A test-only injectable clock -- starts at `T0`, advances only when
    `.advance()` is explicitly called, so tests get fully deterministic
    timestamps and can simulate the passage of arbitrary time (e.g. past
    the idempotency window or the retention period) without a real sleep.
    """

    def __init__(self, start: dt.datetime = T0) -> None:
        self._now = start

    def __call__(self) -> dt.datetime:
        return self._now

    def advance(self, delta: dt.timedelta) -> None:
        self._now = self._now + delta

    def set(self, value: dt.datetime) -> None:
        self._now = value


class DeterministicIdGenerator:
    """A test-only `ingestion_id_generator`/`request_id_generator`
    double -- returns `f"{prefix}{n}"` for an increasing `n`, so tests can
    assert on exact IDs and a `force_collision_once` hook can simulate a
    generated-ID collision deterministically (`coordinator.py`'s retry
    path) without relying on astronomically unlikely real UUID collisions.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0
        self._forced_values: list[str] = []

    def __call__(self) -> str:
        if self._forced_values:
            return self._forced_values.pop(0)
        self._counter += 1
        return f"{self._prefix}{self._counter}"

    def force_next(self, value: str) -> None:
        self._forced_values.append(value)


class _ServerThread(threading.Thread):
    """Runs a real `uvicorn` ASGI server on `127.0.0.1`, an OS-assigned
    ephemeral port -- never a public interface, never a fixed/predictable
    port. `uvicorn.Server.run()` creates and owns its own event loop for
    the lifetime of this thread; `should_exit` triggers a clean,
    deterministic shutdown.
    """

    def __init__(self, app: object) -> None:
        super().__init__(daemon=True)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")  # type: ignore[arg-type]
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        self.server.run()

    @property
    def port(self) -> int:
        return self.server.servers[0].sockets[0].getsockname()[1]


@contextmanager
def run_loopback_server(app: object) -> Iterator[str]:
    """Starts `app` on a real `127.0.0.1` ephemeral-port socket for the
    duration of the `with` block, yielding its base URL, then shuts it
    down deterministically -- required by §13, which specifically calls
    for genuine concurrent HTTP requests against a real running server,
    not merely concurrent in-process coroutines sharing one event loop.
    """
    thread = _ServerThread(app)
    thread.start()
    deadline = time.monotonic() + 10
    while not thread.server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("loopback uvicorn server did not start within 10 seconds.")
        time.sleep(0.005)
    try:
        yield f"http://127.0.0.1:{thread.port}"
    finally:
        thread.server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("loopback uvicorn server did not shut down within 10 seconds.")


class IngestionApiTestHarness:
    """Bundles a fresh `IngestionApiConfig`, its ASGI app, and a helper to
    provision a usable bearer token for a tenant -- one instance per test,
    never shared, so tests never leak state into one another.
    """

    def __init__(
        self,
        *,
        lookup_threshold: int = 1000,
        source_threshold: int = 1000,
        token_rate_threshold: int = 1000,
        capabilities_rate_threshold: int = 1000,
        retention_period: dt.timedelta = dt.timedelta(days=90),
    ) -> None:
        self.clock = MutableClock()
        self.verifier = FakeSecretVerifier()
        self.metadata_store = InMemoryMetadataStore(clock=self.clock)
        self.blob_store = InMemoryReportBlobStore()
        self.token_store = InMemoryTokenStore(secret_verifier=self.verifier)
        self.lookup_limiter = InMemoryAttemptLimiter(threshold=lookup_threshold)
        self.source_limiter = InMemoryAttemptLimiter(threshold=source_threshold)
        self.token_rate_limiter = InMemoryRequestRateLimiter(threshold=token_rate_threshold)
        self.capabilities_rate_limiter = InMemoryRequestRateLimiter(
            threshold=capabilities_rate_threshold
        )
        self.request_ids = DeterministicIdGenerator("req_test_")
        self.ingestion_ids = DeterministicIdGenerator("ing_test_")

        self.config = IngestionApiConfig(
            metadata_store=self.metadata_store,
            blob_store=self.blob_store,
            token_store=self.token_store,
            lookup_limiter=self.lookup_limiter,
            source_limiter=self.source_limiter,
            token_rate_limiter=self.token_rate_limiter,
            capabilities_rate_limiter=self.capabilities_rate_limiter,
            clock=self.clock,
            request_id_generator=self.request_ids,
            ingestion_id_generator=self.ingestion_ids,
            retention_period=retention_period,
        )
        self.app = create_app(self.config)

    def issue_token(
        self,
        tenant_id: str = "tenant-a",
        *,
        scopes: frozenset[TokenScope] = frozenset(
            {TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ, TokenScope.REPORTS_DELETE}
        ),
        revoked: bool = False,
    ) -> str:
        lookup_id = generate_lookup_id()
        secret = generate_secret()
        secret_hash = f"opaque-test-hash:{lookup_id}"
        self.verifier.register(secret_hash, secret)
        record = TokenRecord(
            lookup_id=lookup_id,
            secret_hash=secret_hash,
            tenant_id=tenant_id,
            scopes=scopes,
            revoked=revoked,
            created_at=self.clock(),
        )
        self.token_store.register_for_testing(record)
        return f"{lookup_id}{TOKEN_DELIMITER}{secret}"

    def client(
        self, *, client_host: str = "203.0.113.5", client_port: int = 12345
    ) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(client_host, client_port))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def with_client[T](
    harness: IngestionApiTestHarness,
    fn: Callable[[httpx.AsyncClient], Awaitable[T]],
    **client_kwargs: object,
) -> T:
    async def _run() -> T:
        async with harness.client(**client_kwargs) as client:  # type: ignore[arg-type]
            return await fn(client)

    return run_async(_run())


def valid_kubernetes_report(*, findings: list[dict] | None = None) -> dict:
    if findings is None:
        findings = [
            {
                "check_id": "K8S-IMG-001",
                "title": "Container uses the 'latest' tag",
                "severity": "high",
                "cluster_context": "prod",
                "namespace": "default",
                "resource_kind": "Pod",
                "resource_name": "web-abc123",
                "container_name": "web",
                "evidence": "image: nginx:latest",
                "impact": "Non-reproducible deployments.",
                "recommendation": "Pin to a specific tag or digest.",
                "auto_remediable": False,
                "audited_at": "2026-01-01T00:00:00Z",
            }
        ]
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity_counts[finding["severity"]] += 1
    return {
        "cluster_context": "prod",
        "namespace_filter": None,
        "generated_at": "2026-01-01T00:00:00Z",
        "findings": findings,
        "summary": severity_counts,
    }


def valid_gitlab_report(*, findings: list[dict] | None = None) -> dict:
    if findings is None:
        findings = [
            {
                "check_id": "GL-BR-001",
                "title": "Default branch is unprotected",
                "severity": "critical",
                "project_path": "group/project",
                "resource_kind": "ProtectedBranch",
                "resource_name": "main",
                "job_name": None,
                "evidence": "No protected branch rule matches 'main'.",
                "impact": "Anyone with push access can force-push to the default branch.",
                "recommendation": "Add a protected branch rule for 'main'.",
                "auto_remediable": False,
                "audited_at": "2026-01-01T00:00:00Z",
            }
        ]
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity_counts[finding["severity"]] += 1
    return {
        "platform": "gitlab",
        "gitlab_url": "https://gitlab.example.com",
        "project_id": 1,
        "project_path": "group/project",
        "default_branch": "main",
        "generated_at": "2026-01-01T00:00:00Z",
        "findings": findings,
        "summary": severity_counts,
    }
