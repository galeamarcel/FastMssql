# FastMssql `execute_many()` Transaction Security-Retirement Design

**Status:** approved through the standing design/specification authorization
and self-reviewed on 2026-07-29.

**Scope:** clarify and lock the existing fail-closed behavior when
`Transaction.execute_many()` executes SQL classified by
`requires_connection_retirement()`. This is a specification and coverage
correction. It does not change runtime behavior or the public API.

## Evidence and problem

The focused execute-many design states the normal caller-owned transaction
rule: successful execution restores `Active` without issuing driver-owned
`BEGIN`, `COMMIT`, or `ROLLBACK`.

The repository also has an older, stronger security invariant. SQL containing
an effective `EXECUTE AS`, `EXEC AS`, or `SETUSER` can leave a physical SQL
Server session under another principal. Such a session must never be reset
and returned to the pool. The ResultStream design already specifies that a
successful response is delivered, but the transport is retired and the
caller transaction becomes `Failed`.

A real SQL-auth reproduction against the approved Docker SQL Server showed
the same execute-many behavior:

```text
execute_many_returned=0
query_after_success=RuntimeError: Transaction state is indeterminate
rollback_after_success=RuntimeError: Transaction state is indeterminate
```

This is the safe existing runtime policy, but `EMANY-009` did not encode it
and the focused design could be read as requiring `Active` for every
successful SQL string.

## Considered approaches

### 1. Preserve fail-closed retirement and make the exception explicit

This is the selected approach.

- Deliver the successfully consumed SQL response and affected count.
- Classify the `execute_many` operation as successful.
- Retire the physical connection before it can return to the pool.
- Transition the caller `Transaction` to `Failed`.
- Reject later data operations, `commit()`, and `rollback()`; `close()` remains
  the idempotent terminal cleanup.
- Prove that a later pooled transaction uses a different physical
  `connection_id`.

This matches the existing ResultStream policy and prevents privilege leakage.

### 2. Keep the caller transaction active and retire only at settlement

Rejected. It would require a new persistent retirement flag across every
transaction operation and settlement path. More importantly, it would allow
later application statements to run under the impersonated principal. That
is a new public security model rather than a coverage correction.

### 3. Reject retirement-classified SQL locally

Rejected. The lexical classifier deliberately errs on the safe side and is
not a complete SQL parser. Turning it into an API rejection would add a
breaking restriction and diverge from the established query/stream policy.

## Authoritative behavior

The ordinary caller-transaction contract remains:

- reserve before the first producer pull;
- execute on the caller-owned physical session;
- issue no driver-owned settlement;
- restore `Active` after ordinary synchronized success;
- restore `Active` after a pre-wire producer/conversion failure;
- move to `RollbackOnly` after a synchronized post-wire failure;
- move to `Failed` when protocol synchronization is unknown.

The security-retirement exception takes precedence when
`requires_connection_retirement(sql)` is true:

1. successful protocol completion returns the checked affected count;
2. no driver-owned commit or rollback is claimed;
3. the physical session is marked unusable and closed;
4. SQL Server rolls back any still-open caller transaction when the transport
   closes;
5. the Rust transaction state becomes `Failed`;
6. subsequent query, execute, commit, and rollback operations fail closed;
7. explicit `close()` remains safe and idempotent;
8. the shared pool remains usable and supplies a different physical
   connection to later work.

The returned affected count describes the successfully consumed statement
responses. It is not a durability claim for an uncommitted caller
transaction whose transport is then retired.

For Connection-owned `atomic=True` and `atomic=False` calls, the existing
driver-owned settlement rules remain unchanged. A successful classified call
settles according to its selected mode and then retires the physical session
instead of returning it to the pool.

## Metrics and error contract

A fully consumed retirement-classified response is still one successful
`execute_many` operation:

```text
execute_many.started   += 1
execute_many.completed += 1
execute_many.succeeded += 1
```

The internal `execute`, `begin`, `commit`, and `rollback` operation counters
remain unchanged. No exception is synthesized merely to signal the security
retirement, matching ResultStream.

If SQL execution itself fails, the normal execute-many exception,
privacy-safe progress metadata, metric classification, and cleanup precedence
remain authoritative. Security retirement can strengthen disposition to
`Failed`, but it cannot replace the primary exception.

## Deterministic characterization

Extend the existing `EMANY-009` source test rather than adding another matrix
ID. That case already owns caller-Transaction settlement semantics.

The added SQL-auth scenario must:

1. create an isolated size-one pooled connection with metrics enabled;
2. begin a caller transaction and record its physical `connection_id`;
3. execute one parameter-free `EXECUTE AS USER = 'dbo'` set;
4. require the exact affected count and one successful `execute_many` metric;
5. require the retired transaction to report no live connection;
6. require query, commit, and rollback to reject the `Failed` state;
7. require `close()` to remain successful and idempotent;
8. begin fresh work through the same Connection and require a different
   physical `connection_id`;
9. prove the replacement transaction can query and roll back normally;
10. disconnect and prove zero application sessions through an independent
    DMV observer.

The test must not compare SPIDs because SQL Server may reuse a SPID after
physical retirement. `connection_id` is the physical identity invariant.

## Branch and verification discipline

```text
docs/execute-many-security-retirement-design
  -> test/execute-many-security-retirement
  -> feat/execute-many
```

The test branch is a characterization branch: the approved runtime policy
already exists, so the new assertion is expected to pass rather than provide
an artificial runtime RED. No fix branch is created unless the deterministic
test contradicts this design.

Every commit updates `VERSION.md`. Branches are pushed only to
`galeamarcel/FastMssql`; the original repository push URL remains `DISABLED`.
No wheel, credential, SQL-auth URL, local environment file, or generated
stress artifact is committed.
