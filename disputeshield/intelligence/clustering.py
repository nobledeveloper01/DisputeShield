"""Grouping cases by why they exist (amplifier A10).

**Clustering never modifies a case.** It is a lens over the record, never a writer
to it — it does not set a category, a priority, an outcome or an assignment, and
`tests/test_advisory_only.py` asserts that from the call graph.

A cluster is a hypothesis. Hypotheses presented with the confidence of facts get
acted on wrongly, so every cluster carries its membership and its evidence and is
inspectable down to individual cases.
"""

from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from datetime import datetime, timedelta

from django.utils import timezone

from disputeshield.models import Dispute, RootCauseCluster

MODEL_ID = "attribute-clustering"
MODEL_VERSION = "1"

MIN_CLUSTER_SIZE = 3

# Words that carry no signal about a cause. Not a general stop list: these are
# the words that appear in every complaint this product ever sees.
NOISE = frozenset(
    {
        "the",
        "and",
        "was",
        "for",
        "but",
        "not",
        "have",
        "has",
        "with",
        "this",
        "that",
        "from",
        "been",
        "were",
        "are",
        "you",
        "your",
        "our",
        "i",
        "my",
        "me",
        "it",
        "transfer",
        "payment",
        "money",
        "account",
        "please",
        "still",
        "yet",
        "any",
    }
)


@dataclasses.dataclass(frozen=True)
class Cluster:
    label: str
    basis: str
    case_ids: tuple[str, ...]
    exposure_minor: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    evidence: dict

    @property
    def case_count(self) -> int:
        return len(self.case_ids)


def compute(*, lookback_days: int = 30, now: datetime | None = None) -> tuple[Cluster, ...]:
    """Find clusters. Reads only — the return value is the whole output."""
    now = now or timezone.now()
    since = now - timedelta(days=lookback_days)

    cases = list(
        Dispute.objects.filter(submitted_at__gte=since).only(
            "pk",
            "reference",
            "category",
            "description",
            "transaction_ref",
            "amount_minor",
            "submitted_at",
        )
    )
    if not cases:
        return ()

    clusters = [
        *_by_transaction_prefix(cases),
        *_by_shared_terms(cases),
    ]
    return tuple(sorted(clusters, key=lambda c: (-c.case_count, c.label)))


def persist(clusters: tuple[Cluster, ...], *, tenant, now: datetime | None = None):
    """Store a snapshot for the dashboard. Still writes nothing to a case."""
    now = now or timezone.now()
    rows = [
        RootCauseCluster(
            tenant=tenant,
            label=cluster.label,
            basis=cluster.basis,
            evidence={**cluster.evidence, "case_ids": list(cluster.case_ids[:50])},
            case_count=cluster.case_count,
            exposure_minor=cluster.exposure_minor,
            first_seen_at=cluster.first_seen_at,
            last_seen_at=cluster.last_seen_at,
            computed_at=now,
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
        )
        for cluster in clusters
    ]
    return RootCauseCluster.objects.bulk_create(rows, batch_size=200)


def _by_transaction_prefix(cases: list[Dispute]) -> list[Cluster]:
    """Cases whose transaction references share a prefix — usually one rail."""
    groups: dict[str, list[Dispute]] = defaultdict(list)
    for case in cases:
        if case.transaction_ref and len(case.transaction_ref) >= 4:
            groups[case.transaction_ref[:4]].append(case)

    return [
        _build(
            f"transaction reference {prefix}…", "transaction_prefix", members, {"prefix": prefix}
        )
        for prefix, members in groups.items()
        if len(members) >= MIN_CLUSTER_SIZE
    ]


def _by_shared_terms(cases: list[Dispute]) -> list[Cluster]:
    """Cases whose descriptions share an uncommon term.

    Crude, and honest about it: the evidence records the term, so a compliance
    officer can see exactly what the cluster is claiming and dismiss it in one
    look if the term is meaningless.
    """
    by_term: dict[str, list[Dispute]] = defaultdict(list)
    for case in cases:
        for term in _terms(case.description):
            by_term[term].append(case)

    clusters = []
    for term, members in by_term.items():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        # A term in nearly every case is describing the product, not a cause.
        if len(members) > 0.8 * len(cases):
            continue
        clusters.append(
            _build(f"cases mentioning “{term}”", "shared_term", members, {"term": term})
        )
    return clusters


def _build(label: str, basis: str, members: list[Dispute], evidence: dict) -> Cluster:
    timestamps = [case.submitted_at for case in members if case.submitted_at]
    return Cluster(
        label=label,
        basis=basis,
        case_ids=tuple(case.pk for case in members),
        exposure_minor=sum(case.amount_minor or 0 for case in members),
        first_seen_at=min(timestamps) if timestamps else None,
        last_seen_at=max(timestamps) if timestamps else None,
        evidence={**evidence, "references": [case.reference for case in members[:10]]},
    )


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {word for word in words if word not in NOISE}
