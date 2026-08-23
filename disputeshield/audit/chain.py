"""The hash chain: canonical serialisation and link computation.

Pure functions, no database. That matters for two reasons: it is exhaustively
testable, and an auditor recomputing the chain independently needs an algorithm
they can reimplement from the description rather than from our code.

The canonical form is JSON with sorted keys, no insignificant whitespace and no
non-ASCII escaping surprises. Any ambiguity in serialisation is an ambiguity in
the hash, which would make an honest chain look tampered.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "sha256:" + "0" * 64

# Exactly the fields the hash covers, in order. Adding a field to AuditRecord
# without adding it here means a field nobody's hash protects — so the set is
# explicit, and a test asserts it covers every non-derived model field.
COVERED_FIELDS = (
    "id",
    "tenant_id",
    "sequence",
    "event_type",
    "occurred_at",
    "actor_type",
    "actor_id",
    "actor_ip",
    "subject_type",
    "subject_id",
    "payload",
    "corrects",
)


def canonicalise(content: dict[str, Any]) -> bytes:
    missing = set(COVERED_FIELDS) - set(content)
    if missing:
        raise ValueError(f"Cannot hash a record missing covered fields: {sorted(missing)}")
    ordered = {field: content[field] for field in COVERED_FIELDS}
    return json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")


def compute_hash(content: dict[str, Any], prev_hash: str) -> str:
    digest = hashlib.sha256()
    digest.update(canonicalise(content))
    digest.update(b"\x00")  # unambiguous separator: content cannot forge a prefix
    digest.update(prev_hash.encode("ascii"))
    return "sha256:" + digest.hexdigest()


def record_content(record) -> dict[str, Any]:
    """Extract the covered fields from an AuditRecord instance or row dict."""
    if isinstance(record, dict):
        return {field: record[field] for field in COVERED_FIELDS}
    return {field: getattr(record, field) for field in COVERED_FIELDS}
