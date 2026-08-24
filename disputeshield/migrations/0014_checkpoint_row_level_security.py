"""RLS for checkpoints, plus the same append-only treatment as audit records.

A checkpoint is a signed statement about a chain. A checkpoint that can be
rewritten is a statement that can be made to agree with a chain after the chain
was altered — which would defeat the point of signing it.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE disputeshield_auditcheckpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE disputeshield_auditcheckpoint FORCE ROW LEVEL SECURITY;

CREATE POLICY disputeshield_tenant_isolation ON disputeshield_auditcheckpoint
    USING (tenant_id = current_setting('disputeshield.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));

CREATE TRIGGER disputeshield_auditcheckpoint_immutable
    BEFORE UPDATE OR DELETE ON disputeshield_auditcheckpoint
    FOR EACH ROW EXECUTE FUNCTION disputeshield_audit_immutable();

CREATE TRIGGER disputeshield_auditcheckpoint_no_truncate
    BEFORE TRUNCATE ON disputeshield_auditcheckpoint
    FOR EACH STATEMENT EXECUTE FUNCTION disputeshield_audit_immutable();

REVOKE UPDATE, DELETE, TRUNCATE ON disputeshield_auditcheckpoint FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disputeshield') THEN
        EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON disputeshield_auditcheckpoint
                 FROM disputeshield';
        EXECUTE 'GRANT SELECT, INSERT ON disputeshield_auditcheckpoint TO disputeshield';
    END IF;
END $$;
"""

REVERSE = """
DROP TRIGGER IF EXISTS disputeshield_auditcheckpoint_no_truncate ON disputeshield_auditcheckpoint;
DROP TRIGGER IF EXISTS disputeshield_auditcheckpoint_immutable ON disputeshield_auditcheckpoint;
DROP POLICY IF EXISTS disputeshield_tenant_isolation ON disputeshield_auditcheckpoint;
ALTER TABLE disputeshield_auditcheckpoint NO FORCE ROW LEVEL SECURITY;
ALTER TABLE disputeshield_auditcheckpoint DISABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disputeshield') THEN
        EXECUTE 'GRANT UPDATE, DELETE ON disputeshield_auditcheckpoint TO disputeshield';
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0013_auditcheckpoint")]
    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
