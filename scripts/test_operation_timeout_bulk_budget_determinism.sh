#!/usr/bin/env bash
set -euo pipefail

readonly project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly env_file="${project_root}/.env.sql-auth.local"
readonly iterations="${FASTMSSQL_TIME006_STRESS_ITERATIONS:-20}"
readonly node_id="tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget"

if [[ ! "${iterations}" =~ ^[0-9]+$ ]] \
  || ((iterations < 1 || iterations > 100)); then
  printf '%s\n' \
    'FASTMSSQL_TIME006_STRESS_ITERATIONS must be an integer from 1 through 100' \
    >&2
  exit 2
fi

if [[ ! -f "${env_file}" ]]; then
  printf 'missing %s\n' "${env_file}" >&2
  exit 2
fi

cd "${project_root}"
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

readonly results_path="$(
  mktemp "${TMPDIR:-/tmp}/fastmssql-time006-results.XXXXXX"
)"
trap 'rm -f "${results_path}"' EXIT

for ((iteration = 1; iteration <= iterations; iteration++)); do
  if output="$(
    FASTMSSQL_SQL_AUTH_RESULTS_PATH="${results_path}" \
      uv run pytest -q "${node_id}" 2>&1
  )"; then
    printf '[TIME-006-stress] %d/%d passed\n' \
      "${iteration}" "${iterations}"
  else
    printf '%s\n' "${output}" >&2
    printf '[TIME-006-stress] failed at iteration %d/%d\n' \
      "${iteration}" "${iterations}" >&2
    exit 1
  fi
done
