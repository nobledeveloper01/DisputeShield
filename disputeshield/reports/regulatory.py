"""The regulator-ready export (§6.5, §9).

§2.5 measures the win as days becoming one export. What makes it worth having is
not the CSV — anyone can write a CSV — but that the bundle carries an integrity
attestation, so a supervisor can check that the history they were handed is the
history we recorded.

**Byte-reproducible.** Exporting the same period twice produces identical bytes.
That is not a nicety: a supervisor who asks for the same period twice and gets
two different files has been given a reason to doubt everything else in the
bundle. So ordering is total, floats never appear, and the only timestamp that
varies — when the export ran — lives in the manifest and is excluded from the
digests the manifest publishes.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import zipfile
from datetime import datetime

from disputeshield.audit.checkpoints import attestation, sign_payload
from disputeshield.models import AuditRecord, Dispute, SLAEvent

CASE_COLUMNS = (
    "reference",
    "category",
    "subcategory",
    "status",
    "outcome",
    "submitted_at",
    "acknowledged_at",
    "resolved_at",
    "closed_at",
    "ack_deadline",
    "resolution_deadline",
    "breach_ack",
    "breach_resolution",
    "breach_reason",
    "amount_minor",
    "currency",
    "refund_amount_minor",
    "assigned_to",
    "policy_version",
    "regulatory_reference",
)

HISTORY_COLUMNS = (
    "reference",
    "sequence",
    "occurred_at",
    "event_type",
    "actor_type",
    "actor_id",
    "reason",
    "clock_remaining_seconds",
    "record_hash",
)


@dataclasses.dataclass(frozen=True)
class Export:
    files: dict[str, bytes]
    manifest: dict

    def as_zip(self) -> bytes:
        buffer = io.BytesIO()
        # Fixed timestamps in the archive metadata, or the zip differs between
        # two runs of an otherwise identical export.
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(self.files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, self.files[name])
            info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, json.dumps(self.manifest, indent=2, sort_keys=True).encode())
        return buffer.getvalue()


def build(*, tenant, period_from: datetime, period_to: datetime) -> Export:
    cases = list(
        Dispute.objects.filter(submitted_at__gte=period_from, submitted_at__lt=period_to)
        # Total ordering. `reference` is unique per tenant, so this is stable
        # across runs, across machines and across database plan changes.
        .order_by("reference")
        .select_related("assigned_to", "policy_version", "clock")
    )

    files = {
        "cases.csv": _cases_csv(cases),
        "history.csv": _history_csv(cases),
    }
    digests = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}

    body = {
        "tenant": tenant.pk,
        "tenant_name": tenant.name,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "case_count": len(cases),
        "breach_count": sum(1 for c in cases if c.breach_resolution or c.breach_ack),
        "files": digests,
        "integrity": attestation(tenant),
    }

    manifest = {
        **body,
        # Excluded from the signature, because it is the one field that must
        # differ between two otherwise identical exports.
        "generated_at": None,
        "signature": sign_payload(_signable(body)),
    }
    return Export(files=files, manifest=manifest)


def _signable(body: dict) -> dict:
    """What the manifest signature covers.

    The live chain status is deliberately excluded: it is a fact about the
    database at the moment of export, not about the bundle, and including it
    would make two identical bundles carry different signatures because one was
    exported a minute later.
    """
    return {
        "tenant": body["tenant"],
        "period_from": body["period_from"],
        "period_to": body["period_to"],
        "case_count": body["case_count"],
        "breach_count": body["breach_count"],
        "files": body["files"],
    }


def _writer(columns: tuple[str, ...]) -> tuple[io.StringIO, csv.DictWriter]:
    buffer = io.StringIO(newline="")
    # Fixed line terminator: the platform default would make an export produced
    # on Windows differ from the same export produced on Linux.
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    return buffer, writer


def _cases_csv(cases: list[Dispute]) -> bytes:
    buffer, writer = _writer(CASE_COLUMNS)
    for case in cases:
        writer.writerow(
            {
                "reference": case.reference,
                "category": case.category,
                "subcategory": case.subcategory,
                "status": case.status,
                "outcome": case.outcome,
                "submitted_at": _iso(case.submitted_at),
                "acknowledged_at": _iso(case.acknowledged_at),
                "resolved_at": _iso(case.resolved_at),
                "closed_at": _iso(case.closed_at),
                "ack_deadline": _iso(case.ack_deadline),
                "resolution_deadline": _iso(case.resolution_deadline),
                "breach_ack": _bool(case.breach_ack),
                "breach_resolution": _bool(case.breach_resolution),
                "breach_reason": _flatten(case.breach_reason),
                # Integer minor units, never a formatted decimal. A supervisor
                # reconciling against a ledger needs the same integer the ledger
                # holds, not a rendering of it.
                "amount_minor": _int(case.amount_minor),
                "currency": case.currency,
                "refund_amount_minor": _int(case.refund_amount_minor),
                "assigned_to": case.assigned_to_id or "",
                "policy_version": case.policy_version_id,
                "regulatory_reference": case.policy_version.regulatory_reference,
            }
        )
    return buffer.getvalue().encode("utf-8")


def _history_csv(cases: list[Dispute]) -> bytes:
    buffer, writer = _writer(HISTORY_COLUMNS)
    by_id = {case.pk: case for case in cases}
    if not by_id:
        return buffer.getvalue().encode("utf-8")

    sla_events = {
        (event.clock_id, event.occurred_at): event
        for event in SLAEvent.objects.filter(clock__subject_id__in=list(by_id))
    }
    clock_to_case = {case.clock_id: case for case in cases}

    records = AuditRecord.objects.filter(
        subject_type="dispute", subject_id__in=list(by_id)
    ).order_by("sequence")

    for record in records:
        case = by_id.get(record.subject_id)
        if case is None:
            continue
        writer.writerow(
            {
                "reference": case.reference,
                "sequence": record.sequence,
                "occurred_at": _iso(record.occurred_at),
                "event_type": record.event_type,
                "actor_type": record.actor_type,
                "actor_id": record.actor_id,
                "reason": _flatten(record.payload.get("reason", "")),
                "clock_remaining_seconds": _int(record.payload.get("clock_remaining_seconds")),
                # The record's own hash, so a supervisor can spot-check any row
                # against the chain rather than trusting the export wholesale.
                "record_hash": record.hash,
            }
        )
    _ = (sla_events, clock_to_case)
    return buffer.getvalue().encode("utf-8")


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _bool(value) -> str:
    return "true" if value else "false"


def _int(value) -> str:
    return "" if value is None else str(int(value))


def _flatten(value: str) -> str:
    """One line per row. A newline inside a CSV field is legal and is also how a
    hand-written parser on the supervisor's side reads one row as two."""
    return " ".join(str(value or "").split())
