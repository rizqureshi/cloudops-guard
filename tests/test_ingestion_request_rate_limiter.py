"""Tests for `InMemoryRequestRateLimiter` (Phase 4D correction, §H's
`RequestRateLimiter`): a minimal deterministic reference primitive with no
time dimension -- a fixed per-scope-key request count compared, atomically,
against a fixed configured threshold. Deliberately distinct from
`InMemoryAttemptLimiter` (see `interfaces.RequestRateLimiter`'s docstring):
this counts *ordinary* requests, not failures.
"""

from __future__ import annotations

import threading

import pytest

from cloudops_guard.ingestion.reference import InMemoryRequestRateLimiter


class TestThresholdValidation:
    def test_threshold_below_one_raises(self) -> None:
        with pytest.raises(ValueError):
            InMemoryRequestRateLimiter(threshold=0)

    def test_threshold_of_one_is_allowed(self) -> None:
        InMemoryRequestRateLimiter(threshold=1)


class TestDeterministicThresholdBehavior:
    def test_allowed_and_counted_while_under_threshold(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=3)
        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is True

    def test_rejected_once_threshold_is_reached(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=3)
        for _ in range(3):
            assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is False

    def test_a_rejected_request_is_not_itself_counted(self) -> None:
        # A caller already over budget must not be pushed further into
        # debt by its own rejected calls -- rejecting call N+1 must not
        # change whether call N+2 is also rejected for a reason other
        # than "still over the original threshold."
        limiter = InMemoryRequestRateLimiter(threshold=1)
        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is False
        assert limiter.check_and_record_request("scope-a") is False

    def test_remains_rejected_past_threshold(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=2)
        for _ in range(5):
            limiter.check_and_record_request("scope-a")
        assert limiter.check_and_record_request("scope-a") is False


class TestScopeIndependence:
    def test_one_scope_key_never_affects_another(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=1)
        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is False
        assert limiter.check_and_record_request("scope-b") is True

    def test_layer_style_scope_keys_are_independent_strings(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=1)
        assert limiter.check_and_record_request("token:abc") is True
        assert limiter.check_and_record_request("token:abc") is False
        assert limiter.check_and_record_request("source:abc") is True


class TestResetForTesting:
    def test_reset_clears_a_scopes_request_count(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=1)
        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-a") is False

        limiter.reset_for_testing("scope-a")

        assert limiter.check_and_record_request("scope-a") is True

    def test_reset_of_unknown_scope_is_safe(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=1)
        limiter.reset_for_testing("never-seen")  # must not raise

    def test_reset_does_not_affect_other_scopes(self) -> None:
        limiter = InMemoryRequestRateLimiter(threshold=1)
        limiter.check_and_record_request("scope-a")
        limiter.check_and_record_request("scope-b")

        limiter.reset_for_testing("scope-a")

        assert limiter.check_and_record_request("scope-a") is True
        assert limiter.check_and_record_request("scope-b") is False


class TestConcurrentBoundary:
    def test_configured_ceiling_is_never_exceeded_by_racing_requests(self) -> None:
        # The core atomicity guarantee (§H's own reasoning for
        # `create_or_get_received`, applied here): under many threads
        # racing `check_and_record_request` for the same scope key, the
        # number of `True` results must never exceed the configured
        # threshold, and must never fall short of it either (no lost
        # updates in the other direction).
        threshold = 50
        thread_count = 400
        limiter = InMemoryRequestRateLimiter(threshold=threshold)
        results: list[bool] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(thread_count)

        def worker() -> None:
            barrier.wait()
            allowed = limiter.check_and_record_request("shared-scope")
            with results_lock:
                results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == threshold
        assert results.count(False) == thread_count - threshold

    def test_concurrent_requests_across_distinct_scopes_do_not_interfere(self) -> None:
        threshold = 10
        scopes = [f"scope-{i}" for i in range(20)]
        limiter = InMemoryRequestRateLimiter(threshold=threshold)
        barrier = threading.Barrier(len(scopes) * threshold)
        results_lock = threading.Lock()
        results_by_scope: dict[str, int] = dict.fromkeys(scopes, 0)

        def worker(scope: str) -> None:
            barrier.wait()
            if limiter.check_and_record_request(scope):
                with results_lock:
                    results_by_scope[scope] += 1

        threads = [
            threading.Thread(target=worker, args=(scope,))
            for scope in scopes
            for _ in range(threshold)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(count == threshold for count in results_by_scope.values())
