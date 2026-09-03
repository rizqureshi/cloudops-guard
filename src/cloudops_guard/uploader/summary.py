"""Local, pre-confirmation summary text for `cloudops-guard upload`.

Pure formatting, no I/O of any kind: only `platform`, `endpoint`, total
finding count, severity totals, local report file size, and the
fingerprint -- never finding evidence, resource names, other report
content, credentials, an authorization header, or a server-derived
tenant name (there is no server-derived value available at all before
confirmation -- the server has not been contacted yet).
"""

from __future__ import annotations

from .local_report import LocalReport


def format_local_summary(local_report: LocalReport, endpoint: str) -> str:
    counts = local_report.severity_counts
    lines = [
        "",
        "CloudOps Guard upload summary (local only -- nothing sent yet):",
        f"  Platform:          {local_report.platform}",
        f"  Endpoint:          {endpoint}",
        f"  Findings (total):  {local_report.finding_count}",
        f"    Critical: {counts.get('critical', 0)}",
        f"    High:     {counts.get('high', 0)}",
        f"    Medium:   {counts.get('medium', 0)}",
        f"    Low:      {counts.get('low', 0)}",
        f"  Report file size:  {local_report.file_size_bytes} bytes",
        f"  Report fingerprint: {local_report.fingerprint}",
        "",
    ]
    return "\n".join(lines)
