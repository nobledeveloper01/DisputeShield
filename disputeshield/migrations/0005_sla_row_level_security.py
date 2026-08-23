"""§8.1 layer 3 for the SLA engine's tables.

Every tenant-scoped table added in phase 2 gets the same treatment as phase 1's:
ENABLE plus **FORCE**, so the policy applies to the table owner too. Plain ENABLE
exempts the owner, and in every self-hosted compose install the application role
*is* the owner — the layer would look installed and do nothing.

`BusinessHoursWindow` and `Holiday` carry no tenant column of their own. They are
reachable only through a calendar that is protected, and adding a denormalised
tenant_id purely to satisfy a policy would create a second place for it to be
wrong. `tests/test_tenant_isolation.py` asserts they are unreachable across the
boundary through their parent.
"""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_businesscalendar",
    "disputeshield_slapolicy",
    "disputeshield_slapolicyversion",
    "disputeshield_slaclock",
    "disputeshield_slaevent",
    "disputeshield_sladeadline",
    "disputeshield_notificationoutbox",
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
    dependencies = [
        ("disputeshield", "0004_sweepheartbeat_businesscalendar_businesshourswindow_and_more")
    ]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
