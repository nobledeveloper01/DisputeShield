"""Private object storage, and URLs that are neither guessable nor permanent.

Two properties the exit gate names, and how each is obtained:

  * **Not guessable.** The storage key is derived from the file's own SHA-256 and
    a random identifier, never from the uploader's filename. A filename-derived
    path is both guessable and a path-traversal surface.
  * **Expiring.** Retrieval needs an HMAC over the attachment id and an expiry,
    keyed on the server's secret. A URL that leaks — into a chat log, a support
    ticket, a browser history — stops working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage

DEFAULT_TTL_SECONDS = 300


class SignatureInvalid(Exception):
    """Tampered, expired, or signed with a key that is no longer in use."""


def storage():
    """Private storage. Never `MEDIA_ROOT`, and never anything a web server serves.

    A file under a served directory is retrievable by URL guessing regardless of
    every check in the application, which would make the scan gate decorative.
    """
    root = getattr(settings, "DISPUTESHIELD_ATTACHMENT_ROOT", None) or (
        Path(settings.BASE_DIR) / ".private" / "attachments"
    )
    return FileSystemStorage(location=str(root), base_url=None)


def storage_key(tenant_id: str, attachment_id: str, sha256: str) -> str:
    """Content-addressed, tenant-partitioned, and free of user-supplied text."""
    return f"{tenant_id}/{sha256[:2]}/{attachment_id}"


def put(key: str, content: bytes) -> None:
    from django.core.files.base import ContentFile

    store = storage()
    if store.exists(key):
        store.delete(key)
    store.save(key, ContentFile(content))


def get(key: str) -> bytes:
    with storage().open(key, "rb") as handle:
        return handle.read()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# -- signed retrieval ----------------------------------------------------------


def sign(
    attachment_id: str,
    tenant_id: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
):
    """Sign an attachment id, its tenant, and an expiry.

    The tenant is in the signature because the download view is unauthenticated —
    the signature *is* the authorisation — and row level security needs a tenant
    context before it will return the row. Reading that tenant from the URL and
    trusting it would be a cross-tenant read waiting to happen; reading it from a
    value we signed is safe, because altering it invalidates the signature.
    """
    expires = int(now or time.time()) + ttl_seconds
    return expires, _signature(attachment_id, tenant_id, expires)


def verify(
    attachment_id: str, tenant_id: str, expires: int, signature: str, *, now: int | None = None
) -> None:
    if int(now or time.time()) > expires:
        raise SignatureInvalid("This link has expired.")
    expected = _signature(attachment_id, tenant_id, expires)
    # Constant time: a fast reject on the first wrong byte tells an attacker how
    # much of a forged signature was right.
    if not hmac.compare_digest(expected, signature):
        raise SignatureInvalid("This link is not valid.")


def _signature(attachment_id: str, tenant_id: str, expires: int) -> str:
    message = f"{attachment_id}.{tenant_id}.{expires}".encode()
    digest_bytes = hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest_bytes).decode().rstrip("=")
