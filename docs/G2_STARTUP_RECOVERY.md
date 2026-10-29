# G2: fail-closed startup recovery for the actual MCP listener

**Scope:** PR #16 on top of reviewed PR #15. This integrates already-tested
manager-internal outbox and attempt fencing into the real LocalMCP constructor,
not a standalone simulation. Worker/file/provider operations remain disabled.

## Required startup order

1. Validate localhost bind request and operator token, using existing
   validated configuration. Initialize the canonical AdmissionStore.
2. Open or create manager-private OutboxStore and AttemptFenceStore tables
   in the same SQLite database, with foreign keys and durable WAL writes.
3. BEFORE ThreadingHTTPServer binds a socket, quarantine ALL unresolved
   outbox IN_FLIGHT records to NEEDS_RECONCILIATION.
4. BEFORE socket binding, fence ALL attempt RESERVED records to FENCED.
5. Only on success initialize safe read-only LM Studio probe, bounded
   threading semaphore and HTTP listener. If any recovery stage throws,
   constructor propagates the error. The MCP server cannot start.
6. The recovery_summary attribute is manager-internal; it contains counts
   only, never raw prompts, credentials, file contents or capability digests.
   The MCP status tool remains honest: worker execution OFF, release NO_GO.

## Actual production path test matrix

- Real LocalMCP with a real AdmissionStore SQLite database.
- Seed synthetic uncertain records in both tables using a trusted
  test-only SQLite fixture; current production pilot itself cannot
  create IN_FLIGHT outbox or RESERVED attempts.
- Server constructor must quarantine both before binding, and subsequent
  restarts must be idempotent: no redelivery and no capability reissue.
- A clean new database starts with zero quarantined entries.
- Inject simulated OutboxStore recovery failure and AttemptFenceStore
  recovery failure separately; both must reject startup.
- Spy on ThreadingHTTPServer.__init__ to prove recovery failure occurs
  BEFORE socket binding, not merely after a partial start.
- Run the whole suite on both Windows and Ubuntu, then real official MCP
  SDK client integration and wheel packaging CI.

## Residual and future guarantees NOT established

- No single atomic transaction combines the two quarantine updates.
  This is intentionally safe under a crash: partial progress persists,
  but no listener exists until both succeed on a later restart.
- This does NOT verify provider termination; an abandoned worker process
  can still exist. Even a FENCED attempt needs broker-enforced per-call
  capability checking and independent provider quiescence to be safe.
- No actual Unsloth parent/chat attestation or callback, no secure Win32
  handle-root file broker, no model resource admission and no worker dispatch.
- No exit-time termination protocol or multi-process leader election.
  A future deployment must fence every inactive manager instance and
  prevent two active processes from sharing a database without fencing.
- DB path remains trusted local configuration; secure Windows ACLs and
  hostile same-user process attack resistance have NOT been proven.

**Release decision:** PASS only for the internal startup-recovery integration
when exact-head CI is green. Full G1a/G1b/G1c/G2/G3 and beta remain NO_GO.
