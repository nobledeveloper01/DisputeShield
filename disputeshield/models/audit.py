from __future__ import annotations

from django.db import models

from disputeshield.identifiers import audit_id, checkpoint_id
from disputeshield.tenancy.managers import TenantScopedManager, TenantScopedQuerySet


class AuditRecordQuerySet(TenantScopedQuerySet):
    def delete(self):
        raise PermissionError(
            "Audit records cannot be deleted. A correction is an appended record "
            "carrying `corrects`, never a removal (§8.3)."
        )

    def update(self, **kwargs):
        raise PermissionError(
            "Audit records cannot be updated. A correction is an appended record "
            "carrying `corrects`, never a rewrite (§8.3)."
        )


class AuditRecordManager(TenantScopedManager.from_queryset(AuditRecordQuerySet)):
    pass


class AuditRecord(models.Model):
    """Append-only, hash-chained per tenant (§8.3).

    Four independent things make this immutable, and the redundancy is the point
    — each one alone has a failure mode the others cover:

    1. The ORM refuses (this class). Catches the ordinary mistake.
    2. The application database role holds INSERT and SELECT only. Catches raw SQL.
    3. A BEFORE UPDATE OR DELETE trigger raises regardless of role. Catches the
       superuser, and catches a grant that was never applied.
    4. The hash chain makes any successful tampering detectable after the fact.

    `disputeshield_doctor --strict` verifies 2 and 3 actually exist, because an
    installation where the migration silently failed has an audit trail that is
    immutable only by convention, and no way to find out.
    """

    id = models.CharField(primary_key=True, max_length=32, default=audit_id, editable=False)
    tenant = models.ForeignKey(
        "disputeshield.Tenant", on_delete=models.PROTECT, db_index=True, related_name="+"
    )

    # Monotonic per tenant. The chain's ordering is this, not the timestamp:
    # two records in the same millisecond still have an unambiguous predecessor.
    sequence = models.BigIntegerField()

    event_type = models.CharField(max_length=64, db_index=True)

    # When it happened, and when we recorded it. They differ, and a supervisor
    # asking about a delay is asking about exactly that difference.
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    actor_type = models.CharField(max_length=16)  # system | user | api_key
    actor_id = models.CharField(max_length=64, blank=True)
    actor_ip = models.GenericIPAddressField(null=True, blank=True)

    subject_type = models.CharField(max_length=32, db_index=True)
    subject_id = models.CharField(max_length=64, db_index=True)

    payload = models.JSONField(default=dict)

    # A correction never overwrites. It points at what it corrects, and both stay.
    corrects = models.CharField(max_length=32, blank=True)

    prev_hash = models.CharField(max_length=71)  # "sha256:" + 64 hex
    hash = models.CharField(max_length=71)

    objects = AuditRecordManager()

    class Meta:
        db_table = "disputeshield_auditrecord"
        ordering = ["tenant_id", "sequence"]
        # No add/change/delete permissions can be granted, to anyone, ever.
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "sequence"], name="uniq_audit_sequence_per_tenant"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "subject_type", "subject_id"]),
            models.Index(fields=["tenant", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.subject_type}:{self.subject_id} @{self.sequence}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit records cannot be modified (§8.3).")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit records cannot be deleted (§8.3).")


class AuditCheckpoint(models.Model):
    """A signed statement about a tenant's chain at a moment (§8.3).

    Verification walks the whole chain, which is O(records) and grows forever. A
    checkpoint is what lets a later verification start from a known-good point
    and what gives an auditor something small and signed to keep — the chain
    proves internal consistency, and a checkpoint is our attestation that we
    computed it and got this answer on this date.

    Scoped like everything else that carries a tenant. The first version of this
    model declared a `tenant` field with an unscoped default manager, on the
    reasoning that a platform job reads it — and the registry-walk test in
    `tests/test_tenant_isolation.py` failed it immediately, which is exactly what
    that test is for. Platform jobs get their scope from `for_each_tenant`, not
    from an exemption.
    """

    id = models.CharField(primary_key=True, max_length=32, default=checkpoint_id, editable=False)
    tenant = models.ForeignKey(
        "disputeshield.Tenant", on_delete=models.PROTECT, related_name="+", db_index=True
    )

    sequence_from = models.BigIntegerField()
    sequence_to = models.BigIntegerField()
    record_count = models.BigIntegerField()
    head_hash = models.CharField(max_length=71)

    verified = models.BooleanField()
    failure_detail = models.TextField(blank=True)

    computed_at = models.DateTimeField(auto_now_add=True)
    # HMAC over the checkpoint's own content. Phase 8 (amplifier A8) adds an
    # external RFC 3161 timestamp; until then this proves we produced it, not
    # when — and `GET /v1/audit/verify` reports those as two separate facts so
    # nobody reads the weaker claim as the stronger one.
    signature = models.CharField(max_length=128)

    objects = TenantScopedManager()

    class Meta:
        db_table = "disputeshield_auditcheckpoint"
        ordering = ["tenant_id", "-sequence_to"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "sequence_to"], name="uniq_checkpoint_per_tenant_head"
            )
        ]

    def __str__(self) -> str:
        state = "ok" if self.verified else "FAILED"
        return f"checkpoint {self.sequence_from}-{self.sequence_to} ({state})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Checkpoints are statements about a moment and are immutable.")
        return super().save(*args, **kwargs)
