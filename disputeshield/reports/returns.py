"""Periodic regulatory returns (amplifier A17).

A template is a **specification of what to count**, never a query somebody can
write into a dashboard. Sources come from a closed registry below, so a template
revision can change which rows appear and how they are labelled, and cannot reach
anything the registry does not already expose.

The byte-identity requirement runs the other way from the export's: an export is
reproducible because the data has not changed, while a return is reproducible
because the **template version is pinned to the filing**. A revision must never
alter what was filed last year — a return regenerated under this year's template
would silently disagree with the document the supervisor holds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from disputeshield.models import Dispute, RegulatoryReturn, ReturnTemplate
from disputeshield.models.dispute import Status


class UnknownSource(ValueError):
    """A template asked for a figure the registry does not expose."""


class ApprovalRequiresTwoPeople(PermissionError):
    """Maker-checker one person can satisfy twice is not a control."""


@dataclasses.dataclass(frozen=True)
class Row:
    key: str
    label: str
    value: int


def _cases(period_from, period_to):
    return Dispute.objects.filter(submitted_at__gte=period_from, submitted_at__lt=period_to)


def _received(period_from, period_to, **_) -> int:
    return _cases(period_from, period_to).count()


def _resolved(period_from, period_to, **_) -> int:
    return _cases(period_from, period_to).filter(resolved_at__isnull=False).count()


def _breached(period_from, period_to, **_) -> int:
    return (
        _cases(period_from, period_to)
        .filter(Q(breach_resolution=True) | Q(breach_ack=True))
        .count()
    )


def _outstanding(period_from, period_to, **_) -> int:
    return (
        _cases(period_from, period_to)
        .exclude(status__in=[Status.CLOSED, Status.AUTO_CLOSED, Status.RESOLVED])
        .count()
    )


def _by_category(period_from, period_to, *, category: str = "", **_) -> int:
    return _cases(period_from, period_to).filter(category=category).count()


def _by_outcome(period_from, period_to, *, outcome: str = "", **_) -> int:
    return _cases(period_from, period_to).filter(outcome=outcome).count()


def _escalated_externally(period_from, period_to, **_) -> int:
    return _cases(period_from, period_to).filter(escalations__isnull=False).distinct().count()


def _upheld_externally(period_from, period_to, **_) -> int:
    return (
        _cases(period_from, period_to)
        .filter(escalations__determination="upheld")
        .distinct()
        .count()
    )


# The closed registry. Adding an entry is a decision about what a supervisor may
# be told; a template cannot reach anything absent from it.
SOURCES = {
    "cases_received": _received,
    "cases_resolved": _resolved,
    "cases_breached": _breached,
    "cases_outstanding": _outstanding,
    "cases_by_category": _by_category,
    "cases_by_outcome": _by_outcome,
    "escalated_externally": _escalated_externally,
    "upheld_externally": _upheld_externally,
}


def validate_template(rows: list[dict]) -> tuple[str, ...]:
    """Sources a template asks for that the registry does not expose."""
    return tuple(
        sorted({row.get("source", "") for row in rows if row.get("source") not in SOURCES})
    )


def generate(
    *,
    tenant,
    template: ReturnTemplate,
    period_from: datetime,
    period_to: datetime,
    generated_by: str,
) -> RegulatoryReturn:
    unknown = validate_template(template.rows)
    if unknown:
        raise UnknownSource(
            f"{template.code} v{template.version} asks for {list(unknown)}, which the "
            "registry does not expose. A template specifies what to count; it cannot "
            "reach for something nobody decided to publish."
        )

    rows = [
        dataclasses.asdict(
            Row(
                key=row["key"],
                label=row["label"],
                value=SOURCES[row["source"]](period_from, period_to, **(row.get("filter") or {})),
            )
        )
        for row in template.rows
    ]

    return RegulatoryReturn.objects.create(
        tenant=tenant,
        template=template,
        period_from=period_from,
        period_to=period_to,
        rows=rows,
        content_digest=digest(rows),
        generated_at=timezone.now(),
        generated_by=generated_by,
    )


def regenerate(*, filing: RegulatoryReturn) -> list[dict]:
    """Reproduce a filed return under the template version it was filed with.

    `filing.template` is a `PROTECT` foreign key to a specific version, and
    versions are immutable — so this reaches last year's template even though a
    newer revision exists.
    """
    unknown = validate_template(filing.template.rows)
    if unknown:
        raise UnknownSource(f"template asks for {list(unknown)}")

    return [
        dataclasses.asdict(
            Row(
                key=row["key"],
                label=row["label"],
                value=SOURCES[row["source"]](
                    filing.period_from, filing.period_to, **(row.get("filter") or {})
                ),
            )
        )
        for row in filing.template.rows
    ]


def digest(rows: list[dict]) -> str:
    """Canonical, so the digest depends on the figures rather than on our
    serialisation choices."""
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def approve(*, filing: RegulatoryReturn, approved_by: str, note: str = "") -> RegulatoryReturn:
    """Maker-checker, then hash the approved artefact into the chain.

    Nothing is filed automatically. What is provable afterwards is not that a
    return was produced, but that *this* return was the one approved — which is
    why the digest goes into the audit trail rather than staying on the row.
    """
    from disputeshield import audit

    if approved_by == filing.generated_by:
        raise ApprovalRequiresTwoPeople(
            "A return must be approved by somebody other than whoever generated it."
        )
    if filing.is_approved:
        raise ValueError("That return is already approved.")

    filing.status = RegulatoryReturn.Status.APPROVED
    filing.approved_by = approved_by
    filing.approval_note = note
    filing.approved_at = timezone.now()
    filing.full_clean(exclude=["tenant", "template"])
    filing.save(update_fields=["status", "approved_by", "approval_note", "approved_at"])

    audit.append(
        tenant=filing.tenant,
        event_type="regulatory_return.approved",
        subject_type="regulatory_return",
        subject_id=filing.pk,
        actor_type="user",
        actor_id=approved_by,
        payload={
            "template": f"{filing.template.code} v{filing.template.version}",
            "period_from": filing.period_from.isoformat(),
            "period_to": filing.period_to.isoformat(),
            "content_digest": filing.content_digest,
            "generated_by": filing.generated_by,
        },
    )
    return filing
