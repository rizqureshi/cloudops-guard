#!/usr/bin/env python3
"""Scans the entire checked-out git tree for secret-shaped content
(correction pass, item 9).

**The gap this replaces**: the original version of this scan excluded
the *entire* `tests/` directory from the secret-shaped-content regex, on
the reasoning that this project's own tests deliberately embed
secret-*shaped* sentinel values (e.g. a fake AKIA-prefixed token) to
prove they never leak. That reasoning was correct for the two lines it
was actually protecting, but the exclusion itself was far broader than
that: it silently would have ignored a *real*, accidentally-committed
credential anywhere else in the entire `tests/` tree, which is exactly
the blind spot a secret scanner exists to close.

**The fix**: scan every git-tracked file (except `uv.lock`, a generated
dependency lockfile whose content is package name/version/hash metadata,
never a secret) with no directory exclusion at all, and allowlist only
the exact, narrow, already-reviewed `(file, matched substring)` pairs
below -- a real secret sharing the same shape anywhere else, including a
*different* value in the very same file, still fails the scan.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_SECRET_PATTERN = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|-----BEGIN (?:RSA|EC|OPENSSH) PRIVATE KEY-----"
    r"|sk-[a-zA-Z0-9]{20,}"
)

#: Files entirely excluded from scanning -- generated content, never
#: hand-authored, never a place a real secret would be intentionally or
#: accidentally typed.
_EXCLUDED_FILES = frozenset({"uv.lock"})

#: Exact, narrow allowlist of already-reviewed matches: this project's
#: own security tests deliberately embed secret-*shaped* sentinel values
#: to prove they never leak into an error message or log line. Each
#: entry is `(file_path, matched_substring)` -- a real secret sharing the
#: same shape in a *different* file, or even a *different* value in the
#: same file, is never covered by this allowlist and still fails the
#: scan.
_ALLOWED_MATCHES: frozenset[tuple[str, str]] = frozenset(
    {
        # The real committed sentinel is `AKIAFAKESENTINELVALUE12345`, but
        # `AKIA[0-9A-Z]{16}` is an *exact* 16-character count after the
        # `AKIA` prefix, so it only ever matches this leading 20-character
        # substring, never the sentinel's own longer, full value.
        (
            "tests/test_uploader_transport_response.py",
            "AKIAFAKESENTINELVALU",
        ),
    }
)


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, timeout=30
    )
    return [line for line in result.stdout.splitlines() if line]


def scan() -> list[str]:
    """Returns a list of human-readable violation descriptions -- empty
    if the tree is clean. Never raises for a normal "secret found" case;
    only a genuine I/O error propagates.
    """
    violations: list[str] = []
    for file_path in _tracked_files():
        if file_path in _EXCLUDED_FILES:
            continue
        path = Path(file_path)
        if not path.is_file():
            continue  # a tracked path that is a submodule/symlink-to-nowhere, etc.
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            violations.append(f"{file_path}: could not read file ({exc})")
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in _SECRET_PATTERN.finditer(line):
                matched = match.group(0)
                if (file_path, matched) in _ALLOWED_MATCHES:
                    continue
                violations.append(f"{file_path}:{line_number}: secret-shaped content: {matched!r}")
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI arguments -- this script always scans the full tree
    violations = scan()
    if violations:
        print("possible secret-shaped content found in the checked-out tree:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("secret scan clean: no unallowlisted secret-shaped content found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
