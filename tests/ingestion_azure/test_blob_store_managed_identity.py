"""Narrow, offline-only unit test for
`create_managed_identity_blob_service_client` -- never contacts a real
Azure endpoint. Confirms it uses `DefaultAzureCredential` (never a
storage-account key) and, per correction-pass item 9, strictly validates
`account_url` as a canonical Azure Blob Storage endpoint -- HTTPS scheme
only, no userinfo/port/query/fragment/path, and a hostname matching
exactly `<account>.blob.core.windows.net` -- before constructing
anything. The bare `startswith("https://")` check this replaced would
accept a value like `https://evil.example/@real-account.blob.core.
windows.net` or `https://account.blob.core.windows.net.evil.example`,
either of which hands `DefaultAzureCredential`'s live Azure Storage OAuth
bearer token to an attacker-controlled host.
"""

from __future__ import annotations

import pytest

from cloudops_guard.ingestion_azure.blob_store import create_managed_identity_blob_service_client


def test_rejects_non_https_url() -> None:
    with pytest.raises(ValueError):
        create_managed_identity_blob_service_client("http://example.blob.core.windows.net")


class TestStrictBlobAccountUrlValidation:
    @pytest.mark.parametrize(
        "account_url",
        [
            # Userinfo-smuggled fake domain: everything before the '@'
            # is authority-syntax noise; the real host is the attacker's.
            "https://real-account.blob.core.windows.net@evil.example",
            "https://evil.example/@real-account.blob.core.windows.net",
            # Suffix-only match: a naive `.endswith`/substring check on
            # the raw string would wrongly accept this -- the parsed
            # *hostname* here is `attacker.blob.core.windows.net.evil.example`.
            "https://attacker.blob.core.windows.net.evil.example",
            # Subdomain smuggling in the other direction.
            "https://real-account.blob.core.windows.net.evil.example",
            "https://evil.example",
            "https://169.254.169.254",
            # An explicit, non-default port.
            "https://account.blob.core.windows.net:9999",
            # A path, query, or fragment beyond the bare host.
            "https://account.blob.core.windows.net/some/path",
            "https://account.blob.core.windows.net?x=1",
            "https://account.blob.core.windows.net#frag",
            # A sovereign-cloud suffix -- never approved by this
            # codebase's one recorded region decision (public cloud only).
            "https://account.blob.core.usgovcloudapi.net",
            "https://account.blob.core.chinacloudapi.cn",
            # Storage-account naming violations (too short/too long).
            "https://ab.blob.core.windows.net",
            "https://" + ("a" * 25) + ".blob.core.windows.net",
            # Malformed URL / invalid port syntax.
            "https://account.blob.core.windows.net:not-a-port",
            "not-a-url-at-all",
            "",
        ],
    )
    def test_rejects_every_malicious_or_malformed_url(self, account_url: str) -> None:
        with pytest.raises(ValueError):
            create_managed_identity_blob_service_client(account_url)

    @pytest.mark.parametrize(
        "account_url",
        [
            "https://example.blob.core.windows.net",
            "https://example.blob.core.windows.net/",
            "https://abc.blob.core.windows.net",
            "https://" + ("a" * 24) + ".blob.core.windows.net",
            # Mixed case is safely accepted: `urlsplit(...).hostname`
            # always lowercases (DNS names are themselves case-
            # insensitive, so this is a normalization, not a bypass --
            # the actual HTTP request still resolves to the same host).
            "https://Example.Blob.Core.Windows.Net",
        ],
    )
    def test_accepts_a_genuine_canonical_endpoint(
        self, account_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "cloudops_guard.ingestion_azure.blob_store.DefaultAzureCredential",
            lambda: object(),
        )
        monkeypatch.setattr(
            "cloudops_guard.ingestion_azure.blob_store.BlobServiceClient",
            lambda *, account_url, credential: object(),
        )
        create_managed_identity_blob_service_client(account_url)  # must not raise


def test_uses_default_azure_credential_never_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _FakeCredential:
        pass

    def _fake_default_credential():
        return _FakeCredential()

    class _FakeBlobServiceClient:
        def __init__(self, *, account_url, credential):
            captured["account_url"] = account_url
            captured["credential"] = credential

    monkeypatch.setattr(
        "cloudops_guard.ingestion_azure.blob_store.DefaultAzureCredential", _fake_default_credential
    )
    monkeypatch.setattr(
        "cloudops_guard.ingestion_azure.blob_store.BlobServiceClient", _FakeBlobServiceClient
    )

    create_managed_identity_blob_service_client("https://example.blob.core.windows.net")

    assert captured["account_url"] == "https://example.blob.core.windows.net"
    assert isinstance(captured["credential"], _FakeCredential)
