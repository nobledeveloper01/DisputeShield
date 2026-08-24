"""Tracking a complaint that has gone past the firm (amplifier A6)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute, ExternalCorrespondence, ExternalEscalation


def open_track(
    *,
    dispute: Dispute,
    body: str,
    external_reference: str,
    opened_at=None,
    response_due_at=None,
    actor_id: str,
) -> ExternalEscalation:
    opened_at = opened_at or timezone.now()

    with transaction.atomic():
        escalation = ExternalEscalation.objects.create(
            tenant=dispute.tenant,
            dispute=dispute,
            body=body,
            external_reference=external_reference,
            opened_at=opened_at,
            response_due_at=response_due_at,
            created_by=actor_id,
        )
        audit.append(
            tenant=dispute.tenant,
            event_type="escalation.opened",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user",
            actor_id=actor_id,
            occurred_at=opened_at,
            payload={
                "body": body,
                "external_reference": external_reference,
                "response_due_at": response_due_at.isoformat() if response_due_at else None,
            },
        )
        return escalation


def record_correspondence(
    *,
    escalation: ExternalEscalation,
    direction: str,
    summary: str,
    body: str = "",
    occurred_at=None,
    actor_id: str,
) -> ExternalCorrespondence:
    """Kept on the case rather than in somebody's inbox.

    §11.7's evidence obligation does not stop at the firm's own boundary, and
    "it was in his email" is not a record.
    """
    occurred_at = occurred_at or timezone.now()

    with transaction.atomic():
        entry = ExternalCorrespondence.objects.create(
            tenant=escalation.tenant,
            escalation=escalation,
            direction=direction,
            summary=summary,
            body=body,
            occurred_at=occurred_at,
            recorded_by=actor_id,
        )
        audit.append(
            tenant=escalation.tenant,
            event_type="escalation.correspondence",
            subject_type="dispute",
            subject_id=escalation.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            occurred_at=occurred_at,
            payload={
                "escalation_id": escalation.pk,
                "direction": direction,
                "summary": summary,
            },
        )
        return entry


def close_track(
    *,
    escalation: ExternalEscalation,
    determination: str,
    notes: str,
    actor_id: str,
    closed_at=None,
) -> ExternalEscalation:
    """Record what the body decided. Never infer it, and never overwrite ours.

    If the ombudsman's determination contradicts the internal outcome, both
    stand. Rewriting the internal outcome to agree would destroy the most
    interesting evidence in the case — that the firm and the body reached
    different answers, and when.
    """
    if determination not in ExternalEscalation.Determination.values:
        raise ValueError(f"determination must be one of {ExternalEscalation.Determination.values}")
    if not notes.strip():
        raise ValueError("Closing an external track requires notes on what was decided.")

    closed_at = closed_at or timezone.now()

    with transaction.atomic():
        escalation.closed_at = closed_at
        escalation.determination = determination
        escalation.determination_notes = notes
        escalation.full_clean(exclude=["tenant", "dispute"])
        escalation.save(update_fields=["closed_at", "determination", "determination_notes"])

        internal = escalation.dispute.outcome
        contradicts = bool(internal) and internal != determination

        audit.append(
            tenant=escalation.tenant,
            event_type="escalation.determined",
            subject_type="dispute",
            subject_id=escalation.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            occurred_at=closed_at,
            payload={
                "escalation_id": escalation.pk,
                "body": escalation.body,
                "determination": determination,
                "internal_outcome": internal,
                # Surfaced rather than reconciled. A contradiction between the
                # firm and the body is a finding, not a data error.
                "contradicts_internal_outcome": contradicts,
                "notes": notes,
            },
        )
        return escalation


def open_tracks_for(dispute: Dispute):
    return dispute.escalations.filter(closed_at__isnull=True)
