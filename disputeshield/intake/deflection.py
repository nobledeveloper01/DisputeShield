"""Telling a customer the truth instead of taking their complaint (amplifier A2).

The guardrail is the whole feature: **deflection may never be the only path.** A
customer who wants a case gets a case, always, in one tap, with a full SLA clock.

`file_anyway_enabled` is not a field. It is a constant, returned in every
response, and there is no configuration key that can change it — because a
boolean that a tenant can set to False during an outage is a boolean that will be
set to False during an outage, and complaint suppression is the worst accusation
a regulator can make about a complaints system.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from disputeshield import audit
from disputeshield.models import Incident, IncidentSubscription

# Not configurable. Deliberately a module constant rather than a setting, a
# column or a serializer field — the test asserts no configuration surface can
# reach it.
FILE_ANYWAY_ALWAYS_AVAILABLE = True


@dataclasses.dataclass(frozen=True)
class Deflection:
    incident: Incident | None
    file_anyway: bool = FILE_ANYWAY_ALWAYS_AVAILABLE

    @property
    def deflected(self) -> bool:
        return self.incident is not None

    def as_dict(self) -> dict:
        return {
            "deflected": self.deflected,
            "incident": (
                {
                    "id": self.incident.pk,
                    "title": self.incident.title,
                    "message": self.incident.customer_message,
                    "expected_resolution_at": (
                        self.incident.expected_resolution_at.isoformat()
                        if self.incident.expected_resolution_at
                        else None
                    ),
                }
                if self.incident
                else None
            ),
            # Present in every response, including when nothing was deflected, so
            # a client cannot render a flow that lacks the control by accident.
            "file_anyway": FILE_ANYWAY_ALWAYS_AVAILABLE,
        }


def check(*, tenant, category: str = "", transaction_ref: str = "") -> Deflection:
    """Is this complaint already covered by a declared incident?"""
    for incident in Incident.objects.filter(
        status__in=[Incident.Status.DECLARED, Incident.Status.MITIGATING], ended_at__isnull=True
    ):
        if incident.matches(category=category, transaction_ref=transaction_ref):
            return Deflection(incident=incident)
    return Deflection(incident=None)


def record_deflection(*, tenant, incident: Incident, customer_ref_hash: str) -> None:
    """Every deflection is an audit record and a counted metric.

    §11.2's `deflections_total` is rendered next to case volume so that a drop in
    complaints during an outage is visibly a deflection rather than silently a
    suppression. A feature that reduces recorded complaints has to be the most
    heavily instrumented thing in the product, not the least.
    """
    audit.append(
        tenant=tenant,
        event_type="intake.deflected",
        subject_type="incident",
        subject_id=incident.pk,
        actor_type="system",
        payload={"customer_ref_hash": customer_ref_hash, "incident_title": incident.title},
    )


def subscribe(*, tenant, incident: Incident, customer_ref_hash: str, transaction_ref: str = ""):
    """ "Notify me" — the alternative offered alongside filing, never instead of it."""
    with transaction.atomic():
        subscription, created = IncidentSubscription.objects.get_or_create(
            tenant=tenant,
            incident=incident,
            customer_ref_hash=customer_ref_hash,
            transaction_ref=transaction_ref,
        )
        if created:
            record_deflection(tenant=tenant, incident=incident, customer_ref_hash=customer_ref_hash)
        return subscription


def deflections_total(*, tenant) -> int:
    """The metric §11.2 requires, from the audit trail rather than a counter.

    A counter can be reset; an append-only trail cannot. For a number whose whole
    purpose is to show that complaints were not suppressed, that distinction is
    the point.
    """
    from disputeshield.models import AuditRecord

    return AuditRecord.objects.filter(event_type="intake.deflected").count()
