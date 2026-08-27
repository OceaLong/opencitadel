#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${OPENCITADEL_MIGRATION_USER:?OPENCITADEL_MIGRATION_USER must be set}"
: "${OPENCITADEL_MIGRATION_PASSWORD:?OPENCITADEL_MIGRATION_PASSWORD must be set}"
: "${OPENCITADEL_APP_USER:?OPENCITADEL_APP_USER must be set}"
: "${OPENCITADEL_APP_PASSWORD:?OPENCITADEL_APP_PASSWORD must be set}"
: "${OPENCITADEL_KERNEL_USER:?OPENCITADEL_KERNEL_USER must be set}"
: "${OPENCITADEL_KERNEL_PASSWORD:?OPENCITADEL_KERNEL_PASSWORD must be set}"

for candidate in "$OPENCITADEL_MIGRATION_USER" "$OPENCITADEL_APP_USER" "$OPENCITADEL_KERNEL_USER"; do
  if [ "$candidate" = "$POSTGRES_USER" ]; then
    echo "Runtime and migration roles must differ from the PostgreSQL admin role" >&2
    exit 1
  fi
done

if [ "$OPENCITADEL_MIGRATION_USER" = "$OPENCITADEL_APP_USER" ] || \
   [ "$OPENCITADEL_MIGRATION_USER" = "$OPENCITADEL_KERNEL_USER" ] || \
   [ "$OPENCITADEL_APP_USER" = "$OPENCITADEL_KERNEL_USER" ]; then
  echo "Migration, API, and execution-kernel roles must be distinct" >&2
  exit 1
fi

if [ "$OPENCITADEL_MIGRATION_PASSWORD" = "$POSTGRES_PASSWORD" ] || \
   [ "$OPENCITADEL_APP_PASSWORD" = "$POSTGRES_PASSWORD" ] || \
   [ "$OPENCITADEL_KERNEL_PASSWORD" = "$POSTGRES_PASSWORD" ] || \
   [ "$OPENCITADEL_MIGRATION_PASSWORD" = "$OPENCITADEL_APP_PASSWORD" ] || \
   [ "$OPENCITADEL_MIGRATION_PASSWORD" = "$OPENCITADEL_KERNEL_PASSWORD" ] || \
   [ "$OPENCITADEL_APP_PASSWORD" = "$OPENCITADEL_KERNEL_PASSWORD" ]; then
  echo "Admin, migration, API, and execution-kernel passwords must be distinct" >&2
  exit 1
fi

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=migration_user="$OPENCITADEL_MIGRATION_USER" \
  --set=migration_password="$OPENCITADEL_MIGRATION_PASSWORD" \
  --set=app_user="$OPENCITADEL_APP_USER" \
  --set=app_password="$OPENCITADEL_APP_PASSWORD" \
  --set=kernel_user="$OPENCITADEL_KERNEL_USER" \
  --set=kernel_password="$OPENCITADEL_KERNEL_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SELECT 'CREATE ROLE opencitadel_execution_api NOLOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = 'opencitadel_execution_api'
)
\gexec
ALTER ROLE opencitadel_execution_api WITH NOLOGIN NOSUPERUSER NOBYPASSRLS;

SELECT 'CREATE ROLE opencitadel_execution_kernel NOLOGIN NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = 'opencitadel_execution_kernel'
)
\gexec
ALTER ROLE opencitadel_execution_kernel WITH NOLOGIN NOSUPERUSER NOBYPASSRLS;

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'migration_user', :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN NOINHERIT PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'migration_user', :'migration_password'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN INHERIT PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'app_user', :'app_password'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'kernel_user', :'kernel_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'kernel_user')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN INHERIT PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'kernel_user', :'kernel_password'
)
\gexec

SELECT format('REVOKE %I FROM %I', granted.rolname, recipient.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_roles AS recipient ON recipient.oid = membership.member
WHERE recipient.rolname IN (:'migration_user', :'app_user', :'kernel_user')
ORDER BY recipient.rolname, granted.rolname
\gexec

SELECT format('GRANT opencitadel_execution_api TO %I', :'app_user')
\gexec
SELECT format('GRANT opencitadel_execution_kernel TO %I', :'kernel_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name)
FROM (VALUES (:'migration_user'), (:'app_user'), (:'kernel_user')) AS roles(role_name)
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', role_name)
FROM (VALUES (:'migration_user'), (:'app_user'), (:'kernel_user')) AS roles(role_name)
\gexec
SELECT format(
  'GRANT USAGE ON SCHEMA public TO %I WITH GRANT OPTION',
  :'migration_user'
)
\gexec
SELECT format('GRANT CREATE ON SCHEMA public TO %I', :'migration_user')
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
