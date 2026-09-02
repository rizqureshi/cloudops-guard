"""Real-loopback-server concurrency tests (§13): genuine simultaneous
HTTP requests against a real running `uvicorn` server on `127.0.0.1`
(never only in-process function calls or a single event loop's
cooperative concurrency) -- required specifically because §13 calls out
that in-process concurrency alone is insufficient evidence for the
cross-store dedup and rate-limiting atomicity guarantees this phase adds.

Each test is repeated `STRESS_RUNS` times; any single run's failure fails
the test, and the actual run count is reported in the Phase 4D report
alongside an honest count of any flakes observed during development.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    run_loopback_server,
    valid_kubernetes_report,
)

STRESS_RUNS = 20
CONCURRENT_REQUEST_COUNT = 30


def _envelope(idempotency_key: str | None = None, report: dict | None = None) -> dict:
    body = {
        "platform": "kubernetes",
        "report_schema_version": 1,
        "report": report if report is not None else valid_kubernetes_report(),
    }
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return body


async def _post(client: httpx.AsyncClient, token: str, envelope: dict) -> httpx.Response:
    return await client.post(
        "/api/v1/reports",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        content=json.dumps(envelope),
    )


class TestConcurrentIdenticalPostsWithoutIdempotencyKey:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_exactly_one_201_rest_are_200_same_id_one_blob(self, _run: int) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        envelope = _envelope()

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(
                        *[_post(client, token, envelope) for _ in range(CONCURRENT_REQUEST_COUNT)]
                    )

        responses = asyncio.run(scenario())
        statuses = sorted(r.status_code for r in responses)
        assert statuses.count(201) == 1
        assert statuses.count(200) == CONCURRENT_REQUEST_COUNT - 1

        ingestion_ids = {r.json()["ingestion_id"] for r in responses}
        assert len(ingestion_ids) == 1

        # Exactly one retained blob, no loser-request blobs remain.
        winning_id = next(iter(ingestion_ids))
        assert harness.blob_store.get(f"tenant-a/{winning_id}") is not None
        stored_keys = [
            key
            for key in getattr(
                harness.blob_store, "_blobs", {}
            )  # reference-impl internals, test-only
            if key.startswith("tenant-a/")
        ]
        assert stored_keys == [f"tenant-a/{winning_id}"]


class TestConcurrentIdenticalPostsWithSameIdempotencyKey:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_exactly_one_201_rest_are_200_same_id(self, _run: int) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        envelope = _envelope(idempotency_key="race-key")

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(
                        *[_post(client, token, envelope) for _ in range(CONCURRENT_REQUEST_COUNT)]
                    )

        responses = asyncio.run(scenario())
        statuses = sorted(r.status_code for r in responses)
        assert statuses.count(201) == 1
        assert statuses.count(200) == CONCURRENT_REQUEST_COUNT - 1
        assert len({r.json()["ingestion_id"] for r in responses}) == 1


class TestSameKeyRacingDifferentFingerprints:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_exactly_one_winner_others_are_conflict_or_replay_never_both_created(
        self, _run: int
    ) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        envelopes = []
        for i in range(CONCURRENT_REQUEST_COUNT):
            report = valid_kubernetes_report()
            report["cluster_context"] = f"cluster-{i}"  # distinct fingerprint per request
            envelopes.append(_envelope(idempotency_key="race-key-conflict", report=report))

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(*[_post(client, token, env) for env in envelopes])

        responses = asyncio.run(scenario())
        statuses = [r.status_code for r in responses]
        # Exactly one request establishes the binding (201); every other
        # DIFFERENT-fingerprint request racing the same key is a
        # conflict (400) -- never a second 201/200.
        assert statuses.count(201) == 1
        assert statuses.count(400) == CONCURRENT_REQUEST_COUNT - 1
        assert statuses.count(200) == 0


class TestConcurrentGeneratedIdCollision:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_concurrent_requests_with_a_shared_id_generator_never_corrupt_state(
        self, _run: int
    ) -> None:
        # The first FIXED_SLOT_CALLS calls to this shared, thread-safe
        # generator (across every concurrent request) all return the
        # SAME fixed id -- forcing genuine put_if_absent-level collisions
        # under real concurrency, since multiple requests race for that
        # one storage key. Every call after that returns a fresh, unique
        # id, so a request that loses the race is guaranteed a unique id
        # on its very next attempt (well within
        # MAX_INGESTION_ID_GENERATION_ATTEMPTS) -- unlike an undersized
        # fixed pool, this can never make eventual success for all
        # requests mathematically impossible.
        import threading

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        counter_lock = threading.Lock()
        counter = {"n": 0}
        fixed_slot_id = "ing_collision_slot"
        fixed_slot_calls = 5

        def colliding_then_unique_id_generator() -> str:
            with counter_lock:
                counter["n"] += 1
                n = counter["n"]
            if n <= fixed_slot_calls:
                return fixed_slot_id
            return f"ing_unique_{n}"

        import dataclasses

        harness.config = dataclasses.replace(
            harness.config, ingestion_id_generator=colliding_then_unique_id_generator
        )
        from cloudops_guard.ingestion_api.app import create_app

        harness.app = create_app(harness.config)

        envelopes = []
        for i in range(CONCURRENT_REQUEST_COUNT):
            report = valid_kubernetes_report()
            report["cluster_context"] = f"cluster-{i}"
            envelopes.append(_envelope(report=report))

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(*[_post(client, token, env) for env in envelopes])

        responses = asyncio.run(scenario())
        # Every distinct-content request must still succeed (201) --
        # generated-ID collisions must be fully absorbed by retry, never
        # surfaced as a client-visible error, and every returned
        # ingestion_id must be genuinely distinct (no overwritten record).
        assert all(r.status_code == 201 for r in responses)
        ingestion_ids = [r.json()["ingestion_id"] for r in responses]
        assert len(set(ingestion_ids)) == CONCURRENT_REQUEST_COUNT
        # Non-vacuous: the fixed-slot id was actually assigned to exactly
        # one winning request -- proving a real collision-and-retry cycle
        # occurred, not merely that every id happened to be unique from
        # the start.
        assert fixed_slot_id in ingestion_ids


class TestConcurrentRequestLimitBoundary:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_configured_ceiling_is_never_exceeded_under_real_concurrent_http_load(
        self, _run: int
    ) -> None:
        threshold = 15
        harness = IngestionApiTestHarness(capabilities_rate_threshold=threshold)

        async def scenario() -> list[httpx.Response]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    return await asyncio.gather(
                        *[
                            client.get("/api/v1/capabilities")
                            for _ in range(CONCURRENT_REQUEST_COUNT)
                        ]
                    )

        responses = asyncio.run(scenario())
        statuses = [r.status_code for r in responses]
        assert statuses.count(200) == threshold
        assert statuses.count(429) == CONCURRENT_REQUEST_COUNT - threshold


class TestConcurrentGetDeleteAroundRetirement:
    @pytest.mark.parametrize("_run", range(STRESS_RUNS))
    def test_concurrent_get_and_delete_never_produce_an_inconsistent_view(self, _run: int) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")

        async def setup_and_race() -> tuple[list[httpx.Response], list[httpx.Response]]:
            with run_loopback_server(harness.app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                    post_resp = await _post(client, token, _envelope())
                    ingestion_id = post_resp.json()["ingestion_id"]
                    headers = {"Authorization": f"Bearer {token}"}

                    get_task = [
                        client.get(f"/api/v1/reports/{ingestion_id}", headers=headers)
                        for _ in range(CONCURRENT_REQUEST_COUNT // 2)
                    ]
                    delete_task = [
                        client.delete(f"/api/v1/reports/{ingestion_id}", headers=headers)
                        for _ in range(CONCURRENT_REQUEST_COUNT // 2)
                    ]
                    results = await asyncio.gather(*get_task, *delete_task)
                    gets = results[: CONCURRENT_REQUEST_COUNT // 2]
                    deletes = results[CONCURRENT_REQUEST_COUNT // 2 :]
                    return gets, deletes

        gets, deletes = asyncio.run(setup_and_race())

        # Every GET is either 200 (still received) or 404 (already
        # retired by a racing DELETE) -- never anything else.
        assert all(r.status_code in (200, 404) for r in gets)
        # Every DELETE succeeds (200) -- idempotent by design, so a
        # racing DELETE never fails outright.
        assert all(r.status_code == 200 for r in deletes)
        # All DELETEs that returned a reason agree on it (never two
        # different "true original" reasons for the same record).
        reasons = {r.json()["reason"] for r in deletes}
        assert reasons == {"customer_requested"}
