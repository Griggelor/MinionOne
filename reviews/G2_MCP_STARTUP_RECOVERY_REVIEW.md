# G2 MCP startup recovery — second-pass security / architecture review

**PR:** #16 (on top of PR #15). Reviewer role is a separate critical
pass by the SAME development assistant, NOT an independent third-party audit.
**Decision:** limited integration PASS, full worker/beta release NO-GO.

## Verified production path and test-first evidence

Actual MCP server constructor now first creates canonical AdmissionStore
then opens the same SQLite manager DB through the OutboxStore and
AttemptFenceStore, invokes quarantine_in_flight and fence_unreconciled,
and ONLY THEN constructs its HTTP listening socket. The existing six
MCP tools do not expose the outbox, attempt reservation or worker dispatch.

A standalone test file first committed at 4f000cc... was evaluated on
both GitHub operating systems and intentionally failed: five new startup
recovery tests exposed the unimplemented connection; the red CI run is
https://github.com/Griggelor/MinionOne/actions/runs/40924081134
(95 total tests, five errors on each OS). This is actual red/green evidence
rather than simply inferring tests would have failed.

The production change at b89d546... wired pre-bind recovery and a
manager-private counts-only recovery_summary. GitHub Actions run
https://github.com/Griggelor/MinionOne/actions/runs/40924172291
confirmed Windows **95/95 PASS (36.990s)**, Ubuntu **95/95 PASS (13.081s)**.
An additional fault injection proves failed recovery cannot even invoke
the HTTP base-server constructor; that test belongs to a later commit,
which requires its OWN green CI evidence before merge.

## Negative / recovery scenarios

- In-flight outbox message is marked NEEDS_RECONCILIATION, never delivered.
- Reserved attempt becomes FENCED, never reissued after restart.
- Second restart does not re-quarantine or increment attempt counters.
- Clean startup reports zero quarantined events.
- Database failure at either recovery stage denies server startup.
- Socket binding cannot occur before both recovery stages complete.
- No provider invocation or worker tool exposure is introduced.

## Architecture limitations

CRITICAL: A separate process could be using the same SQLite database
while a second instance starts and fences its attempts. No cross-process
leader election, Windows named mutex, stable process ownership or provider
quiescence exists. This API MUST NOT be considered a complete distributed
manager. Deploy as a single process with worker execution disabled.

CRITICAL: The recovery updates are two consecutive durable SQLite
transactions, not one single cross-table transaction. A crash between
them leaves partial quarantine; since no socket has bound and both
operations are idempotent, a restart is conservative, but future real
worker/provider execution still requires reconciled atomic state.

HIGH: On-disk SQLite ACL isolation, tamper-evident main audit journal,
true Unsloth original-chat authorization and NTFS handle-bound broker
are all unproven; GitHub runner tests cannot supply those guarantees.

HIGH: Fencing a manager-owned capability is not proof that a previously
launched provider call ceased; there are no real worker calls in this
pilot. Do NOT promote to RUNNING or COMPLETED from this evidence.

**Beta verdict NO-GO.** If exact-head Windows/Linux and official MCP
SDK CI pass, merge the narrow startup recovery change; keep all worker
and file tools disabled.
