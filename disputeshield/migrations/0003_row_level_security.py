"""§8.1 layer 3: a query that forgets to scope returns nothing.

Two details carry the whole weight of this migration:

  * **FORCE ROW LEVEL SECURITY.** Plain ENABLE exempts the table owner, and in
    any deployment where the application role also owns the schema — which is
    every self-hosted docker-compose install — that exemption makes the layer
    inert while looking installed.

  * **`current_setting(..., true)`** returns NULL when unset, so `tenant_id = NULL`
    is NULL, is not true, and the policy denies. No context means no rows, which
    is the correct failure. Using the two-argument form is what stops an unset
    variable from raising instead of denying, because a raise in the wrong place
    turns a safe denial into a 500.

`Tenant` itself is deliberately not protected: a policy on it would be circular
(creating a tenant would require already being scoped to it) and it holds a name
and a status. Every table that holds case data hangs off it and is protected.
"""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_agent",
    "disputeshield_apikey",
    "disputeshield_auditrecord",
]


def _forward() -> str:
    statements = []
    for table in PROTECTED_TABLES:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        statements.append(
            f"""
CREATE POLICY disputeshield_tenant_isolation ON {table}
    USING (tenant_id = current_setting('disputeshield.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));
"""
        )
    return "\n".join(statements)


def _reverse() -> str:
    statements = []
    for table in PROTECTED_TABLES:
        statements.append(f"DROP POLICY IF EXISTS disputeshield_tenant_isolation ON {table};")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(statements)


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0002_audit_immutability")]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
