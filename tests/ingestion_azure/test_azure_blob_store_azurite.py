"""Behavioral and concurrency tests for `AzureBlobReportBlobStore`
against a real, local Azurite instance -- task 12, item 5 (Azure Blob
conditional creation). Mirrors `tests/test_ingestion_blob_store.py`'s own
coverage of the `ReportBlobStore` contract for the in-memory reference
implementation.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from cloudops_guard.ingestion_azure.blob_store import AzureBlobReportBlobStore

pytestmark = pytest.mark.azurite


@pytest.fixture()
def store(azurite_container_client) -> AzureBlobReportBlobStore:
    client, container_name = azurite_container_client
    return AzureBlobReportBlobStore(client, container_name=container_name)


def test_put_then_get(store: AzureBlobReportBlobStore) -> None:
    store.put("t1/ing1", b"hello world")
    assert store.get("t1/ing1") == b"hello world"


def test_put_overwrites(store: AzureBlobReportBlobStore) -> None:
    store.put("t1/ing1", b"first")
    store.put("t1/ing1", b"second")
    assert store.get("t1/ing1") == b"second"


def test_get_missing_returns_none(store: AzureBlobReportBlobStore) -> None:
    assert store.get("t1/missing") is None


def test_delete_missing_is_safe(store: AzureBlobReportBlobStore) -> None:
    store.delete("t1/never-existed")  # must not raise


def test_delete_then_get_returns_none(store: AzureBlobReportBlobStore) -> None:
    store.put("t1/ing1", b"data")
    store.delete("t1/ing1")
    assert store.get("t1/ing1") is None
    store.delete("t1/ing1")  # repeated delete must remain safe


def test_put_if_absent_succeeds_on_fresh_key(store: AzureBlobReportBlobStore) -> None:
    assert store.put_if_absent("t1/ing1", b"data") is True
    assert store.get("t1/ing1") == b"data"


def test_put_if_absent_never_overwrites_existing(store: AzureBlobReportBlobStore) -> None:
    store.put_if_absent("t1/ing1", b"original")
    result = store.put_if_absent("t1/ing1", b"attempted overwrite")
    assert result is False
    assert store.get("t1/ing1") == b"original"


def test_put_if_absent_rejects_non_bytes() -> None:
    from azure.storage.blob import BlobServiceClient

    # Constructing a store is enough; no network call should occur before
    # the type check raises.
    store = AzureBlobReportBlobStore(
        BlobServiceClient(account_url="https://example.blob.core.windows.net", credential=None),
        container_name="reports",
    )
    with pytest.raises(TypeError):
        store.put_if_absent("t1/ing1", "not bytes")  # type: ignore[arg-type]


class TestConcurrentConditionalCreate:
    def test_exactly_one_of_many_concurrent_writers_wins(
        self, store: AzureBlobReportBlobStore
    ) -> None:
        """The core conditional-create proof: `CONCURRENCY` real threads
        race `put_if_absent` for the exact same key -- Azure Blob
        Storage's own `If-None-Match: *` precondition must guarantee
        exactly one wins, never a silent overwrite (task 4's explicit
        requirement).
        """
        concurrency = 20

        def attempt(i: int) -> bool:
            return store.put_if_absent("t1/shared-key", f"writer-{i}".encode())

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(attempt, range(concurrency)))

        assert results.count(True) == 1
        # The stored content must be from whichever single writer won --
        # never corrupted, never a mix of two writers' bytes.
        stored = store.get("t1/shared-key")
        assert stored is not None
        assert stored.decode().startswith("writer-")
