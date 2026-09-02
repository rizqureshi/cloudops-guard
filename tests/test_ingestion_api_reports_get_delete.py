"""`GET /api/v1/reports/{id}` and `DELETE /api/v1/reports/{id}` tests
(§E.3, §E.4) -- tenant isolation, cross-tenant/unknown/retired/expired-
tombstone indistinguishability, scope enforcement, and the complete
retirement lifecycle.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx

from cloudops_guard.ingestion.models import TokenScope
from tests.ingestion_api_support import (
    IngestionApiTestHarness,
    valid_kubernetes_report,
    with_client,
)


def _post_report(harness: IngestionApiTestHarness, token: str) -> httpx.Response:
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

    return with_client(harness, _do)


def _get(harness: IngestionApiTestHarness, ingestion_id: str, token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}

    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(f"/api/v1/reports/{ingestion_id}", headers=headers)

    return with_client(harness, _do)


def _delete(
    harness: IngestionApiTestHarness, ingestion_id: str, token: str | None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}

    async def _do(client: httpx.AsyncClient) -> httpx.Response:
        return await client.delete(f"/api/v1/reports/{ingestion_id}", headers=headers)

    return with_client(harness, _do)


class TestGetReceipt:
    def test_get_received_record_returns_current_fields(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        resp = _get(harness, ingestion_id, token)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingestion_id"] == ingestion_id
        assert body["status"] == "received"
        assert body["report_fingerprint"] == post_resp.json()["report_fingerprint"]
        assert "reason" not in body
        assert "retired_at" not in body
        assert "deleted_at" not in body

    def test_get_never_returns_report_content(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        resp = _get(harness, post_resp.json()["ingestion_id"], token)
        body_text = resp.text
        assert "findings" not in body_text
        assert "cluster_context" not in body_text
        assert "K8S-IMG-001" not in body_text

    def test_get_unknown_id_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _get(harness, "ing_never-existed", token)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_get_no_authorization_header_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _get(harness, "ing_whatever", None)
        assert resp.status_code == 401

    def test_get_without_read_scope_is_403(self) -> None:
        harness = IngestionApiTestHarness()
        write_token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, write_token)
        read_only_less_token = harness.issue_token(
            "tenant-a", scopes=frozenset({TokenScope.REPORTS_WRITE})
        )
        resp = _get(harness, post_resp.json()["ingestion_id"], read_only_less_token)
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_get_cross_tenant_is_identical_404(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        post_resp = _post_report(harness, token_a)
        ingestion_id = post_resp.json()["ingestion_id"]

        cross_tenant_resp = _get(harness, ingestion_id, token_b)
        unknown_resp = _get(harness, "ing_definitely-unknown", token_b)
        assert cross_tenant_resp.status_code == unknown_resp.status_code == 404
        assert cross_tenant_resp.json() == {
            "ok": False,
            "error": "not_found",
            "request_id": cross_tenant_resp.json()["request_id"],
        }
        assert cross_tenant_resp.json()["error"] == unknown_resp.json()["error"]

    def test_get_after_retirement_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        _delete(harness, ingestion_id, token)
        resp = _get(harness, ingestion_id, token)
        assert resp.status_code == 404


class TestDeleteRetirement:
    def test_delete_received_record_retires_it(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        resp = _delete(harness, ingestion_id, token)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingestion_id"] == ingestion_id
        assert body["status"] == "retired"
        assert body["reason"] == "customer_requested"
        assert body["retired_at"] == "2026-01-01T00:00:00Z"
        assert body["deleted_at"] is None
        assert "report_fingerprint" not in body

    def test_repeated_delete_is_idempotent_and_preserves_original_reason(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        first = _delete(harness, ingestion_id, token)
        harness.clock.advance(dt.timedelta(hours=1))
        second = _delete(harness, ingestion_id, token)
        assert second.status_code == 200
        assert second.json()["reason"] == "customer_requested"
        assert second.json()["retired_at"] == first.json()["retired_at"]

    def test_delete_after_automatic_retention_expiry_never_overwrites_reason(self) -> None:
        from cloudops_guard.ingestion.models import RetirementReason

        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        harness.metadata_store.mark_retired(
            "tenant-a", ingestion_id, harness.clock(), RetirementReason.RETENTION_EXPIRED
        )

        resp = _delete(harness, ingestion_id, token)
        assert resp.status_code == 200
        assert resp.json()["reason"] == "retention_expired"

    def test_delete_unknown_id_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        resp = _delete(harness, "ing_never-existed", token)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_delete_cross_tenant_is_identical_404(self) -> None:
        harness = IngestionApiTestHarness()
        token_a = harness.issue_token("tenant-a")
        token_b = harness.issue_token("tenant-b")
        post_resp = _post_report(harness, token_a)
        ingestion_id = post_resp.json()["ingestion_id"]

        resp = _delete(harness, ingestion_id, token_b)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"
        # tenant-a's own record is unaffected by tenant-b's failed DELETE.
        get_resp = _get(harness, ingestion_id, token_a)
        assert get_resp.status_code == 200

    def test_delete_without_delete_scope_is_403(self) -> None:
        harness = IngestionApiTestHarness()
        write_token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, write_token)
        limited_token = harness.issue_token(
            "tenant-a", scopes=frozenset({TokenScope.REPORTS_WRITE, TokenScope.REPORTS_READ})
        )
        resp = _delete(harness, post_resp.json()["ingestion_id"], limited_token)
        assert resp.status_code == 403

    def test_delete_after_tombstone_expiry_is_404(self) -> None:
        harness = IngestionApiTestHarness()
        token = harness.issue_token("tenant-a")
        post_resp = _post_report(harness, token)
        ingestion_id = post_resp.json()["ingestion_id"]

        _delete(harness, ingestion_id, token)
        # Physically purge, then advance past the tombstone retention.
        from cloudops_guard.ingestion_api.lifecycle import purge_retired_ingestion

        purge_retired_ingestion(harness.config, "tenant-a", ingestion_id, now=harness.clock())
        harness.clock.advance(dt.timedelta(days=91))

        resp = _delete(harness, ingestion_id, token)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_delete_no_authorization_header_is_401(self) -> None:
        harness = IngestionApiTestHarness()
        resp = _delete(harness, "ing_whatever", None)
        assert resp.status_code == 401
