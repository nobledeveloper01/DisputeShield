"""Choosing which resolved cases a supervisor reviews (amplifier A15).

Two properties, and the second is the one that matters:

  * **Sampling is uniformly random** over the eligible set. A supervisor who
    reviews whatever is at the top of a list reviews the newest cases, and the
    newest cases are not where the problems are.
  * **Forced-review criteria cannot be disabled.** They are a module constant, not
    configuration, for the same reason `file_anyway` is: a checkbox that can be
    unticked during a busy quarter will be unticked during a busy quarter, and the
    cases it exempts are precisely the ones a supervisor most needs to see.
"""

from __future__ import annotations

import dataclasses
import secrets
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from disputeshield.models import Dispute, QaReview
from disputeshield.models.dispute import Status

DEFAULT_SAMPLE_PERCENT = 5

# Not configuration. Every one of these is a case where being wrong is expensive
# and the ordinary sampling rate would probably miss it.
FORCED_CRITERIA: tuple[tuple[str, Q, str], ...] = (
    ("reopened", Q(status=Status.REOPENED), "the customer disputed the outcome"),
    ("escalated", Q(escalations__isnull=False), "it went past the firm"),
    ("breached", Q(breach_resolution=True) | Q(breach_ack=True), "it breached its window"),
    (
        "high_value",
        Q(amount_minor__gte=10_000_000),
        "the amount is in the top band",
    ),
)


@dataclasses.dataclass(frozen=True)
class Selection:
    forced: tuple[Dispute, ...]
    sampled: tuple[Dispute, ...]

    @property
    def total(self) -> int:
        return len(self.forced) + len(self.sampled)


def eligible(*, period_from: datetime, period_to: datetime):
    """Resolved cases in the period that have not already been reviewed."""
    return (
        Dispute.objects.filter(
            resolved_at__isnull=False,
            resolved_at__gte=period_from,
            resolved_at__lt=period_to,
        )
        .exclude(qa_reviews__isnull=False)
        .distinct()
    )


def select(
    *, period_from: datetime, period_to: datetime, percent: int = DEFAULT_SAMPLE_PERCENT
) -> Selection:
    """Every forced case, plus a uniform random sample of the rest."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")

    pool = list(eligible(period_from=period_from, period_to=period_to))
    forced_ids: set[str] = set()
    forced: list[tuple[Dispute, str, str]] = []

    for name, criterion, reason in FORCED_CRITERIA:
        for case in eligible(period_from=period_from, period_to=period_to).filter(criterion):
            if case.pk not in forced_ids:
                forced_ids.add(case.pk)
                forced.append((case, name, reason))

    remainder = [case for case in pool if case.pk not in forced_ids]
    target = round(len(remainder) * percent / 100)

    # `secrets` rather than `random`: a sample an agent could predict is a sample
    # an agent could prepare for, and the point of QA is that they cannot.
    chosen: list[Dispute] = []
    available = list(remainder)
    for _ in range(min(target, len(available))):
        chosen.append(available.pop(secrets.randbelow(len(available))))

    return Selection(forced=tuple(case for case, _, _ in forced), sampled=tuple(chosen))


def open_reviews(
    *, tenant, period_from: datetime, period_to: datetime, percent: int = DEFAULT_SAMPLE_PERCENT
) -> list[QaReview]:
    """Create the review rows. Writes nothing to any case."""
    selection = select(period_from=period_from, period_to=period_to, percent=percent)

    forced_reasons = {}
    for name, criterion, reason in FORCED_CRITERIA:
        for case in eligible(period_from=period_from, period_to=period_to).filter(criterion):
            forced_reasons.setdefault(case.pk, f"{name}: {reason}")

    rows = [
        QaReview(
            tenant=tenant,
            dispute=case,
            agent_id=case.assigned_to_id or "",
            trigger=QaReview.Trigger.FORCED,
            forced_reason=forced_reasons.get(case.pk, ""),
        )
        for case in selection.forced
    ]
    rows += [
        QaReview(
            tenant=tenant,
            dispute=case,
            agent_id=case.assigned_to_id or "",
            trigger=QaReview.Trigger.SAMPLED,
        )
        for case in selection.sampled
    ]
    return QaReview.objects.bulk_create(rows, ignore_conflicts=True)


def score(*, review: QaReview, scores: dict[str, int], notes: str, reviewed_by: str) -> QaReview:
    """Record the score. A record about the review, never about the case.

    Deliberately does not write to the dispute, and the audit event names the
    review as its subject — a QA score is somebody's opinion of how a case was
    handled, and filing it into the case's own history would put an opinion where
    a regulator reads facts.
    """
    from disputeshield import audit

    if reviewed_by == review.agent_id:
        raise PermissionError(
            "An agent cannot review their own case. Quality assurance one person "
            "can perform on themselves is not assurance."
        )

    review.scores = scores
    review.notes = notes
    review.reviewed_at = timezone.now()
    review.reviewed_by = reviewed_by
    review.save(update_fields=["scores", "notes", "reviewed_at", "reviewed_by"])

    audit.append(
        tenant=review.tenant,
        event_type="qa.reviewed",
        subject_type="qa_review",
        subject_id=review.pk,
        actor_type="user",
        actor_id=reviewed_by,
        payload={
            "dispute_id": review.dispute_id,
            "agent_id": review.agent_id,
            "trigger": review.trigger,
            "average": review.average,
        },
    )
    return review


def respond(*, review: QaReview, agent_id: str, response: str) -> QaReview:
    """The agent's reply to a score about their own work.

    §3.2's coaching view is only fair if the person being coached can answer, and
    a scorecard nobody may contest is a scorecard nobody trusts.
    """
    if agent_id != review.agent_id:
        raise PermissionError("Only the reviewed agent may respond to their own review.")

    review.agent_response = response
    review.responded_at = timezone.now()
    review.save(update_fields=["agent_response", "responded_at"])
    return review


def scorecard(*, agent_id: str) -> dict:
    """Per-agent rollup for the coaching view."""
    reviews = QaReview.objects.filter(agent_id=agent_id, reviewed_at__isnull=False)
    averages = [review.average for review in reviews if review.average is not None]
    return {
        "agent_id": agent_id,
        "reviews": len(averages),
        "average": round(sum(averages) / len(averages), 2) if averages else None,
        "forced": reviews.filter(trigger=QaReview.Trigger.FORCED).count(),
    }
