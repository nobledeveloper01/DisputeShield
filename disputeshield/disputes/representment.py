"""Building an acquirer-facing representment pack (amplifier A5).

The one place in the product that moves from cost avoidance to recovered revenue:
a representment that misses the scheme's deadline or omits a required element is
money the fintech simply loses, and the evidence needed is already sitting on the
case.

Two clocks, and they are not the same clock. The regulatory resolution window and
the scheme representment window run concurrently, have different rules, and one
can expire while the other is comfortable. The scheme's window is wall-clock and
never moves when the case is paused — a card scheme does not care that the firm
is waiting on the customer.

**DisputeShield builds and exports the pack. It does not submit it**, and it never
represents itself as having submitted it. Submission is the acquirer's channel and
the fintech's decision.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute, ReasonCode, Representment, SLADeadline

# The scheme warns before it expires, like the regulatory clock does. Same
# thresholds, entirely separate rows.
SCHEME_WARNING_THRESHOLDS = (50, 80, 95)


class EvidenceIncomplete(ValueError):
    """The pack is missing an element the scheme requires for this reason code."""


@dataclasses.dataclass(frozen=True)
class Pack:
    reference: str
    scheme: str
    reason_code: str
    respond_by: str
    evidence: dict
    content_digest: str

    def as_json(self) -> bytes:
        """Canonical, so an acquirer receiving the same pack twice sees the same bytes."""
        body = {
            "reference": self.reference,
            "scheme": self.scheme,
            "reason_code": self.reason_code,
            "respond_by": self.respond_by,
            "evidence": self.evidence,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), indent=2).encode()


def open_representment(
    *,
    dispute: Dispute,
    reason_code: ReasonCode,
    chargeback_reference: str,
    chargeback_at=None,
    actor_id: str,
) -> Representment:
    """Start the scheme clock. Independent of the regulatory one by construction."""
    chargeback_at = chargeback_at or timezone.now()
    respond_by = chargeback_at + timedelta(days=reason_code.response_window_days)

    with transaction.atomic():
        representment = Representment(
            tenant=dispute.tenant,
            dispute=dispute,
            reason_code=reason_code,
            chargeback_reference=chargeback_reference,
            chargeback_at=chargeback_at,
            respond_by=respond_by,
            created_by=actor_id,
        )
        representment.full_clean(exclude=["tenant", "dispute", "reason_code"])
        representment.save()

        _materialise_scheme_deadlines(dispute, chargeback_at, respond_by)

        audit.append(
            tenant=dispute.tenant,
            event_type="representment.opened",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user",
            actor_id=actor_id,
            occurred_at=chargeback_at,
            payload={
                "scheme": reason_code.scheme,
                "reason_code": reason_code.code,
                "chargeback_reference": chargeback_reference,
                "respond_by": respond_by.isoformat(),
                "required_evidence": list(reason_code.required_keys),
            },
        )
        return representment


def _materialise_scheme_deadlines(dispute, chargeback_at, respond_by) -> None:
    """Rows on the case's clock, marked unpausable.

    On the same clock so one sweep fires both, and `pausable=False` so pausing
    the regulatory window never moves the scheme's. Wall-clock arithmetic, because
    a scheme observes neither the firm's business hours nor its holidays.
    """
    window = respond_by - chargeback_at

    rows = [
        SLADeadline(
            tenant=dispute.tenant,
            clock=dispute.clock,
            kind=SLADeadline.Kind.SCHEME_REPRESENTMENT,
            fires_at=respond_by,
            pausable=False,
        )
    ]
    rows.extend(
        SLADeadline(
            tenant=dispute.tenant,
            clock=dispute.clock,
            kind=SLADeadline.Kind.SCHEME_WARNING,
            threshold_percent=threshold,
            fires_at=chargeback_at + window * threshold / 100,
            pausable=False,
        )
        for threshold in SCHEME_WARNING_THRESHOLDS
    )
    SLADeadline.objects.bulk_create(rows, ignore_conflicts=True)


def attach_evidence(
    *, representment: Representment, key: str, value, actor_id: str
) -> Representment:
    required = {item["key"] for item in representment.reason_code.evidence_requirements}
    if key not in required:
        raise ValueError(
            f"{key!r} is not part of {representment.reason_code} evidence. The scheme "
            "decides what a representment contains, not us."
        )

    with transaction.atomic():
        representment.evidence = {**representment.evidence, key: value}
        representment.status = (
            Representment.Status.READY
            if representment.is_complete
            else Representment.Status.GATHERING
        )
        representment.save(update_fields=["evidence", "status"])

        audit.append(
            tenant=representment.tenant,
            event_type="representment.evidence_attached",
            subject_type="dispute",
            subject_id=representment.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            payload={"key": key, "still_missing": list(representment.missing_evidence)},
        )
        return representment


def build_pack(*, representment: Representment, actor_id: str) -> Pack:
    """Assemble the pack, refusing if the scheme's checklist is not satisfied.

    Exporting an incomplete pack is the expensive failure: the acquirer rejects
    it, the window closes, and the money is gone. Better to refuse here, where
    there is still time to gather the missing element.
    """
    missing = representment.missing_evidence
    if missing:
        raise EvidenceIncomplete(
            f"{representment.reason_code} requires {list(missing)}, which the pack does "
            "not carry. An incomplete pack is rejected by the acquirer after the window "
            "has closed."
        )

    body = {
        key: representment.evidence[key]
        for key in sorted(representment.evidence)
        if key in {item["key"] for item in representment.reason_code.evidence_requirements}
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    pack = Pack(
        reference=representment.chargeback_reference,
        scheme=representment.reason_code.scheme,
        reason_code=representment.reason_code.code,
        respond_by=representment.respond_by.isoformat(),
        evidence=body,
        content_digest=digest,
    )

    with transaction.atomic():
        representment.status = Representment.Status.EXPORTED
        representment.exported_at = timezone.now()
        representment.save(update_fields=["status", "exported_at"])

        audit.append(
            tenant=representment.tenant,
            event_type="representment.exported",
            subject_type="dispute",
            subject_id=representment.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            payload={
                "chargeback_reference": representment.chargeback_reference,
                "content_digest": digest,
                # Stated in the record, not just in the documentation.
                "submitted_by_disputeshield": False,
            },
        )
    return pack


def record_submission(*, representment: Representment, submitted_at, actor_id: str):
    """What the fintech told us they did. We never submit.

    The field and the event are both named for who acted, so a later reader
    cannot mistake this for DisputeShield having filed anything.
    """
    with transaction.atomic():
        representment.submitted_at = submitted_at
        representment.status = Representment.Status.RECORDED_SUBMITTED
        representment.save(update_fields=["submitted_at", "status"])

        audit.append(
            tenant=representment.tenant,
            event_type="representment.submission_recorded_by_fintech",
            subject_type="dispute",
            subject_id=representment.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            payload={
                "submitted_at": submitted_at.isoformat(),
                "submitted_by_disputeshield": False,
            },
        )
        return representment
