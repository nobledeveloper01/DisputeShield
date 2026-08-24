"""Repeat-claimant and first-party fraud signals (amplifier A13).

The amplifier with the most serious failure mode in the product, restated here
because whoever reads this next will be tempted to wire it into something:

  A signal that influences an outcome turns a complaints system into an automated
  denial system, which is a consumer-protection violation with the audit trail
  helpfully documenting it.

So a signal is **context for an agent**, presented with its evidence. It is never
an input to an SLA policy, never changes a priority, never gates a channel, and is
structurally incapable of producing an outcome — asserted from the call graph in
`tests/test_advisory_only.py`.

A rejection must always be justified by case-specific findings recorded by a named
human. The case record shows the signal was present without showing it was
decisive.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from django.utils import timezone

from disputeshield.models import Dispute, RiskSignal

MODEL_ID = "frequency-signals"
MODEL_VERSION = "1"

# Deliberately unexciting thresholds. A signal that fires often is a signal an
# agent learns to ignore, and an ignored signal is worse than none because it
# still sits in the record looking like context somebody weighed.
REPEAT_CLAIM_THRESHOLD = 4
REPEAT_CLAIM_WINDOW_DAYS = 90
RAPID_FILING_MINUTES = 10
CROSS_PROVIDER_THRESHOLD = 3


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str
    summary: str
    evidence: dict


def evaluate(*, dispute: Dispute, now: datetime | None = None) -> tuple[Finding, ...]:
    """Compute the signals for one case. Pure: writes nothing."""
    now = now or timezone.now()
    findings = [
        _repeat_claimant(dispute, now),
        _rapid_filing(dispute),
        _cross_provider(dispute, now),
    ]
    return tuple(finding for finding in findings if finding is not None)


def record(*, dispute: Dispute, findings: tuple[Finding, ...], now: datetime | None = None):
    """Store the findings against the case, as context and nothing more.

    Note what this function does *not* do: it does not touch `dispute.priority`,
    `dispute.status`, `dispute.outcome` or the clock. There is no branch here that
    could.
    """
    now = now or timezone.now()
    rows = [
        RiskSignal(
            tenant=dispute.tenant,
            dispute=dispute,
            kind=finding.kind,
            summary=finding.summary,
            # A signal without its evidence is an accusation.
            evidence=finding.evidence,
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            computed_at=now,
        )
        for finding in findings
    ]
    return RiskSignal.objects.bulk_create(rows, ignore_conflicts=True)


def _repeat_claimant(dispute: Dispute, now: datetime) -> Finding | None:
    since = now - timedelta(days=REPEAT_CLAIM_WINDOW_DAYS)
    count = (
        Dispute.objects.filter(customer_ref_hash=dispute.customer_ref_hash, submitted_at__gte=since)
        .exclude(pk=dispute.pk)
        .count()
    )
    if count < REPEAT_CLAIM_THRESHOLD:
        return None
    return Finding(
        kind=RiskSignal.Kind.REPEAT_CLAIMANT,
        summary=(f"{count} other cases from this customer in {REPEAT_CLAIM_WINDOW_DAYS} days"),
        evidence={
            "count": count,
            "window_days": REPEAT_CLAIM_WINDOW_DAYS,
            "threshold": REPEAT_CLAIM_THRESHOLD,
            # So an agent can check the claim rather than take it.
            "references": list(
                Dispute.objects.filter(
                    customer_ref_hash=dispute.customer_ref_hash, submitted_at__gte=since
                )
                .exclude(pk=dispute.pk)
                .values_list("reference", flat=True)[:10]
            ),
        },
    )


def _rapid_filing(dispute: Dispute) -> Finding | None:
    entry = dispute.context_entries.order_by("occurred_at").first()
    if entry is None or not dispute.submitted_at:
        return None

    gap = dispute.submitted_at - entry.occurred_at
    if gap > timedelta(minutes=RAPID_FILING_MINUTES) or gap < timedelta(0):
        return None
    return Finding(
        kind=RiskSignal.Kind.RAPID_FILING,
        summary=f"filed {int(gap.total_seconds() // 60)} minutes after the transaction event",
        evidence={
            "minutes": int(gap.total_seconds() // 60),
            "threshold_minutes": RAPID_FILING_MINUTES,
            "context_summary": entry.summary,
        },
    )


def _cross_provider(dispute: Dispute, now: datetime) -> Finding | None:
    since = now - timedelta(days=REPEAT_CLAIM_WINDOW_DAYS)
    prefixes = {
        (ref or "")[:4]
        for ref in Dispute.objects.filter(
            customer_ref_hash=dispute.customer_ref_hash, submitted_at__gte=since
        ).values_list("transaction_ref", flat=True)
        if ref
    }
    if len(prefixes) < CROSS_PROVIDER_THRESHOLD:
        return None
    return Finding(
        kind=RiskSignal.Kind.CROSS_PROVIDER,
        summary=f"claims across {len(prefixes)} distinct transaction prefixes",
        evidence={"prefixes": sorted(prefixes), "threshold": CROSS_PROVIDER_THRESHOLD},
    )
