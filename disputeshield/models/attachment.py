from __future__ import annotations

from django.db import models

from disputeshield.identifiers import new_id
from disputeshield.tenancy.managers import TenantScopedModel


def attachment_id() -> str:
    return new_id("att")


def template_id() -> str:
    return new_id("tpl")


def context_id() -> str:
    return new_id("ctx")


class DisputeAttachment(TenantScopedModel):
    """Evidence a customer or an agent attached to a case.

    Not retrievable by anyone until `scan_status == 'clean'` — including the
    person who uploaded it. That last part is the one worth stating: an uploader
    who can fetch their own file back before it is scanned has a working
    file-hosting endpoint on a fintech's domain, and the malware never needs to
    reach an agent to be useful.
    """

    class ScanStatus(models.TextChoices):
        PENDING = "pending", "Awaiting scan"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        FAILED = "failed", "Scan failed"

    id = models.CharField(primary_key=True, max_length=32, default=attachment_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="attachments", on_delete=models.PROTECT
    )

    uploaded_by_type = models.CharField(max_length=16)  # customer | agent
    uploaded_by_id = models.CharField(max_length=64, blank=True)

    # The name the uploader gave it, kept for display only. Never used to decide
    # what the file is, and never used to build a storage path.
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)

    storage_key = models.CharField(max_length=512)
    scan_status = models.CharField(
        max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING, db_index=True
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    scan_detail = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_disputeattachment"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["tenant", "dispute", "scan_status"])]

    def __str__(self) -> str:
        return f"{self.filename} ({self.scan_status})"

    @property
    def is_retrievable(self) -> bool:
        return self.scan_status == self.ScanStatus.CLEAN

    def delete(self, *args, **kwargs):
        raise PermissionError(
            "Attachments are evidence and are not deletable. Retention and legal "
            "hold decide when they go (§11.7)."
        )


class ResponseTemplate(TenantScopedModel):
    """A canned reply with variable substitution (§3.3 B3).

    Templates are agent-facing text. The substitution engine is deliberately not
    a template language: see `disputeshield/templates_engine.py` for why a
    compliance product should not ship a Turing-complete renderer that a
    non-engineer edits in a dashboard.
    """

    id = models.CharField(primary_key=True, max_length=32, default=template_id, editable=False)
    name = models.CharField(max_length=128)
    category = models.CharField(max_length=64, blank=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_responsetemplate"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_template_name_per_tenant")
        ]

    def __str__(self) -> str:
        return self.name


class TransactionContext(TenantScopedModel):
    """What the fintech knows about the transaction, attached to the case (§7.3).

    Pushed by the host application; never pulled. §7.1's strongest security
    claim is that DisputeShield holds no standing access to the customer's
    database, and a context endpoint that reached back for data would quietly
    retire it.
    """

    id = models.CharField(primary_key=True, max_length=32, default=context_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="context_entries", on_delete=models.PROTECT
    )
    source = models.CharField(max_length=64)  # ledger | provider | support | …
    occurred_at = models.DateTimeField()
    summary = models.CharField(max_length=255)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_transactioncontext"
        ordering = ["occurred_at", "id"]
        indexes = [models.Index(fields=["tenant", "dispute", "occurred_at"])]

    def __str__(self) -> str:
        return f"{self.source}: {self.summary}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                "Context entries are a record of what was known at a moment. A "
                "correction is a new entry, never a rewrite."
            )
        return super().save(*args, **kwargs)
