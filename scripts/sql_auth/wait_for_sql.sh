#!/usr/bin/env bash
set -euo pipefail

readonly compose_file="docker-compose.sql-auth.yml"
readonly env_file=".env.sql-auth.local"
readonly container_name="fastmssql-sql-auth-dev"
readonly max_attempts=90

if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  status="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "${container_name}" 2>/dev/null || true
  )"
  if [[ "${status}" == "healthy" ]]; then
    exit 0
  fi
  sleep 2
done

docker compose --env-file "${env_file}" -f "${compose_file}" ps
docker logs --tail 200 "${container_name}"
echo "${container_name} did not become healthy within 180 seconds" >&2
exit 1
