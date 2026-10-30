# G2-E29-02: durable rejection of parent control requests

Status: implemented development slice, v0.16.0.dev0. Related: issue #29,
normative v0.6 ADR-022 (lifecycle, canonical state and journal) and the handoff
error-UX requirements. This is not completion of G2 or a beta approval.

## Problem and scope

In v0.15 an authorized `minionone_resume_request` could return
`RESUME_NOT_READY` with no durable job event. After restart the parent could
read the job but could not recover the rejected operation's diagnosis.

This slice covers **valid, authorized job-specific ABORT and RESUME_REQUEST**
commands rejected with `STALE_VERSION`, `IDEMPOTENCY_CONFLICT`, or
`RESUME_NOT_READY`. It does not journal malformed/unauthenticated requests,
unknown jobs, model discovery, provider/worker failures or arbitrary internal
exceptions. Those need their own trusted source and transaction integration.

## Ownership and transaction

`AdmissionStore` owns canonical `jobs`, `admission_events`, control transactions
and rejection persistence. `TimeoutControlStore` uses that owner's transaction;
it does not open a second transaction to log a failure. No logger retries after
a failed command and no code infers a job state from an error code.

1. Authenticate the configured principal/thread and validate command fields.
2. `BEGIN IMMEDIATE`; verify ownership before reading any receipt or error.
3. Establish a savepoint for the command. An existing accepted receipt has
   priority. Otherwise an existing rejected command preserves its failure class;
   different parameters with that key produce `IDEMPOTENCY_CONFLICT`.
4. On a classified rejection, roll back partial command writes to the savepoint.
5. Append an event and its typed error metadata in the same transaction, using
   the job state/version and observed attempt epoch from that transaction.
6. Commit before raising the safe exception to the MCP transport. A crash before
   commit leaves neither error row; a crash after commit leaves both.

Successful controls keep their existing CAS and receipt behavior. A rejected
command changes neither `jobs.state` nor `jobs.version`. Event `seq` advances
independently of state version. Clients must use the version in **job status**
for a new command, not the historical version in an error event.

## Public projection

`minionone_job_events` retains its existing response shape and normal events.
Rejected controls add these fields to their event:

| Field | Meaning |
|---|---|
| `type`, `schema_version` | `CONTROL_REJECTED`, `1` |
| `status`, `state_version` | Historical canonical state and version at rejection |
| `operation`, `phase` | `ABORT` or `RESUME_REQUEST`, `PARENT_CONTROL` |
| `attempt_epoch` | Observed attempt epoch, or `null` if none exists |
| `seq`, `occurred_utc` | Canonical event order and server UTC timestamp |
| `operation_id` | `error:` plus a job/operation-scoped SHA-256 correlation digest |
| `error` | Fixed `{code,message,action,retryable}` snapshot |

Job status includes `latest_control_error` only when a rejection exists. It is
**historical** metadata and may describe an earlier version than the current
job. A later successful action does not erase the audit trail. This field does
not mean the current job failed or that Resume started.

The diagnostic snapshot comes exclusively from the fixed catalog, selected by
known exception type. Exception messages, supplied diagnostic attributes,
credentials and raw client operation keys are not copied into new error rows.
Existing successful admission/control receipt storage is unchanged; callers
must still use opaque, non-sensitive operation identifiers there.

## Capacity and storage failure

The durable limit is 128 distinct rejected operation keys per job, across both
control kinds. Replays use the original entry without allocating another one.
There is no silent eviction. The existing events read limit of 500 covers this
slice's bounded control-error timeline; general lifecycle pagination/retention
remains future work before a full worker runtime.

At capacity, or when writing/committing a rejection fails, return the fixed
`ERROR_JOURNAL_UNAVAILABLE` diagnostic, with `retryable=false`. Do not claim the
original rejection was durably recorded. Accepted controls (including a valid
abort) are not blocked by error-journal capacity. An unavailable database may
also prevent status reads; no database can journal into storage it cannot write.
Broad SQLite taxonomy, health reporting and retention tooling remain open.

## Migration and rollback

The new `control_errors` table is an additive metadata child of the existing
`admission_events(job_id,seq)` timeline. There is no jobs-table rebuild for this
slice. Existing v0.10/v0.13 canonical migrations still run before the addition;
v0.15 databases gain the table on startup. Foreign keys tie each metadata row
to exactly one canonical event.

Stop all MinionOne processes and use SQLite backup on a consistent private
database before upgrading a pilot machine. Test on synthetic data first. To
roll back, restore that backup with the old binary while stopped. Running an
old binary against an upgraded database is not a supported rollback: the old
binary cannot project the new error metadata or preserve rejected receipts.

## Evidence and remaining gates

`tests/test_durable_control_errors.py` exercises the real HTTP MCP endpoint,
real SQLite transactions, restart, failed and accepted receipt replay, races,
wrong principals/threads/bearer, canaries, quotas, failed event writes, deferred
commit failure and process exits before/after commit. Synthetic RUNNING and
checkpoint fixtures are explicitly test-only; no live worker starts.

See `evidence/g2-control-errors/TEST_RUN_SUMMARY.json` for executed commands and
counts. CI is portable-contract evidence, not a Windows file-broker proof or a
real Unsloth test. Real parent identity/wakeup, provider/worker errors, general
durable manager execution, immutable artifacts, independent external review and
license/provenance gates remain open. Issue #29 and beta remain **NO-GO**.
