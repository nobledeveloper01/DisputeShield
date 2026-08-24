from __future__ import annotations

from django.db import models

from disputeshield.identifiers import import_batch_id, subject_key_id
from disputeshield.tenancy.managers import TenantScopedModel


class SubjectKey(TenantScopedModel):
    """A per-subject data key, and the only thing a shred destroys (amplifier A20).

    §11.7 is admirably honest that deletion in an append-only system is genuinely
    difficult. Crypto-shredding is the resolution of that tension: the record and
    its hash chain stay intact and verifiable, while the content becomes
    permanently unrecoverable.

    That works only because the chain hashes **what is stored** — ciphertext and
    metadata — rather than the plaintext. Destroying the key changes no row, so
    nothing the chain covers moves, and the chain still verifies afterwards. Both
    halves of that are asserted in `tests/test_residency.py`, because either one
    alone is worthless: a shred that breaks the chain destroys the evidence, and a
    shred that leaves the content readable is not a shred.

    Irreversible, and requiring two people. The shred event is itself an audit
    record — which is exactly right, because the fact that data was erased on a
    lawful request is something that must be provable.
    """

    id = models.CharField(primary_key=True, max_length=32, default=subject_key_id, editable=False)

    # The subject this key protects. A customer, so an erasure request destroys
    # exactly their content and nobody else's.
    subject_hash = models.CharField(max_length=64, db_index=True)

    # The data key, wrapped by the tenant's master key. For BYOK the master lives
    # in the customer's KMS, so revoking it renders this unwrappable without us
    # being involved — which is the property BYOK is bought for.
    wrapped_key = models.BinaryField()
    master_key_ref = models.CharField(max_length=128, blank=True)

    destroyed_at = models.DateTimeField(null=True, blank=True)
    destroyed_by = models.CharField(max_length=64, blank=True)
    destruction_approved_by = models.CharField(max_length=64, blank=True)
    destruction_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_subjectkey"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "subject_hash"], name="uniq_key_per_subject")
        ]
        indexes = [models.Index(fields=["tenant", "destroyed_at"])]

    def __str__(self) -> str:
        return f"key for {self.subject_hash[:12]}… ({'destroyed' if self.is_destroyed else 'live'})"

    @property
    def is_destroyed(self) -> bool:
        return self.destroyed_at is not None


class ImportBatch(TenantScopedModel):
    """History brought in from somewhere else (amplifier A18).

    The single largest adoption blocker in the product: a compliance officer
    cannot adopt a system that starts empty, because their retention obligation
    covers cases that already exist.

    Imported history must stay **distinguishable from native history, forever**.
    An imported case's trail carries no integrity claim from us — we did not
    witness it — and the chain says so plainly rather than absorbing foreign data
    and implying we vouch for it.
    """

    class Source(models.TextChoices):
        ZENDESK = "zendesk", "Zendesk"
        FRESHDESK = "freshdesk", "Freshdesk"
        INTERCOM = "intercom", "Intercom"
        CSV = "csv", "CSV export"
        MAILBOX = "mailbox", "IMAP archive"

    id = models.CharField(primary_key=True, max_length=32, default=import_batch_id, editable=False)
    source = models.CharField(max_length=16, choices=Source.choices)
    description = models.CharField(max_length=255, blank=True)

    # Hash of the file we were handed, so "this is what you gave us" is provable.
    source_digest = models.CharField(max_length=64, blank=True)
    imported_at = models.DateTimeField()
    imported_by = models.CharField(max_length=64, blank=True)

    cases_imported = models.PositiveIntegerField(default=0)
    cases_rejected = models.PositiveIntegerField(default=0)
    rejections = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_importbatch"
        ordering = ["-imported_at"]

    def __str__(self) -> str:
        return f"{self.source} import ({self.cases_imported} cases)"
