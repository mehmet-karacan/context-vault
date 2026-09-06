-- This file is evaluated by the official PostgreSQL image only for an empty
-- cluster.  It creates the login role before Alembic runs; the migration
-- one-shot applies and verifies object grants after the schema exists.
\getenv runtime_user POSTGRES_RUNTIME_USER
\getenv runtime_password POSTGRES_RUNTIME_PASSWORD
\getenv admin_user POSTGRES_USER
\getenv admin_password POSTGRES_PASSWORD
\getenv database_schema DATABASE_SCHEMA

SELECT (
    length(:'runtime_user') BETWEEN 1 AND 63
    AND :'runtime_user' ~ '^[a-z_][a-z0-9_]*$'
    AND :'runtime_user' <> :'admin_user'
    AND length(:'runtime_password') BETWEEN 32 AND 256
    AND :'runtime_password' <> :'admin_password'
    AND length(:'database_schema') BETWEEN 1 AND 63
) AS runtime_inputs_valid
\gset

\if :runtime_inputs_valid
\else
\echo 'runtime database bootstrap inputs are invalid'
-- ON_ERROR_STOP makes invalid identity configuration fail the empty-cluster
-- initialization without printing either credential.
SELECT 1 / 0;
\endif

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  :'runtime_user',
  :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user')
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  :'runtime_user',
  :'runtime_password'
)
\gexec

SELECT format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', :'database_schema')
\gexec
SELECT format('REVOKE CREATE ON SCHEMA %I FROM %I', :'database_schema', :'runtime_user')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'runtime_user')
\gexec
SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database())
\gexec
SELECT format(
  'REVOKE TEMPORARY ON DATABASE %I FROM %I',
  current_database(),
  :'runtime_user'
)
\gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'database_schema', :'runtime_user')
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  current_user,
  :'database_schema',
  :'runtime_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
  current_user,
  :'database_schema',
  :'runtime_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
  current_user,
  :'database_schema'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO %I',
  current_user,
  :'database_schema',
  :'runtime_user'
)
\gexec

\echo 'runtime database role bootstrap: PASS'
