"""§8.3: immutability enforced by the database, not promised by the application.

Three independent mechanisms, because each one alone has a hole the others cover:

  * The trigger raises regardless of the role attempting the write — including a
    superuser, and including a session where the grants below were never applied.
  * The revoked grants stop the application role before it reaches the trigger,
    so the ordinary failure is a permission error rather than an exception from
    deep inside a transaction.
  * TRUNCATE is a separate statement-level trigger, because row-level triggers do
    not fire for it. A DELETE-proof table that can still be truncated is not
    append-only, and this is the exact gap that makes "we blocked deletes" untrue.

`disputeshield_doctor --strict` verifies both the trigger and the grants at
install time. The reverse operations are real, not stubs, because
`tests/test_doctor_detects_missing_trigger.py` reverts this migration and asserts
that the doctor then fails — a preflight nobody has watched fail is a preflight
nobody should trust.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION disputeshield_audit_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'disputeshield: audit records are append-only; % is not permitted on %',
        TG_OP, TG_TABLE_NAME
        USING HINT = 'Corrections are appended with `corrects`, never applied (spec 8.3).';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER disputeshield_auditrecord_immutable
    BEFORE UPDATE OR DELETE ON disputeshield_auditrecord
    FOR EACH ROW EXECUTE FUNCTION disputeshield_audit_immutable();

CREATE TRIGGER disputeshield_auditrecord_no_truncate
    BEFORE TRUNCATE ON disputeshield_auditrecord
    FOR EACH STATEMENT EXECUTE FUNCTION disputeshield_audit_immutable();

REVOKE UPDATE, DELETE, TRUNCATE ON disputeshield_auditrecord FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disputeshield') THEN
        EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON disputeshield_auditrecord FROM disputeshield';
        EXECUTE 'GRANT SELECT, INSERT ON disputeshield_auditrecord TO disputeshield';
    END IF;
END $$;
"""

REVERSE = """
DROP TRIGGER IF EXISTS disputeshield_auditrecord_no_truncate ON disputeshield_auditrecord;
DROP TRIGGER IF EXISTS disputeshield_auditrecord_immutable ON disputeshield_auditrecord;
DROP FUNCTION IF EXISTS disputeshield_audit_immutable();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disputeshield') THEN
        EXECUTE 'GRANT UPDATE, DELETE ON disputeshield_auditrecord TO disputeshield';
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0001_initial")]
    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
