from __future__ import annotations

from django.core.validators import validate_email
from django.db import models

from disputeshield.identifiers import report_recipient_id
from disputeshield.tenancy.managers import TenantScopedModel


class ReportRecipient(TenantScopedModel):
    """An address a regulatory export may be sent to. An allowlist, not a field.

    A regulatory export is a disclosure of **every case in the period**. An
    endpoint that emails one to an address supplied in the request body is a data
    exfiltration route with a documented API — a single compromised compliance
    session, or one mistyped domain, sends the whole period somewhere nobody
    intended and nothing stops it. So the address is registered first, as its own
    audited act, and the send can only choose from what is already registered.

    Registration is deliberately the harder half: it is compliance-only, it
    requires a stated reason, and it writes an audit record naming who added the
    address. The send then has nothing interesting to authorise, which is the
    point — the decision was made earlier, by someone accountable, on the record.

    Deactivated rather than deleted, for the reason everything here is: "who was
    allowed to receive our disputes data in March" is a question a supervisor is
    entitled to ask, and a deleted row cannot answer it.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=report_recipient_id, editable=False
    )

    address = models.EmailField(max_length=254, validators=[validate_email])

    # Who this is, in the tenant's own words: "FCA supervision inbox", "Group
    # compliance archive". Shown wherever the address is, because an address
    # alone does not tell a reviewer whether it should be there.
    label = models.CharField(max_length=128)

    added_by = models.CharField(max_length=64)
    reason = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)
    deactivated_by = models.CharField(max_length=64, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_reportrecipient"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "address"], name="disputeshield_recipient_unique_per_tenant"
            )
        ]
        indexes = [models.Index(fields=["tenant", "is_active"])]

    def __str__(self) -> str:
        return f"{self.label} <{self.address}>"
