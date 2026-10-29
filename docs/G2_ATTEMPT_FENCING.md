# G2 — one-shot attempt reservation and fencing (internal-only slice)

**Phase:** Experimental; NOT a dispatcher. This module does not launch LM
Studio/Flint, grant filesystem operations, access projects or acknowledge
results. The existing MCP pilot still has worker_execution_enabled=false.

## Grounded trust boundary

- The canonical state owner is the existing manager SQLite jobs table.
  There is NO second job-state machine. The reservation ledger holds a
  separate fence record referencing an existing job_id.
- A reservation requires an existing QUEUED job at the exact expected state
  version. It is transacted under BEGIN IMMEDIATE, with lifetime quota.
  The ACTUAL public AdmissionStore creates only ACCEPTED jobs and
  therefore cannot trigger this experimental reservation.
- The first reservation returns a random 256-bit capability; only its SHA-256
  digest is stored. Replaying the same idempotency key never returns the
  capability again, including after restart. Using a different key or state
  version cannot mint a second capability.
- Manager revocation and recovery move RESERVED to permanently FENCED.
  No reissue, lease renewal, provider call, result completion, file operation
  or reconciliation-bypass API exists in this slice.
- A fence check also checks canonical job state/version. An outdated token
  cannot be made current by mutating the ledger receipt.

## Adversarial contract

- Concurrent reservation: exactly one first capability.
- Replayed command: status metadata only, no plaintext capability.
- Unknown job, altered operation, stale CAS, invalid token, wrong job/epoch.
- Changed canonical job status/version makes capability stale.
- Restart without secret means unrecoverable capability (fail closed).
- Crash recovery fences uncertain attempts and will not launch a retry.
- Persistent quota and denial of pilot ACCEPTED jobs.
- Tests explicitly close SQLite handles for Windows temp-file cleanup.

These are unit/security-invariant tests of SQLite behavior, NOT proof of
actual worker execution or sandbox enforcement.

## Architectural review and next work

A token that passes is_current is still NOT an authorization to read or
write a file, not an Unsloth parent identity, and not a provider lease.
The future manager must separately prove authenticated caller ownership,
trusted job/attempt scope, Win32 handle-bound broker enforcement, budgets,
provider quiescence, origin, and fencing at every tool boundary.

Deliberately limited to epoch 1 and one attempt per job. The future multi-
attempt retry design must require verified checkpoint, provider state and
new epoch, and must integrate with the canonical 17-state manager policy.
Do not add that feature by relaxing this one-shot constraint.

**Release status:** G2 partial, G1a/G1b/G1c/G3 blocked. Beta NO-GO.
