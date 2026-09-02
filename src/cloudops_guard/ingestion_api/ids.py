"""Opaque identifier generation (`docs/milestones/v0.4.0-ingestion-api.md`
§G, "Enumeration of ingestion IDs"): every ID here is server-generated,
high-entropy, and non-sequential -- never derived from tenant ID,
timestamp ordering, or an incrementing counter.
"""

from __future__ import annotations

import uuid


def generate_request_id() -> str:
    """A fresh, opaque `request_id` -- generated for every HTTP request,
    success or failure alike (§E's opening paragraph).
    """
    return f"req_{uuid.uuid4().hex}"


def generate_ingestion_id() -> str:
    """A fresh, opaque `ingestion_id` -- §G's proposed UUIDv4 form."""
    return f"ing_{uuid.uuid4().hex}"
