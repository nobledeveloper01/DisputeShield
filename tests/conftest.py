from __future__ import annotations

import pytest
from django.db import connection

from disputeshield.identifiers import generate_api_key
from disputeshield.models import Agent, APIKey, Tenant
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

    def sql_flush(self, style, tables, *args, **kwargs):
        statements = original(self, style, tables, *args, **kwargs)
        if any("auditrecord" in table for table in tables):
            # Both guards have to be lifted, and that is worth noticing: the
            # first attempt fails on the revoked grant, not on the trigger.
            statements = [
                "GRANT TRUNCATE ON disputeshield_auditrecord TO CURRENT_USER",
                "ALTER TABLE disputeshield_auditrecord "
                "DISABLE TRIGGER disputeshield_auditrecord_no_truncate",
                *statements,
                "ALTER TABLE disputeshield_auditrecord "
                "ENABLE TRIGGER disputeshield_auditrecord_no_truncate",
                "REVOKE TRUNCATE ON disputeshield_auditrecord FROM CURRENT_USER",
            ]
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
