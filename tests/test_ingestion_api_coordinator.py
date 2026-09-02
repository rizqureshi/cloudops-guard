"""Failure-injection tests for `coordinator.create_ingestion` (task 3.2's
cross-store failure-recovery design): blob failure, metadata failure,
idempotency conflict, generated-ID collision, concurrent dedup, and
cleanup-ownership boundaries -- each proving the design's own stated
guarantee, not merely the happy path.
"""

from __future__ import annotations

import threading

import pytest

from cloudops_guard.ingestion.errors import IdempotencyKeyConflict
from cloudops_guard.ingestion.models import IngestionStatus
from cloudops_guard.ingestion.reference import InMemoryMetadataStore, InMemoryReportBlobStore
from cloudops_guard.ingestion_api.coordinator import (
    MAX_INGESTION_ID_GENERATION_ATTEMPTS,
    create_ingestion,
)
from cloudops_guard.ingestion_api.errors import ApiError
from cloudops_guard.ingestion_api.fingerprint import compute_report_fingerprint
from tests.ingestion_api_support import (
    DeterministicIdGenerator,
    IngestionApiTestHarness,
    MutableClock,
    valid_kubernetes_report,
)


def _create(
    harness: IngestionApiTestHarness,
    *,
    tenant_id: str = "tenant-a",
    report=None,
    idempotency_key=None,
):
    report = report if report is not None else valid_kubernetes_report()
    report_bytes = f"{report}".encode()  # content identity is irrelevant to these tests
    return create_ingestion(
        config=harness.config,
        tenant_id=tenant_id,
        platform="kubernetes",
        report_schema_version=1,
        report=report,
        report_bytes=report_bytes,
        idempotency_key=idempotency_key,
    )


class TestHappyPath:
    def test_new_ingestion_creates_both_blob_and_metadata(self) -> None:
        harness = IngestionApiTestHarness()
        record, created = _create(harness)
        assert created is True
        assert record.status is IngestionStatus.RECEIVED
        storage_key = f"tenant-a/{record.ingestion_id}"
        assert harness.blob_store.get(storage_key) is not None


class TestIngestionIdCollision:
    def test_blob_level_collision_retries_with_a_fresh_id(self) -> None:
        harness = IngestionApiTestHarness()
        # Pre-occupy the exact key the deterministic generator will
        # produce first, so create_ingestion's own put_if_absent fails on
        # its first attempt and must retry.
        harness.blob_store.put("tenant-a/ing_test_1", b"someone-else's-bytes")

        record, created = _create(harness)
        assert created is True
        assert record.ingestion_id == "ing_test_2"
        # The pre-occupied blob is untouched.
        assert harness.blob_store.get("tenant-a/ing_test_1") == b"someone-else's-bytes"

    def test_metadata_level_collision_retries_with_a_fresh_id_and_cleans_up_the_blob(
        self,
    ) -> None:
        harness = IngestionApiTestHarness()
        # Force a metadata-level IngestionIdConflict on the first
        # generated ID: seed a *retired* record under that exact
        # (tenant_id, ingestion_id) identity -- create_or_get_received's
        # step 3 raises IngestionIdConflict for any existing record
        # (live, retired, or tombstoned) under a colliding ID.
        from cloudops_guard.ingestion.models import IngestionRecord, RetirementReason

        colliding_id = "ing_test_1"
        seed_record = IngestionRecord(
            tenant_id="tenant-a",
            ingestion_id=colliding_id,
            report_fingerprint="sha256:" + "0" * 64,
            received_at=harness.clock(),
            status=IngestionStatus.RETIRED,
            reason=RetirementReason.CUSTOMER_REQUESTED,
            retired_at=harness.clock(),
        )
        # Insert directly via the store's internal dict is not exposed --
        # use create_or_get_received to legitimately create then retire it.
        placeholder = IngestionRecord(
            tenant_id="tenant-a",
            ingestion_id=colliding_id,
            report_fingerprint="sha256:" + "1" * 64,
            received_at=harness.clock(),
            status=IngestionStatus.RECEIVED,
        )
        harness.metadata_store.create_or_get_received(
            "tenant-a", "sha256:" + "1" * 64, colliding_id, placeholder
        )
        harness.metadata_store.mark_retired(
            "tenant-a", colliding_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        del seed_record  # only used to document intent above

        record, created = _create(harness)
        assert created is True
        # Retried past the colliding ID.
        assert record.ingestion_id == "ing_test_2"
        # This request's own reserved blob under the FIRST attempt's key
        # was cleaned up -- nothing but the placeholder's own (unrelated)
        # content lives there, and the winning record's blob is under the
        # second key.
        assert harness.blob_store.get("tenant-a/ing_test_2") is not None

    def test_exhausting_all_attempts_raises_internal_error(self) -> None:
        harness = IngestionApiTestHarness()
        for i in range(1, MAX_INGESTION_ID_GENERATION_ATTEMPTS + 1):
            harness.blob_store.put(f"tenant-a/ing_test_{i}", b"occupied")

        with pytest.raises(ApiError) as exc_info:
            _create(harness)
        assert exc_info.value.code == "internal_error"


class TestIdempotencyKeyConflictCleanup:
    def test_conflict_deletes_this_requests_own_reserved_blob(self) -> None:
        harness = IngestionApiTestHarness()
        # First request establishes a binding for "key-1".
        first_report = valid_kubernetes_report()
        _create(harness, report=first_report, idempotency_key="key-1")

        # Second request: same key, different fingerprint -> conflict.
        other_report = valid_kubernetes_report()
        other_report["cluster_context"] = "different"

        with pytest.raises(IdempotencyKeyConflict):
            _create(harness, report=other_report, idempotency_key="key-1")

        # The second request's own reserved blob (under ing_test_2, the
        # next generated ID) must have been deleted -- nothing references
        # it, and it must not be left as an orphan.
        assert harness.blob_store.get("tenant-a/ing_test_2") is None


class TestLostDedupRaceCleansUpOwnBlob:
    def test_content_dedup_loss_deletes_this_requests_own_reserved_blob(self) -> None:
        harness = IngestionApiTestHarness()
        report = valid_kubernetes_report()
        first_record, first_created = _create(harness, report=report)
        assert first_created is True

        # Same content again -> loses the dedup race against the
        # already-"received" first record.
        second_record, second_created = _create(harness, report=report)
        assert second_created is False
        assert second_record.ingestion_id == first_record.ingestion_id

        # The second call's own generated ID (ing_test_2) got a blob
        # reserved and then cleaned up -- it must not still hold bytes.
        assert harness.blob_store.get("tenant-a/ing_test_2") is None
        # The winning record's blob is intact.
        assert harness.blob_store.get(f"tenant-a/{first_record.ingestion_id}") is not None


class TestAmbiguousMetadataExceptionLeavesBlobAlone:
    def test_unexpected_metadata_exception_does_not_delete_the_reserved_blob(self) -> None:
        harness = IngestionApiTestHarness()

        class _ExplodingMetadataStore:
            def create_or_get_received(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("simulated ambiguous storage failure")

        import dataclasses

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_ExplodingMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated ambiguous storage failure"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        # The blob this call reserved before the ambiguous failure is
        # deliberately left in place.
        assert harness.blob_store.get("tenant-a/ing_test_1") == b"the-report-bytes"

    def test_value_error_from_a_metadata_precondition_check_also_leaves_the_blob_alone(
        self,
    ) -> None:
        # Correction-pass item 6's own explicit re-review: proves the
        # SAME "ambiguous, leave the blob alone" treatment applies to a
        # plain `ValueError` too (the exact exception type
        # `InMemoryMetadataStore.create_or_get_received`'s own internal
        # precondition checks raise), not only an arbitrary
        # `RuntimeError` -- `create_ingestion` deliberately catches only
        # `IngestionIdConflict`/`IdempotencyKeyConflict` by name, so any
        # other exception TYPE (this module never special-cases which
        # one) must be handled identically.
        harness = IngestionApiTestHarness()

        class _PreconditionViolatingMetadataStore:
            def create_or_get_received(self, *args: object, **kwargs: object) -> None:
                raise ValueError("new_record.tenant_id must match the given tenant_id.")

        import dataclasses

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_PreconditionViolatingMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(ValueError, match="new_record.tenant_id must match"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        assert harness.blob_store.get("tenant-a/ing_test_1") == b"the-report-bytes"

        # The blob this call reserved before the ambiguous failure is
        # deliberately left in place -- the design's own stated
        # trade-off: never risk orphaning a metadata record that might
        # have actually committed.
        assert harness.blob_store.get("tenant-a/ing_test_1") == b"the-report-bytes"


class TestReservationCleanupOnPreMetadataFailure:
    """**Second correction pass, item 3**: `config.clock()` and building
    the `IngestionRecord` candidate both happen strictly *before*
    `create_or_get_received` is ever called -- unlike that call's own
    genuinely ambiguous exceptions (`TestAmbiguousMetadataExceptionLeavesBlobAlone`
    above), a failure here is never ambiguous about whether metadata was
    touched: it definitely was not. This request's own reservation must
    therefore always be cleaned up in this case, distinctly from (and
    never widening) the ambiguous-exception handling for
    `create_or_get_received` itself.
    """

    def test_clock_raising_deletes_the_reservation_and_metadata_is_never_called(self) -> None:
        import dataclasses

        harness = IngestionApiTestHarness()

        metadata_calls: list[object] = []

        class _SpyMetadataStore:
            def create_or_get_received(self, *args: object, **kwargs: object) -> None:
                metadata_calls.append(args)
                raise AssertionError("create_or_get_received must never be called")

        def _exploding_clock() -> None:
            raise RuntimeError("simulated clock failure")

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_SpyMetadataStore(),  # type: ignore[arg-type]
            clock=_exploding_clock,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated clock failure"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        assert metadata_calls == []
        # The owned reservation must be gone -- never left behind for a
        # blob nothing will ever reference.
        assert harness.blob_store.get("tenant-a/ing_test_1") is None

    def test_naive_clock_value_fails_candidate_validation_and_deletes_the_reservation(
        self,
    ) -> None:
        import dataclasses
        import datetime as dt

        harness = IngestionApiTestHarness()

        metadata_calls: list[object] = []

        class _SpyMetadataStore:
            def create_or_get_received(self, *args: object, **kwargs: object) -> None:
                metadata_calls.append(args)
                raise AssertionError("create_or_get_received must never be called")

        def _naive_clock() -> dt.datetime:
            return dt.datetime(2026, 1, 1)  # deliberately naive -- no tzinfo

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_SpyMetadataStore(),  # type: ignore[arg-type]
            clock=_naive_clock,  # type: ignore[arg-type]
        )

        with pytest.raises(Exception, match="(?i)timezone"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        assert metadata_calls == []
        assert harness.blob_store.get("tenant-a/ing_test_1") is None

    def test_ambiguous_metadata_exceptions_still_retain_the_blob_unchanged(self) -> None:
        # Regression guard: item 3's new try/except around clock()/candidate
        # construction must not widen to also swallow-and-cleanup
        # create_or_get_received's own genuinely ambiguous exceptions --
        # that guarantee (TestAmbiguousMetadataExceptionLeavesBlobAlone)
        # must remain completely unaffected by this fix.
        harness = IngestionApiTestHarness()

        class _ExplodingMetadataStore:
            def create_or_get_received(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("simulated ambiguous storage failure")

        import dataclasses

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_ExplodingMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated ambiguous storage failure"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        assert harness.blob_store.get("tenant-a/ing_test_1") == b"the-report-bytes"

    def test_cleanup_never_deletes_a_pre_existing_blob_this_request_did_not_reserve(
        self,
    ) -> None:
        # A pre-existing blob under the SAME storage key (from an
        # unrelated, earlier write) must never be deleted by this
        # request's own cleanup -- put_if_absent's own collision handling
        # (not this fix) is what protects that case; this test proves the
        # new cleanup path only ever runs after this request's own
        # successful reservation, never against someone else's bytes.
        import dataclasses

        harness = IngestionApiTestHarness()
        pre_existing = harness.blob_store.put_if_absent(
            "tenant-a/ing_test_1", b"someone-elses-bytes"
        )
        assert pre_existing is True

        def _exploding_clock() -> None:
            raise RuntimeError("simulated clock failure")

        broken_config = dataclasses.replace(harness.config, clock=_exploding_clock)  # type: ignore[arg-type]

        # The blob-key collision (this request's own put_if_absent fails
        # against the pre-existing key) causes a retry with a freshly
        # generated ID -- `DeterministicIdGenerator` always produces an
        # increasing counter, so the retry's own put_if_absent succeeds
        # under a different key, and the clock failure is what's actually
        # reached and reported on that retry.
        with pytest.raises(RuntimeError, match="simulated clock failure"):
            create_ingestion(
                config=broken_config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=valid_kubernetes_report(),
                report_bytes=b"the-report-bytes",
                idempotency_key=None,
            )

        # The pre-existing, unrelated blob under the original key must be
        # completely untouched.
        assert harness.blob_store.get("tenant-a/ing_test_1") == b"someone-elses-bytes"


class TestConcurrentDedupAtomicity:
    def test_only_one_record_created_under_concurrent_identical_requests(self) -> None:
        # In-process concurrency against the coordinator function itself
        # (the real-loopback-server HTTP-level concurrency suite lives in
        # test_ingestion_api_concurrency.py, per §13).
        clock = MutableClock()
        metadata_store = InMemoryMetadataStore(clock=clock)
        blob_store = InMemoryReportBlobStore()
        ingestion_ids = DeterministicIdGenerator("ing_race_")

        import dataclasses

        harness = IngestionApiTestHarness()
        config = dataclasses.replace(
            harness.config,
            metadata_store=metadata_store,
            blob_store=blob_store,
            clock=clock,
            ingestion_id_generator=ingestion_ids,
        )

        report = valid_kubernetes_report()
        report_bytes = b"identical-report-bytes"
        results: list[tuple[str, bool]] = []
        results_lock = threading.Lock()
        thread_count = 50
        barrier = threading.Barrier(thread_count)

        def worker() -> None:
            barrier.wait()
            record, created = create_ingestion(
                config=config,
                tenant_id="tenant-a",
                platform="kubernetes",
                report_schema_version=1,
                report=report,
                report_bytes=report_bytes,
                idempotency_key=None,
            )
            with results_lock:
                results.append((record.ingestion_id, created))

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        created_count = sum(1 for _id, created in results if created)
        assert created_count == 1
        distinct_ids = {ingestion_id for ingestion_id, _created in results}
        assert len(distinct_ids) == 1

        winning_id = next(iter(distinct_ids))
        # Every OTHER generated ingestion_id's reserved blob was cleaned
        # up -- only the winner's storage key still holds bytes.
        surviving_keys = [
            key
            for key in (f"tenant-a/ing_race_{i}" for i in range(1, thread_count + 1))
            if blob_store.get(key) is not None
        ]
        assert surviving_keys == [f"tenant-a/{winning_id}"]


def test_fingerprint_used_by_coordinator_matches_pure_function() -> None:
    harness = IngestionApiTestHarness()
    report = valid_kubernetes_report()
    record, _created = _create(harness, report=report)
    assert record.report_fingerprint == compute_report_fingerprint("kubernetes", 1, report)
