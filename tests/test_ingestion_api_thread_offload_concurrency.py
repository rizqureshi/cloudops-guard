"""Correction-pass item 5: proves blocking work genuinely runs off the
event loop, not merely that clients start requests "concurrently."
Real-loopback-server tests only (§13's own real-socket requirement
applies here too -- an in-process `ASGITransport` test shares a single
event loop with the application and would not distinguish "genuinely
threaded" from "accidentally still serialized").
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict, IngestionIdConflict
from cloudops_guard.ingestion_api.app import create_app
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    run_loopback_server,
    valid_kubernetes_report,
)

STRESS_RUNS = 20


class _OverlapTrackingMetadataStore:
    """Wraps a real `InMemoryMetadataStore`, instrumenting
    `create_or_get_received` with a `threading.Barrier` requiring
    `required_overlap` calls to be simultaneously *inside* this wrapper
    (past authentication/validation, about to call the real store) before
    any of them is allowed to proceed into it. This makes overlap
    deterministic and directly observable, rather than merely probable --
    the barrier itself cannot release until `required_overlap` distinct
    worker threads have genuinely, simultaneously reached this call.
    """

    def __init__(self, real_store: object, *, required_overlap: int) -> None:
        self._real = real_store
        self._barrier = threading.Barrier(required_overlap, timeout=10)
        self.max_observed_concurrent = 0
        self.call_count = 0
        self._active = 0
        self._active_lock = threading.Lock()

    def create_or_get_received(self, *args: object, **kwargs: object) -> object:
        with self._active_lock:
            self._active += 1
            self.call_count += 1
            self.max_observed_concurrent = max(self.max_observed_concurrent, self._active)
        try:
            # Blocks here until `required_overlap` calls have all
            # simultaneously arrived -- the only way this line is ever
            # reached by every waiting thread is if they were genuinely
            # running in parallel, off the event loop, at the same time.
            self._barrier.wait()
            return self._real.create_or_get_received(*args, **kwargs)  # type: ignore[attr-defined]
        except threading.BrokenBarrierError as exc:  # pragma: no cover - failure path
            raise RuntimeError(
                "not enough concurrent calls reached create_or_get_received in time -- "
                "blocking work is not actually running off the event loop"
            ) from exc
        finally:
            with self._active_lock:
                self._active -= 1

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


def _envelope(idempotency_key: str | None = None, cluster: str = "prod") -> dict:
    report = valid_kubernetes_report()
    report["cluster_context"] = cluster
    body: dict = {"platform": "kubernetes", "report_schema_version": 1, "report": report}
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return body


class TestGenuineOverlapInsideMetadataStore:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_at_least_two_requests_genuinely_overlap_inside_create_or_get_received(
        self, _run: int
    ) -> None:
        import dataclasses

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        tracking_store = _OverlapTrackingMetadataStore(harness.metadata_store, required_overlap=2)
        harness.config = dataclasses.replace(harness.config, metadata_store=tracking_store)
        harness.app = create_app(harness.config)

        # Two DISTINCT contents -- both requests must genuinely reach
        # create_or_get_received (a content-based dedup short-circuit
        # earlier would defeat the point of this test).
        envelopes = [_envelope(cluster="prod"), _envelope(cluster="staging")]

        async def post(client: httpx.AsyncClient, envelope: dict) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(envelope),
            )

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(*[post(client, env) for env in envelopes])

        responses = asyncio.run(scenario())
        assert all(r.status_code == 201 for r in responses)
        assert tracking_store.max_observed_concurrent >= 2
        assert tracking_store.call_count == 2


class TestResponsivenessUnderConcurrentSlowWork:
    def test_slow_verifier_does_not_block_a_concurrent_capabilities_request(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        real_verifier = harness.verifier
        slow_seconds = 1.0

        class _SlowVerifier:
            def __call__(self, presented_secret: str, secret_hash: str) -> bool:
                time.sleep(slow_seconds)
                return real_verifier(presented_secret, secret_hash)

        harness.token_store._secret_verifier = _SlowVerifier()  # type: ignore[attr-defined]

        async def do_slow_post(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(_envelope()),
            )

        async def do_fast_capabilities(client: httpx.AsyncClient) -> tuple[httpx.Response, float]:
            # Give the slow request a head start so it is genuinely
            # in-flight (already inside its worker thread) before this
            # one is issued.
            await asyncio.sleep(0.1)
            start = time.monotonic()
            resp = await client.get("/api/v1/capabilities")
            elapsed = time.monotonic() - start
            return resp, elapsed

        async def scenario() -> tuple[httpx.Response, httpx.Response, float]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    slow_task = asyncio.create_task(do_slow_post(client))
                    fast_result = await do_fast_capabilities(client)
                    slow_resp = await slow_task
                    return slow_resp, fast_result[0], fast_result[1]

        slow_resp, fast_resp, fast_elapsed = asyncio.run(scenario())
        assert slow_resp.status_code == 201
        assert fast_resp.status_code == 200
        # The capabilities request must complete well before the slow
        # verifier's artificial delay -- proving it was never queued
        # behind the slow request on a shared, blocked event loop.
        assert fast_elapsed < slow_seconds / 2


class TestMutationVerificationOfThreadOffload:
    """A deliberate, in-test "mutation" of the offload mechanism itself
    (never the production source file) -- an alternate ASGI app that
    calls the same blocking logic directly, inline, with no
    `anyio.to_thread.run_sync` -- proving `TestGenuineOverlapInsideMetadataStore`
    actually detects the absence of real concurrency rather than passing
    vacuously regardless of implementation.
    """

    def test_overlap_test_would_fail_without_thread_offload(self) -> None:
        import dataclasses

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        tracking_store = _OverlapTrackingMetadataStore(harness.metadata_store, required_overlap=2)
        harness.config = dataclasses.replace(harness.config, metadata_store=tracking_store)

        import cloudops_guard.ingestion_api.app as app_module

        # A hand-rolled ASGI app that dispatches through the SAME
        # synchronous handler logic but WITHOUT anyio.to_thread.run_sync
        # -- i.e. exactly what app.py looked like before this correction.
        async def inline_app(scope: dict, receive: object, send: object) -> None:
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()  # type: ignore[misc]
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})  # type: ignore[misc]
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})  # type: ignore[misc]
                        return
                return
            from starlette.requests import Request

            request = Request(scope, receive)  # type: ignore[arg-type]
            request_id = harness.config.request_id_generator()
            try:
                principal = app_module._authenticate_and_authorize_for_write(
                    request, harness.config
                )
                raw_body = await app_module.read_bounded_body(
                    request, app_module.MAX_REQUEST_BODY_BYTES
                )
                body, status_code = app_module._ingest_report_blocking(
                    harness.config, request_id, raw_body, principal
                )
                response = app_module.ok_response(body, status_code=status_code)
            except app_module.ApiError as exc:
                response = app_module.error_response(exc, request_id)
            except (IdempotencyKeyConflict, IngestionIdConflict):
                response = app_module.error_response(
                    app_module.ApiError(app_module.INVALID_REQUEST), request_id
                )
            await response(scope, receive, send)  # type: ignore[misc]

        envelopes = [_envelope(cluster="prod"), _envelope(cluster="staging")]

        async def post(client: httpx.AsyncClient, envelope: dict) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(envelope),
            )

        async def scenario() -> list[httpx.Response] | None:
            with run_loopback_server(inline_app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=3.0) as client:
                    try:
                        return await asyncio.gather(*[post(client, env) for env in envelopes])
                    except httpx.ReadTimeout:
                        return None

        # Without thread offload, both requests execute fully serially on
        # the one event loop; the second one's call into
        # create_or_get_received can never overlap with the first's
        # (which is itself blocked waiting on a 2-party barrier that a
        # concurrent call can never arrive at) -- so this deadlocks until
        # the barrier's own timeout, which this test observes as a
        # timeout/broken-barrier failure rather than two 201s.
        result = asyncio.run(scenario())
        if result is not None:
            assert not all(r.status_code == 201 for r in result)
