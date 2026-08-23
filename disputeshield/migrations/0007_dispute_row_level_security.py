"""§8.1 layer 3 for the case tables. ENABLE plus FORCE, as in phases 1 and 2."""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_dispute",
    "disputeshield_disputemessage",
    "disputeshield_idempotencyrecord",
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
        ("disputeshield", "0006_tenant_customer_ref_salt_dispute_disputemessage_and_more")
    ]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
