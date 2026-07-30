#!/usr/bin/env bash
set -uo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly artifact_dir="${root_dir}/.artifacts/sql-auth"
readonly container_name="fastmssql-sql-auth-dev"
# Tiberius 0.12.3 predates Rust 1.94 and triggers these audited legacy lint
# categories in unchanged vendored code. Keep every other warning denied.
readonly tiberius_clippy_legacy_lints=(
  -A clippy::doc_lazy_continuation
  -A clippy::extra_unused_lifetimes
  -A clippy::large_enum_variant
  -A clippy::io_other_error
  -A clippy::needless_lifetimes
  -A clippy::legacy_numeric_constants
  -A clippy::cast_enum_truncation
  -A clippy::derivable_impls
  -A clippy::manual_div_ceil
  -A clippy::items_after_test_module
)
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

display_lane_name() {
  case "$1" in
    upstream)
      printf '%s' "original-local-regression"
      ;;
    *)
      printf '%s' "$1"
      ;;
  esac
}

record() {
  local name="$1"
  local display_name
  display_name="$(display_lane_name "${name}")"
  shift
  local log_path="${artifact_dir}/${name}.log"
  local exit_path="${artifact_dir}/${name}.exitcode"
  local command_path="${artifact_dir}/${name}.command"
  local code

  echo "[sql-auth] ${display_name}: $*"
  printf '%q ' "$@" >"${command_path}"
  printf '\n' >>"${command_path}"
  "$@" >"${log_path}" 2>&1
  code=$?
  printf '%s\n' "${code}" >"${exit_path}"
  if [[ "${code}" -ne 0 ]]; then
    required_failures=$((required_failures + 1))
    echo "[sql-auth] ${display_name}: FAILED (${code}); see ${log_path}" >&2
  else
    echo "[sql-auth] ${display_name}: passed"
  fi
}

record uv-sync uv sync --locked --all-extras --dev

if ! cargo_test_python="$(
  uv run python -c 'import sys; print(sys.executable)'
)" || [[ -z "${cargo_test_python}" || ! -x "${cargo_test_python}" ]]; then
  echo "unable to resolve the worktree Python executable" >&2
  exit 2
fi
readonly cargo_test_python

if ! cargo_test_python_home="$(
  uv run python -c 'import sys; print(sys.base_prefix)'
)" || [[ -z "${cargo_test_python_home}" || ! -d "${cargo_test_python_home}" ]]; then
  echo "unable to resolve the worktree Python base prefix" >&2
  exit 2
fi
readonly cargo_test_python_home

record maturin-develop uv run maturin develop --release
record cargo-fmt cargo fmt --check
record cargo-clippy cargo clippy --all-targets -- -D warnings
record cargo-test \
  env \
  "PYO3_PYTHON=${cargo_test_python}" \
  "PYTHONHOME=${cargo_test_python_home}" \
  cargo test --locked
record tiberius-fmt \
  cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
record tiberius-clippy \
  cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --all-targets -- -D warnings "${tiberius_clippy_legacy_lints[@]}"
record tiberius-lib \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --lib

record compose-up \
  docker compose --env-file "${env_file}" \
  -f docker-compose.sql-auth.yml up -d --force-recreate sqlserver
record provision scripts/sql_auth/provision.sh
record tiberius-token-safety-sql-auth \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --test token_safety_sql_auth -- --test-threads=1
record tiberius-bulk-column-subset-sql-auth \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --test bulk_column_subset_sql_auth -- --test-threads=1
record tiberius-response-sql-auth \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --test response_events_sql_auth -- --test-threads=1
record named-instance-load scripts/sql_auth/run_named_instance_stress.sh
record result-stream-load scripts/sql_auth/run_result_stream_stress.sh
record query-many-load scripts/sql_auth/run_query_many_stress.sh

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
  tests/sql_auth_strict/test_pool_observability.py
  tests/sql_auth_strict/test_operation_metrics.py
  tests/sql_auth_strict/test_sql_features.py
  tests/sql_auth_strict/test_parameters_strict.py
  tests/sql_auth_strict/test_type_mapping_strict.py
  tests/sql_auth_strict/test_results_strict.py
  tests/sql_auth_strict/test_resultsets_streaming.py
  tests/sql_auth_strict/test_resultstream_lifecycle.py
  tests/sql_auth_strict/test_rpc_results.py
  tests/sql_auth_strict/test_batch_strict.py
  tests/sql_auth_strict/test_native_bulk_strict.py
  tests/sql_auth_strict/test_native_bulk_iterable_strict.py
  tests/sql_auth_strict/test_execute_many_strict.py
  tests/sql_auth_strict/test_query_many_strict.py
  tests/sql_auth_strict/test_named_instance_strict.py
  tests/sql_auth_strict/test_transactions_strict.py
  tests/sql_auth_strict/test_operation_timeouts.py
  tests/sql_auth_strict/test_lifecycle.py
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
  FASTMSSQL_LOAD_METRICS_PATH="${artifact_dir}/load-metrics.json" \
  uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  tests/sql_auth_strict/test_named_instance_strict.py \
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
  --report-output docs/SQL_AUTH_TEST_REPORT.md \
  --require-complete

if [[ "${required_failures}" -ne 0 ]]; then
  echo "[sql-auth] ${required_failures} required lane(s) failed" >&2
  exit 1
fi

echo "[sql-auth] all required lanes passed"
