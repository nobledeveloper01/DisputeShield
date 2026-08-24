"""Draining the outbox (D7).

The sweep writes intent; this sends it. Split because they have different failure
modes and different retry semantics — and because §11.3's SLO is on the clock
advancing, not on an email provider's latency, so a slow provider must never be
able to make the compliance clock look stalled.
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
from typing import ClassVar

from django.db import transaction
from django.utils import timezone

from disputeshield.models import NotificationOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6


@dataclasses.dataclass(frozen=True)
class DispatchResult:
    sent: int
    failed: int
    exhausted: int


class Channel:
    name = "channel"

    def send(self, notification: NotificationOutbox) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class EmailChannel(Channel):
    name = "email"

    def send(self, notification: NotificationOutbox) -> None:
        from django.core.mail import send_mail

        payload = notification.payload
        subject = _subject_for(notification.event_type, payload)
        send_mail(
            subject=subject,
            # The body carries a case reference and a deadline, never case
            # content. An SLA warning that quotes the customer's description puts
            # that description into an inbox with its own retention.
            message=(
                f"Case {payload.get('subject_id')} is due at {payload.get('due_at')}.\n"
                f"Time remaining: {payload.get('remaining_seconds')}s."
            ),
            from_email=None,
            recipient_list=_recipients(notification),
            fail_silently=False,
        )


class SlackChannel(Channel):
    name = "slack"

    def send(self, notification: NotificationOutbox) -> None:
        raise NotImplementedError(
            "Slack delivery needs a per-tenant webhook, which lands with tenant "
            "settings in phase 6. Until then a tenant configuring Slack is told "
            "so rather than having notifications silently disappear."
        )


class ConsoleChannel(Channel):
    """Development and CI. Records the send instead of performing one."""

    name = "console"
    sent: ClassVar[list[dict]] = []

    def send(self, notification: NotificationOutbox) -> None:
        ConsoleChannel.sent.append(
            {
                "idempotency_key": notification.idempotency_key,
                "event_type": notification.event_type,
                "payload": notification.payload,
            }
        )


def get_channel(name: str) -> Channel:
    from disputeshield import conf

    configured = conf.get("NOTIFICATION_CHANNELS")
    path = configured.get(name)
    if not path:
        return ConsoleChannel()
    module_name, _, class_name = path.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)()


def dispatch(*, limit: int = 100) -> DispatchResult:
    """Drain the outbox for every tenant.

    Tenant by tenant, for the reason given in `disputeshield/tenancy/platform.py`:
    a cross-tenant query with no tenant context returns nothing, and a dispatcher
    that quietly sends nothing is a breach alert nobody receives.
    """
    from disputeshield.tenancy.platform import for_each_tenant

    sent = failed = exhausted = 0
    for tenant_sent, tenant_failed, tenant_exhausted in for_each_tenant(
        lambda _tenant_id: _dispatch_one_tenant(limit)
    ):
        sent += tenant_sent
        failed += tenant_failed
        exhausted += tenant_exhausted

    return DispatchResult(sent=sent, failed=failed, exhausted=exhausted)


def _dispatch_one_tenant(limit: int) -> tuple[int, int, int]:
    sent = failed = exhausted = 0

    pending = list(
        NotificationOutbox.objects.all_tenants()
        .filter(status=NotificationOutbox.Status.PENDING)
        .order_by("created_at")[:limit]
    )

    for notification in pending:
        with transaction.atomic():
            notification.attempts += 1
            try:
                get_channel(notification.channel).send(notification)
            except Exception as exc:
                notification.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if notification.attempts >= MAX_ATTEMPTS:
                    # Parked, not dropped (§8.6 principle 2). A notification that
                    # vanishes after six failures is a breach alert nobody
                    # received and nobody can prove was owed.
                    notification.status = NotificationOutbox.Status.FAILED
                    exhausted += 1
                    logger.error(
                        "notification exhausted its retries",
                        extra={
                            "key": notification.idempotency_key,
                            "error": notification.last_error,
                        },
                    )
                else:
                    failed += 1
                notification.save(update_fields=["attempts", "last_error", "status"])
                continue

            notification.status = NotificationOutbox.Status.SENT
            notification.sent_at = timezone.now()
            notification.save(update_fields=["attempts", "status", "sent_at"])
            sent += 1

    return sent, failed, exhausted


def _recipients(notification: NotificationOutbox) -> list[str]:
    return notification.payload.get("recipients") or []


def _subject_for(event_type: str, payload: dict) -> str:
    if event_type.endswith("resolution") or event_type.endswith("acknowledgement"):
        return f"SLA breached: {payload.get('subject_id')}"
    return f"SLA warning ({payload.get('threshold_percent')}%): {payload.get('subject_id')}"
