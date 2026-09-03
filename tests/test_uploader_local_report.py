"""Local report loading/validation/platform-dispatch tests for
`cloudops_guard.uploader.local_report` -- everything §4/§12 "Contract and
fingerprinting" of the Phase 4E task requires, entirely local, no
network access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cloudops_guard.ingestion_api.limits import MAX_REPORT_BYTES
from cloudops_guard.uploader.errors import LocalReportError
from cloudops_guard.uploader.local_report import load_and_validate_local_report
from tests.ingestion_api_support import valid_gitlab_report, valid_kubernetes_report

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ingestion_fingerprint_fixtures_v1.json"


def _write_report(tmp_path: Path, report: dict) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return report_dir


class TestPlatformDispatch:
    def test_no_platform_field_selects_kubernetes(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_kubernetes_report())
        result = load_and_validate_local_report(report_dir)
        assert result.platform == "kubernetes"

    def test_platform_gitlab_selects_gitlab(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, valid_gitlab_report())
        result = load_and_validate_local_report(report_dir)
        assert result.platform == "gitlab"

    def test_unsupported_platform_marker_is_rejected(self, tmp_path: Path) -> None:
        report = valid_kubernetes_report()
        report["platform"] = "aws"
        report_dir = _write_report(tmp_path, report)
        with pytest.raises(LocalReportError, match="unsupported platform marker"):
            load_and_validate_local_report(report_dir)

    def test_gitlab_validation_failure_is_not_retried_as_kubernetes(self, tmp_path: Path) -> None:
        # A GitLab report missing a required GitLab-only field
        # (project_id) must fail as a GitLab validation error -- never
        # silently re-attempted against the Kubernetes model (which would
        # also reject it, but for a completely different, misleading
        # reason).
        report = valid_gitlab_report()
        del report["project_id"]
        report_dir = _write_report(tmp_path, report)
        with pytest.raises(LocalReportError, match="report-contract validation"):
            load_and_validate_local_report(report_dir)


class TestFileRequirements:
    def test_missing_report_directory_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(LocalReportError, match="does not exist"):
            load_and_validate_local_report(tmp_path / "does-not-exist")

    def test_missing_report_file_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        with pytest.raises(LocalReportError, match="does not exist"):
            load_and_validate_local_report(report_dir)

    def test_report_dir_pointing_at_a_directory_named_report_json_is_rejected(
        self, tmp_path: Path
    ) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").mkdir()
        with pytest.raises(LocalReportError, match="not a regular file"):
            load_and_validate_local_report(report_dir)

    def test_oversized_file_is_rejected_before_being_read(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        path = report_dir / "report.json"
        # Sparse file: allocate a file whose *declared* size exceeds the
        # limit without actually writing that many real bytes -- proves
        # the size check runs from the open handle's own fstat() alone,
        # before any read.
        with path.open("wb") as f:
            f.seek(MAX_REPORT_BYTES + 1)
            f.write(b"\0")
        with pytest.raises(LocalReportError, match="exceeding the"):
            load_and_validate_local_report(report_dir)


class TestFileSizeToctouCorrection:
    """**Correction pass, item 4.** The original implementation checked
    size via a separate `Path.stat()` call and then read via a separate
    `Path.read_bytes()` call -- two independent filesystem lookups with a
    window between them. These tests prove the fix: one opened file
    descriptor is used for both the metadata check and the read, and the
    *actual* bytes read -- never a possibly-stale declared size -- is
    what enforces the ceiling.
    """

    def test_actual_oversized_content_is_rejected_even_when_declared_metadata_is_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulates a file that has already grown past the ceiling by
        # the time it is opened, while whatever `fstat()` happens to
        # report is stale/misleading (here, deliberately tiny) -- proves
        # the bounded read's own actual-byte-count check is the real
        # enforcement, never the declared size alone.
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        path = report_dir / "report.json"
        oversized_body = b"{" + b" " * (MAX_REPORT_BYTES + 500) + b"}"
        path.write_bytes(oversized_body)

        real_fstat = os.fstat

        def stale_fstat(fd: int):
            real_result = real_fstat(fd)
            return os.stat_result(
                (
                    real_result.st_mode,
                    real_result.st_ino,
                    real_result.st_dev,
                    real_result.st_nlink,
                    real_result.st_uid,
                    real_result.st_gid,
                    1,  # a deliberately tiny, misleading declared size
                    real_result.st_atime,
                    real_result.st_mtime,
                    real_result.st_ctime,
                )
            )

        monkeypatch.setattr("cloudops_guard.uploader.local_report.os.fstat", stale_fstat)

        with pytest.raises(LocalReportError, match="exceeding the"):
            load_and_validate_local_report(report_dir)

    def test_metadata_and_read_use_the_same_open_file_descriptor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A second, independent path-based lookup (`Path.stat()` /
        # `os.stat()`) would be exactly the TOCTOU gap this correction
        # closes -- asserting it is never called proves the only
        # metadata check performed is the fstat() on the already-open
        # handle used for the read.
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        report_path = report_dir / "report.json"
        report_path.write_text(json.dumps(valid_kubernetes_report()), encoding="utf-8")

        real_stat = Path.stat

        def guarded_path_stat(self: Path, *args: object, **kwargs: object) -> object:
            if self == report_path:
                raise AssertionError(
                    "a separate Path.stat() call on report.json must never be used "
                    "-- only fstat() on the single already-open file handle."
                )
            # Any other Path.stat() call (pytest's own bookkeeping, tmp_path
            # cleanup, etc.) is unrelated to this module and must proceed
            # normally -- only report.json's own metadata lookup is guarded.
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", guarded_path_stat)

        result = load_and_validate_local_report(report_dir)
        assert result.platform == "kubernetes"

    def test_declared_oversize_rejection_never_reads_the_full_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        path = report_dir / "report.json"
        with path.open("wb") as f:
            f.seek(MAX_REPORT_BYTES + 1)
            f.write(b"\0")

        real_open = Path.open

        class _NoReadProxy:
            def __init__(self, real_handle: object) -> None:
                self._real_handle = real_handle

            def fileno(self) -> int:
                return self._real_handle.fileno()  # type: ignore[no-any-return]

            def read(self, *args: object, **kwargs: object) -> bytes:
                raise AssertionError(
                    "the declared-oversize rejection must happen before any read() call."
                )

            def __enter__(self) -> _NoReadProxy:
                return self

            def __exit__(self, *exc_info: object) -> bool:
                self._real_handle.close()  # type: ignore[attr-defined]
                return False

        def fake_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
            if self == path and mode == "rb":
                return _NoReadProxy(real_open(self, mode, *args, **kwargs))
            return real_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        with pytest.raises(LocalReportError, match="exceeding the"):
            load_and_validate_local_report(report_dir)

    def test_no_network_or_credential_access_on_size_rejection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        path = report_dir / "report.json"
        with path.open("wb") as f:
            f.seek(MAX_REPORT_BYTES + 1)
            f.write(b"\0")

        import socket

        def forbidden_socket(*args: object, **kwargs: object) -> object:
            raise AssertionError("no socket may be opened while validating a local report.")

        monkeypatch.setattr(socket, "socket", forbidden_socket)
        monkeypatch.delenv("CLOUDOPS_GUARD_INGESTION_TOKEN", raising=False)

        with pytest.raises(LocalReportError, match="exceeding the"):
            load_and_validate_local_report(report_dir)


class TestStrictJsonRejections:
    def test_invalid_json_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b"{not valid json")
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_invalid_utf8_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b'{"a": "\xff\xfe"}')
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_duplicate_object_key_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(
            b'{"cluster_context":"a","cluster_context":"b","namespace_filter":null,'
            b'"generated_at":"2026-01-01T00:00:00Z","findings":[],"summary":{}}'
        )
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_bare_nan_literal_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b'{"cluster_context": NaN}')
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_bare_infinity_literal_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b'{"cluster_context": Infinity}')
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_lone_surrogate_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "report.json").write_bytes(b'{"cluster_context": "\\ud800"}')
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_unsafe_integer_is_rejected(self, tmp_path: Path) -> None:
        report = valid_kubernetes_report()
        report["extra_unsafe_number"] = 2**53
        report_dir = _write_report(tmp_path, report)
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)

    def test_excessive_nesting_is_rejected(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        nested = ("[" * 1000) + "1" + ("]" * 1000)
        (report_dir / "report.json").write_bytes(nested.encode())
        with pytest.raises(LocalReportError, match="strict JSON validation"):
            load_and_validate_local_report(report_dir)


class TestReportContractRejections:
    def test_invalid_structure_is_rejected(self, tmp_path: Path) -> None:
        report_dir = _write_report(tmp_path, {"not": "a valid report"})
        with pytest.raises(LocalReportError, match="report-contract validation"):
            load_and_validate_local_report(report_dir)

    def test_inconsistent_summary_is_rejected(self, tmp_path: Path) -> None:
        report = valid_kubernetes_report()
        report["summary"] = {"critical": 99, "high": 0, "medium": 0, "low": 0}
        report_dir = _write_report(tmp_path, report)
        with pytest.raises(LocalReportError, match="report-contract validation"):
            load_and_validate_local_report(report_dir)

    def test_too_many_findings_is_rejected(self, tmp_path: Path) -> None:
        finding = valid_kubernetes_report()["findings"][0]
        report = valid_kubernetes_report(findings=[finding] * 10_001)
        report["summary"] = {"critical": 0, "high": 0, "medium": 10_001, "low": 0}
        report_dir = _write_report(tmp_path, report)
        with pytest.raises(LocalReportError, match="report-contract validation"):
            load_and_validate_local_report(report_dir)


class TestSuccessfulLoad:
    def test_kubernetes_report_loads_with_correct_summary(self, tmp_path: Path) -> None:
        finding = valid_kubernetes_report()["findings"][0]  # severity: high
        finding_medium = dict(finding, severity="medium")
        report = valid_kubernetes_report(findings=[finding, finding_medium])
        report["summary"] = {"critical": 0, "high": 1, "medium": 1, "low": 0}
        report_dir = _write_report(tmp_path, report)
        result = load_and_validate_local_report(report_dir)
        assert result.finding_count == 2
        assert result.severity_counts == {"critical": 0, "high": 1, "medium": 1, "low": 0}
        assert result.file_size_bytes == (report_dir / "report.json").stat().st_size
        assert result.fingerprint.startswith("sha256:")

    def test_gitlab_report_loads_with_correct_summary(self, tmp_path: Path) -> None:
        report = valid_gitlab_report()
        report_dir = _write_report(tmp_path, report)
        result = load_and_validate_local_report(report_dir)
        assert result.platform == "gitlab"
        assert result.finding_count == len(report["findings"])


class TestSharedFingerprintFixtureParity:
    """Consumes `tests/fixtures/ingestion_fingerprint_fixtures_v1.json`
    directly, through the uploader's own on-disk loading path (writing
    each fixture's `report` to a real `report.json` and loading it) --
    proving the CLI uploader's fingerprint matches the shared,
    versioned fixture byte-for-byte, exactly like the ingestion API's
    own `tests/test_ingestion_api_shared_fingerprint_fixtures.py`.
    """

    @staticmethod
    def _cases() -> list[dict]:
        doc = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        return doc["cases"]

    @pytest.mark.parametrize("case", _cases(), ids=[c["name"] for c in _cases()])
    def test_uploader_fingerprint_matches_the_hard_coded_expected_fingerprint(
        self, case: dict, tmp_path: Path
    ) -> None:
        # GitLab fixture reports already carry their own required
        # "platform": "gitlab" field (GitLabAuditReport's own field);
        # Kubernetes fixture reports have none, matching this uploader's
        # own dispatch rule exactly -- no special-casing needed here.
        report_dir = _write_report(tmp_path, case["report"])
        result = load_and_validate_local_report(report_dir)
        assert result.platform == case["platform"]
        assert result.fingerprint == case["expected_fingerprint"]
