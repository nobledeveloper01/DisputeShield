"""A drafted reply, grounded strictly in the case's own content (amplifier A12).

Two rules, both structural:

  * **Retrieval is restricted to the case and the tenant's own artefacts.** A
    draft cannot be grounded in something the customer never said and the firm
    never wrote.
  * **No autonomous send, on any channel, under any configuration.** This module
    returns text. It does not call `add_message`, it has no channel client, and
    `tests/test_advisory_only.py` asserts that from the call graph — so no
    configuration can enable one, because there is nothing to configure.

The draft is recorded alongside what the agent actually sent, which makes the
difference between the two measurable evidence about the tool rather than a
matter of opinion.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from disputeshield import audit
from disputeshield.intelligence.grounding import UngroundedDraft, check, enforce
from disputeshield.models import Dispute, DisputeMessage, ResponseTemplate, Suggestion

MODEL_ID = "template-copilot"
MODEL_VERSION = "1"


@dataclasses.dataclass(frozen=True)
class Draft:
    body: str
    sources: tuple[str, ...]
    suggestion: Suggestion | None = None


def retrieve_sources(dispute: Dispute) -> tuple[str, ...]:
    """Everything a draft may be grounded in, and nothing else.

    The case's own content plus the tenant's own templates and resolved history.
    Not the internet, not another tenant's cases, and not the model's own prior
    output — grounding a draft in an earlier draft is how an invention becomes a
    fact.
    """
    sources: list[str] = [dispute.description or ""]

    # Customer-visible messages only. An internal note is not something the
    # customer may be told back (§10).
    sources.extend(
        dispute.messages.filter(visibility=DisputeMessage.Visibility.CUSTOMER).values_list(
            "body", flat=True
        )
    )
    sources.extend(entry.summary for entry in dispute.context_entries.all())
    sources.extend(ResponseTemplate.objects.values_list("body", flat=True))

    # Facts the case itself carries, so a draft may quote the reference and the
    # date the customer was already given.
    sources.append(dispute.reference)
    if dispute.resolution_deadline:
        sources.append(dispute.resolution_deadline.isoformat())
        sources.append(dispute.resolution_deadline.strftime("%d %B"))
        sources.append(dispute.resolution_deadline.strftime("%A"))
    if dispute.amount_minor is not None:
        sources.append(str(dispute.amount_minor))
        sources.append(f"{dispute.amount_minor / 100:,.2f}")
    if dispute.transaction_ref:
        sources.append(dispute.transaction_ref)

    return tuple(s for s in sources if s)


def draft_reply(*, dispute: Dispute, body: str, actor_id: str) -> Draft:
    """Ground a candidate reply, or refuse it.

    Refusal is the product. A warning next to a draft is one an agent under queue
    pressure clicks past; a block is a thing they have to resolve.
    """
    sources = retrieve_sources(dispute)
    enforce(body, list(sources))

    with transaction.atomic():
        suggestion = Suggestion.objects.create(
            tenant=dispute.tenant,
            dispute=dispute,
            kind=Suggestion.Kind.REPLY_DRAFT,
            value=body,
            rationale="grounded in the case's own content",
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
                "kind": Suggestion.Kind.REPLY_DRAFT,
                "model_id": MODEL_ID,
                "model_version": MODEL_VERSION,
                # Never the body: the draft is already stored on the suggestion,
                # and duplicating customer-facing prose into the audit payload
                # puts it in a second place with a different export path.
                "length": len(body),
            },
        )
    return Draft(body=body, sources=sources, suggestion=suggestion)


def would_be_blocked(*, dispute: Dispute, body: str) -> tuple[str, ...]:
    """What a draft claims that its sources do not support. For a preview."""
    result = check(body, list(retrieve_sources(dispute)))
    return tuple(f"{c.kind}: {c.text}" for c in result.unsupported)


__all__ = ["Draft", "UngroundedDraft", "draft_reply", "retrieve_sources", "would_be_blocked"]
