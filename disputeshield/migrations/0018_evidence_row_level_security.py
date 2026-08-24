"""RLS for the phase 8 tables, plus an append-only guard where one carries evidence.

External correspondence is not merely tenant data: a letter to or from a
regulator is something a supervisor may later ask us to produce unchanged. It
gets the same trigger treatment as the audit table.
"""

from django.db import migrations

PROTECTED_TABLES = [
    "disputeshield_legalhold",
    "disputeshield_erasurerequest",
    "disputeshield_checkpointanchor",
    "disputeshield_externalescalation",
    "disputeshield_externalcorrespondence",
    "disputeshield_returntemplate",
    "disputeshield_regulatoryreturn",
]

APPEND_ONLY = ["disputeshield_externalcorrespondence"]


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
    dependencies = [("disputeshield", "0017_erasurerequest_externalescalation_and_more")]
    operations = [migrations.RunSQL(sql=_forward(), reverse_sql=_reverse())]
