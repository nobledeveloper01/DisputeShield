"""Placing, releasing and honouring legal holds (amplifier A7)."""

from __future__ import annotations

import dataclasses

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute, ErasureRequest, LegalHold


class ReleaseRequiresTwoPeople(PermissionError):
    """A hold one person can quietly lift is not a hold."""


class HeldMaterial(PermissionError):
    """Refused because a legal hold covers it."""


@dataclasses.dataclass(frozen=True)
class HoldCheck:
    held: bool
    holds: tuple[LegalHold, ...]

    @property
    def references(self) -> tuple[str, ...]:
        return tuple(hold.matter_reference for hold in self.holds)


def place(
    *,
    tenant,
    name: str,
    matter_reference: str,
    reason: str,
    scope: str,
    placed_by: str,
    **target,
) -> LegalHold:
    if not matter_reference.strip() or not reason.strip():
        raise ValueError(
            "A hold needs a matter reference and a reason. A hold nobody can trace "
            "to a matter is a hold nobody can decide to release."
        )

    with transaction.atomic():
        hold = LegalHold(
            tenant=tenant,
            name=name,
            matter_reference=matter_reference,
            reason=reason,
            scope=scope,
            placed_by=placed_by,
            **target,
        )
        hold.full_clean(exclude=["tenant"])
        hold.save()

        audit.append(
            tenant=tenant,
            event_type="legal_hold.placed",
            subject_type="legal_hold",
            subject_id=hold.pk,
            actor_type="user",
            actor_id=placed_by,
            payload={
                "scope": scope,
                "matter_reference": matter_reference,
                "reason": reason,
                "dispute_id": hold.dispute_id,
                "category": hold.category,
            },
        )
        return hold


def release(*, hold: LegalHold, released_by: str, approved_by: str, reason: str) -> LegalHold:
    """Two people, and they must be different ones."""
    if not reason.strip():
        raise ValueError("Releasing a hold requires a reason.")
    if not approved_by.strip():
        raise ReleaseRequiresTwoPeople("Releasing a hold requires a second approver.")
    if approved_by == released_by:
        raise ReleaseRequiresTwoPeople(
            "The approver must be somebody other than the person releasing the hold. "
            "A two-person rule one person can satisfy twice is a one-person rule."
        )
    if not hold.is_active:
        raise ValueError("That hold has already been released.")

    with transaction.atomic():
        hold.released_at = timezone.now()
        hold.released_by = released_by
        hold.release_approved_by = approved_by
        hold.release_reason = reason
        hold.save(
            update_fields=["released_at", "released_by", "release_approved_by", "release_reason"]
        )

        audit.append(
            tenant=hold.tenant,
            event_type="legal_hold.released",
            subject_type="legal_hold",
            subject_id=hold.pk,
            actor_type="user",
            actor_id=released_by,
            payload={
                "approved_by": approved_by,
                "reason": reason,
                "matter_reference": hold.matter_reference,
            },
        )
        return hold


def check(dispute: Dispute) -> HoldCheck:
    """Every active hold covering this case."""
    covering = [
        hold for hold in LegalHold.objects.filter(released_at__isnull=True) if hold.covers(dispute)
    ]
    return HoldCheck(held=bool(covering), holds=tuple(covering))


def holds_for_customer(customer_ref_hash: str) -> tuple[LegalHold, ...]:
    """Holds that block erasure for a subject, across all of their cases."""
    covering: list[LegalHold] = []
    for dispute in Dispute.objects.filter(customer_ref_hash=customer_ref_hash):
        covering.extend(check(dispute).holds)
    for hold in LegalHold.objects.filter(
        released_at__isnull=True,
        scope=LegalHold.Scope.CUSTOMER,
        customer_ref_hash=customer_ref_hash,
    ):
        covering.append(hold)
    return tuple({hold.pk: hold for hold in covering}.values())


def decide_erasure(*, request: ErasureRequest, decided_by: str) -> ErasureRequest:
    """Decide a data-subject erasure request, and record the decision either way.

    §11.7 is explicit that the procedure must state what is deleted, what is
    pseudonymised and what is retained under a legal-obligation basis. A refusal
    is therefore an outcome with words attached, not an absence of one — refusing
    a request silently is its own violation.
    """
    blocking = holds_for_customer(request.customer_ref_hash)

    with transaction.atomic():
        if blocking:
            request.outcome = ErasureRequest.Outcome.REFUSED_LEGAL_HOLD
            request.blocking_holds = [hold.matter_reference for hold in blocking]
            request.outcome_reason = (
                "This material is subject to a legal hold and cannot be erased while "
                "the hold stands. The hold exists because the material is relevant to "
                f"an ongoing matter ({', '.join(sorted(request.blocking_holds))}). "
                "You will be told when it is released."
            )
        else:
            request.outcome = ErasureRequest.Outcome.REFUSED_RETENTION
            request.outcome_reason = (
                "Complaint records are retained for seven years under a regulatory "
                "record-keeping obligation, which is a lawful basis that overrides "
                "erasure. Your identifying details are stored pseudonymised; the case "
                "record itself is retained."
            )

        request.decided_at = timezone.now()
        request.decided_by = decided_by
        request.save(
            update_fields=[
                "outcome",
                "outcome_reason",
                "blocking_holds",
                "decided_at",
                "decided_by",
            ]
        )

        audit.append(
            tenant=request.tenant,
            event_type=f"erasure.{request.outcome}",
            subject_type="erasure_request",
            subject_id=request.pk,
            actor_type="user",
            actor_id=decided_by,
            payload={
                "customer_ref_hash": request.customer_ref_hash,
                "blocking_holds": request.blocking_holds,
                # The words the requester was given, recorded verbatim. A refusal
                # a supervisor cannot read back is a refusal we cannot defend.
                "reason_given": request.outcome_reason,
            },
        )
        return request
