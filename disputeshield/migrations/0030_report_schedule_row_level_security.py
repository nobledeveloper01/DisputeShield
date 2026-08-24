"""RLS for the report schedule table.

A schedule decides that a period leaves this system every month, unattended. A
row writable across a tenant boundary would let one tenant arrange for another
tenant's disputes to be mailed out on a recurring basis, which is the same
exposure as the allowlist and gets the same treatment.
"""

from django.db import migrations

TABLE = "disputeshield_reportschedule"

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
    dependencies = [("disputeshield", "0029_report_schedules")]
    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
