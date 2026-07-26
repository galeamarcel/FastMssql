#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly source_sha="$(git -C "${root_dir}" rev-parse HEAD)"

cd "${root_dir}"
test -f "${env_file}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a
mkdir -p "${artifact_dir}"

uv run python scripts/sql_auth/operation_metrics_stress.py \
  --operations 99999 \
  --workers 200 \
  --pool-size 100 \
  --pairs 3 \
  --maximum-median-degradation 0.15 \
  --source-sha "${source_sha}" \
  --metrics-output \
    "${artifact_dir}/operation-metrics-stress.json"
