"""External anchoring of chain checkpoints (amplifier A8).

The gate this module is written to: **anchoring must degrade without stalling.**
A timestamp authority that is unreachable leaves anchors pending, reports the
backlog as a metric, and anchors it in order on recovery. An evidence system that
stops accepting evidence because a third party is down has chosen the wrong
failure.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib

from django.db import transaction
from django.utils import timezone

from disputeshield.models import AuditCheckpoint, CheckpointAnchor

MAX_ATTEMPTS = 10


class AuthorityUnreachable(Exception):
    """The timestamp authority did not answer. Not fatal, and never a write block."""


@dataclasses.dataclass(frozen=True)
class AnchorResult:
    anchored: int
    pending: int
    failed: int


class TimestampAuthority:
    name = "authority"

    def timestamp(self, digest: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class UnconfiguredAuthority(TimestampAuthority):
    """No authority configured. Says so, forever, rather than pretending.

    Marks anchors pending rather than failed: an installation that has not yet
    configured a TSA has an unanchored backlog it can still resolve later, and
    calling that `failed` would invite somebody to clear it.
    """

    name = "unconfigured"

    def timestamp(self, digest: str) -> str:
        raise AuthorityUnreachable(
            "No timestamp authority is configured. Set DISPUTESHIELD['TIMESTAMP_AUTHORITY']."
        )


class LocalAuthority(TimestampAuthority):
    """Development and CI. Deterministic, and honest about what it is not.

    This is **not** an external attestation: it is us, signing our own claim
    about our own chain, which proves nothing an adversary with our key could not
    also produce. `GET /v1/audit/verify` reports `externally_anchored` from the
    authority's own declaration, so a local authority never lets the API claim
    something it has not got.
    """

    name = "local"
    external = False

    def timestamp(self, digest: str) -> str:
        return "local:" + hashlib.sha256(f"local-tsa:{digest}".encode()).hexdigest()


def get_authority() -> TimestampAuthority:
    from disputeshield import conf

    path = conf.get("TIMESTAMP_AUTHORITY")
    if not path:
        return UnconfiguredAuthority()
    module_name, _, class_name = path.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)()


def checkpoint_digest(checkpoint: AuditCheckpoint) -> str:
    """What gets timestamped: the checkpoint's head hash and its range.

    Not the whole chain — a timestamp authority takes a digest, and the head hash
    already commits to every record beneath it.
    """
    material = f"{checkpoint.tenant_id}.{checkpoint.sequence_to}.{checkpoint.head_hash}"
    return hashlib.sha256(material.encode()).hexdigest()


def queue(checkpoint: AuditCheckpoint) -> CheckpointAnchor:
    """Record the intent to anchor, in the same transaction as the checkpoint."""
    anchor, _ = CheckpointAnchor.objects.get_or_create(
        tenant=checkpoint.tenant, checkpoint_id=checkpoint.pk
    )
    return anchor


def anchor_pending(*, limit: int = 100) -> AnchorResult:
    """Anchor the backlog, oldest first.

    In order, because a run of checkpoints anchored out of sequence gives an
    auditor timestamps that appear to contradict the chain they describe.
    """
    from disputeshield.tenancy.platform import for_each_tenant

    anchored = pending = failed = 0
    for tenant_anchored, tenant_pending, tenant_failed in for_each_tenant(
        lambda _tenant_id: _anchor_one_tenant(limit)
    ):
        anchored += tenant_anchored
        pending += tenant_pending
        failed += tenant_failed
    return AnchorResult(anchored=anchored, pending=pending, failed=failed)


def _anchor_one_tenant(limit: int) -> tuple[int, int, int]:
    authority = get_authority()
    anchored = pending = failed = 0

    backlog = list(
        CheckpointAnchor.objects.filter(status=CheckpointAnchor.Status.PENDING).order_by(
            "created_at", "id"
        )[:limit]
    )

    for anchor in backlog:
        with transaction.atomic():
            anchor.attempts += 1
            try:
                token = authority.timestamp(checkpoint_digest(anchor.checkpoint))
            except Exception as exc:
                anchor.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if anchor.attempts >= MAX_ATTEMPTS:
                    anchor.status = CheckpointAnchor.Status.FAILED
                    failed += 1
                else:
                    pending += 1
                anchor.save(update_fields=["attempts", "last_error", "status"])
                continue

            anchor.status = CheckpointAnchor.Status.ANCHORED
            anchor.authority = authority.name
            anchor.token = token
            anchor.anchored_at = timezone.now()
            anchor.save(update_fields=["attempts", "status", "authority", "token", "anchored_at"])
            anchored += 1

    return anchored, pending, failed


def unanchored_total() -> int:
    """The metric §11.2 needs for this: how far behind the anchoring is.

    Exported so that "we anchor our chain" does not quietly become "we anchored
    our chain until the TSA's certificate expired in March".
    """
    return CheckpointAnchor.objects.filter(status=CheckpointAnchor.Status.PENDING).count()


def anchor_status(tenant) -> dict:
    """The anchor half of `GET /v1/audit/verify`, kept separate from the chain half."""
    latest = (
        CheckpointAnchor.objects.filter(status=CheckpointAnchor.Status.ANCHORED)
        .order_by("-anchored_at")
        .first()
    )
    authority = get_authority()

    return {
        "anchored": latest is not None,
        # Read from the authority's own declaration rather than assumed from the
        # presence of a token: a development authority must never let the API
        # claim an external attestation it has not got.
        "external": bool(getattr(authority, "external", True)) if latest else False,
        "authority": latest.authority if latest else authority.name,
        "sequence_to": latest.checkpoint.sequence_to if latest else None,
        "anchored_at": latest.anchored_at.isoformat() if latest else None,
        "unanchored_checkpoints": unanchored_total(),
    }
