from __future__ import annotations

import hashlib
import hmac

from django.db import models

from disputeshield.identifiers import (
    contact_id,
    inbound_message_id,
    ingest_address_id,
)
from disputeshield.tenancy.managers import TenantScopedModel


class Channel(models.TextChoices):
    """Every way a complaint arrives (§2.1, amplifier A1).

    The widget solves one of these. A fintech that installs DisputeShield and
    still runs a shared inbox has two systems of record and a regulatory answer
    of "some of them are tracked", which is worse than one untracked inbox
    because it looks like coverage.
    """

    WIDGET = "widget", "Widget"
    EMAIL = "email", "Email"
    WHATSAPP = "whatsapp", "WhatsApp"
    USSD = "ussd", "USSD"
    PHONE = "phone", "Phone call"
    SOCIAL = "social", "Social media"
    WEB_FORM = "web_form", "Web form"


def hash_identity(tenant, identity: str) -> str:
    """A channel identity — an address, a number, a handle — pseudonymised.

    Same treatment as `customer_ref` (§8.4), and for the same reason: a phone
    number is more identifying than a customer reference, not less.
    """
    return hmac.new(
        tenant.customer_ref_salt.encode(), identity.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


class IngestAddress(TenantScopedModel):
    """Where a tenant's complaints arrive on a given channel."""

    id = models.CharField(
        primary_key=True, max_length=32, default=ingest_address_id, editable=False
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    address = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_ingestaddress"
        constraints = [
            models.UniqueConstraint(fields=["channel", "address"], name="uniq_ingest_address")
        ]

    def __str__(self) -> str:
        return f"{self.channel}: {self.address}"


class DisputeContact(TenantScopedModel):
    """The identity a case is allowed to receive messages from, per channel.

    This is the whole of A1's guardrail. Channel identity never grants case
    access on its own — an inbound message from an address that is not on this
    list is quarantined for a human to attribute, never appended and never echoed
    back into the thread.

    Stored hashed, so an operator reviewing a quarantine queue is comparing
    digests rather than reading a list of customers' phone numbers.
    """

    id = models.CharField(primary_key=True, max_length=32, default=contact_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="contacts", on_delete=models.PROTECT
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    identity_hash = models.CharField(max_length=64, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_disputecontact"
        constraints = [
            models.UniqueConstraint(
                fields=["dispute", "channel", "identity_hash"], name="uniq_contact_per_channel"
            )
        ]
        indexes = [models.Index(fields=["tenant", "channel", "identity_hash"])]

    def __str__(self) -> str:
        return f"{self.channel} contact for {self.dispute_id}"


class InboundMessage(TenantScopedModel):
    """Something that arrived, and what we decided to do with it.

    Every inbound message is recorded before it is matched, including the ones
    that turn out to be nothing. A message that arrived and was silently dropped
    is a complaint the firm cannot prove it never received.
    """

    class State(models.TextChoices):
        MATCHED = "matched", "Appended to a case"
        FILED = "filed", "Opened a new case"
        UNMATCHED_REVIEW = "unmatched_review", "Needs a human to attribute"
        QUARANTINED = "quarantined", "Sender is not the case's verified contact"
        IGNORED = "ignored", "Auto-reply, bounce or duplicate"

    id = models.CharField(
        primary_key=True, max_length=32, default=inbound_message_id, editable=False
    )
    channel = models.CharField(max_length=16, choices=Channel.choices, db_index=True)

    # Hashed. The raw value never lands in the database (§8.4).
    from_identity_hash = models.CharField(max_length=64, db_index=True)
    # A per-channel conversation key: an email thread root, a WhatsApp
    # conversation id, a USSD session. Hashed for the same reason.
    thread_key_hash = models.CharField(max_length=64, blank=True, db_index=True)

    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    received_at = models.DateTimeField()

    state = models.CharField(max_length=24, choices=State.choices, db_index=True)
    state_reason = models.CharField(max_length=255, blank=True)
    dispute = models.ForeignKey(
        "disputeshield.Dispute",
        null=True,
        blank=True,
        related_name="inbound_messages",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_inboundmessage"
        ordering = ["received_at", "id"]
        indexes = [
            models.Index(fields=["tenant", "state", "received_at"]),
            models.Index(fields=["tenant", "channel", "thread_key_hash"]),
        ]

    def __str__(self) -> str:
        return f"{self.channel} message ({self.state})"

    def save(self, *args, **kwargs):
        if not self._state.adding and set(kwargs.get("update_fields") or []) - {
            "state",
            "state_reason",
            "dispute",
        }:
            raise PermissionError(
                "An inbound message records what arrived. Only its disposition may change."
            )
        return super().save(*args, **kwargs)
