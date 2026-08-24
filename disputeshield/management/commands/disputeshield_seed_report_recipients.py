"""Seed the report allowlist with recipients that cannot receive mail.

Every address here is under a domain RFC 2606 and RFC 6761 reserve for exactly
this: `example.test` and `.invalid` are guaranteed never to resolve. That is the
point rather than a formality — this command exists to be run against a
developer's machine and against CI, where a mistake in the delivery code would
otherwise put a whole period's dispute data into somebody's real inbox. A
reserved domain makes that impossible at the DNS layer, not merely unlikely at
the review layer.

The command refuses to touch a `live` tenant for the same reason
`disputeshield_seed_e2e` does: a fixed allowlist written down in a public
repository has no business on a tenant holding real complaints.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from disputeshield import audit
from disputeshield.models import ReportRecipient, Tenant
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

SEEDED_BY = "agt_seed_command"

# Labelled the way a real allowlist would be, so the fixture exercises the review
# question the label exists to answer: does this address belong here?
RECIPIENTS = [
    {
        "address": "compliance@example.test",
        "label": "Internal compliance archive (sample)",
        "reason": "Sample recipient for local development. Not a real mailbox.",
    },
    {
        "address": "supervision@example.test",
        "label": "Supervisory returns inbox (sample)",
        "reason": "Sample recipient standing in for a regulator's inbox.",
    },
    {
        "address": "audit-archive@example.invalid",
        "label": "External auditor archive (sample)",
        "reason": "Sample third-party recipient, to exercise a non-internal destination.",
    },
]


class Command(BaseCommand):
    help = "Register sample report recipients on a non-live tenant."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="Tenant id or slug.")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        reference = options["tenant"]
        tenant = (
            Tenant.objects.filter(pk=reference).first()
            or Tenant.objects.filter(slug=reference).first()
        )
        if tenant is None:
            raise CommandError(f"No tenant matching {reference!r}.")

        if tenant.environment == Tenant.Environment.LIVE:
            raise CommandError(
                "Refusing to seed sample recipients on a live tenant. These addresses are "
                "written down in a public repository, and the allowlist is what decides "
                "where a whole period's disclosure may be sent."
            )

        created = 0
        # Both contexts, as a request establishes them: the manager refuses
        # without the application one, and RLS returns nothing without the
        # database one.
        with context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
            for entry in RECIPIENTS:
                recipient, was_created = ReportRecipient.objects.get_or_create(
                    tenant=tenant,
                    address=entry["address"],
                    defaults={
                        "label": entry["label"],
                        "added_by": SEEDED_BY,
                        "reason": entry["reason"],
                    },
                )
                if not was_created:
                    continue
                created += 1
                # Seeded recipients are audited exactly like registered ones. An
                # allowlist entry that appeared without a record is the one a
                # reviewer cannot account for.
                audit.append(
                    tenant=tenant,
                    event_type="report.recipient_registered",
                    subject_type="report_recipient",
                    subject_id=recipient.pk,
                    actor_type="system",
                    actor_id=SEEDED_BY,
                    payload={
                        "address": entry["address"],
                        "label": entry["label"],
                        "reason": entry["reason"],
                        "seeded": True,
                    },
                )

        self.stdout.write(
            f"{created} sample recipient(s) registered on {tenant.slug}; "
            f"{len(RECIPIENTS) - created} already present."
        )
