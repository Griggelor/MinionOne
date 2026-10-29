# G2 timeout/resume-request second-pass security review

Scope: PR #27, a manager-only runtime-control slice plus one parent-facing
MCP request-intent tool. Reviewer is the same development assistant acting
as a separate critical reviewer, NOT an independent external auditor.
Beta remains NO-GO.

Canonical state and reason are distinct:
- INTERRUPTED + reason TIMEOUT is valid only after a trusted manager
  observes the expected RUNNING job version and matching fenced attempt.
- If external worker quiescence is not confirmed, the stored state must be
  NEEDS_RECONCILIATION, and Resume is unavailable.
- A missing trusted checkpoint also disables Resume.
- Read-time availability checks the attempt fence again; old capabilities
  cannot become active while the UI still advertises safe resumption.

State, version, timeout receipt and event are committed under one SQLite
BEGIN IMMEDIATE transaction. The parent can request continuation using a
preconfigured operator identity and optimistic expected version; this is
audited, idempotent, single-winner, and survives a manager restart.
Crucially it leaves the canonical state INTERRUPTED, with
resume_requested=true and resume_started=false; no worker start is possible.
The parent may cancel instead of resuming.

Known old v0.10 and v0.13 SQLite constrained job schemas are migrated in
place with foreign key verification and unchanged worker_started=0 check.
The real public MCP tool inventory adds a request-only tool; official SDK
tests must check it. Input/work/output file broker rights remain absent.

Adversarial cases include wrong identity, stale version, duplicate keys,
concurrent requests, unknown checkpoint, uncertain provider termination,
fence reopened and synthetic schema migration with populated child events.

Remaining critical blockers:
1. No real provider-worker quiescence attestation or process fencing;
   the present quiescence boolean is passed by trusted synthetic manager
   test code, not measured on the target OS.
2. No attempt epoch >=2, durable relaunch, preflight checkpoint validation,
   token/runtime budget carry-over or safe Windows NTFS file broker.
3. No attested real Unsloth original-chat identity or callback; the current
   static bearer is a pre-alpha operator credential.
4. No automated notification delivery on timeout or user-facing graphical
   Resume control; MCP exposes the status and request operation.
5. No external security audit or real long-running agent E2E.

The scope may pass only when exact-head Windows/Linux, official MCP SDK,
and Win32 feasibility workflows succeed. No live worker/beta GO.
