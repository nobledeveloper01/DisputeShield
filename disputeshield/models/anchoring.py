from __future__ import annotations

from django.db import models

from disputeshield.identifiers import anchor_id
from disputeshield.tenancy.managers import TenantScopedModel


class CheckpointAnchor(TenantScopedModel):
    """An external attestation that a checkpoint existed on a date (A8).

    The hash chain proves internal consistency: no record was altered relative to
    its neighbours. It cannot prove *when* the chain existed, because an adversary
    with full control could rebuild a consistent chain after the fact. An RFC 3161
    timestamp from a third party closes that gap, turning "we can show our records
    are consistent" into "we can show these records existed on this date and
    somebody who is not us attests to it".

    Anchoring must never block a write. A timestamp authority that is unreachable
    leaves anchors `pending`, which is reported as a metric and retried — the
    alternative is an evidence system that stops accepting evidence because a
    third party is down.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting the timestamp authority"
        ANCHORED = "anchored", "Timestamped"
        FAILED = "failed", "Rejected by the authority"

    id = models.CharField(primary_key=True, max_length=32, default=anchor_id, editable=False)

    # Deliberately not a ForeignKey.
    #
    # Postgres enforces a foreign key by taking a `FOR KEY SHARE` lock on the
    # referenced row, and a row lock requires UPDATE or DELETE privilege on the
    # parent table. Migration 0014 revoked exactly those on the checkpoint table
    # to make it append-only — so a foreign key into it fails with "permission
    # denied", for the application role, on every insert.
    #
    # You cannot hold a database-enforced reference to a table whose rows you
    # have deliberately removed the ability to lock. The relationship is enforced
    # in the service instead, which is cheap here: checkpoints are immutable and
    # are never deleted, so the integrity a foreign key would buy is already a
    # property of the parent.
    checkpoint_id = models.CharField(max_length=32, unique=True, db_index=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    authority = models.CharField(max_length=128, blank=True)
    # The RFC 3161 token, or a transparency-log inclusion proof. Opaque to us on
    # purpose: verification is the authority's algorithm, not ours, and an
    # auditor should be able to check it without our code.
    token = models.TextField(blank=True)
    anchored_at = models.DateTimeField(null=True, blank=True)

    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_checkpointanchor"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["tenant", "status", "created_at"])]

    def __str__(self) -> str:
        return f"anchor for {self.checkpoint_id} ({self.status})"

    @property
    def checkpoint(self):
        """The checkpoint this anchors. Scoped, so it cannot cross a tenant."""
        from disputeshield.models import AuditCheckpoint

        return AuditCheckpoint.objects.get(pk=self.checkpoint_id)
