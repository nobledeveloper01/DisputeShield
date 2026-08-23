"""Server-minted, customer-scoped session tokens (§4.3, ADR-0002).

The whole security argument of the widget rests here. The scope is decided on the
fintech's own backend, where the customer's identity is actually known, and the
browser is never trusted with the question. A customer cannot see another
customer's disputes by tampering with the frontend, because the frontend was never
asked.

Opaque and Redis-backed rather than a JWT, so a leaked token can be revoked — one
session, every session for a customer, or every session minted by one key. Thirty
minutes of unrevocable access is thirty minutes we would have to describe to a
regulator as something we watched happen.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
from datetime import timedelta

import redis
from django.conf import settings
from django.utils import timezone

from disputeshield import conf
from disputeshield.models.dispute import hash_customer_ref

PREFIX = "dst_"
NAMESPACE = "disputeshield:session"
MAX_TRANSACTIONS = 50


class SessionExpired(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Session:
    token_hash: str
    tenant_id: str
    customer_ref_hash: str
    display_name: str
    transactions: tuple[dict, ...]
    api_key_id: str
    expires_at: str

    @property
    def customer_scope(self) -> str:
        return self.customer_ref_hash


def _client() -> redis.Redis:
    # The cache instance, not the broker (§11.1). A cache flush therefore signs
    # customers out — the widget fails closed and quietly per §8.6 principle 1,
    # the host page is untouched, and nothing is lost but a thirty-minute session.
    # Putting sessions on the broker would trade that for the ability to destroy
    # the SLA sweep's task queue, which is not a trade worth making.
    return redis.Redis.from_url(settings.CACHES["default"]["LOCATION"], decode_responses=True)


def digest(token: str) -> str:
    """Tokens are stored hashed, like API keys.

    A Redis snapshot in a backup, a `KEYS` dump in an incident, or a log line
    should not contain anything usable.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def mint(
    *,
    tenant,
    customer_ref: str,
    api_key_id: str,
    display_name: str = "",
    transactions: list[dict] | None = None,
    ttl_seconds: int | None = None,
) -> tuple[str, Session]:
    if not customer_ref:
        raise ValueError("customer_ref is required — it is the token's entire scope.")

    ttl_seconds = min(
        ttl_seconds or conf.get("SESSION_LIFETIME_SECONDS"),
        conf.get("SESSION_LIFETIME_SECONDS") * 2,
    )
    transactions = (transactions or [])[:MAX_TRANSACTIONS]

    token = PREFIX + secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

    session = Session(
        token_hash=digest(token),
        tenant_id=tenant.pk,
        # The raw customer_ref is never stored. The widget queries by hash, which
        # is also what the dispute rows carry (§8.4).
        customer_ref_hash=hash_customer_ref(tenant, customer_ref),
        display_name=display_name,
        transactions=tuple(transactions),
        api_key_id=api_key_id,
        expires_at=expires_at.isoformat(),
    )

    client = _client()
    pipeline = client.pipeline()
    pipeline.set(_key(session.token_hash), json.dumps(dataclasses.asdict(session)), ex=ttl_seconds)
    # Index by customer and by minting key, so revocation can be broad without a
    # scan. A revocation that requires a KEYS sweep is a revocation nobody runs
    # during the incident that needs it.
    pipeline.sadd(_customer_index(tenant.pk, session.customer_ref_hash), session.token_hash)
    pipeline.expire(_customer_index(tenant.pk, session.customer_ref_hash), ttl_seconds * 2)
    pipeline.sadd(_key_index(api_key_id), session.token_hash)
    pipeline.expire(_key_index(api_key_id), ttl_seconds * 2)
    pipeline.execute()

    return token, session


def resolve(token: str) -> Session:
    raw = _client().get(_key(digest(token)))
    if raw is None:
        raise SessionExpired("No such session, or it has expired.")
    payload = json.loads(raw)
    payload["transactions"] = tuple(payload["transactions"])
    return Session(**payload)


def revoke(token: str) -> bool:
    return bool(_client().delete(_key(digest(token))))


def revoke_for_customer(tenant_id: str, customer_ref_hash: str) -> int:
    """Every session for one customer. The response to a compromised device."""
    client = _client()
    index = _customer_index(tenant_id, customer_ref_hash)
    hashes = client.smembers(index)
    if not hashes:
        return 0
    removed = client.delete(*[_key(h) for h in hashes])
    client.delete(index)
    return removed


def revoke_for_key(api_key_id: str) -> int:
    """Every session minted by one key. The response to a leaked secret key.

    Available immediately, rather than after a key rotation completes — which is
    the difference between a contained incident and a thirty-minute wait.
    """
    client = _client()
    index = _key_index(api_key_id)
    hashes = client.smembers(index)
    if not hashes:
        return 0
    removed = client.delete(*[_key(h) for h in hashes])
    client.delete(index)
    return removed


def _key(token_hash: str) -> str:
    return f"{NAMESPACE}:tok:{token_hash}"


def _customer_index(tenant_id: str, customer_ref_hash: str) -> str:
    return f"{NAMESPACE}:cust:{tenant_id}:{customer_ref_hash}"


def _key_index(api_key_id: str) -> str:
    return f"{NAMESPACE}:key:{api_key_id}"
