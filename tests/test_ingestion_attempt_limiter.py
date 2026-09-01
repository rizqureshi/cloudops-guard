"""Tests for `InMemoryAttemptLimiter`: a minimal deterministic reference
primitive with no time dimension and no auth/rate-limiting-product
behavior -- just an independent, per-scope-key failure count compared
against a fixed configured threshold.
"""

from __future__ import annotations

import pytest

from cloudops_guard.ingestion.reference import InMemoryAttemptLimiter


class TestThresholdValidation:
    def test_threshold_below_one_raises(self) -> None:
        with pytest.raises(ValueError):
            InMemoryAttemptLimiter(threshold=0)

    def test_threshold_of_one_is_allowed(self) -> None:
        InMemoryAttemptLimiter(threshold=1)


class TestDeterministicThresholdBehavior:
    def test_not_blocked_before_threshold_reached(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=3)
        limiter.record_failure("scope-a")
        limiter.record_failure("scope-a")
        assert limiter.is_blocked("scope-a") is False

    def test_blocked_once_threshold_reached(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=3)
        for _ in range(3):
            limiter.record_failure("scope-a")
        assert limiter.is_blocked("scope-a") is True

    def test_remains_blocked_past_threshold(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=3)
        for _ in range(5):
            limiter.record_failure("scope-a")
        assert limiter.is_blocked("scope-a") is True

    def test_unrecorded_scope_is_never_blocked(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=1)
        assert limiter.is_blocked("never-seen") is False


class TestScopeIndependence:
    def test_one_scope_key_never_affects_another(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=2)
        limiter.record_failure("scope-a")
        limiter.record_failure("scope-a")
        assert limiter.is_blocked("scope-a") is True
        assert limiter.is_blocked("scope-b") is False

    def test_layer_style_scope_keys_are_independent_strings(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=1)
        limiter.record_failure("lookup_id:abc")
        assert limiter.is_blocked("lookup_id:abc") is True
        assert limiter.is_blocked("source:abc") is False


class TestResetForTesting:
    def test_reset_clears_a_scopes_failure_count(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=1)
        limiter.record_failure("scope-a")
        assert limiter.is_blocked("scope-a") is True

        limiter.reset_for_testing("scope-a")

        assert limiter.is_blocked("scope-a") is False

    def test_reset_of_unknown_scope_is_safe(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=1)
        limiter.reset_for_testing("never-seen")  # must not raise

    def test_reset_does_not_affect_other_scopes(self) -> None:
        limiter = InMemoryAttemptLimiter(threshold=1)
        limiter.record_failure("scope-a")
        limiter.record_failure("scope-b")

        limiter.reset_for_testing("scope-a")

        assert limiter.is_blocked("scope-a") is False
        assert limiter.is_blocked("scope-b") is True
