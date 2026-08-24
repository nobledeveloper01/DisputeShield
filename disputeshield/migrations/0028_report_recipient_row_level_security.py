"""RLS for the report-recipient allowlist.

The table decides where a whole period's disclosure is allowed to be sent. A row
readable — or worse, writable — across a tenant boundary would let one tenant add
an address to another tenant's allowlist, and the export would then leave for a
destination that tenant never approved, through a feature working exactly as
designed. Same FORCE as everywhere else: the application role owns the schema in
every self-hosted install.
"""

from django.db import migrations

TABLE = "disputeshield_reportrecipient"


FORWARD = f"""
ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;
CREATE POLICY disputeshield_tenant_isolation ON {TABLE}
    USING (tenant_id = current_setting('disputeshield.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));
"""

REVERSE = f"""
DROP POLICY IF EXISTS disputeshield_tenant_isolation ON {TABLE};
ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0027_report_recipients")]
    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
