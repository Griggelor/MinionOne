# G2 Outbox slice — ADR-022 conformance record

**Status:** Internal experimental implementation. **NOT CONNECTED TO MCP OR PROVIDER.**

## Decision owner and boundary

The trusted manager owns the outbox table in the **same SQLite database**
as the canonical jobs table. External message delivery remains a separate,
unverified provider effect. The worker, MCP caller and model text have no API
to mark delivery, provide a receipt or authorize a retry.

The outbox implementation has only these state transitions:

- PREPARED: hashed intended payload and idempotency key committed.
- IN_FLIGHT: unique one-time claim committed **before** any sender effect.
- NEEDS_RECONCILIATION: an in-flight claim with unknown external outcome,
  including after restart. It cannot return to PREPARED in this slice.

There is deliberately **no DELIVERED state, retry or message sending code**.
A later integration must verify real provider receipts and authorized
recipient/chat identity, and must not bypass this conservative stop gate.

## Invariants tested

1. Prepare requires an existing manager job at RESULT_READY/INTEGRATING,
   and an exact expected job state version. Current admission-only manager
   has no such eligible state and is therefore denied.
2. Per-job command key and digest are persistent. Same key/same arguments
   returns the original receipt even after a later state change; altered
   arguments return an idempotency conflict.
3. Prepared-intent quota persists across process restart and blocks without
   partial writes. Replays do not consume new quota.
4. Concurrent claim uses BEGIN IMMEDIATE and one CAS on PREPARED/version=0;
   precisely one claimant receives a fresh internal capability token.
5. Claims commit IN_FLIGHT before any future possible provider operation.
   Retrying the same claim, even with the same command key, is forbidden.
6. Recovery quarantines ambiguous IN_FLIGHT entries. Re-running recovery
   does not create, resend or falsely mark a message delivered.
7. No recipient URL, raw message, prompt or secret is written into the
   outbox rows. The dispatch-token digest alone persists.
8. Nonexistent jobs, invalid identifiers and stale versions fail closed.

The tests use a synthetic future-manager jobs table for RESULT_READY/INTEGRATING.
A separate integration test against the **real** AdmissionStore verifies that
the current non-executing ACCEPTED state cannot create a delivery intent.
This test distinction matters: there is no real end-to-end delivery yet.

## Residual safety and scope limitations

- These Python methods are manager-internal and are NOT authenticated
  transport endpoints. Their presence does not prove an access boundary.
- Current job states are not yet fully migrated to the normative v0.6
  lifecycle; the separate outbox table does not change job states.
- Provider-specific message IDs, read-only reconciliation, verified
  delivery ACK and the true Unsloth original-chat recipient are missing.
- No Windows file handles/NTFS sandbox, target GPU/residency tests or
  live Unsloth confirmation have been performed here.
- Path to the SQLite database is trusted operator configuration, **not**
  a hardened multi-user Windows security service; host-process tampering
  and adversarial ACL/race attacks are not solved by WAL.
- There is no automatic call to quarantine_in_flight on startup yet;
  the future manager orchestrator must call it **before** any potential send.

**Gate verdict:** PASS only for the bounded internal durable outbox
invariants if actual dual-OS CI is green. G1a/G1b/G2/G3 and beta remain NO-GO.
