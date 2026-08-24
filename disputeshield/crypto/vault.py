"""Sealing content so it can later be crypto-shredded (amplifier A20).

The property this exists for, stated once:

  **After a shred, the hash chain still verifies and the content is
  unrecoverable.** Either half alone is worthless — a shred that breaks the chain
  destroys the evidence a regulator is entitled to, and a shred that leaves the
  content readable is not a shred.

Both hold because the chain hashes *what is stored*: ciphertext and metadata,
never plaintext. Destroying a key changes no row, so nothing the chain covers
moves.

Sealing is **opt-in per tenant**. A tenant with it off has content that can only
be deleted, and an append-only system cannot delete — so the honest answer to an
erasure request there is the refusal §11.7 describes. Turning sealing on is what
buys the ability to say yes.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from disputeshield.models import SubjectKey

SEALED_PREFIX = "sealed:v1:"
SHREDDED_MARKER = "[content erased under a lawful request]"


class Shredded(Exception):
    """The key that protected this content has been destroyed.

    Not an error to be handled away: the content is gone, permanently, and a
    caller's only correct response is to show that it was erased.
    """


class KeyUnavailable(Exception):
    """The master key could not unwrap this data key.

    Under BYOK this is what a customer revoking their key looks like from here.
    We cannot recover it, and saying so plainly is the point of the arrangement.
    """


@dataclasses.dataclass(frozen=True)
class ShredResult:
    subject_hash: str
    keys_destroyed: int


def _master_key(tenant) -> bytes:
    """Wraps a tenant's data keys.

    The local derivation below is development only. A hosted deployment sets
    `ENCRYPTION_KEY_REF` to a `kms://` key, and under BYOK that key lives in the
    customer's own KMS — which is what makes revocation theirs to perform.
    """
    material = hashlib.sha256(
        f"disputeshield-master:{settings.SECRET_KEY}:{tenant.pk}".encode()
    ).digest()
    return base64.urlsafe_b64encode(material)


def key_for(tenant, subject_hash: str, *, create: bool = True) -> SubjectKey | None:
    existing = SubjectKey.objects.filter(subject_hash=subject_hash).first()
    if existing is not None or not create:
        return existing

    data_key = Fernet.generate_key()
    return SubjectKey.objects.create(
        tenant=tenant,
        subject_hash=subject_hash,
        wrapped_key=Fernet(_master_key(tenant)).encrypt(data_key),
        master_key_ref=str(getattr(settings, "DISPUTESHIELD", {}).get("ENCRYPTION_KEY_REF", "")),
    )


def _data_key(tenant, key_row: SubjectKey) -> bytes:
    if key_row.is_destroyed:
        raise Shredded(
            f"The key protecting {key_row.subject_hash[:12]}… was destroyed on "
            f"{key_row.destroyed_at.isoformat()}. The content is unrecoverable."
        )
    try:
        return Fernet(_master_key(tenant)).decrypt(bytes(key_row.wrapped_key))
    except InvalidToken as exc:
        raise KeyUnavailable(
            "This data key cannot be unwrapped with the tenant's master key. Under "
            "BYOK that is what a revoked customer key looks like from here, and we "
            "cannot recover it."
        ) from exc


def seal(tenant, subject_hash: str, plaintext: str) -> str:
    """Encrypt content under the subject's key. A no-op when sealing is off."""
    if not tenant.content_sealing_enabled or not plaintext:
        return plaintext

    key_row = key_for(tenant, subject_hash)
    token = Fernet(_data_key(tenant, key_row)).encrypt(plaintext.encode()).decode()
    return f"{SEALED_PREFIX}{token}"


def unseal(tenant, subject_hash: str, stored: str) -> str:
    """Decrypt, or say plainly that the content was erased.

    Returns the marker rather than raising for a shredded subject, because every
    caller that displays content needs to show *something*, and "erased under a
    lawful request" is the true thing to show.
    """
    if not stored or not stored.startswith(SEALED_PREFIX):
        return stored

    key_row = key_for(tenant, subject_hash, create=False)
    if key_row is None:
        return SHREDDED_MARKER
    try:
        data_key = _data_key(tenant, key_row)
    except (Shredded, KeyUnavailable):
        return SHREDDED_MARKER

    try:
        return Fernet(data_key).decrypt(stored.removeprefix(SEALED_PREFIX).encode()).decode()
    except InvalidToken:
        return SHREDDED_MARKER


def is_sealed(stored: str) -> bool:
    return bool(stored) and stored.startswith(SEALED_PREFIX)


def shred(
    *, tenant, subject_hash: str, requested_by: str, approved_by: str, reason: str
) -> ShredResult:
    """Destroy a subject's key. Irreversible, and it takes two people.

    Checked against legal hold first: §11.7's retention obligation and an erasure
    request point in opposite directions, and the hold wins — silently shredding
    material under hold would be spoliation of evidence performed by a feature
    built to be lawful.
    """
    from disputeshield import audit
    from disputeshield.retention import holds

    if not reason.strip():
        raise ValueError("A shred requires a reason. It cannot be undone.")
    if not approved_by.strip() or approved_by == requested_by:
        raise PermissionError(
            "Crypto-shredding requires a second, different approver. It is "
            "irreversible, and a two-person rule one person can satisfy twice is a "
            "one-person rule."
        )

    blocking = holds.holds_for_customer(subject_hash)
    if blocking:
        raise holds.HeldMaterial(
            "This subject's material is under legal hold "
            f"({', '.join(sorted({h.matter_reference for h in blocking}))}) and cannot "
            "be shredded while the hold stands."
        )

    with transaction.atomic():
        keys = list(SubjectKey.objects.filter(subject_hash=subject_hash, destroyed_at__isnull=True))
        now = timezone.now()
        for key_row in keys:
            key_row.destroyed_at = now
            key_row.destroyed_by = requested_by
            key_row.destruction_approved_by = approved_by
            key_row.destruction_reason = reason
            # The wrapped key is overwritten as well as marked. A flag alone
            # leaves the material sitting in a backup somebody can restore.
            key_row.wrapped_key = b""
            key_row.save(
                update_fields=[
                    "destroyed_at",
                    "destroyed_by",
                    "destruction_approved_by",
                    "destruction_reason",
                    "wrapped_key",
                ]
            )

        audit.append(
            tenant=tenant,
            event_type="crypto.shredded",
            subject_type="subject_key",
            subject_id=subject_hash,
            actor_type="user",
            actor_id=requested_by,
            payload={
                "keys_destroyed": len(keys),
                "approved_by": approved_by,
                "reason": reason,
                # The fact that data was erased on a lawful request is itself
                # something that must be provable.
                "irreversible": True,
            },
        )
    return ShredResult(subject_hash=subject_hash, keys_destroyed=len(keys))
