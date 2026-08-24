"""Category, priority and routing suggestions (amplifier A11).

Category selection decides which SLA policy applies, and therefore which
regulatory window the case runs on. A customer picking the wrong item from a
dropdown starts the wrong clock, and nobody notices until the case breaches a
window it was never on.

**The model proposes; a human or the customer disposes.** Nothing here writes to
`Dispute` — every output is a `Suggestion` row, and the acceptance or override is
what produces both the training signal and the accuracy metric.
"""

from __future__ import annotations

import dataclasses
import re

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute, Suggestion

MODEL_ID = "keyword-triage"
MODEL_VERSION = "1"

# A transparent, inspectable baseline. Deliberately not a black box for v1.4: a
# compliance officer asking "why did it say that?" gets an answer, and the
# accuracy metric below is what decides whether anything more opaque earns its
# place later.
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "failed_transfer": ("transfer", "debited", "not received", "reversal", "nip"),
    "card_chargeback": ("card", "chargeback", "atm", "pos", "merchant"),
    "unauthorised_debit": ("unauthorised", "unauthorized", "didn't authorise", "fraud", "stolen"),
    "failed_airtime": ("airtime", "data bundle", "recharge", "top up", "topup"),
}


@dataclasses.dataclass(frozen=True)
class Proposal:
    kind: str
    value: str
    confidence: float
    rationale: str


def propose(*, dispute: Dispute) -> tuple[Suggestion, ...]:
    """Suggest a category and a priority. Never applies either."""
    proposals = [p for p in (_category(dispute), _priority(dispute)) if p is not None]

    created: list[Suggestion] = []
    with transaction.atomic():
        for proposal in proposals:
            suggestion = Suggestion.objects.create(
                tenant=dispute.tenant,
                dispute=dispute,
                kind=proposal.kind,
                value=proposal.value,
                confidence=proposal.confidence,
                rationale=proposal.rationale,
                model_id=MODEL_ID,
                model_version=MODEL_VERSION,
            )
            audit.append(
                tenant=dispute.tenant,
                event_type="suggestion.proposed",
                subject_type="dispute",
                subject_id=dispute.pk,
                actor_type="system",
                payload={
                    "suggestion_id": suggestion.pk,
                    "kind": proposal.kind,
                    "value": proposal.value,
                    "confidence": proposal.confidence,
                    # Named on every record, so a shift in behaviour is
                    # attributable to a specific model rather than to "the AI".
                    "model_id": MODEL_ID,
                    "model_version": MODEL_VERSION,
                },
            )
            created.append(suggestion)
    return tuple(created)


def decide(*, suggestion: Suggestion, chosen_value: str, actor_id: str) -> Suggestion:
    """Record what the human chose. The difference is the accuracy signal.

    This does **not** apply the choice to the case. Applying a category is
    `disputeshield.disputes.service`'s job, performed by a human through the
    ordinary path, and keeping the two separate is what stops a suggestion from
    quietly becoming a decision.
    """
    accepted = chosen_value.strip() == suggestion.value.strip()

    with transaction.atomic():
        suggestion.disposition = (
            Suggestion.Disposition.ACCEPTED if accepted else Suggestion.Disposition.OVERRIDDEN
        )
        suggestion.chosen_value = chosen_value
        suggestion.decided_at = timezone.now()
        suggestion.decided_by = actor_id
        suggestion.save(update_fields=["disposition", "chosen_value", "decided_at", "decided_by"])

        audit.append(
            tenant=suggestion.tenant,
            event_type=f"suggestion.{suggestion.disposition}",
            subject_type="dispute",
            subject_id=suggestion.dispute_id,
            actor_type="user",
            actor_id=actor_id,
            payload={
                "suggestion_id": suggestion.pk,
                "kind": suggestion.kind,
                "suggested": suggestion.value,
                "chosen": chosen_value,
                "model_id": suggestion.model_id,
                "model_version": suggestion.model_version,
            },
        )
        return suggestion


def accuracy(*, model_id: str = MODEL_ID, model_version: str = MODEL_VERSION) -> dict:
    """Exported per tenant, so a model degrading is visible without an investigation."""
    decided = Suggestion.objects.filter(
        model_id=model_id,
        model_version=model_version,
        disposition__in=[Suggestion.Disposition.ACCEPTED, Suggestion.Disposition.OVERRIDDEN],
    )
    total = decided.count()
    if not total:
        # Absent rather than 1.0. An untested model is not a perfect one.
        return {
            "model_id": model_id,
            "model_version": model_version,
            "decided": 0,
            "accuracy": None,
        }

    accepted = decided.filter(disposition=Suggestion.Disposition.ACCEPTED).count()
    return {
        "model_id": model_id,
        "model_version": model_version,
        "decided": total,
        "accepted": accepted,
        "accuracy": round(accepted / total, 4),
    }


def _category(dispute: Dispute) -> Proposal | None:
    text = f"{dispute.description} {dispute.subcategory}".lower()
    scores = {
        category: sum(1 for hint in hints if hint in text)
        for category, hints in CATEGORY_HINTS.items()
    }
    best = max(scores, key=lambda key: scores[key])
    if not scores[best]:
        return None

    matched = [hint for hint in CATEGORY_HINTS[best] if hint in text]
    return Proposal(
        kind=Suggestion.Kind.CATEGORY,
        value=best,
        confidence=round(min(0.5 + 0.15 * scores[best], 0.95), 2),
        rationale=f"matched {matched}",
    )


def _priority(dispute: Dispute) -> Proposal | None:
    """A suggestion, and one a human sees next to its reason.

    §3.3 lists priority prediction under **Won't** for anything that acts on its
    own. This proposes and stops.
    """
    if dispute.amount_minor and dispute.amount_minor >= 10_000_000:
        return Proposal(
            kind=Suggestion.Kind.PRIORITY,
            value="high",
            confidence=0.7,
            rationale=f"amount_minor {dispute.amount_minor} is in the top band",
        )
    if re.search(r"\b(?:vulnerable|elderly|disabled|bereave)", dispute.description or "", re.I):
        return Proposal(
            kind=Suggestion.Kind.PRIORITY,
            value="high",
            confidence=0.6,
            rationale="the description mentions a potentially vulnerable customer",
        )
    return None
