#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly local_env_file="${root_dir}/.env.sql-auth.local"
readonly shared_env_file="${root_dir}/../../.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly operations="${FASTMSSQL_NAMED_INSTANCE_OPERATIONS:-1000}"
readonly workers="${FASTMSSQL_NAMED_INSTANCE_WORKERS:-32}"
readonly pool_size="${FASTMSSQL_NAMED_INSTANCE_POOL_SIZE:-8}"
readonly allow_extended="${FASTMSSQL_NAMED_INSTANCE_ALLOW_EXTENDED:-0}"
readonly rss_growth_limit_bytes="${FASTMSSQL_NAMED_INSTANCE_RSS_GROWTH_LIMIT_BYTES:-134217728}"
readonly event_loop_gap_limit_seconds="${FASTMSSQL_NAMED_INSTANCE_EVENT_LOOP_GAP_LIMIT_SECONDS:-0.100}"
readonly operation_timeout_seconds="${FASTMSSQL_NAMED_INSTANCE_OPERATION_TIMEOUT_SECONDS:-30}"

if [[ "${allow_extended}" != "0" && "${allow_extended}" != "1" ]]; then
  echo "FASTMSSQL_NAMED_INSTANCE_ALLOW_EXTENDED must be 0 or 1" >&2
  exit 2
fi

if [[ -f "${local_env_file}" ]]; then
  readonly env_file="${local_env_file}"
elif [[ -f "${shared_env_file}" ]]; then
  readonly env_file="${shared_env_file}"
else
  echo "missing .env.sql-auth.local for named-instance stress" >&2
  exit 2
fi

if [[ -x "${root_dir}/.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/.venv/bin/python"
elif [[ -x "${root_dir}/../../.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/../../.venv/bin/python"
else
  echo "missing project Python environment for named-instance stress" >&2
  exit 2
fi

if [[ -n "${FASTMSSQL_NAMED_INSTANCE_STRESS_METRICS_PATH:-}" ]]; then
  readonly metrics_path="${FASTMSSQL_NAMED_INSTANCE_STRESS_METRICS_PATH}"
elif [[ "${allow_extended}" == "1" ]]; then
  readonly metrics_path="${artifact_dir}/named-instance-stress-extended.json"
else
  readonly metrics_path="${artifact_dir}/named-instance-stress.json"
fi

cd "${root_dir}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

mkdir -p "${artifact_dir}"
readonly source_sha="$(git rev-parse HEAD)"
arguments=(
  --operations "${operations}"
  --workers "${workers}"
  --pool-size "${pool_size}"
  --operation-timeout-seconds "${operation_timeout_seconds}"
  --rss-growth-limit-bytes "${rss_growth_limit_bytes}"
  --event-loop-gap-limit-seconds "${event_loop_gap_limit_seconds}"
  --source-sha "${source_sha}"
  --metrics-output "${metrics_path}"
)
if [[ "${allow_extended}" == "1" ]]; then
  arguments+=(--allow-extended)
fi

"${python_bin}" scripts/sql_auth/named_instance_stress.py "${arguments[@]}"
