"""Bringing history in from somewhere else (amplifier A18).

The single largest adoption blocker in the product: a compliance officer cannot
adopt a system that starts empty, because their retention obligation covers cases
that already exist, and running the old system alongside for seven years is not an
answer anybody accepts.

The hard part is not parsing. It is that **imported history must stay
distinguishable from native history, forever.** An imported case's trail carries
no integrity claim from us — we did not witness it — and the chain says so plainly
rather than absorbing foreign data and implying we vouch for it.

Two consequences fall out, and both are asserted:

  * Imported cases are excluded from live SLA computation. Their clocks are
    stopped on creation: a case closed in 2021 does not get a deadline in 2026,
    and a sweep that fired on one would page somebody about a five-year-old
    complaint.
  * The regulatory export visibly separates imported rows from native ones.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute, ImportBatch, SLAClock, SLAPolicy
from disputeshield.models.dispute import Status, hash_customer_ref

IMPORT_MARKER = "imported"

REQUIRED_COLUMNS = ("external_reference", "customer_ref", "category", "description", "opened_at")


@dataclasses.dataclass(frozen=True)
class ImportResult:
    batch: ImportBatch
    imported: tuple[str, ...]
    rejected: tuple[dict, ...]


class ImportRejected(ValueError):
    pass


def import_csv(
    *, tenant, content: bytes, source: str, imported_by: str, default_category: str = "other"
) -> ImportResult:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
    if missing:
        raise ImportRejected(f"the file is missing required columns: {sorted(missing)}")

    imported: list[str] = []
    rejected: list[dict] = []

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            tenant=tenant,
            source=source,
            source_digest=hashlib.sha256(content).hexdigest(),
            imported_at=timezone.now(),
            imported_by=imported_by,
        )

        for index, row in enumerate(reader, start=2):
            try:
                imported.append(_import_row(tenant, batch, row, default_category))
            except ImportRejected as exc:
                rejected.append({"line": index, "reason": str(exc)})

        batch.cases_imported = len(imported)
        batch.cases_rejected = len(rejected)
        batch.rejections = rejected[:200]
        batch.save(update_fields=["cases_imported", "cases_rejected", "rejections"])

        audit.append(
            tenant=tenant,
            event_type="import.completed",
            subject_type="import_batch",
            subject_id=batch.pk,
            actor_type="user",
            actor_id=imported_by,
            payload={
                "source": source,
                # What we were handed, so "this is the file you gave us" is provable.
                "source_digest": batch.source_digest,
                "imported": len(imported),
                "rejected": len(rejected),
                # Stated in the record, not only in the documentation.
                "disputeshield_witnessed": False,
            },
        )

    return ImportResult(batch=batch, imported=tuple(imported), rejected=tuple(rejected))


def _import_row(tenant, batch: ImportBatch, row: dict, default_category: str) -> str:
    reference = (row.get("external_reference") or "").strip()
    if not reference:
        raise ImportRejected("no external_reference")

    opened_at = _parse(row.get("opened_at"))
    if opened_at is None:
        raise ImportRejected(f"unreadable opened_at {row.get('opened_at')!r}")

    category = (row.get("category") or "").strip() or default_category
    policy = SLAPolicy.objects.filter(category=category).first()
    if policy is None or policy.current_version is None:
        policy = SLAPolicy.objects.filter(category=default_category).first()
    if policy is None or policy.current_version is None:
        raise ImportRejected(f"no SLA policy for {category!r} and no default")

    closed_at = _parse(row.get("closed_at"))

    # Stopped on creation. A case closed in 2021 must not acquire a deadline in
    # 2026, and a sweep firing on one would page somebody about a five-year-old
    # complaint.
    clock = SLAClock.objects.create(
        tenant=tenant,
        subject_type="dispute",
        subject_id=reference,
        policy_version=policy.current_version,
        started_at=opened_at,
        state=SLAClock.State.STOPPED,
        stopped_at=closed_at or opened_at,
    )

    dispute = Dispute.objects.create(
        tenant=tenant,
        reference=f"IMP-{reference[:24]}",
        customer_ref_hash=hash_customer_ref(tenant, row.get("customer_ref") or reference),
        customer_display_name=(row.get("display_name") or "")[:128],
        category=policy.category,
        # The marker that survives everywhere: on the case, in the export, and in
        # every audit record about it.
        subcategory=IMPORT_MARKER,
        description=row.get("description") or "",
        policy_version=policy.current_version,
        clock=clock,
        submitted_at=opened_at,
        closed_at=closed_at,
        status=Status.CLOSED if closed_at else Status.INVESTIGATING,
        # Deadlines equal to the start instant: present because the column is not
        # nullable, and inert because the clock is stopped.
        ack_deadline=opened_at,
        resolution_deadline=opened_at,
    )
    clock.subject_id = dispute.pk
    clock.save(update_fields=["subject_id"])

    audit.append(
        tenant=tenant,
        event_type="import.case",
        subject_type="dispute",
        subject_id=dispute.pk,
        actor_type="system",
        occurred_at=opened_at,
        payload={
            "batch_id": batch.pk,
            "source": batch.source,
            "external_reference": reference,
            # The claim we are careful *not* to make. Absorbing foreign history
            # and implying we vouch for it is the failure this whole module is
            # arranged to avoid.
            "disputeshield_witnessed": False,
            "attested_import_digest": hashlib.sha256(
                f"{batch.source_digest}:{reference}".encode()
            ).hexdigest(),
        },
    )
    return dispute.pk


def is_imported(dispute: Dispute) -> bool:
    return dispute.subcategory == IMPORT_MARKER


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
