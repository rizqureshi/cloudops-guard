"""**Correction pass, item 6.** Report-content-leakage proof for
`cloudops-guard upload`: distinctive sentinel strings placed in
report-supplied content (an unsupported `platform` value, a duplicate
JSON object key, and finding `evidence`/`resource_name` fields) must
never appear in stdout, a raised exception's message or `repr`, or
`repr(LocalReport)`/`repr(UploadResult)` -- mirroring
`test_uploader_credential_leakage.py`'s pattern, but for report content
rather than the bearer token.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudops_guard.uploader.errors import LocalReportError
from cloudops_guard.uploader.local_report import load_and_validate_local_report
from cloudops_guard.uploader.service import run_upload
from tests.ingestion_api_support import valid_kubernetes_report

ENDPOINT = "https://ingest.example.com/api/v1/reports"

#: Unmistakable and secret-shaped -- unambiguous if it leaks anywhere it
#: should not. Deliberately plain ASCII: `repr()` (used by `!r`
#: formatting and by exception/dataclass reprs generally) escapes a raw
#: control character into a printable `\xNN`-style sequence, which would
#: make a naive substring check pass even though the *content* still
#: leaked -- `SENTINEL_CORE` alone is what every leakage assertion below
#: checks for, so escaping can never hide a real leak. `SENTINEL` wraps
#: it in a control character (an ANSI escape) for the tests that also
#: want to confirm no literal control byte reaches raw output.
SENTINEL_CORE = "SENTINELREPORTCONTENTDONOTLEAK" + ("Q" * 20)
SENTINEL = f"\x1b[31m{SENTINEL_CORE}\x1b[0m"


def _report_dir(tmp_path: Path) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    return report_dir


class TestUnsupportedPlatformValueNeverLeaks:
    def test_sentinel_platform_value_never_appears_in_the_error(self, tmp_path: Path) -> None:
        report = valid_kubernetes_report()
        report["platform"] = SENTINEL
        report_dir = _report_dir(tmp_path)
        (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

        with pytest.raises(LocalReportError) as exc_info:
            load_and_validate_local_report(report_dir)

        assert SENTINEL not in str(exc_info.value)
        assert SENTINEL not in repr(exc_info.value)
        assert SENTINEL_CORE not in str(exc_info.value)
        assert SENTINEL_CORE not in repr(exc_info.value)


class TestDuplicateObjectKeyNeverLeaks:
    def test_sentinel_duplicate_key_name_never_appears_in_the_error(self, tmp_path: Path) -> None:
        report_dir = _report_dir(tmp_path)
        # Deliberately uses SENTINEL_CORE alone (no raw control character)
        # as the duplicate key: a literal control character embedded in a
        # JSON string is itself invalid per RFC 8259 and gets rejected by
        # the underlying `json` module before `_object_pairs_hook`'s own
        # duplicate-key detection ever runs -- using SENTINEL here would
        # silently test the wrong code path (an "invalid control
        # character" decode error, not the duplicate-key one).
        raw = ('{"' + SENTINEL_CORE + '": "a", "' + SENTINEL_CORE + '": "b"}').encode("utf-8")
        (report_dir / "report.json").write_bytes(raw)

        with pytest.raises(LocalReportError) as exc_info:
            load_and_validate_local_report(report_dir)

        assert SENTINEL_CORE not in str(exc_info.value)
        assert SENTINEL_CORE not in repr(exc_info.value)


class TestNestedFindingContentNeverLeaksThroughReprOrOutput:
    def _sentinel_report(self) -> dict:
        finding = {
            "check_id": "K8S-IMG-001",
            "title": "Container uses the 'latest' tag",
            "severity": "high",
            "cluster_context": "prod",
            "namespace": "default",
            "resource_kind": "Pod",
            "resource_name": f"pod-{SENTINEL}",
            "container_name": "web",
            "evidence": f"image: {SENTINEL}",
            "impact": "Non-reproducible deployments.",
            "recommendation": "Pin to a specific tag or digest.",
            "auto_remediable": False,
            "audited_at": "2026-01-01T00:00:00Z",
        }
        return valid_kubernetes_report(findings=[finding])

    def test_repr_of_local_report_never_contains_sentinel_content(self, tmp_path: Path) -> None:
        report_dir = _report_dir(tmp_path)
        (report_dir / "report.json").write_text(
            json.dumps(self._sentinel_report()), encoding="utf-8"
        )

        result = load_and_validate_local_report(report_dir)
        assert SENTINEL not in repr(result)
        assert SENTINEL_CORE not in repr(result)

    def test_repr_of_upload_result_never_contains_sentinel_content(self, tmp_path: Path) -> None:
        report_dir = _report_dir(tmp_path)
        (report_dir / "report.json").write_text(
            json.dumps(self._sentinel_report()), encoding="utf-8"
        )
        captured: list[str] = []

        result = run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=True,
            yes=False,
            print_fn=captured.append,
        )

        assert SENTINEL not in repr(result)
        assert SENTINEL not in repr(result.local_report)
        assert SENTINEL_CORE not in repr(result)
        assert SENTINEL_CORE not in repr(result.local_report)
        for line in captured:
            assert SENTINEL not in line
            assert SENTINEL_CORE not in line

    def test_dry_run_stdout_never_contains_sentinel_content(self, tmp_path: Path) -> None:
        report_dir = _report_dir(tmp_path)
        (report_dir / "report.json").write_text(
            json.dumps(self._sentinel_report()), encoding="utf-8"
        )
        captured: list[str] = []

        run_upload(
            report_dir=report_dir,
            endpoint_raw=ENDPOINT,
            dry_run=True,
            yes=False,
            print_fn=captured.append,
        )

        for line in captured:
            assert SENTINEL not in line
            assert SENTINEL_CORE not in line
