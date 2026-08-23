"""The API key table is the authentication root, and cannot be read-scoped.

§8.1's layer 1 is "an API key resolves to exactly one tenant" — which means the
key lookup necessarily happens *before* a tenant context exists. Migration 0003
put a blanket tenant policy on this table anyway, which made every authentication
attempt return zero rows and answer 401. The model's own docstring already said
this ("scoping the lookup by tenant would be circular"); the migration contradicted
it, and the contradiction was invisible until an endpoint existed to exercise it.

The replacement splits the policy by command:

  * **SELECT is unscoped.** It has to be. What that exposes is a key prefix and an
    Argon2id hash — finding the row still requires the secret, and the secret is
    not in it. This is the same argument that keeps `Tenant` unprotected.
  * **INSERT, UPDATE and DELETE stay tenant-scoped.** One tenant cannot mint,
    rotate or revoke another tenant's keys, which is the property that actually
    matters here.

`Tenant` is the other table with this shape, and for the same reason.
"""

from django.db import migrations

FORWARD = """
DROP POLICY IF EXISTS disputeshield_tenant_isolation ON disputeshield_apikey;

CREATE POLICY disputeshield_apikey_lookup ON disputeshield_apikey
    FOR SELECT USING (true);

CREATE POLICY disputeshield_apikey_insert ON disputeshield_apikey
    FOR INSERT WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));

CREATE POLICY disputeshield_apikey_update ON disputeshield_apikey
    FOR UPDATE USING (tenant_id = current_setting('disputeshield.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));

CREATE POLICY disputeshield_apikey_delete ON disputeshield_apikey
    FOR DELETE USING (tenant_id = current_setting('disputeshield.tenant_id', true));
"""

REVERSE = """
DROP POLICY IF EXISTS disputeshield_apikey_delete ON disputeshield_apikey;
DROP POLICY IF EXISTS disputeshield_apikey_update ON disputeshield_apikey;
DROP POLICY IF EXISTS disputeshield_apikey_insert ON disputeshield_apikey;
DROP POLICY IF EXISTS disputeshield_apikey_lookup ON disputeshield_apikey;

CREATE POLICY disputeshield_tenant_isolation ON disputeshield_apikey
    USING (tenant_id = current_setting('disputeshield.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('disputeshield.tenant_id', true));
"""


class Migration(migrations.Migration):
    dependencies = [("disputeshield", "0007_dispute_row_level_security")]
    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
