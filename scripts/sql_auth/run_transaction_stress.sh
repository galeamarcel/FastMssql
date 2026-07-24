#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly profiles="${FASTMSSQL_TRANSACTION_STRESS_PROFILES:-10_000:100,99_999:100,99_999:200}"
readonly metrics_path="${artifact_dir}/transaction-stress-metrics.json"

cd "${root_dir}"

if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

mkdir -p "${artifact_dir}"
uv run python scripts/sql_auth/transaction_stress.py \
  --profiles "${profiles}" \
  --connection-strategy persistent \
  --metrics-output "${metrics_path}"
