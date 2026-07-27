#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${OPENCITADEL_APP_USER:?OPENCITADEL_APP_USER must be set}"
: "${OPENCITADEL_APP_PASSWORD:?OPENCITADEL_APP_PASSWORD must be set}"

if [ "$POSTGRES_USER" = "$OPENCITADEL_APP_USER" ]; then
  echo "Application database role must differ from the PostgreSQL admin role" >&2
  exit 1
fi

if [ "$POSTGRES_PASSWORD" = "$OPENCITADEL_APP_PASSWORD" ]; then
  echo "Application and PostgreSQL admin passwords must be distinct" >&2
  exit 1
fi

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=admin_user="$POSTGRES_USER" \
  --set=app_user="$OPENCITADEL_APP_USER" \
  --set=app_password="$OPENCITADEL_APP_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'app_user',
  :'app_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = :'app_user'
)
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
  :'app_user',
  :'app_password'
)
\gexec

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

SELECT format(
  'ALTER DATABASE %I OWNER TO %I',
  current_database(),
  :'app_user'
)
\gexec

SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user')
\gexec
SELECT format('GRANT ALL ON SCHEMA public TO %I', :'app_user')
\gexec

-- Existing volumes may contain objects created by the former admin-backed
-- migration role. Transfer only application relations in the public schema;
-- extension functions/types and system catalogs remain admin-owned.
SELECT format(
  'ALTER %s %I.%I OWNER TO %I',
  CASE c.relkind
    WHEN 'S' THEN 'SEQUENCE'
    WHEN 'v' THEN 'VIEW'
    WHEN 'm' THEN 'MATERIALIZED VIEW'
    WHEN 'f' THEN 'FOREIGN TABLE'
    ELSE 'TABLE'
  END,
  n.nspname,
  c.relname,
  :'app_user'
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND pg_get_userbyid(c.relowner) = :'admin_user'
ORDER BY c.relkind, c.relname
\gexec
SQL
