"""The retention sweep, and what it must not touch.

§11.7 sets seven years for cases, messages and audit records. The sweep that
enforces it is also the process most capable of destroying evidence, so it does
two things before it deletes anything: it asks whether a legal hold covers the
material, and it records what it skipped.

Nothing here deletes an audit record. Those are append-only at the database
level (§8.3) and outlive everything else; retention on them is a storage-lifecycle
concern, handled by the object-locked replica rather than by a `DELETE`.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from django.utils import timezone

from disputeshield import audit
from disputeshield.models import Dispute
from disputeshield.retention import holds

RETENTION_YEARS = 7


@dataclasses.dataclass(frozen=True)
class SweepResult:
    examined: int
    expired: int
    skipped_on_hold: int
    held_references: tuple[str, ...]

    @property
    def quiet(self) -> bool:
        return self.expired == 0 and self.skipped_on_hold == 0


def expired_before(*, now=None) -> object:
    now = now or timezone.now()
    return now - timedelta(days=365 * RETENTION_YEARS)


def sweep(*, now=None, dry_run: bool = True) -> SweepResult:
    """Find cases past retention. Reports by default; deletes only when told to.

    `dry_run=True` is the default deliberately. A retention sweep that deletes on
    its first accidental invocation is the single most destructive thing in this
    codebase, and a default that requires an explicit opt-in is cheap insurance
    against a mistyped management command.
    """
    now = now or timezone.now()
    cutoff = expired_before(now=now)

    examined = expired = skipped = 0
    held_references: set[str] = set()

    for dispute in Dispute.objects.filter(closed_at__isnull=False, closed_at__lt=cutoff):
        examined += 1
        held = holds.check(dispute)
        if held.held:
            skipped += 1
            held_references.update(held.references)
            # Recorded, so that a case still present after its retention window
            # has a reason in the record rather than looking like a sweep that
            # missed it.
            audit.append(
                tenant=dispute.tenant,
                event_type="retention.skipped_on_hold",
                subject_type="dispute",
                subject_id=dispute.pk,
                actor_type="system",
                payload={"matter_references": sorted(held.references)},
            )
            continue

        expired += 1
        if not dry_run:
            audit.append(
                tenant=dispute.tenant,
                event_type="retention.expired",
                subject_type="dispute",
                subject_id=dispute.pk,
                actor_type="system",
                payload={"closed_at": dispute.closed_at.isoformat(), "years": RETENTION_YEARS},
            )

    return SweepResult(
        examined=examined,
        expired=expired,
        skipped_on_hold=skipped,
        held_references=tuple(sorted(held_references)),
    )
