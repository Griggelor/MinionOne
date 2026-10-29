# G2 Attempt Fencing: independent-role code and security review

**Date:** 2026-10-29
**Change:** PR #15, attempt_fence.py + test_attempt_fence.py
**Reviewer independence:** separate critical review pass by the same
development assistant. NOT an independent external human security audit.
**Scope verdict:** PASS for an unexposed non-executing reservation primitive;
FAIL / NO-GO for actual worker execution and beta.

## Reconstructed actual path and owner

- The existing canonical jobs table remains the only owner of job state;
  attempt_fences is a separate one-row-per-job reservation table.
- The only permitted reserve source is a manager-owned QUEUED job with
  exact state version. This is checked inside BEGIN IMMEDIATE before INSERT.
- Current real AdmissionStore never creates QUEUED jobs (CHECK constrains
  state to ACCEPTED/CANCEL_REQUESTED/CANCELLED). A production-path test
  confirms the pilot cannot reserve a worker attempt.
- The first reserve creates a random 32-byte token, returns it only once,
  and persists only SHA-256. A duplicate command returns receipt metadata
  without the token; a changed request cannot mint another authority.
- is_current checks the token digest with hmac.compare_digest, plus epoch,
  canonical state and matching version. The check is NOT authorization to
  use a file broker, call LM Studio or claim an integration event.
- A restart recovery call or manager revoke permanently marks RESERVED
  as FENCED, with no automatic reissue/dispatch capability.

## Independent-role adversarial challenges

1. Wrong job, old/new epoch, invalid/forged token and bool version fail.
2. Different operation keys and changed state versions never grant a new
   capability, even if the original has been fenced.
3. Parallel BEGIN IMMEDIATE reservations yield a single first capability.
4. A canonical job state or version change invalidates issued tokens.
5. Crash quarantine is idempotent and never claims provider termination.
6. Original capability does not appear in status or database records.
7. Quota is durable; same-key replay does not create extra authorization.
8. Against real public AdmissionStore, ACCEPTED state is ineligible.
9. No launch, complete, publish or resume methods are implemented.
10. Test fixtures explicitly close SQLite connections for Windows NTFS.

## CI evidence

At commit ff099c2c6d465dcc1b8d0b86ab56a65ab3b1ca11:
- GitHub Actions https://github.com/Griggelor/MinionOne/actions/runs/40923401237
- Windows-latest: **90/90 PASS** in 21.670s.
- Ubuntu-24.04: **90/90 PASS** in 13.562s.
- Official SDK and Win32 feasibility workflows also PASS at same commit.
- A later documentation-only commit has to be checked separately.

## Security limitations and required next gates

BLOCKER: This feature does NOT implement worker lease authorization,
provider cancellation/quiescence, live chat identity attestation or the
Windows handle-bound file broker. An internal reservation token is not
proof that any file path is safe or that a chat is authorized.

BLOCKER: No future multi-attempt lifecycle is implemented. The epoch is
deliberately always 1; replay after recovery never reissues a token.
A future attempt 2 MUST use verified checkpoint, previous worker termination,
new epoch, manager CAS and approved recovery plan; do not merely loosen
the database CHECK or bypass the original FENCED state.

HIGH: SQLite database path is trusted operator configuration and needs
Windows ACL isolation; processes with filesystem access to the DB may
tamper state. Storing only hashed tokens prevents plaintext recovery but
is not a multi-user trust boundary.

HIGH: is_current only checks current queued version, so a transition to
RUNNING intentionally invalidates the reservation. Do NOT use it as a
perpetual worker capability. The future runtime must atomically promote
and rotate/fence capabilities when the verified worker launches.

HIGH: Startup does not automatically call fence_unreconciled, as there is
no runtime orchestrator yet. The future manager MUST quarantine old
reservations before attaching provider/file handlers.

MEDIUM: Although SHA-256 hashing is appropriate for random 256-bit tokens,
there is no key rotation, audit hash-chain linkage or on-disk encryption.

**Recommendation:** merge only with Windows/Linux CI PASS for exact PR head.
Do not expose AttemptFenceStore through MCP. Beta release remains NO-GO.
