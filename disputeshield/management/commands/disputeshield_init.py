"""Seed the configuration a new installation needs before it can accept a dispute.

Idempotent: safe to re-run, and re-running never overwrites a value an operator
has since changed. §6.2 lists this as an install step, so it behaves like one —
no prompts, no partial state on failure, and an exit code that means something.
"""

from __future__ import annotations

import json
from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from disputeshield.models import (
    BusinessCalendar,
    BusinessHoursWindow,
    SLAPolicy,
    SLAPolicyVersion,
    Tenant,
)
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

DEFAULT_CATEGORIES = (
    ("failed_transfer", 72, "CBN Consumer Protection Framework s.4.2"),
    ("card_chargeback", 120, "Card scheme dispute window"),
    ("unauthorised_debit", 48, "CBN Consumer Protection Framework s.4.2"),
    ("failed_airtime", 24, ""),
    ("other", 72, ""),
)


class Command(BaseCommand):
    help = "Seed default categories, a business calendar and SLA policies."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant-name", default="Default", help="Name for the first tenant.")
        parser.add_argument("--tenant-slug", default="default")
        parser.add_argument(
            "--timezone", default="UTC", help="IANA name for the business calendar."
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        tenant, created = Tenant.objects.get_or_create(
            slug=options["tenant_slug"], defaults={"name": options["tenant_name"]}
        )

        with context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
            calendar, fresh = BusinessCalendar.objects.get_or_create(
                tenant=tenant,
                name="Business hours",
                defaults={"timezone_name": options["timezone"]},
            )
            if fresh:
                for weekday in range(5):
                    BusinessHoursWindow.objects.create(
                        calendar=calendar,
                        weekday=weekday,
                        opens_at=time(9, 0),
                        closes_at=time(17, 0),
                    )

            seeded = []
            for category, hours, reference in DEFAULT_CATEGORIES:
                policy, _ = SLAPolicy.objects.get_or_create(tenant=tenant, category=category)
                if policy.current_version is None:
                    SLAPolicyVersion.objects.create(
                        tenant=tenant,
                        policy=policy,
                        version=1,
                        calendar=calendar,
                        resolution_hours=hours,
                        warning_thresholds=[50, 80, 95],
                        regulatory_reference=reference,
                    )
                    seeded.append(category)

        self.stdout.write(
            json.dumps(
                {
                    "tenant": tenant.pk,
                    "tenant_created": created,
                    "categories_seeded": seeded,
                    "next": "disputeshield_doctor --strict",
                }
            )
        )
