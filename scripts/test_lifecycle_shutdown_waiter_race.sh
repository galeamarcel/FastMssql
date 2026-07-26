#!/usr/bin/env bash
set -euo pipefail

readonly project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly iterations="${FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS:-200}"
readonly test_name="lifecycle::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor"

if [[ ! "${iterations}" =~ ^[0-9]+$ ]] \
  || (( iterations < 1 || iterations > 1000 )); then
  printf '%s\n' \
    'FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS must be an integer from 1 through 1000' \
    >&2
  exit 2
fi

cd "${project_root}"

for ((iteration = 1; iteration <= iterations; iteration++)); do
  if output="$(cargo test --locked "${test_name}" -- --exact 2>&1)"; then
    printf '[lifecycle-waiter-stress] %d/%d passed\n' \
      "${iteration}" "${iterations}"
  else
    printf '%s\n' "${output}" >&2
    printf '[lifecycle-waiter-stress] failed at iteration %d/%d\n' \
      "${iteration}" "${iterations}" >&2
    exit 1
  fi
done
