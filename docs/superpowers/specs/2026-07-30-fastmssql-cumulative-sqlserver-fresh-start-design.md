# FastMssql cumulative SQL Server fresh-start gate design

Date: 2026-07-30
Status: approved inline and ready for TDD
Parent program: `feat/batch-bulk`, Task 12 cumulative verification

## Problem

The canonical SQL-auth runner currently starts its dedicated SQL Server with:

```text
docker compose ... up -d sqlserver
```

When the container already exists and its configuration and image are
unchanged, Compose reuses the running container. A cumulative validation run
therefore inherits the SQL Server process state left by earlier stress and
full-suite runs.

On cumulative candidate
`b997ab3d185728e7640ed63e290f34c15196a13c`, the unchanged
`BULK-008` native-bulk test timed out after its request waited on
`RESOURCE_SEMAPHORE`. The waiting request required about 58 MB of query
memory, while its resource semaphore exposed about 15 MB. SQL Server reported
the process at 100% memory utilization and a low-memory condition.

After restarting only the dedicated container and reprovisioning the same
named-volume databases:

- the normal resource semaphores exposed 638–837 MB;
- there were zero resource-semaphore waiters and no low-memory flag;
- the exact unchanged test passed in 0.14 seconds.

This evidence identifies inherited SQL Server process pressure as the gate
failure. It does not identify a FastMssql native-bulk defect.

## Decision

The canonical full runner MUST recreate the dedicated `sqlserver` service
before provisioning:

```text
docker compose ... up -d --force-recreate sqlserver
```

Docker Compose defines `--force-recreate` as recreating containers even when
their configuration and image have not changed. Compose preserves mounted
volumes during recreation, so the named
`fastmssql_sql_auth_data` volume remains intact.

Authoritative reference:
[Docker Compose `up`](https://docs.docker.com/reference/cli/docker/compose/up/).

## Alternatives rejected

### `docker compose restart sqlserver`

Rejected because it requires the service container to exist already. The
canonical runner must support both first-time bootstrap and repeat execution.

### SQL Server memory tuning

Rejected because changing `max server memory`, the container memory limit, or
resource-governor settings would alter the test environment without fixing
the inherited-process-state defect.

### Retrying the timed-out test

Rejected because it would hide a non-deterministic prerequisite and could
repeat an expensive or state-changing database operation.

### Removing the named volume

Rejected because it is destructive, unnecessary, and would erase persistent
test databases. The fix recreates only the container.

## Executable contract

The existing full-runner sandbox test will use a real executable fake
`docker` command that records every invocation. It will assert externally
observable runner behavior:

1. exactly one Docker invocation is made by `run_all.sh`;
2. its literal argument vector is:

   ```text
   compose --env-file <sandbox-env> -f docker-compose.sql-auth.yml
   up -d --force-recreate sqlserver
   ```

3. the compose invocation completes before the sandbox provision script
   executes;
4. the runner still reports all required lanes and exits successfully.

The production change that makes this contract fail is removal, misspelling,
or reordering of `--force-recreate`, or moving provision before container
recreation. The test executes the runner rather than inspecting source text.

## Branch and TDD topology

```text
verify/batch-bulk-merge
  -> docs/cumulative-sqlserver-fresh-start-design
  -> test/cumulative-sqlserver-fresh-start
  -> fix/cumulative-sqlserver-fresh-start
  -> verify/batch-bulk-merge
```

The RED branch changes only the executable contract and `VERSION.md`. The fix
branch changes only `scripts/sql_auth/run_all.sh` and `VERSION.md`, except for
an evidence-backed test-fixture correction if the RED run exposes one.

## Verification

The fix is acceptable only when all of the following hold:

- the new executable contract fails on the RED branch for the missing
  `--force-recreate` behavior;
- it passes unchanged on the fix branch;
- the complete matrix-contract file passes;
- Ruff, compileall, `git diff --check`, privacy/artifact checks and
  code-review-graph review pass;
- the exact previously timed-out SQL-auth test passes after recreation;
- a complete `scripts/sql_auth/run_all.sh` run passes from one clean exact
  cumulative SHA;
- the generated evidence records the recreated compose command and reports a
  clean source tree.

## Security, ownership and publication

- The runner remains hard-bound to `fastmssql-sql-auth-dev`.
- Secrets remain in ignored `.env.sql-auth.local` and are never recorded by
  the new test.
- No database volume is deleted.
- All branches and commits are published only to
  `galeamarcel/FastMssql`.
- The original repository remains fetch-only with push URL `DISABLED`.

## Residual risk

A fresh SQL Server process does not guarantee unlimited host memory. Genuine
host or container exhaustion must still fail visibly. This design removes
only inherited process pressure and deliberately adds no retry that could
conceal such a failure.
