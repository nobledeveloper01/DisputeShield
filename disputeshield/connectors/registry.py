"""Resolving a tenant's connector, decrypting its credential, and auditing the call."""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from typing import ClassVar

from django.utils import timezone as django_timezone

from disputeshield import audit
from disputeshield.connectors.base import (
    Connector,
    ConnectorUnavailable,
    ProviderEvent,
    ProviderTransaction,
)
from disputeshield.models import ProviderCall, ProviderConnector


class StubConnector(Connector):
    """Development and CI. Answers from a fixture, reaches no network.

    A real provider client belongs behind the same interface; this exists so the
    degradation path and the audit trail are exercised without a network, and so
    that CI does not depend on a third party's uptime to test that we survive
    their downtime.
    """

    provider = "generic"
    fixtures: ClassVar[dict[str, ProviderTransaction]] = {}
    unavailable = False

    def fetch_transaction(self, reference: str) -> ProviderTransaction:
        if self.unavailable:
            raise ConnectorUnavailable("provider did not answer")
        found = self.fixtures.get(reference)
        if found is None:
            raise ConnectorUnavailable(f"no such transaction: {reference}")
        return found

    def fetch_timeline(self, reference: str) -> list[ProviderEvent]:
        if self.unavailable:
            raise ConnectorUnavailable("provider did not answer")
        return [
            ProviderEvent(
                occurred_at=datetime(2026, 8, 19, 9, 20, tzinfo=UTC),
                kind="reversal_queued",
                summary="Reversal queued on the rail",
                detail={"reference": reference},
            )
        ]

    def health(self) -> bool:
        return not self.unavailable


@dataclasses.dataclass(frozen=True)
class ContextResult:
    available: bool
    transaction: ProviderTransaction | None = None
    timeline: tuple[ProviderEvent, ...] = ()
    reason: str = ""


def build(connector_row: ProviderConnector) -> Connector:
    from disputeshield.connectors.crypto import decrypt_credential

    return StubConnector(
        base_url=connector_row.base_url,
        credential=decrypt_credential(connector_row),
    )


def fetch_context(*, dispute, reference: str = "") -> ContextResult:
    """Enrich a case with the provider's own view, or say plainly that we could not.

    Never raises to the caller. A connector failure degrades the case to "context
    unavailable"; it does not block filing, and it does not stop an agent working
    the queue (§8.6 principle 1).
    """
    reference = reference or dispute.transaction_ref
    if not reference:
        return ContextResult(available=False, reason="the case carries no transaction reference")

    connector_row = ProviderConnector.objects.filter(is_active=True).first()
    if connector_row is None:
        return ContextResult(available=False, reason="no provider connector is configured")

    client = build(connector_row)
    started = time.perf_counter()
    try:
        transaction = client.fetch_transaction(reference)
        timeline = tuple(client.fetch_timeline(reference))
    except Exception as exc:
        _record_call(
            connector_row,
            dispute,
            reference,
            started,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        return ContextResult(available=False, reason=str(exc))

    _record_call(connector_row, dispute, reference, started, ok=True, status_code=200)
    return ContextResult(available=True, transaction=transaction, timeline=timeline)


def _record_call(
    connector_row: ProviderConnector,
    dispute,
    reference: str,
    started: float,
    *,
    ok: bool,
    status_code: int | None = None,
    error: str = "",
) -> None:
    """The exact request made, and never the credential that made it."""
    occurred_at = django_timezone.now()
    ProviderCall.objects.create(
        tenant=connector_row.tenant,
        connector=connector_row,
        dispute=dispute,
        method="GET",
        path=f"/transactions/{reference}",
        request_summary={"reference": reference},
        status_code=status_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=ok,
        error=error[:255],
        occurred_at=occurred_at,
    )
    audit.append(
        tenant=connector_row.tenant,
        event_type="provider.called" if ok else "provider.call_failed",
        subject_type="dispute",
        subject_id=dispute.pk if dispute else "",
        actor_type="system",
        occurred_at=occurred_at,
        payload={
            "provider": connector_row.provider,
            "method": "GET",
            "path": f"/transactions/{reference}",
            "ok": ok,
            "error": error[:255],
        },
    )
