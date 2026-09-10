"""Tests for `scripts/scan_for_secrets.py` (correction pass, item 9) --
the replacement for the workflow's original whole-`tests/`-directory
secret-scan exclusion, which would have silently ignored a real,
accidentally-committed credential anywhere in that entire tree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scan_for_secrets as scanner  # noqa: E402


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, path: str, content: str) -> None:
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=repo, check=True)


class TestScanRejectsRealSecretsRegardlessOfLocation:
    """The exact property the original whole-directory exclusion broke:
    a real secret-shaped string anywhere under `tests/` must still be
    caught -- not just in production code or documentation.
    """

    @pytest.mark.parametrize(
        "secret_line",
        [
            "AKIA1234567890ABCDEF",
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "sk-abcdefghijklmnopqrstu",
        ],
    )
    def test_a_real_secret_in_a_test_file_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret_line: str
    ) -> None:
        repo = _git_repo(tmp_path)
        _commit(repo, "tests/test_something_unrelated.py", f'VALUE = "{secret_line}"\n')
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations, f"expected a violation for {secret_line!r}"

    def test_a_real_secret_in_production_code_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        _commit(repo, "src/app/config.py", 'API_KEY = "AKIA1234567890ABCDEF"\n')
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations

    def test_a_real_secret_in_documentation_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        _commit(repo, "docs/setup.md", "Use this key: AKIA1234567890ABCDEF\n")
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations


class TestScanExecutionErrorsFailClosed:
    def test_git_ls_files_failure_propagates_not_silently_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_tracked_files` uses `check=True` -- a real `git` failure
        (e.g. not actually inside a git repository) must raise, never be
        swallowed into an empty, falsely-clean scan.
        """
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        monkeypatch.chdir(not_a_repo)
        with pytest.raises(subprocess.CalledProcessError):
            scanner._tracked_files()


class TestNarrowAllowlistOnly:
    def test_the_exact_allowlisted_sentinel_in_its_own_file_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        _commit(
            repo,
            "tests/test_uploader_transport_response.py",
            'secret_shaped_body = b\'{"unexpected": "AKIAFAKESENTINELVALUE12345"}\'\n',
        )
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations == []

    def test_the_same_allowlisted_substring_in_a_different_file_is_still_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The allowlist is scoped to `(file, matched substring)` pairs,
        never the matched substring alone -- the same fake sentinel
        value appearing in a *different* file must still be flagged
        (defense in depth: an allowlist keyed only on content, not
        location, would be far too easy to accidentally widen).
        """
        repo = _git_repo(tmp_path)
        _commit(
            repo,
            "tests/test_something_else.py",
            'VALUE = "AKIAFAKESENTINELVALUE12345"\n',
        )
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations

    def test_a_genuinely_different_secret_in_the_allowlisted_file_is_still_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The allowlist covers only the exact matched substring, not
        'anything in this file is exempt' -- a real secret added
        elsewhere in the same allowlisted file must still be caught.
        """
        repo = _git_repo(tmp_path)
        _commit(
            repo,
            "tests/test_uploader_transport_response.py",
            'sentinel = "AKIAFAKESENTINELVALUE12345"\nreal_looking = "AKIA1234567890ABCDEF"\n',
        )
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations, (
            "a second, different secret-shaped string in the same file must still be caught"
        )
        assert all("AKIAFAKESENTINELVALU" not in v for v in violations), (
            "the allowlisted sentinel itself must not be reported"
        )


class TestUvLockIsExcluded:
    def test_uv_lock_content_is_never_scanned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        # A real secret-shaped string, deliberately placed in uv.lock, to
        # prove the exclusion actually suppresses scanning that file --
        # never because no such content happens to exist there today.
        _commit(repo, "uv.lock", "# AKIA1234567890ABCDEF\n")
        monkeypatch.chdir(repo)
        violations = scanner.scan()
        assert violations == []


class TestMainCli:
    def test_clean_tree_exits_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _git_repo(tmp_path)
        _commit(repo, "src/app.py", "print('hello world')\n")
        monkeypatch.chdir(repo)
        assert scanner.main([]) == 0

    def test_dirty_tree_exits_one_and_reports_to_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _git_repo(tmp_path)
        _commit(repo, "src/app.py", 'KEY = "AKIA1234567890ABCDEF"\n')
        monkeypatch.chdir(repo)
        exit_code = scanner.main([])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "AKIA1234567890ABCDEF" in captured.err
