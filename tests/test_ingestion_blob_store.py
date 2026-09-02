"""Tests for `InMemoryReportBlobStore` and `storage_keys.derive_storage_key`."""

from __future__ import annotations

import threading

import pytest

from cloudops_guard.ingestion.errors import InvalidIdentifierError
from cloudops_guard.ingestion.reference import InMemoryReportBlobStore
from cloudops_guard.ingestion.storage_keys import derive_storage_key


class TestBlobStorePutGetDelete:
    def test_put_then_get_roundtrips_exact_bytes(self) -> None:
        store = InMemoryReportBlobStore()
        data = b"\x00\x01report-bytes\xff"
        store.put("tenant-a/ing-1", data)
        assert store.get("tenant-a/ing-1") == data

    def test_get_missing_key_returns_none(self) -> None:
        store = InMemoryReportBlobStore()
        assert store.get("tenant-a/missing") is None

    def test_delete_then_get_returns_none(self) -> None:
        store = InMemoryReportBlobStore()
        store.put("tenant-a/ing-1", b"data")
        store.delete("tenant-a/ing-1")
        assert store.get("tenant-a/ing-1") is None

    def test_repeated_delete_is_safe(self) -> None:
        store = InMemoryReportBlobStore()
        store.delete("tenant-a/missing")
        store.delete("tenant-a/missing")

    def test_delete_of_never_stored_key_is_safe(self) -> None:
        store = InMemoryReportBlobStore()
        store.put("tenant-a/ing-1", b"data")
        store.delete("tenant-a/other")
        assert store.get("tenant-a/ing-1") == b"data"

    def test_put_rejects_non_bytes(self) -> None:
        store = InMemoryReportBlobStore()
        with pytest.raises(TypeError):
            store.put("tenant-a/ing-1", "not-bytes")  # type: ignore[arg-type]

    def test_put_accepts_bytearray(self) -> None:
        store = InMemoryReportBlobStore()
        store.put("tenant-a/ing-1", bytearray(b"data"))
        assert store.get("tenant-a/ing-1") == b"data"

    def test_stored_bytes_are_decoupled_from_a_mutated_source_bytearray(self) -> None:
        store = InMemoryReportBlobStore()
        source = bytearray(b"original")
        store.put("tenant-a/ing-1", source)
        source[0:1] = b"X"
        assert store.get("tenant-a/ing-1") == b"original"

    def test_overwriting_a_key_replaces_its_value(self) -> None:
        store = InMemoryReportBlobStore()
        store.put("tenant-a/ing-1", b"first")
        store.put("tenant-a/ing-1", b"second")
        assert store.get("tenant-a/ing-1") == b"second"

    def test_tenant_scoped_keys_do_not_collide(self) -> None:
        store = InMemoryReportBlobStore()
        key_a = derive_storage_key("tenant-a", "ing-1")
        key_b = derive_storage_key("tenant-b", "ing-1")
        store.put(key_a, b"a-data")
        store.put(key_b, b"b-data")
        assert store.get(key_a) == b"a-data"
        assert store.get(key_b) == b"b-data"


class TestBlobStorePutIfAbsent:
    """`put_if_absent` (Phase 4D correction, `interfaces.ReportBlobStore`):
    the safe "reserve a brand-new key" primitive `POST /api/v1/reports`
    exclusively uses -- see that interface's docstring for why plain
    `put` cannot safely back that write path.
    """

    def test_fresh_key_stores_bytes_and_returns_true(self) -> None:
        store = InMemoryReportBlobStore()
        data = b"\x00\x01report-bytes\xff"
        assert store.put_if_absent("tenant-a/ing-1", data) is True
        assert store.get("tenant-a/ing-1") == data

    def test_existing_key_from_plain_put_is_never_overwritten(self) -> None:
        store = InMemoryReportBlobStore()
        store.put("tenant-a/ing-1", b"original")
        assert store.put_if_absent("tenant-a/ing-1", b"attacker-or-collision-bytes") is False
        assert store.get("tenant-a/ing-1") == b"original"

    def test_existing_key_from_a_prior_put_if_absent_is_never_overwritten(self) -> None:
        store = InMemoryReportBlobStore()
        assert store.put_if_absent("tenant-a/ing-1", b"first") is True
        assert store.put_if_absent("tenant-a/ing-1", b"second") is False
        assert store.get("tenant-a/ing-1") == b"first"

    def test_put_if_absent_rejects_non_bytes(self) -> None:
        store = InMemoryReportBlobStore()
        with pytest.raises(TypeError):
            store.put_if_absent("tenant-a/ing-1", "not-bytes")  # type: ignore[arg-type]

    def test_put_if_absent_accepts_bytearray(self) -> None:
        store = InMemoryReportBlobStore()
        assert store.put_if_absent("tenant-a/ing-1", bytearray(b"data")) is True
        assert store.get("tenant-a/ing-1") == b"data"

    def test_stored_bytes_are_decoupled_from_a_mutated_source_bytearray(self) -> None:
        store = InMemoryReportBlobStore()
        source = bytearray(b"original")
        store.put_if_absent("tenant-a/ing-1", source)
        source[0:1] = b"X"
        assert store.get("tenant-a/ing-1") == b"original"

    def test_after_delete_put_if_absent_can_reserve_the_key_again(self) -> None:
        store = InMemoryReportBlobStore()
        store.put_if_absent("tenant-a/ing-1", b"first")
        store.delete("tenant-a/ing-1")
        assert store.put_if_absent("tenant-a/ing-1", b"second") is True
        assert store.get("tenant-a/ing-1") == b"second"

    def test_tenant_scoped_keys_do_not_collide(self) -> None:
        store = InMemoryReportBlobStore()
        key_a = derive_storage_key("tenant-a", "ing-1")
        key_b = derive_storage_key("tenant-b", "ing-1")
        assert store.put_if_absent(key_a, b"a-data") is True
        assert store.put_if_absent(key_b, b"b-data") is True
        assert store.get(key_a) == b"a-data"
        assert store.get(key_b) == b"b-data"

    def test_exactly_one_concurrent_writer_wins_for_the_same_key(self) -> None:
        # The core atomicity guarantee: under many threads racing
        # put_if_absent for the same storage_key, exactly one must
        # succeed, its bytes (never any other writer's) must be the ones
        # actually stored, and every other writer must observe False and
        # never have touched the store.
        writer_count = 200
        store = InMemoryReportBlobStore()
        results: list[tuple[int, bool]] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(writer_count)

        def worker(writer_id: int) -> None:
            barrier.wait()
            won = store.put_if_absent("tenant-a/ing-1", f"writer-{writer_id}".encode())
            with results_lock:
                results.append((writer_id, won))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(writer_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [writer_id for writer_id, won in results if won]
        assert len(winners) == 1
        assert store.get("tenant-a/ing-1") == f"writer-{winners[0]}".encode()

    def test_concurrent_writes_to_distinct_keys_all_succeed(self) -> None:
        writer_count = 100
        store = InMemoryReportBlobStore()
        barrier = threading.Barrier(writer_count)

        def worker(writer_id: int) -> None:
            barrier.wait()
            key = f"tenant-a/ing-{writer_id}"
            assert store.put_if_absent(key, f"data-{writer_id}".encode()) is True

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(writer_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for i in range(writer_count):
            assert store.get(f"tenant-a/ing-{i}") == f"data-{i}".encode()


class TestDeriveStorageKey:
    def test_composes_tenant_and_ingestion_id(self) -> None:
        assert derive_storage_key("tenant-a", "ing-1") == "tenant-a/ing-1"

    @pytest.mark.parametrize("field", ["tenant_id", "ingestion_id"])
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "a/b",
            "a\\b",
            "../etc/passwd",
            "..",
            ".",
            "/etc/passwd",
            "a\x00b",
            " leading-space",
            "trailing-space ",
            "a" * 257,
        ],
    )
    def test_rejects_dangerous_or_malformed_identifiers(self, field: str, value: str) -> None:
        kwargs = {"tenant_id": "tenant-a", "ingestion_id": "ing-1"}
        kwargs[field] = value
        with pytest.raises(InvalidIdentifierError):
            derive_storage_key(**kwargs)

    def test_rejects_non_string_identifier(self) -> None:
        with pytest.raises(InvalidIdentifierError):
            derive_storage_key(123, "ing-1")  # type: ignore[arg-type]

    def test_never_normalizes_a_rejected_value_into_an_accepted_one(self) -> None:
        # A traversal-laden identifier must be rejected outright -- never
        # silently stripped/collapsed into something that would still
        # compose a valid-looking key.
        with pytest.raises(InvalidIdentifierError):
            derive_storage_key("tenant-a", "../ing-1")

    def test_distinct_identifiers_never_derive_a_colliding_key(self) -> None:
        # These would collide under naive string concatenation without a
        # separator; the "/" separator keeps them distinct.
        key_1 = derive_storage_key("tenant-a", "ing-1")
        key_2 = derive_storage_key("tenant-a-ing", "1")
        assert key_1 == "tenant-a/ing-1"
        assert key_2 == "tenant-a-ing/1"
        assert key_1 != key_2
