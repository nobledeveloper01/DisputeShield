"""Envelope encryption for connector credentials (§8.4).

A KMS master key wraps a per-tenant data key, and the data key encrypts the
credential. The local implementation derives the tenant key from the project
secret, which is fine for development and is *not* what a hosted deployment runs
— `ENCRYPTION_KEY_REF` names a `kms://` key there, and the doctor refuses to
start without it.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CredentialUnreadable(Exception):
    """The credential could not be decrypted with this tenant's key."""


def _tenant_key(tenant_id: str) -> bytes:
    material = hashlib.sha256(
        f"disputeshield-connector:{settings.SECRET_KEY}:{tenant_id}".encode()
    ).digest()
    return base64.urlsafe_b64encode(material)


def encrypt_credential(tenant, credential: str) -> bytes:
    return Fernet(_tenant_key(tenant.pk)).encrypt(credential.encode())


def decrypt_credential(connector_row) -> str:
    try:
        return (
            Fernet(_tenant_key(connector_row.tenant_id))
            .decrypt(bytes(connector_row.credential_ciphertext))
            .decode()
        )
    except InvalidToken as exc:
        # A credential encrypted under another tenant's key is unreadable here,
        # which is the property that makes a per-tenant key worth having.
        raise CredentialUnreadable(
            "This credential cannot be decrypted with this tenant's key."
        ) from exc
