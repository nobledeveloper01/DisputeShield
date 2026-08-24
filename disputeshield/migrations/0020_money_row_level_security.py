"""RLS for the phase 9 tables, plus append-only on the outbound call record.

`ProviderCall` is the answer to "what did you ask our provider about me?". A
record of reaching outside the trust boundary that can be edited afterwards
answers nothing, so it gets the audit table's trigger.
"""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_reasoncode",
    "disputeshield_representment",
    "disputeshield_providerconnector",
    "disputeshield_providercall",
    "disputeshield_settlementconfirmation",
]

APPEND_ONLY = ["disputeshield_providercall"]


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
    for table in APPEND_ONLY:
        statements.append(
            f"""
CREATE TRIGGER {table}_immutable
    BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION disputeshield_audit_immutable();
"""
        )
    return "\n".join(statements)


def _reverse() -> str:
    statements = []
    for table in APPEND_ONLY:
        statements.append(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table};")
    for table in PROTECTED_TABLES:
        statements.append(f"DROP POLICY IF EXISTS disputeshield_tenant_isolation ON {table};")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(statements)


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0019_sladeadline_pausable_alter_sladeadline_kind_and_more")]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
