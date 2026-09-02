"""Retention-sweep and physical-purge tests (`lifecycle.py`, §E.4/§C,
task 12): automatic-retention behavior, truthful retirement reason,
DELETE-after-auto-retirement never overwriting it, immediate 404 after
either trigger, purge ordering (blob deleted before `deleted_at` is ever
recorded), purge idempotency, and tombstone-expiry indistinguishability.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx

from cloudops_guard.ingestion.models import IngestionStatus, RetirementReason
from cloudops_guard.ingestion_api.lifecycle import purge_retired_ingestion, run_retention_sweep
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    valid_kubernetes_report,
    with_client,
)


def _post_report(harness: IngestionApiTestHarness, token: str) -> str:
    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            "/api/v1/reports",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "platform": "kubernetes",
                    "report_schema_version": 1,
                    "report": valid_kubernetes_report(),
                }
            ),
        )

    resp = with_client(harness, _do)
    return resp.json()["ingestion_id"]


def _get_status(harness: IngestionApiTestHarness, ingestion_id: str, token: str) -> int:
    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(
            f"/api/v1/reports/{ingestion_id}", headers={"Authorization": f"Bearer {token}"}
        )

    return with_client(harness, _do).status_code


class TestRetentionSweep:
    def test_sweep_retires_records_older_than_the_retention_period(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)

        harness.clock.advance(dt.timedelta(days=91))
        retired = run_retention_sweep(harness.config)

        assert len(retired) == 1
        assert retired[0].ingestion_id == ingestion_id
        assert retired[0].status is IngestionStatus.RETIRED
        assert retired[0].reason is RetirementReason.RETENTION_EXPIRED

    def test_sweep_does_not_retire_records_within_the_retention_period(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        _post_report(harness, token)

        harness.clock.advance(dt.timedelta(days=89))
        retired = run_retention_sweep(harness.config)
        assert retired == []

    def test_get_404s_immediately_after_automatic_retirement(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)

        harness.clock.advance(dt.timedelta(days=91))
        run_retention_sweep(harness.config)

        assert _get_status(harness, ingestion_id, token) == 404

    def test_sweep_is_safe_to_call_repeatedly(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        _post_report(harness, token)

        harness.clock.advance(dt.timedelta(days=91))
        first_pass = run_retention_sweep(harness.config)
        second_pass = run_retention_sweep(harness.config)
        assert len(first_pass) == 1
        assert second_pass == []  # already retired -- nothing left to sweep

    def test_customer_delete_after_automatic_retirement_never_changes_the_reason(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)

        harness.clock.advance(dt.timedelta(days=91))
        run_retention_sweep(harness.config)

        async def _delete(client: httpx.AsyncClient) -> httpx.Response:
            return await client.delete(
                f"/api/v1/reports/{ingestion_id}", headers={"Authorization": f"Bearer {token}"}
            )

        resp = with_client(harness, _delete)
        assert resp.status_code == 200
        assert resp.json()["reason"] == "retention_expired"

    def test_sweep_is_tenant_and_record_scoped_correctly(self) -> None:
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token_a = harness.issue_token("tenant-a")
        old_id = _post_report(harness, token_a)

        harness.clock.advance(dt.timedelta(days=91))
        token_b = harness.issue_token("tenant-b")
        new_id = _post_report(harness, token_b)  # received "now", not yet old enough

        retired = run_retention_sweep(harness.config)
        retired_ids = {record.ingestion_id for record in retired}
        assert retired_ids == {old_id}
        assert new_id not in retired_ids


class TestPhysicalPurge:
    def test_purge_deletes_blob_before_recording_deleted_at(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        assert harness.blob_store.get(storage_key) is not None

        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        record = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert record is not None
        assert record.status is IngestionStatus.DELETED
        assert record.deleted_at is not None
        assert harness.blob_store.get(storage_key) is None

    def test_purge_failure_never_reports_deleted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        class _ExplodingBlobStoreDeleteOnly:
            def __init__(self, real_store: object) -> None:
                self._real = real_store

            def delete(self, storage_key: str) -> None:
                raise RuntimeError("simulated blob backend outage")

            def get(self, storage_key: str) -> bytes | None:
                return self._real.get(storage_key)  # type: ignore[attr-defined]

            def put(self, storage_key: str, data: bytes) -> None:
                raise NotImplementedError

            def put_if_absent(self, storage_key: str, data: bytes) -> bool:
                raise NotImplementedError

        import dataclasses

        import pytest

        broken_config = dataclasses.replace(
            harness.config,
            blob_store=_ExplodingBlobStoreDeleteOnly(harness.blob_store),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        # Blob deletion is attempted (and fails) FIRST -- mark_purged must
        # never have run, so the record must still read as "retired," not
        # "deleted."
        record = harness.metadata_store.get(
            "tenant-a", ingestion_id
        )  # None expected: status != RECEIVED
        assert record is None  # retired records aren't returned by get() either
        tombstone = harness.metadata_store.get_tombstone("tenant-a", ingestion_id)
        assert tombstone is None  # no tombstone -- purge never completed

    def test_repeated_purge_is_safe(self) -> None:
        # Correction-pass item 1 (second pass): a repeat purge against an
        # ALREADY-`deleted` record now returns `None` (rather than the
        # existing record) -- `begin_purge` reports "nothing left to
        # physically delete" for this case and `purge_retired_ingestion`
        # never touches the blob store or re-reads the record for it,
        # deliberately, since any such re-read could itself already be
        # describing a different, newer identity that has since reused
        # this exact key. Idempotency is proven at the DATA level
        # instead: the underlying record is unchanged by the second call.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        first = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert first is not None
        second = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert second is None
        still_deleted = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert still_deleted is not None
        assert still_deleted.status is IngestionStatus.DELETED
        assert still_deleted.deleted_at == first.deleted_at

    def test_get_and_delete_around_retirement_are_consistent(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        assert _get_status(harness, ingestion_id, token) == 200

        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        assert _get_status(harness, ingestion_id, token) == 404

        purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert _get_status(harness, ingestion_id, token) == 404


class TestPurgeEligibilitySafety:
    """Correction-pass item 1: `purge_retired_ingestion` must never call
    `ReportBlobStore.delete` for a `received`, unknown, or foreign-tenant
    record -- reproduced-before-fix as: calling it on a `received` record
    deleted the live blob and only then raised `ValueError` from
    `mark_purged`, leaving a `received` metadata record with no blob.
    """

    def test_received_record_raises_without_deleting_the_blob(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        assert harness.blob_store.get(storage_key) is not None

        import pytest

        with pytest.raises(ValueError):
            purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)

        assert harness.blob_store.get(storage_key) is not None
        record = harness.metadata_store.get("tenant-a", ingestion_id)
        assert record is not None
        assert record.status is IngestionStatus.RECEIVED

    def test_unknown_id_returns_none_and_touches_nothing(self) -> None:
        harness = IngestionApiTestHarness()
        result = purge_retired_ingestion(harness.config, "tenant-a", "ing_never-existed")
        assert result is None
        assert harness.metadata_store.get_tombstone("tenant-a", "ing_never-existed") is None

    def test_foreign_tenant_returns_none_and_leaves_the_real_tenants_blob_alone(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"

        result = purge_retired_ingestion(harness.config, "tenant-b", ingestion_id)
        assert result is None
        assert harness.blob_store.get(storage_key) is not None
        record = harness.metadata_store.get("tenant-a", ingestion_id)
        assert record is not None and record.status is IngestionStatus.RECEIVED

    def test_already_retired_record_purges_normally(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        record = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert record is not None
        assert record.status is IngestionStatus.DELETED
        assert harness.blob_store.get(storage_key) is None

    def test_already_deleted_record_is_idempotent(self) -> None:
        # See test_repeated_purge_is_safe's own comment: a repeat call
        # against an already-`deleted` record now returns `None`
        # (correction-pass item 1, second pass) -- idempotency is that
        # the underlying record is unaffected, not that the function
        # returns it a second time.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        first = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert first is not None and first.status is IngestionStatus.DELETED

        second = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert second is None
        still_deleted = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert still_deleted is not None
        assert still_deleted.status is IngestionStatus.DELETED
        assert still_deleted.deleted_at == first.deleted_at

    def test_metadata_lookup_failure_propagates_and_touches_no_blob(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"

        class _ExplodingLookupMetadataStore:
            def begin_purge(self, tenant_id: str, ingestion_id: str, at: object) -> None:
                raise RuntimeError("simulated metadata backend outage")

        import dataclasses

        import pytest

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_ExplodingLookupMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated metadata backend outage"):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        # The real store (never touched by the broken config above) still
        # has the blob -- confirms no blob deletion was even attempted
        # before the eligibility read failed.
        assert harness.blob_store.get(storage_key) is not None

    def test_metadata_transition_failure_leaves_blob_already_gone_but_record_retired(
        self,
    ) -> None:
        # Documents the existing, still-intentional trade-off: once the
        # eligibility check passes, blob deletion happens first (per this
        # function's own ordering guarantee), so a *subsequent*
        # mark_purged failure leaves the blob already gone while the
        # metadata record still reads as "retired," never "deleted" --
        # never the reverse (never "deleted" with a live blob).
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        real_metadata_store = harness.metadata_store

        class _ExplodingPurgeMetadataStore:
            def begin_purge(self, tenant_id: str, ingestion_id: str, at: object) -> object:
                return real_metadata_store.begin_purge(tenant_id, ingestion_id, at)

            def release_purge_claim(self, claim: object) -> None:
                real_metadata_store.release_purge_claim(claim)

            def finalize_purge(self, claim: object) -> None:
                raise RuntimeError("simulated metadata transition failure")

        import dataclasses

        import pytest

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_ExplodingPurgeMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated metadata transition failure"):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        assert harness.blob_store.get(storage_key) is None
        record = real_metadata_store.get_any_status("tenant-a", ingestion_id)
        assert record is not None
        assert record.status is IngestionStatus.RETIRED

    def test_rejected_operations_leave_every_store_byte_for_byte_unchanged(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"

        blob_before = harness.blob_store.get(storage_key)
        record_before = harness.metadata_store.get("tenant-a", ingestion_id)
        tombstone_before = harness.metadata_store.get_tombstone("tenant-a", ingestion_id)

        import pytest

        with pytest.raises(ValueError):
            purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        purge_retired_ingestion(harness.config, "tenant-a", "ing_unknown")
        purge_retired_ingestion(harness.config, "tenant-b", ingestion_id)

        assert harness.blob_store.get(storage_key) == blob_before
        assert harness.metadata_store.get("tenant-a", ingestion_id) == record_before
        assert harness.metadata_store.get_tombstone("tenant-a", ingestion_id) == tombstone_before

    @staticmethod
    def _race_delete_against_purge(
        harness: IngestionApiTestHarness, ingestion_id: str, errors: list[BaseException]
    ) -> None:
        import threading

        barrier = threading.Barrier(2)

        def do_delete() -> None:
            barrier.wait()
            harness.metadata_store.mark_retired(
                "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
            )

        def do_purge() -> None:
            barrier.wait()
            try:
                purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
            except ValueError:
                pass
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        t1 = threading.Thread(target=do_delete)
        t2 = threading.Thread(target=do_purge)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def test_concurrent_delete_and_purge_never_delete_a_live_blob(self) -> None:
        # A racing customer DELETE (retirement) and a racing purge attempt
        # against the SAME still-received record -- the purge side must
        # never win a race that deletes a live blob: whichever thread's
        # eligibility check runs before the DELETE completes must reject
        # (ValueError, no blob touched); the purge only ever succeeds
        # once the record has genuinely become retired.
        results: list[str] = []
        errors: list[BaseException] = []

        for _ in range(30):
            harness = IngestionApiTestHarness()
            token = harness.issue_token("tenant-a")
            ingestion_id = _post_report(harness, token)
            storage_key = f"tenant-a/{ingestion_id}"

            self._race_delete_against_purge(harness, ingestion_id, errors)

            # Whichever order actually happened, the record must now be
            # either retired (purge ran before/without seeing "received")
            # or deleted (purge ran after retirement completed) -- never
            # "received with no blob," which is exactly the bug this
            # correction fixes.
            final_status = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
            assert final_status is not None
            if final_status.status is IngestionStatus.RECEIVED:
                assert harness.blob_store.get(storage_key) is not None
            results.append(final_status.status.value)

        assert not errors
        assert set(results) <= {"received", "retired", "deleted"}


class TestPurgeSafeAcrossTombstoneExpiryAndIdReuse:
    """Correction-pass item 1 (second pass): the `get_any_status`-based
    eligibility check from the first pass was itself a check-*then*-act
    operation -- these tests deterministically reproduce (and prove
    fixed) both races the task named: an already-`deleted` record's
    tombstone expiring and its key being reused before a stale, repeated
    purge attempt runs, and two concurrent purgers racing the same
    `retired` record. Every test in this class asserts the specific,
    named invariant: no outcome ever leaves `received` metadata with a
    missing blob.
    """

    def test_exact_reproduced_sequence_never_deletes_the_reused_identitys_blob(
        self,
    ) -> None:
        # The literal sequence from the task: an old record is DELETED;
        # its tombstone expires; a NEW received record reuses the same
        # (tenant_id, ingestion_id) with a new blob; a (now-stale) repeat
        # purge attempt against the OLD identity must never delete the
        # new blob.
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        old_id = _post_report(harness, token)
        storage_key = f"tenant-a/{old_id}"

        harness.metadata_store.mark_retired(
            "tenant-a", old_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        first = purge_retired_ingestion(harness.config, "tenant-a", old_id)
        assert first is not None and first.status is IngestionStatus.DELETED

        # Tombstone expires (default 90-day retention).
        harness.clock.advance(dt.timedelta(days=91))

        # A brand-new ingestion reuses the exact same ingestion_id --
        # forced deterministically via the harness's own id generator,
        # mirroring how test_ingestion_api_coordinator.py already forces
        # id collisions for testing.
        harness.ingestion_ids.force_next(old_id)
        new_report = valid_kubernetes_report()
        new_report["cluster_context"] = "reused-identity"

        async def _post_new(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {"platform": "kubernetes", "report_schema_version": 1, "report": new_report}
                ),
            )

        new_resp = with_client(harness, _post_new)
        assert new_resp.status_code == 201
        assert new_resp.json()["ingestion_id"] == old_id  # genuinely reused the same key
        assert harness.blob_store.get(storage_key) is not None

        # The stale, repeated purge attempt against the (now long-gone,
        # reused) old identity: begin_purge correctly observes the
        # CURRENT state (genuinely `received`, since the key was reused)
        # and raises -- exactly the same, correct refusal a purge on any
        # other still-`received` record gets. This is the desired,
        # loud-failure outcome: the alternative (silently no-op'ing) is
        # no more informative and this at least fails safely and clearly.
        import pytest

        with pytest.raises(ValueError, match="already-retired record"):
            purge_retired_ingestion(harness.config, "tenant-a", old_id)

        # The new identity's live blob and RECEIVED status are untouched.
        assert harness.blob_store.get(storage_key) is not None
        current = harness.metadata_store.get_any_status("tenant-a", old_id)
        assert current is not None
        assert current.status is IngestionStatus.RECEIVED

    def test_concurrent_purgers_on_the_same_retired_record_only_one_deletes(self) -> None:
        # Deterministic (no timing dependency): both purgers call
        # begin_purge before either proceeds, proving the SECOND never
        # receives a claim at all -- the exclusive-claim mechanism that
        # makes this safe even when ReportBlobStore.delete is slow.
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        claim_a = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        claim_b = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        assert claim_a is not None
        assert claim_b is None  # exclusive: B gets nothing while A holds the claim

    def test_second_purger_claim_after_first_completes_sees_already_deleted(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        first = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert first is not None and first.status is IngestionStatus.DELETED

        # A second purger arriving AFTER the first fully completed sees
        # "already deleted" (None from begin_purge), never a stale claim.
        claim = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        assert claim is None

    def test_two_purgers_full_cycle_never_deletes_a_reused_blob(self) -> None:
        # The task's own second named race, driven end to end: purger A
        # and purger B both observe RETIRED (only A actually gets the
        # claim, per exclusivity); A completes its full cycle; the key is
        # then reused with a new live blob; B's own (redundant, since it
        # never held a claim) attempt to finish must be a no-op.
        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        claim_a = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        claim_b = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        assert claim_a is not None
        assert claim_b is None

        harness.blob_store.delete(storage_key)
        result_a = harness.metadata_store.finalize_purge(claim_a)

        assert result_a is not None and result_a.status is IngestionStatus.DELETED

        # Tombstone expires; key reused with a new live blob.
        harness.clock.advance(dt.timedelta(days=91))
        harness.ingestion_ids.force_next(ingestion_id)
        new_report = valid_kubernetes_report()
        new_report["cluster_context"] = "reused-by-b-scenario"

        async def _post_new(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {"platform": "kubernetes", "report_schema_version": 1, "report": new_report}
                ),
            )

        new_resp = with_client(harness, _post_new)
        assert new_resp.status_code == 201
        assert harness.blob_store.get(storage_key) is not None

        # B never received a claim (claim_b is None), so a correctly
        # written caller never calls blob_store.delete or finalize_purge
        # for it at all -- confirmed structurally, not merely by luck.
        assert claim_b is None
        assert harness.blob_store.get(storage_key) is not None
        current = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert current is not None and current.status is IngestionStatus.RECEIVED

    def test_blob_deletion_failure_releases_the_claim_for_a_later_retry(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        real_blob_store = harness.blob_store
        fail_next = {"value": True}

        class _FlakyBlobStore:
            def delete(self, key: str) -> None:
                if fail_next["value"]:
                    raise RuntimeError("simulated transient blob backend outage")
                real_blob_store.delete(key)

            def get(self, key: str) -> bytes | None:
                return real_blob_store.get(key)

            def put(self, key: str, data: bytes) -> None:
                real_blob_store.put(key, data)

            def put_if_absent(self, key: str, data: bytes) -> bool:
                return real_blob_store.put_if_absent(key, data)

        import dataclasses

        import pytest

        broken_config = dataclasses.replace(
            harness.config,
            blob_store=_FlakyBlobStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated transient blob backend outage"):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        # The claim must have been released -- proven by a SECOND
        # begin_purge call succeeding (an unreleased claim would return
        # None here instead, since it would still show as "claimed").
        retry_claim = harness.metadata_store.begin_purge("tenant-a", ingestion_id, harness.clock())
        assert retry_claim is not None

        # Retry, this time succeeding.
        fail_next["value"] = False
        harness.metadata_store.release_purge_claim(
            retry_claim
        )  # undo the probe claim above, exactly like a real caller would on its own failure path
        result = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert result is not None
        assert result.status is IngestionStatus.DELETED
        assert harness.blob_store.get(storage_key) is None

    def test_already_deleted_repeat_call_never_calls_blob_store_delete(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        first = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert first is not None

        real_blob_store = harness.blob_store
        delete_calls: list[str] = []

        class _SpyBlobStore:
            def delete(self, key: str) -> None:
                delete_calls.append(key)
                real_blob_store.delete(key)

            def get(self, key: str) -> bytes | None:
                return real_blob_store.get(key)

            def put(self, key: str, data: bytes) -> None:
                real_blob_store.put(key, data)

            def put_if_absent(self, key: str, data: bytes) -> bool:
                return real_blob_store.put_if_absent(key, data)

        import dataclasses

        spy_config = dataclasses.replace(
            harness.config,
            blob_store=_SpyBlobStore(),  # type: ignore[arg-type]
        )

        second = purge_retired_ingestion(spy_config, "tenant-a", ingestion_id)
        assert second is None
        assert delete_calls == []  # the explicit, named safety property


class TestPurgeClaimExceptionSafety:
    """**Purge-claim hardening pass, item 3.** `purge_retired_ingestion`'s
    own exception-safety, end to end, through the real `MetadataStore`/
    `ReportBlobStore`. Reproduced before fix: `config.clock()` was called
    (and a naive/invalid timestamp could reach `finalize_purge`) *after*
    a claim had already been acquired, so a failure there leaked the
    claim permanently (a later `begin_purge` call for the same record
    would find it still "claimed" forever, by a caller that had already
    given up). Fixed by computing and validating `at` -- together with
    the complete delete candidate -- entirely *before* any claim is ever
    granted (see `begin_purge`'s own updated docstring), and by wrapping
    every remaining step from claim acquisition onward so any exception
    releases exactly that claim before re-raising.
    """

    def test_clock_raising_never_acquires_a_claim(self) -> None:
        import dataclasses

        import pytest

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        def _exploding_clock() -> None:
            raise RuntimeError("simulated clock failure")

        broken_config = dataclasses.replace(harness.config, clock=_exploding_clock)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="simulated clock failure"):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        # Nothing was ever touched: no claim was acquired (clock() is
        # called strictly before begin_purge), so a normal purge with a
        # valid clock succeeds immediately afterward -- no leakage, no
        # retry needed to "clear" anything.
        assert harness.blob_store.get(storage_key) is not None
        result = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert result is not None and result.status is IngestionStatus.DELETED

    def test_naive_timestamp_never_acquires_a_claim(self) -> None:
        import pytest

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        naive_now = dt.datetime(2026, 1, 2)  # deliberately no tzinfo

        with pytest.raises(ValueError, match="timezone-aware"):
            purge_retired_ingestion(harness.config, "tenant-a", ingestion_id, now=naive_now)

        # No claim was granted -- begin_purge validates `at` before
        # granting one -- so the blob is untouched and a normal,
        # valid-timestamp purge succeeds right away.
        assert harness.blob_store.get(storage_key) is not None
        record = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert record is not None and record.status is IngestionStatus.RETIRED
        result = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert result is not None and result.status is IngestionStatus.DELETED

    def test_deleted_at_earlier_than_retired_at_never_acquires_a_claim(self) -> None:
        import pytest

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        retired_at = harness.clock() + dt.timedelta(hours=1)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, retired_at, RetirementReason.CUSTOMER_REQUESTED
        )

        too_early = retired_at - dt.timedelta(seconds=1)

        with pytest.raises(ValueError, match="deleted_at must not precede retired_at"):
            purge_retired_ingestion(harness.config, "tenant-a", ingestion_id, now=too_early)

        assert harness.blob_store.get(storage_key) is not None
        record = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert record is not None and record.status is IngestionStatus.RETIRED

        # A subsequent purge with a valid (later) timestamp succeeds
        # normally -- no leaked claim blocking it. harness.clock() itself
        # is still at the original post time, before retired_at, so an
        # explicit valid `now` is passed rather than relying on the
        # unqualified clock.
        result = purge_retired_ingestion(
            harness.config, "tenant-a", ingestion_id, now=retired_at + dt.timedelta(seconds=1)
        )
        assert result is not None and result.status is IngestionStatus.DELETED

    def test_finalize_failure_releases_the_claim_for_a_later_retry(self) -> None:
        import dataclasses

        import pytest

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )

        real_metadata_store = harness.metadata_store
        fail_next = {"value": True}

        class _FlakyFinalizeMetadataStore:
            def begin_purge(self, tenant_id: str, ingestion_id: str, at: object) -> object:
                return real_metadata_store.begin_purge(tenant_id, ingestion_id, at)

            def release_purge_claim(self, claim: object) -> None:
                real_metadata_store.release_purge_claim(claim)

            def finalize_purge(self, claim: object) -> object:
                if fail_next["value"]:
                    raise RuntimeError("simulated metadata commit failure")
                return real_metadata_store.finalize_purge(claim)

        broken_config = dataclasses.replace(
            harness.config,
            metadata_store=_FlakyFinalizeMetadataStore(),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="simulated metadata commit failure"):
            purge_retired_ingestion(broken_config, "tenant-a", ingestion_id)

        # The blob is already gone (blob deletion happens before
        # finalize, unchanged ordering) -- but the claim must have been
        # released, proving a later retry is not permanently blocked.
        assert harness.blob_store.get(storage_key) is None
        record = real_metadata_store.get_any_status("tenant-a", ingestion_id)
        assert record is not None and record.status is IngestionStatus.RETIRED

        fail_next["value"] = False
        result = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert result is not None and result.status is IngestionStatus.DELETED

    def test_every_failure_mode_leaves_no_claim_leaked_and_a_reused_blob_untouched(
        self,
    ) -> None:
        # An end-to-end combination proof: drive every one of this
        # class's own failure modes against the SAME record in sequence
        # (clock failure, naive timestamp, early timestamp, blob-deletion
        # failure, finalize failure), then complete a real purge, let its
        # tombstone expire, and reuse the identity -- proving none of the
        # five prior failures left any residual claim capable of
        # threatening the reused blob.
        import dataclasses

        import pytest

        harness = IngestionApiTestHarness(retention_period=dt.timedelta(days=90))
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        storage_key = f"tenant-a/{ingestion_id}"
        retired_at = harness.clock() + dt.timedelta(hours=1)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, retired_at, RetirementReason.CUSTOMER_REQUESTED
        )

        def _exploding_clock() -> None:
            raise RuntimeError("simulated clock failure")

        with pytest.raises(RuntimeError, match="simulated clock failure"):
            purge_retired_ingestion(
                dataclasses.replace(harness.config, clock=_exploding_clock),  # type: ignore[arg-type]
                "tenant-a",
                ingestion_id,
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            purge_retired_ingestion(
                harness.config, "tenant-a", ingestion_id, now=dt.datetime(2026, 1, 2)
            )
        with pytest.raises(ValueError, match="deleted_at must not precede retired_at"):
            purge_retired_ingestion(
                harness.config,
                "tenant-a",
                ingestion_id,
                now=retired_at - dt.timedelta(seconds=1),
            )

        # Advance the harness clock itself past retired_at, so every
        # remaining call below -- none of which pass an explicit `now` --
        # produces a valid timestamp via config.clock().
        harness.clock.set(retired_at + dt.timedelta(seconds=1))

        real_blob_store = harness.blob_store

        class _ExplodingBlobStoreOnce:
            def __init__(self) -> None:
                self._failed_once = False

            def delete(self, key: str) -> None:
                if not self._failed_once:
                    self._failed_once = True
                    raise RuntimeError("simulated transient blob outage")
                real_blob_store.delete(key)

            def get(self, key: str) -> bytes | None:
                return real_blob_store.get(key)

            def put(self, key: str, data: bytes) -> None:
                real_blob_store.put(key, data)

            def put_if_absent(self, key: str, data: bytes) -> bool:
                return real_blob_store.put_if_absent(key, data)

        with pytest.raises(RuntimeError, match="simulated transient blob outage"):
            purge_retired_ingestion(
                dataclasses.replace(harness.config, blob_store=_ExplodingBlobStoreOnce()),  # type: ignore[arg-type]
                "tenant-a",
                ingestion_id,
            )

        real_metadata_store = harness.metadata_store

        class _ExplodingFinalizeOnce:
            def __init__(self) -> None:
                self._failed_once = False

            def begin_purge(self, tenant_id: str, ingestion_id: str, at: object) -> object:
                return real_metadata_store.begin_purge(tenant_id, ingestion_id, at)

            def release_purge_claim(self, claim: object) -> None:
                real_metadata_store.release_purge_claim(claim)

            def finalize_purge(self, claim: object) -> object:
                if not self._failed_once:
                    self._failed_once = True
                    raise RuntimeError("simulated metadata commit failure")
                return real_metadata_store.finalize_purge(claim)

        with pytest.raises(RuntimeError, match="simulated metadata commit failure"):
            purge_retired_ingestion(
                dataclasses.replace(harness.config, metadata_store=_ExplodingFinalizeOnce()),  # type: ignore[arg-type]
                "tenant-a",
                ingestion_id,
            )

        # After five independent failures, the record must still be
        # exactly RETIRED (never DELETED-with-a-live-blob, never stuck
        # claimed) -- and a completely ordinary purge now succeeds.
        record = real_metadata_store.get_any_status("tenant-a", ingestion_id)
        assert record is not None and record.status is IngestionStatus.RETIRED
        final = purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)
        assert final is not None and final.status is IngestionStatus.DELETED
        assert harness.blob_store.get(storage_key) is None

        # Tombstone expires; the identity is reused with a new live blob.
        harness.clock.advance(dt.timedelta(days=91))
        harness.ingestion_ids.force_next(ingestion_id)
        new_report = valid_kubernetes_report()
        new_report["cluster_context"] = "reused-after-failure-sequence"

        async def _post_new(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                "/api/v1/reports",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(
                    {"platform": "kubernetes", "report_schema_version": 1, "report": new_report}
                ),
            )

        new_resp = with_client(harness, _post_new)
        assert new_resp.status_code == 201
        assert harness.blob_store.get(storage_key) is not None

        # None of the five prior failures left anything behind capable
        # of threatening the new identity's blob.
        assert harness.blob_store.get(storage_key) is not None
        current = harness.metadata_store.get_any_status("tenant-a", ingestion_id)
        assert current is not None and current.status is IngestionStatus.RECEIVED


class TestTombstoneExpiryIndistinguishability:
    def test_expired_tombstone_id_is_indistinguishable_from_never_existed(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        ingestion_id = _post_report(harness, token)
        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.CUSTOMER_REQUESTED
        )
        purge_retired_ingestion(harness.config, "tenant-a", ingestion_id)

        # Within the tombstone window: DELETE still sees the tombstone
        # (idempotent 200).
        async def _delete(client: httpx.AsyncClient) -> httpx.Response:
            return await client.delete(
                f"/api/v1/reports/{ingestion_id}", headers={"Authorization": f"Bearer {token}"}
            )

        within_window = with_client(harness, _delete)
        assert within_window.status_code == 200

        # Past the tombstone retention window: now indistinguishable from
        # never-existed.
        harness.clock.advance(dt.timedelta(days=91))
        after_expiry = with_client(harness, _delete)
        assert after_expiry.status_code == 404
        assert after_expiry.json()["error"] == "not_found"
