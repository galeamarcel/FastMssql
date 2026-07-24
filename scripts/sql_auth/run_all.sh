#!/usr/bin/env bash
set -uo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly container_name="fastmssql-sql-auth-dev"
required_failures=0

cd "${root_dir}"

if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

if [[ "${FASTMSSQL_SQL_AUTH_CONTAINER:-${container_name}}" != "${container_name}" ]]; then
  echo "refusing Docker target other than ${container_name}" >&2
  exit 2
fi

mkdir -p "${artifact_dir}"

record() {
  local name="$1"
  shift
  local log_path="${artifact_dir}/${name}.log"
  local exit_path="${artifact_dir}/${name}.exitcode"
  local command_path="${artifact_dir}/${name}.command"
  local code

  echo "[sql-auth] ${name}: $*"
  printf '%q ' "$@" >"${command_path}"
  printf '\n' >>"${command_path}"
  "$@" >"${log_path}" 2>&1
  code=$?
  printf '%s\n' "${code}" >"${exit_path}"
  if [[ "${code}" -ne 0 ]]; then
    required_failures=$((required_failures + 1))
    echo "[sql-auth] ${name}: FAILED (${code}); see ${log_path}" >&2
  else
    echo "[sql-auth] ${name}: passed"
  fi
}

record uv-sync uv sync --locked --all-extras --dev
record maturin-develop uv run maturin develop --release
record cargo-fmt cargo fmt --check
record cargo-clippy cargo clippy --all-targets -- -D warnings
record cargo-test cargo test

record compose-up \
  docker compose --env-file "${env_file}" \
  -f docker-compose.sql-auth.yml up -d sqlserver
record provision scripts/sql_auth/provision.sh

export FASTMSSQL_TEST_CONNECTION_STRING="Server=${FASTMSSQL_SQL_AUTH_HOST},${FASTMSSQL_SQL_AUTH_PORT};Database=fastmssql_upstream_regression;User Id=${FASTMSSQL_SQL_AUTH_OWNER_USER};Password=${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD};Encrypt=True;TrustServerCertificate=True"
export FAST_MSSQL_TEST_DB_USER="${FASTMSSQL_SQL_AUTH_OWNER_USER}"
export FAST_MSSQL_TEST_DB_PASSWORD="${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}"
export FAST_MSSQL_TEST_SERVER="${FASTMSSQL_SQL_AUTH_HOST}"
export FAST_MSSQL_TEST_PORT="${FASTMSSQL_SQL_AUTH_PORT}"
export FAST_MSSQL_TEST_DATABASE="fastmssql_upstream_regression"

readonly strict_functional=(
  tests/sql_auth_strict/test_matrix_contract.py
  tests/sql_auth_strict/test_environment_auth.py
  tests/sql_auth_strict/test_connection.py
  tests/sql_auth_strict/test_pool.py
  tests/sql_auth_strict/test_sql_features.py
  tests/sql_auth_strict/test_parameters_strict.py
  tests/sql_auth_strict/test_type_mapping_strict.py
  tests/sql_auth_strict/test_results_strict.py
  tests/sql_auth_strict/test_batch_strict.py
  tests/sql_auth_strict/test_transactions_strict.py
  tests/sql_auth_strict/test_errors_tls.py
)

record strict \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/strict-results.json" \
  uv run pytest "${strict_functional[@]}" \
  -m "not resilience and not load" \
  --junitxml="${artifact_dir}/strict.xml" -vv

record async \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/async-results.json" \
  uv run pytest tests/sql_auth_strict/test_async_strict.py \
  --junitxml="${artifact_dir}/async.xml" -vv

record framework \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/framework-results.json" \
  FASTMSSQL_FRAMEWORK_METRICS_PATH="${artifact_dir}/framework-metrics.json" \
  uv run pytest tests/sql_auth_strict/test_framework_integration.py \
  --junitxml="${artifact_dir}/framework.xml" -vv

record resilience \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/resilience-results.json" \
  uv run pytest tests/sql_auth_strict/test_resilience_load.py \
  -m resilience --junitxml="${artifact_dir}/resilience.xml" -vv

record load \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/load-results.json" \
  uv run pytest tests/sql_auth_strict/test_resilience_load.py \
  -m load --junitxml="${artifact_dir}/load.xml" -vv

record upstream \
  uv run pytest -n 1 tests \
  --ignore=tests/test_azure_auth_advanced.py \
  --ignore=tests/test_azure_authentication.py \
  --ignore=tests/test_azure_cli_path_validation.py \
  --ignore=tests/test_transaction_azure_auth.py \
  --ignore=tests/test_transaction_azure_auth_advanced.py \
  --ignore=tests/sql_auth_strict \
  --junitxml="${artifact_dir}/upstream.xml" -vv

record report \
  uv run python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results "${artifact_dir}/strict-results.json" \
  --artifact-dir "${artifact_dir}" \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md

if [[ "${required_failures}" -ne 0 ]]; then
  echo "[sql-auth] ${required_failures} required lane(s) failed" >&2
  exit 1
fi

echo "[sql-auth] all required lanes passed"
