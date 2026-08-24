from __future__ import annotations

import pytest
from django.db import connection

from disputeshield.identifiers import generate_api_key
from disputeshield.models import (
    Agent,
    APIKey,
    BusinessCalendar,
    BusinessHoursWindow,
    Holiday,
    SLAPolicy,
    SLAPolicyVersion,
    Tenant,
)
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context


@pytest.fixture(autouse=True, scope="session")
def _let_the_test_harness_flush_the_audit_table(django_db_setup, django_db_blocker):
    """Django's transactional-test teardown TRUNCATEs every table in the database.

    The audit table refuses TRUNCATE — deliberately, because a DELETE-proof table
    that can still be truncated is not append-only. So the harness and the product
    are in genuine conflict here, and the resolution matters: the escape lives in
    the test harness, never in the product.

    This patch is session-scoped, applies only under pytest, and touches nothing
    that ships. `tests/test_immutability.py::test_truncate_is_blocked_too` still
    asserts the guard is live, and it passes with this patch installed — because
    the patch drops the trigger only for the exact statement Django's flush emits.
    """
    # The concrete backend class, not BaseDatabaseOperations: the PostgreSQL
    # backend overrides sql_flush, so patching the base does nothing at all —
    # silently, and the only symptom is the teardown error you were trying to fix.
    operations_class = type(connection.ops)
    original = operations_class.sql_flush

    # Every table carrying an append-only guard. Derived from a list rather than
    # hard-coded once, because phase 6 added a second such table and the patch
    # that only knew about the first failed the whole suite's teardown.
    guarded = ("disputeshield_auditrecord", "disputeshield_auditcheckpoint")

    def sql_flush(self, style, tables, *args, **kwargs):
        statements = original(self, style, tables, *args, **kwargs)
        present = [name for name in guarded if any(name == table for table in tables)]
        if present:
            # Both guards have to be lifted per table, and that is worth
            # noticing: the first attempt fails on the revoked grant, not on the
            # trigger.
            before = []
            after = []
            for name in present:
                before += [
                    f"GRANT TRUNCATE ON {name} TO CURRENT_USER",
                    f"ALTER TABLE {name} DISABLE TRIGGER {name}_no_truncate",
                ]
                after += [
                    f"ALTER TABLE {name} ENABLE TRIGGER {name}_no_truncate",
                    f"REVOKE TRUNCATE ON {name} FROM CURRENT_USER",
                ]
            statements = [*before, *statements, *after]
        return statements

    operations_class.sql_flush = sql_flush
    yield
    operations_class.sql_flush = original


@pytest.fixture
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(name="Acme Payments", slug="acme")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(name="Borealis Bank", slug="borealis")


@pytest.fixture
def as_tenant():
    """Establish both isolation contexts the way a real request does.

    Layer 2 (the Python contextvar the scoped managers read) and layer 3 (the
    Postgres session variable RLS reads) are set together, because a test that
    sets only one is testing half the mechanism while appearing to test both.
    """

    import contextlib

    @contextlib.contextmanager
    def _as(tenant: Tenant):
        with context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
            yield tenant

    return _as


@pytest.fixture
def raw_sql():
    """Execute SQL on the test connection, bypassing the ORM entirely.

    Every immutability assertion in this suite goes through here. Asserting that
    the ORM refuses an update tests Django; asserting that Postgres refuses one
    tests the guarantee the product is sold on.
    """

    def _execute(sql: str, params: list | None = None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params or [])
            if cursor.description:
                return cursor.fetchall()
            return None

    return _execute


@pytest.fixture
def make_agent(as_tenant):
    def _make(tenant: Tenant, email: str = "ngozi@example.com", role: str = Agent.Role.AGENT):
        with as_tenant(tenant):
            return Agent.objects.create(
                tenant=tenant, email=email, display_name="Ngozi O.", role=role
            )

    return _make


@pytest.fixture
def make_api_key(as_tenant):
    def _make(tenant: Tenant, environment: str = "live"):
        full, prefix = generate_api_key(environment)
        with as_tenant(tenant):
            key = APIKey.objects.create(
                tenant=tenant,
                name="default",
                environment=environment,
                prefix=prefix,
                key_hash=f"argon2-placeholder-{prefix}",
            )
        return full, key

    return _make


@pytest.fixture
def tamper(raw_sql):
    """Simulate an attacker who has already defeated every application-level control.

    Getting here takes three separate escalations, and that is worth stating
    plainly because it is the argument for the design:

      1. **Re-grant UPDATE and DELETE.** The application role does not have them,
         so the first attempt fails with a permission error, not a trigger error.
      2. **Disable the trigger.** Which needs table ownership.
      3. **Scope the session to the victim tenant.** Row level security is FORCEd,
         so with no tenant context the UPDATE matches nothing and silently
         succeeds at changing zero rows.

    Only then is tampering possible at all — and the chain still catches it. That
    is what defence in depth buys: the last layer does not depend on any of the
    earlier ones having held.

    `SET CONSTRAINTS ALL IMMEDIATE` first, because Django creates deferrable
    foreign keys and Postgres refuses to ALTER a table with pending trigger
    events.
    """

    import contextlib

    @contextlib.contextmanager
    def _tamper(tenant):
        raw_sql("SET CONSTRAINTS ALL IMMEDIATE")
        raw_sql("GRANT UPDATE, DELETE ON disputeshield_auditrecord TO CURRENT_USER")
        raw_sql(
            "ALTER TABLE disputeshield_auditrecord "
            "DISABLE TRIGGER disputeshield_auditrecord_immutable"
        )
        try:
            with db_tenant_context(tenant.pk):
                yield
        finally:
            raw_sql(
                "ALTER TABLE disputeshield_auditrecord "
                "ENABLE TRIGGER disputeshield_auditrecord_immutable"
            )
            raw_sql("REVOKE UPDATE, DELETE ON disputeshield_auditrecord FROM CURRENT_USER")

    return _tamper


@pytest.fixture
def make_calendar(as_tenant):
    """A Monday-to-Friday 09:00-17:00 calendar in the tenant's timezone."""
    from datetime import time

    def _make(tenant, *, timezone_name="Africa/Lagos", always_open=False, holidays=()):
        with as_tenant(tenant):
            # get_or_create so a test can file several cases without colliding on
            # the calendar's unique name — a tenant has one calendar, not one per case.
            calendar, fresh = BusinessCalendar.objects.get_or_create(
                tenant=tenant,
                name=f"{timezone_name} standard",
                defaults={"timezone_name": timezone_name, "always_open": always_open},
            )
            if not fresh:
                return calendar
            if not always_open:
                for weekday in range(5):
                    BusinessHoursWindow.objects.create(
                        calendar=calendar,
                        weekday=weekday,
                        opens_at=time(9, 0),
                        closes_at=time(17, 0),
                    )
            for observed_on in holidays:
                Holiday.objects.create(
                    calendar=calendar, observed_on=observed_on, name="Public holiday"
                )
            return calendar

    return _make


@pytest.fixture
def make_policy(as_tenant, make_calendar):
    def _make(
        tenant,
        *,
        category="failed_transfer",
        calendar=None,
        acknowledgement_minutes=60,
        resolution_hours=8,
        business_hours_only=True,
        warning_thresholds=(50, 80, 95),
    ):
        calendar = calendar or make_calendar(tenant)
        with as_tenant(tenant):
            policy, _ = SLAPolicy.objects.get_or_create(tenant=tenant, category=category)
            existing = policy.versions.order_by("-version").first()
            if existing is not None:
                return existing
            return SLAPolicyVersion.objects.create(
                tenant=tenant,
                policy=policy,
                version=1,
                calendar=calendar,
                acknowledgement_minutes=acknowledgement_minutes,
                resolution_hours=resolution_hours,
                business_hours_only=business_hours_only,
                warning_thresholds=list(warning_thresholds),
                regulatory_reference="CBN Consumer Protection Framework s.4.2",
            )

    return _make


@pytest.fixture
def make_dispute(as_tenant, make_policy):
    """File a case the way the API does — through the service, never the ORM."""

    def _make(tenant, *, policy_version=None, customer_ref="usr_9931", **kwargs):
        from disputeshield.disputes import service

        policy_version = policy_version or make_policy(tenant)
        with as_tenant(tenant):
            return service.file_dispute(
                tenant=tenant,
                customer_ref=customer_ref,
                category=kwargs.pop("category", "failed_transfer"),
                description=kwargs.pop("description", "Transfer failed but I was debited"),
                policy_version=policy_version,
                actor_type="api_key",
                actor_id=kwargs.pop("actor_id", "key_test"),
                **kwargs,
            )

    return _make


@pytest.fixture
def api_key_for(as_tenant):
    """A usable API key, hashed the way the product hashes them."""

    def _make(tenant, environment="live"):
        from disputeshield.api.authentication import hash_key
        from disputeshield.identifiers import generate_api_key

        full, prefix = generate_api_key(environment)
        with as_tenant(tenant):
            key = APIKey.objects.create(
                tenant=tenant,
                name="test key",
                environment=environment,
                prefix=prefix,
                key_hash=hash_key(full),
            )
        return full, key

    return _make


@pytest.fixture
def client_for(api_key_for):
    """An APIClient authenticated as a tenant, optionally acting for an agent."""

    def _make(tenant, agent=None):
        from rest_framework.test import APIClient

        full, _ = api_key_for(tenant)
        client = APIClient()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {full}"}
        if agent is not None:
            headers["HTTP_X_DISPUTESHIELD_ACTING_AGENT"] = agent.pk
        client.credentials(**headers)
        return client

    return _make


@pytest.fixture
def publishable_key_for(as_tenant):
    def _make(tenant, environment="live"):
        from disputeshield.api.authentication import hash_key
        from disputeshield.identifiers import generate_api_key

        full, prefix = generate_api_key(environment, kind="publishable")
        with as_tenant(tenant):
            key = APIKey.objects.create(
                tenant=tenant,
                name="widget key",
                environment=environment,
                kind=APIKey.Kind.PUBLISHABLE,
                prefix=prefix,
                key_hash=hash_key(full),
            )
        return full, key

    return _make


@pytest.fixture
def allowed_origin(as_tenant):
    def _make(tenant, origin="https://app.acme.io"):
        from disputeshield.models import AllowedOrigin

        with as_tenant(tenant):
            return AllowedOrigin.objects.create(tenant=tenant, origin=origin)

    return _make


@pytest.fixture
def session_for(api_key_for, as_tenant, make_policy):
    """Mint a real session the way a fintech's backend does."""

    def _make(tenant, customer_ref="usr_9931", transactions=None):
        from disputeshield.api import sessions

        make_policy(tenant)  # a category must exist before a case can be filed
        _, key = api_key_for(tenant)
        with as_tenant(tenant):
            token, session = sessions.mint(
                tenant=tenant,
                customer_ref=customer_ref,
                api_key_id=key.pk,
                display_name="A. Okafor",
                transactions=transactions
                if transactions is not None
                else [
                    {
                        "reference": "TXN-2026-08-11-8842",
                        "amount_minor": 5_000_000,
                        "currency": "NGN",
                        "description": "Transfer to GTBank ****4421",
                        "status": "failed",
                    }
                ],
            )
        return token, session

    return _make


@pytest.fixture
def widget_client(session_for):
    def _make(tenant, customer_ref="usr_9931", transactions=None):
        from rest_framework.test import APIClient

        token, session = session_for(tenant, customer_ref, transactions)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client, session

    return _make


@pytest.fixture(autouse=True)
def _clean_session_store():
    """Sessions live in Redis, which no test transaction rolls back."""
    yield
    try:
        from disputeshield.api import sessions

        client = sessions._client()
        keys = client.keys(f"{sessions.NAMESPACE}:*")
        if keys:
            client.delete(*keys)
    except Exception as exc:
        # Best effort. A Redis that is down fails the tests that need it, loudly,
        # and should not also fail the ones that do not.
        print(f"session store cleanup skipped: {exc}")
