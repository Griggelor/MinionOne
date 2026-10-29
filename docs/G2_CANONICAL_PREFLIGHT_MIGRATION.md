# G2 — canonical pre-dispatch SQLite migration (PR #19)

**Scope:** one canonical job owner. Existing MinionOne MCP pilot remains
authenticated to its static operator credential only and exposes NO new tools.

## Why this change exists

Before this patch the persisted jobs.state CHECK allowed only ACCEPTED,
CANCEL_REQUESTED, CANCELLED. The code had a pure normative 17-state
transition policy, but no durable, manager-owned VALIDATING, QUEUED or
RESOURCE_CHECK state. A separate runtime_jobs table would risk having
two contradictory owners; this implementation instead changes the CHECK
on the **existing** jobs table, preserving the rest of its structure.

## Schema and migration contract

- Allowed stored states: ACCEPTED, VALIDATING, QUEUED, RESOURCE_CHECK,
  CANCEL_REQUESTED, CANCELLED. Other canonical states are deliberately
  NOT yet persisted or reachable.
- The database CHECK(worker_started=0) is retained unchanged. There is
  still no worker execution, process handle, actual file broker, provider
  call, output publication or completion API.
- New installations create the constrained six-state schema.
- On existing v0.10 installations the manager first creates the
  historical auxiliary tables, recognizes the known legacy constraint,
  turns off FK *enforcement* inside the trusted migration connection,
  runs one BEGIN IMMEDIATE transaction, copies all columns into a
  verified replacement table, drops the old jobs parent, renames the
  replacement, runs PRAGMA foreign_key_check over the entire database,
  and only then commits. FK enforcement is restored before returning.
- Unexpected legacy schema or broken existing FK references abort the
  migration and preserve the original jobs table. No unsupported
  arbitrary schema rewriting is attempted.
- Existing child tables (admissions, controls, admission_events,
  outbox_intents, attempt_fences) still refer to the same jobs key.
  No migration deletes their records or resets receipts.

## Manager-only pre-dispatch transitions

The internal AdmissionStore.advance_pre_dispatch method accepts a
trusted canonical job ID, manager operation ID, expected state version
and one of VALIDATING / QUEUED / RESOURCE_CHECK. It is NOT exported
through MCP/worker. The existing lifecycle.check_transition is invoked
inside BEGIN IMMEDIATE *before* any UPDATE. The transition, new
monotonic per-job event sequence and idempotent original receipt are
committed together. A stale version, illegal graph edge, duplicate key
with different content or injected policy rejection produces no
partial write.

Existing authenticated parent request_abort now supports each of
these non-executing preparation states. Quiescent cancellation remains
safe only because worker_started=0 is database-enforced, and the journal
sequence is allocated dynamically rather than assuming seq=2/3.

## Adversarial acceptance tests

- Full ACCEPTED -> VALIDATING -> QUEUED -> RESOURCE_CHECK path.
- Direct ACCEPTED -> QUEUED, versioned abort and CANCELLED.
- Concurrent optimistic CAS, single winner.
- Persistent original idempotency receipt after a later state and a
  process restart; conflicting reuse denied.
- Malformed job IDs, operation keys, types and forged final states fail.
- Mocked canonical policy rejection rolls back state, event and receipt.
- Historical v0.10 database with children migrates without data loss;
  broken legacy FK prevents migration and rolls back.
- Existing attempt-fence and outbox FK structures remain valid.
- Actual MCP advertised tool list unchanged; no worker tool.
- Database still rejects worker_started=1 at SQL CHECK level.

## Unresolved design and release gates

This is an incremental pre-dispatch engine, **NOT the full canonical
persistent 17-state manager**. No authenticated real Unsloth thread
identity, sealed input snapshot/hash manifest, validated resource
telemetry from target, secure Win32 handle-bound broker, provider
fencing/quiescence, worker launch, trusted RESULT_READY artifact,
integration ACK, delivery receipt, or target E2E test exists.

The migration is a sensitive operation: before applying it to actual
pilot data, snapshot/backup the private DB under exclusive ownership,
stop all MinionOne instances, and test against synthetic data. Avoid
claiming safe multi-instance startup while leader-election is absent.

**Gate:** exact-head dual-OS green CI and security review required for
merging this bounded component. G1a/G1b/G1c/G2/G3 and beta remain
NO-GO until complete and independently evidenced.
