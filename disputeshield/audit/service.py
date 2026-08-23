"""Appending to the audit trail. The only supported way to write evidence.

ADR-0003: the append takes a per-tenant transaction-scoped advisory lock and
runs inside the same transaction as the domain write it describes. Two
consequences fall out, and both are the reason the lock is worth its cost:

  * Two concurrent appends cannot read the same head and fork the chain. A fork
    is indistinguishable from tampering, so without this the nightly verifier
    pages for a security incident caused by two agents clicking at once.
  * A resolved dispute with no audit record — and an audit record for a
    resolution that rolled back — are both impossible rather than unlikely.
"""

from __future__ import annotations

from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from disputeshield import conf
from disputeshield.audit.chain import GENESIS, compute_hash, record_content
from disputeshield.models import AuditRecord, Tenant
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

ACTOR_TYPES = frozenset({"system", "user", "api_key", "customer"})


class ActorRequired(ValueError):
    """An unattributed state change is not evidence of anything."""


class TenantMismatch(RuntimeError):
    """Raised when an append targets a tenant other than the active one.

    Without this guard, appending for tenant B inside a request scoped to tenant
    A would move the transaction's RLS context to B — leaving every subsequent
    query in that request reading B's rows. The failure would be silent, and it
    would be a cross-tenant read produced by the audit trail.
    """


def append(
    *,
    tenant: Tenant,
    event_type: str,
    subject_type: str,
    subject_id: str,
    actor_type: str,
    actor_id: str = "",
    actor_ip: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at=None,
    corrects: str = "",
) -> AuditRecord:
    # §8.3 lists system|user|api_key. `customer` is added deliberately: a customer
    # filing a case or replying to one is an actor whose actions are evidence, and
    # recording them as `api_key` would attribute the customer's own words to the
    # fintech's integration. They are identified by their pseudonymous
    # `customer_ref_hash`, which is attributable without being identifying.
    if actor_type not in ACTOR_TYPES:
        raise ActorRequired(
            f"actor_type must be one of {'|'.join(sorted(ACTOR_TYPES))}, got {actor_type!r}. "
            "Every audit record names who acted, including the scheduler."
        )
    if actor_type != "system" and not actor_id:
        raise ActorRequired("A non-system actor must carry an actor_id.")

    active = context.get()
    if active is not None and active != tenant.pk:
        raise TenantMismatch(
            f"Cannot append an audit record for tenant {tenant.pk} while the active "
            f"tenant context is {active}."
        )

    with transaction.atomic(), db_tenant_context(tenant.pk):
        # The RLS scope is established for the append and handed back afterwards
        # (ADR-0005). A sweep that appends for many tenants inside one
        # transaction would otherwise leave the last one's scope in place.
        _lock_tenant(tenant)
        head = (
            AuditRecord.objects.all_tenants()
            .filter(tenant_id=tenant.pk)
            .order_by("-sequence")
            .values("sequence", "hash")
            .first()
        )
        sequence = (head["sequence"] + 1) if head else 1
        prev_hash = head["hash"] if head else GENESIS

        record = AuditRecord(
            tenant=tenant,
            sequence=sequence,
            event_type=event_type,
            occurred_at=occurred_at or timezone.now(),
            actor_type=actor_type,
            actor_id=actor_id,
            actor_ip=actor_ip,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload or {},
            corrects=corrects,
            prev_hash=prev_hash,
        )
        record.hash = compute_hash(record_content(record), prev_hash)
        record.save(force_insert=True)
        return record


def correct(
    *, original: AuditRecord, reason: str, actor_type: str, actor_id: str = ""
) -> AuditRecord:
    """Append a compensating record. The original stays, forever (§8.3).

    This is the only correction mechanism in the product. It reads as more work
    than an edit because it is more work than an edit — and because the record of
    having been wrong is itself evidence a supervisor is entitled to see.
    """
    return append(
        tenant=original.tenant,
        event_type=f"{original.event_type}.corrected",
        subject_type=original.subject_type,
        subject_id=original.subject_id,
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"reason": reason, "corrects_sequence": original.sequence},
        corrects=original.pk,
    )


def _lock_tenant(tenant: Tenant) -> None:
    """Serialise this tenant's chain appends for the rest of the transaction.

    Transaction-scoped, so it releases on commit or rollback and there is no path
    to leaking a held lock. Per tenant, so tenants never contend with each other.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [conf.get("ADVISORY_LOCK_NAMESPACE"), tenant.lock_key],
        )
