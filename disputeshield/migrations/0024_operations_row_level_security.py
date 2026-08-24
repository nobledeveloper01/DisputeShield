"""RLS for the phase 11 tables.

`QaReview` carries a supervisor's opinion of an agent's work and
`WebhookEndpoint` carries a signing secret. Both are tenant data of a kind that
should never be visible across a boundary even by accident.
"""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_policysimulation",
    "disputeshield_qareview",
    "disputeshield_webhookendpoint",
    "disputeshield_webhookdelivery",
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
        ("disputeshield", "0023_webhookendpoint_webhookdelivery_policysimulation_and_more")
    ]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
