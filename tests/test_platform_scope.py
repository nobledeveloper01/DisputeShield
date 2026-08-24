"""Background work must function with no tenant context to inherit.

The bug this file guards against was live and invisible: the SLA sweep, the
notification dispatcher and the deadline reconciler all queried across tenants
directly. Row level security is FORCEd, so a query with no tenant context returns
**zero rows** — and every one of them passed its tests, because the tests
inherited a context from a fixture that held one open around the `yield`.

In production Celery calls them with nothing to inherit. The sweep would have
fired nothing, for every tenant, forever. The heartbeat would have stayed fresh
and §11.5's runbook would never have triggered, because the scheduler was
perfectly healthy — it was the queries that were empty.

So every test here asserts the context is `None` before it starts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from disputeshield.models import NotificationOutbox, SLADeadline
from disputeshield.sla import clock as clock_service
from disputeshield.sla import sweeper
from disputeshield.tenancy import context

pytestmark = pytest.mark.django_db

LAGOS = ZoneInfo("Africa/Lagos")
UTC = UTC


def wednesday(hour: int = 9) -> datetime:
    return datetime(2026, 8, 19, hour, 0, tzinfo=LAGOS).astimezone(UTC)


@pytest.fixture
def clock_without_ambient_context(tenant_a, make_policy, as_tenant):
    """Builds a clock, then **leaves** the tenant context, like Celery would."""
    version = make_policy(tenant_a, resolution_hours=8)
    with as_tenant(tenant_a):
        clock = clock_service.start(
            tenant=tenant_a,
            subject_id="dsp_1",
            policy_version=version,
            started_at=wednesday(9),
        )
    assert context.get() is None, "the fixture must not leave a context open"
    return clock


class TestTheSweep:
    def test_it_fires_with_no_ambient_tenant_context(self, clock_without_ambient_context):
        assert context.get() is None
        result = sweeper.sweep(now=wednesday(17) + timedelta(hours=1))
        assert result.fired > 0, (
            "the sweep found nothing with no tenant context — row level security "
            "returned zero rows and the compliance clock silently stopped"
        )

    def test_it_fires_for_every_tenant_not_just_one(
        self, tenant_a, tenant_b, make_policy, as_tenant
    ):
        for tenant in (tenant_a, tenant_b):
            version = make_policy(tenant, resolution_hours=8)
            with as_tenant(tenant):
                clock_service.start(
                    tenant=tenant,
                    subject_id="dsp_1",
                    policy_version=version,
                    started_at=wednesday(9),
                )

        assert context.get() is None
        result = sweeper.sweep(now=wednesday(17) + timedelta(hours=1))

        with as_tenant(tenant_a):
            a_fired = SLADeadline.objects.filter(fired_at__isnull=False).count()
        with as_tenant(tenant_b):
            b_fired = SLADeadline.objects.filter(fired_at__isnull=False).count()

        assert a_fired > 0 and b_fired > 0
        assert result.fired == a_fired + b_fired

    def test_an_inactive_tenants_clocks_are_left_alone(
        self, tenant_a, make_policy, as_tenant, clock_without_ambient_context
    ):
        """A deactivated tenant is not one whose deadlines should keep firing
        notifications at people who no longer have an account."""
        tenant_a.is_active = False
        tenant_a.save(update_fields=["is_active"])

        assert sweeper.sweep(now=wednesday(17) + timedelta(hours=1)).fired == 0


class TestTheDispatcher:
    def test_it_drains_with_no_ambient_tenant_context(self, tenant_a, as_tenant):
        from disputeshield.notifications.dispatcher import ConsoleChannel, dispatch

        ConsoleChannel.sent.clear()
        with as_tenant(tenant_a):
            NotificationOutbox.objects.create(
                tenant=tenant_a, idempotency_key="k1", event_type="sla.resolution", payload={}
            )

        assert context.get() is None
        assert dispatch().sent == 1
        ConsoleChannel.sent.clear()

    def test_it_drains_every_tenant(self, tenant_a, tenant_b, as_tenant):
        from disputeshield.notifications.dispatcher import ConsoleChannel, dispatch

        ConsoleChannel.sent.clear()
        for tenant in (tenant_a, tenant_b):
            with as_tenant(tenant):
                NotificationOutbox.objects.create(
                    tenant=tenant, idempotency_key="k1", event_type="sla.resolution", payload={}
                )

        assert dispatch().sent == 2
        ConsoleChannel.sent.clear()


class TestTheReconciler:
    def test_it_checks_with_no_ambient_tenant_context(self, clock_without_ambient_context):
        from disputeshield.sla.reconcile import reconcile

        assert context.get() is None
        result = reconcile()
        assert result.checked > 0, (
            "the reconciler reported a clean bill of health for a database it never actually read"
        )
        assert result.ok


class TestThePattern:
    def test_platform_modules_do_not_query_across_tenants_directly(self):
        """Greps the background-work modules for the mistake itself.

        `all_tenants()` inside a `for_each_tenant` block is correct and common.
        What is never correct is calling it at module top level in a task entry
        point, where there is no context — so this checks that each of these
        modules routes through `for_each_tenant`.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "disputeshield"
        entry_points = [
            root / "sla" / "sweeper.py",
            root / "sla" / "reconcile.py",
            root / "notifications" / "dispatcher.py",
        ]

        missing = [
            path.name
            for path in entry_points
            if "all_tenants(" in path.read_text() and "for_each_tenant" not in path.read_text()
        ]
        assert not missing, (
            f"{missing} query across tenants without iterating them — row level "
            "security will return nothing when Celery calls them"
        )


class TestTheTransactionRequirement:
    """`SET LOCAL` outside a transaction is discarded, silently.

    Postgres warns and continues. The variable is never set, row level security
    matches nothing, every query returns zero rows, and nothing raises. That shape
    of bug appeared three times in this codebase — the SLA sweep, the attachment
    download, and the packaged-install smoke test — before it was made loud.
    """

    @pytest.mark.django_db(transaction=True)
    def test_setting_the_context_outside_a_transaction_raises(self, tenant_a):
        from disputeshield.tenancy.middleware import NoTransaction, set_tenant_context

        with pytest.raises(NoTransaction, match="discarded"):
            set_tenant_context(tenant_a.pk)

    @pytest.mark.django_db(transaction=True)
    def test_for_each_tenant_opens_its_own_transaction(self, tenant_a, tenant_b):
        """A Celery task runs in autocommit, so the helper cannot assume one."""
        from disputeshield.tenancy.platform import for_each_tenant

        seen = list(for_each_tenant(lambda tenant_id: tenant_id))
        assert set(seen) == {tenant_a.pk, tenant_b.pk}

    @pytest.mark.django_db(transaction=True)
    def test_one_tenants_failure_does_not_roll_back_the_others(
        self, tenant_a, tenant_b, make_policy, as_tenant
    ):
        """One transaction per tenant, not one for the loop: a failure sweeping
        the eleventh tenant must not undo the ten before it."""
        from django.db import transaction

        from disputeshield.models import NotificationOutbox
        from disputeshield.tenancy.platform import for_each_tenant

        # `Tenant` orders by name, so which tenant comes first is a property of
        # the fixture data rather than of the ids. The test records what it saw
        # instead of predicting it.
        succeeded: list[str] = []

        def work(tenant_id):
            NotificationOutbox.objects.create(
                tenant_id=tenant_id, idempotency_key="k", event_type="e", payload={}
            )
            if succeeded:
                raise RuntimeError("this tenant blew up")
            succeeded.append(tenant_id)
            return tenant_id

        with pytest.raises(RuntimeError):
            list(for_each_tenant(work))

        assert len(succeeded) == 1
        kept = tenant_a if tenant_a.pk == succeeded[0] else tenant_b
        rolled_back = tenant_b if kept is tenant_a else tenant_a

        with transaction.atomic(), as_tenant(kept):
            assert NotificationOutbox.objects.count() == 1, "a committed tenant lost its work"
        with transaction.atomic(), as_tenant(rolled_back):
            assert NotificationOutbox.objects.count() == 0, "the failed tenant kept a partial write"
