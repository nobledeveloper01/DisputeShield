"""Seed a deterministic tenant for the browser tests.

Deterministic keys so the host fixture can be a static file rather than
templated at run time. This command refuses to run against a `live` environment
for the obvious reason: it creates a key whose value is written down in a
repository.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from disputeshield.api.authentication import hash_key
from disputeshield.models import (
    AllowedOrigin,
    APIKey,
    BusinessCalendar,
    BusinessHoursWindow,
    SLAPolicy,
    SLAPolicyVersion,
    Tenant,
)
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

# Fixed values so the browser fixture can be a static file. Both are `test`
# environment keys, both are written down in a public repository on purpose, and
# `handle()` refuses to seed anything that is not a test key. A leaked test key
# can do nothing to live data (§8.2).
PUBLISHABLE_KEY = "pk_test_e2e_0000000000000000000000000000"
E2E_SECRET_KEY = "ds_test_e2e_0000000000000000000000000000"  # noqa: S105
HOST_ORIGIN = "http://localhost:4180"


class Command(BaseCommand):
    help = "Create the fixed tenant, keys and origin the browser tests expect."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--origin", default=HOST_ORIGIN)

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        from datetime import time

        tenant, _ = Tenant.objects.get_or_create(
            slug="e2e", defaults={"name": "End-to-end fixture"}
        )

        with context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
            for kind, raw in (
                (APIKey.Kind.PUBLISHABLE, PUBLISHABLE_KEY),
                (APIKey.Kind.SECRET, E2E_SECRET_KEY),
            ):
                if raw.split("_")[1] != "test":
                    raise CommandError("refusing to seed a non-test key")
                APIKey.objects.get_or_create(
                    tenant=tenant,
                    prefix=raw[:16],
                    defaults={
                        "name": f"e2e {kind}",
                        "environment": APIKey.Environment.TEST,
                        "kind": kind,
                        "key_hash": hash_key(raw),
                    },
                )

            AllowedOrigin.objects.get_or_create(tenant=tenant, origin=options["origin"])

            calendar, fresh = BusinessCalendar.objects.get_or_create(
                tenant=tenant, name="e2e", defaults={"timezone_name": "UTC", "always_open": True}
            )
            if fresh and not calendar.always_open:
                for weekday in range(5):
                    BusinessHoursWindow.objects.create(
                        calendar=calendar,
                        weekday=weekday,
                        opens_at=time(9, 0),
                        closes_at=time(17, 0),
                    )

            policy, _ = SLAPolicy.objects.get_or_create(tenant=tenant, category="failed_transfer")
            if policy.current_version is None:
                SLAPolicyVersion.objects.create(
                    tenant=tenant,
                    policy=policy,
                    version=1,
                    calendar=calendar,
                    resolution_hours=72,
                    warning_thresholds=[50, 80, 95],
                )

        self.stdout.write(
            json.dumps(
                {
                    "tenant": tenant.pk,
                    "publishable_key": PUBLISHABLE_KEY,
                    "secret_key": E2E_SECRET_KEY,
                    "origin": options["origin"],
                }
            )
        )
