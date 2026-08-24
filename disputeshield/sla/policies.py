"""Publishing SLA policy terms. The only path that creates a version.

ADR-0004 makes a policy's terms immutable and versioned: editing a policy
publishes version n+1, and a dispute pins the version in force when it was
filed. That is what makes "what standard was this case judged against" a
question with an answer years later, which is the question a supervisor actually
asks about a breach.

§7.3 documents `PATCH /v1/sla-policies/{id}`, and the two are reconciled here
rather than by picking one: a PATCH is accepted, and its effect is to **publish a
new version**. The policy resource changes; nothing that any case was judged
under is rewritten. A response that quietly mutated the terms in place would
satisfy the endpoint documentation and destroy the evidence.

Every publish is audited with the terms that changed, because a policy change is
itself something a supervisor asks about — a breach rate that improves the week
after the resolution window doubled is a fact about the policy, not about the
operation.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from disputeshield.models import BusinessCalendar, SLAPolicy, SLAPolicyVersion

# The fields a version is made of. Listed once so the diff, the serializer and
# the audit payload cannot disagree about what a policy's terms are.
TERMS = (
    "acknowledgement_minutes",
    "resolution_hours",
    "business_hours_only",
    "warning_thresholds",
    "escalate_at_percent",
    "auto_close_after_hours",
    "reopen_window_hours",
    "regulatory_reference",
)


class InvalidTerms(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Published:
    policy: SLAPolicy
    version: SLAPolicyVersion
    changed: dict


def validate(terms: dict) -> dict:
    """Refuse terms that cannot describe a real window.

    Each of these has been chosen because the failure it prevents is silent. A
    zero-hour resolution window breaches every case the moment it is filed; a
    threshold above 100 never fires and looks configured; an escalation point
    after the deadline escalates a case that has already breached.
    """
    cleaned = dict(terms)

    if cleaned.get("resolution_hours", 0) < 1:
        raise InvalidTerms(
            "resolution_hours must be at least 1. A zero-hour window breaches "
            "every case the moment it is filed."
        )
    if cleaned.get("acknowledgement_minutes", 0) < 1:
        raise InvalidTerms("acknowledgement_minutes must be at least 1.")

    thresholds = cleaned.get("warning_thresholds") or []
    if not isinstance(thresholds, list) or any(
        not isinstance(value, int) or not 1 <= value <= 99 for value in thresholds
    ):
        raise InvalidTerms(
            "warning_thresholds must be whole percentages between 1 and 99. A threshold at or "
            "above 100 never fires, and a policy that looks configured but warns nobody is "
            "worse than one with no warnings at all."
        )
    cleaned["warning_thresholds"] = sorted(set(thresholds))

    escalate = cleaned.get("escalate_at_percent", 0)
    if not 1 <= escalate <= 99:
        raise InvalidTerms(
            "escalate_at_percent must be between 1 and 99. Escalating at 100 escalates a case "
            "that has already breached, which is a notification rather than an escalation."
        )

    if cleaned.get("reopen_window_hours", 0) < 1:
        raise InvalidTerms("reopen_window_hours must be at least 1.")
    if cleaned.get("auto_close_after_hours", 0) < 1:
        raise InvalidTerms("auto_close_after_hours must be at least 1.")

    return cleaned


def diff(previous: SLAPolicyVersion | None, terms: dict) -> dict:
    """What actually changed, as `{field: [before, after]}`.

    Recorded rather than the whole new version, because "the resolution window
    went from 72 to 168 hours on the 4th" is the sentence a supervisor needs, and
    it is not recoverable from two full snapshots without someone comparing them.
    """
    if previous is None:
        return {field: [None, terms.get(field)] for field in TERMS if terms.get(field) is not None}

    changed = {}
    for field in TERMS:
        before = getattr(previous, field)
        after = terms.get(field, before)
        if before != after:
            changed[field] = [before, after]
    return changed


def publish(
    *, tenant, category: str, terms: dict, calendar=None, actor_id: str, description: str = ""
) -> Published:
    """Create the policy if it is new, then publish version n+1 of its terms."""
    from disputeshield import audit

    cleaned = validate(terms)

    with transaction.atomic():
        policy, _created = SLAPolicy.objects.get_or_create(
            tenant=tenant, category=category, defaults={"description": description}
        )
        previous = policy.versions.order_by("-version").first()

        if calendar is None:
            calendar = previous.calendar if previous else BusinessCalendar.objects.first()
        if calendar is None:
            raise InvalidTerms(
                "This tenant has no business calendar, so a business-hours policy has no hours "
                "to compute against. Run disputeshield_init or create one first."
            )

        changed = diff(previous, cleaned)
        if previous is not None and not changed and calendar == previous.calendar:
            # Nothing to publish. A no-op version is a row in the change history
            # that a reviewer has to read and discard, and enough of them make the
            # history useless for the one thing it exists for.
            return Published(policy=policy, version=previous, changed={})

        version = SLAPolicyVersion.objects.create(
            tenant=tenant,
            policy=policy,
            version=(previous.version + 1) if previous else 1,
            calendar=calendar,
            created_by=actor_id,
            **{
                field: cleaned.get(field, getattr(previous, field) if previous else None)
                for field in TERMS
                if cleaned.get(field, getattr(previous, field, None)) is not None
            },
        )

        audit.append(
            tenant=tenant,
            event_type="sla_policy.published",
            subject_type="sla_policy",
            subject_id=policy.pk,
            actor_type="user",
            actor_id=actor_id,
            payload={
                "category": category,
                "version": version.version,
                "changed": changed,
                "calendar": calendar.name,
            },
        )

    return Published(policy=policy, version=version, changed=changed)
