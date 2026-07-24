#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly container_name="fastmssql-sql-auth-dev"

cd "${root_dir}"
if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

required=(
  FASTMSSQL_SQL_AUTH_SA_PASSWORD
  FASTMSSQL_SQL_AUTH_OWNER_PASSWORD
  FASTMSSQL_SQL_AUTH_READONLY_PASSWORD
  FASTMSSQL_SQL_AUTH_DENIED_PASSWORD
)
for variable in "${required[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "missing ${variable} in ${env_file}" >&2
    exit 2
  fi
done

scripts/sql_auth/wait_for_sql.sh
docker exec -i "${container_name}" /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -C -b \
  -v OwnerPassword="${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
     ReadonlyPassword="${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
     DeniedPassword="${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  < scripts/sql_auth/provision.sql
