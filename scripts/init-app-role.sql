-- The application role is not the owner. It has INSERT and SELECT on the audit
-- table and nothing else there (§8.3). The migration that creates the audit
-- table grants accordingly; this file only establishes the role separation that
-- makes the grant meaningful.
--
-- A deployment where the app connects as the owner has an audit trail that is
-- immutable only by convention. disputeshield_doctor --strict refuses to start
-- in that configuration.

CREATE ROLE disputeshield WITH LOGIN PASSWORD 'disputeshield';
GRANT CONNECT ON DATABASE disputeshield TO disputeshield;
GRANT USAGE ON SCHEMA public TO disputeshield;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO disputeshield;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO disputeshield;

-- RLS must apply to the app role. The owner bypasses it, which is why the app
-- role exists at all.
ALTER ROLE disputeshield SET row_security = on;
