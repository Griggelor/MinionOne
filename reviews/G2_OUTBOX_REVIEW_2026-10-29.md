# G2 outbox independent-role security/architecture review

**Scope:** PR #12, source files outbox.py and test_outbox_durable.py.
**Reviewer:** separate critical pass by the same development assistant, NOT a
third-party independent audit. Verdict: **PASS with residual risks** for an
unexposed internal persistence primitive only; **FAIL/NO-GO for beta.**

## Reconstructed production path

1. Trusted manager code constructs OutboxStore against an existing SQLite
   manager database with canonical job_id/state/version fields.
2. PREPARE: strict IDs/digest/version, BEGIN IMMEDIATE, canonical job state
   RESULT_READY/INTEGRATING + exact version check, per-job quota, immutable
   idempotency receipt and unique insert. Replays return the original receipt.
3. CLAIM: check canonical job remains eligible at its original version,
   commit IN_FLIGHT and a new capability-token SHA256 in one transaction.
   Only then does manager get a confidential dispatch token.
4. QUARANTINE: without calling any provider, mark all unresolved IN_FLIGHT
   as NEEDS_RECONCILIATION in one commit. Repeated quarantine is idempotent.
5. No SEND, RETRY, ACK, DELIVERED or exposed MCP/worker method exists.

## Negative/adversarial tests authored

- Invalid names and SHA256, bool versions, unknown job.
- Canonical non-executing AdmissionStore job is refused; tests using a
  synthetic future RESULT_READY job are not actual end-to-end production tests.
- Repeated idempotency key, conflicting digest, conflicting state version.
- Persistent quota/restart; parallel dispatch claims (only one CAS winner).
- Crash/uncertain effect never blindly reclaims or sends.
- Job version changed after prepare blocks claim.
- Stored records contain digests, not arbitrary prose/URLs/secrets.
- Explicitly reject simulation of an unverified provider delivery receipt.

## Windows/Linux issue found in test architecture

Original test fixture used sqlite3.connect as a context manager: commit/rollback
does **not** close a Python sqlite3 connection. Windows held open database
handles and TempDirectory cleanup failed with WinError 32. All fixture
connections must be explicitly closed with contextlib.closing. This is a
test-harness failure; do not claim Windows PASS until re-run with this fix.

## BLOCKER / HIGH residuals

1. **No Unsloth recipient binding, provider receipt ID, read-only reconciliation
   or chat wakeup.** There can be no proof of DELIVERED.
2. **No production sender or startup orchestration:** the manager currently
   never calls prepare/claim/quarantine. Operational recovery is not yet wired.
3. **No full canonical 17-state durable job model.** The actual AdmissionStore
   currently refuses all outgoing outbox intents because it has no RESULT_READY.
4. Outbox does not verify payload digest against a manager-pinned immutable
   artifact; input SHA here is a *trusted call parameter*, not an attestation.
5. No Windows NTFS file broker; SQLite path is trusted admin configuration,
   not a strong multi-user Windows security boundary.
6. Quarantine in a multi-manager scenario requires leader/epoch fencing and
   verified provider quiescence. No automatic reissue can be implemented until
   those capabilities are proven.
7. Event history is not yet cryptographically linked to the manager's main
   journal; future transaction integration and recovery tests remain open.

**Review conditions before merge:** exact PR head must have successful Windows
and Ubuntu CI, run log with test totals and zero errors, and separate import/
package test after merge. A test-only failure must be fixed and documented.

**Beta verdict:** NO-GO, even if all isolated tests are green.
