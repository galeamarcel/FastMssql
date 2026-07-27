#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly local_env_file="${root_dir}/.env.sql-auth.local"
readonly shared_env_file="${root_dir}/../../.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly profiles="${FASTMSSQL_RESULT_STREAM_STRESS_PROFILES:-1000:64}"
readonly rss_growth_limit_bytes="${FASTMSSQL_RESULT_STREAM_STRESS_RSS_GROWTH_LIMIT_BYTES:-134217728}"

if [[ -n "${FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH:-}" ]]; then
  readonly metrics_path="${FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH}"
elif [[ "${profiles}" == "1000:64" ]]; then
  readonly metrics_path="${artifact_dir}/result-stream-stress-metrics.json"
else
  readonly metrics_path="${artifact_dir}/result-stream-stress-metrics-extended.json"
fi

if [[ -n "${FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH:-}" ]]; then
  readonly results_path="${FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH}"
elif [[ "${profiles}" == "1000:64" ]]; then
  readonly results_path="${artifact_dir}/result-stream-load-results.json"
else
  readonly results_path="${artifact_dir}/result-stream-load-results-extended.json"
fi

if [[ -f "${local_env_file}" ]]; then
  readonly env_file="${local_env_file}"
elif [[ -f "${shared_env_file}" ]]; then
  readonly env_file="${shared_env_file}"
else
  echo "missing .env.sql-auth.local for result-stream stress" >&2
  exit 2
fi

if [[ -x "${root_dir}/.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/.venv/bin/python"
elif [[ -x "${root_dir}/../../.venv/bin/python" ]]; then
  readonly python_bin="${root_dir}/../../.venv/bin/python"
else
  echo "missing project Python environment for result-stream stress" >&2
  exit 2
fi

cd "${root_dir}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

mkdir -p "${artifact_dir}"
rm -f "${metrics_path}"
if [[ "${profiles}" == "1000:64" ]] \
  && [[ "${rss_growth_limit_bytes}" == "134217728" ]]; then
  rm -f "${results_path}"
fi

"${python_bin}" scripts/sql_auth/result_stream_stress.py \
  --profiles "${profiles}" \
  --pool-size 8 \
  --buffer-size 8 \
  --rss-growth-limit-bytes "${rss_growth_limit_bytes}" \
  --metrics-output "${metrics_path}" \
  --results-output "${results_path}"
