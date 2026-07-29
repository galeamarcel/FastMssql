#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly local_env_file="${root_dir}/.env.sql-auth.local"
readonly shared_env_file="${root_dir}/../../.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly profiles="${FASTMSSQL_QUERY_MANY_PROFILES:-1_000:sync:true:10:10,1_000:async:false:25:8,10_000:sync:false:50:16,10_000:async:true:100:16}"
readonly allow_extended="${FASTMSSQL_QUERY_MANY_ALLOW_EXTENDED:-0}"
readonly metrics_path="${FASTMSSQL_QUERY_MANY_METRICS_PATH:-${artifact_dir}/query-many-stress.json}"
readonly rss_growth_limit_bytes="${FASTMSSQL_QUERY_MANY_RSS_GROWTH_LIMIT_BYTES:-134217728}"
readonly event_loop_gap_limit_seconds="${FASTMSSQL_QUERY_MANY_EVENT_LOOP_GAP_LIMIT_SECONDS:-0.100}"
readonly operation_timeout_seconds="${FASTMSSQL_QUERY_MANY_OPERATION_TIMEOUT_SECONDS:-300}"

if [[ "${allow_extended}" != "0" && "${allow_extended}" != "1" ]]; then
  echo "FASTMSSQL_QUERY_MANY_ALLOW_EXTENDED must be 0 or 1" >&2
  exit 2
fi

if [[ -f "${local_env_file}" ]]; then
  readonly env_file="${local_env_file}"
elif [[ -f "${shared_env_file}" ]]; then
  readonly env_file="${shared_env_file}"
else
  echo "missing .env.sql-auth.local for query-many stress" >&2
  exit 2
fi

if [[ -x "${root_dir}/.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/.venv/bin/python"
elif [[ -x "${root_dir}/../../.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/../../.venv/bin/python"
else
  echo "missing project Python environment for query-many stress" >&2
  exit 2
fi

cd "${root_dir}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

mkdir -p "${artifact_dir}"

arguments=(
  --profiles "${profiles}"
  --metrics-output "${metrics_path}"
  --rss-growth-limit-bytes "${rss_growth_limit_bytes}"
  --event-loop-gap-limit-seconds "${event_loop_gap_limit_seconds}"
  --operation-timeout-seconds "${operation_timeout_seconds}"
)
if [[ "${allow_extended}" == "1" ]]; then
  arguments+=(--allow-extended)
fi

"${python_bin}" scripts/sql_auth/query_many_stress.py "${arguments[@]}"
