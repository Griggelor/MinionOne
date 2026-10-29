# G2 canonical pre-dispatch: second-pass architecture/security review

**PR #19** — same assistant acting in a separate reviewer role; **not** an
independent third-party penetration test. **Decision:** pass for manager-only
non-executing persistence and schema migration, **NO-GO** for real workers
and community beta.

## Reconstructed production path

AdmissionStore remains the canonical and ONLY persisted owner of job state.
The existing jobs table is migrated in place to permit six *pre-execution*
states: ACCEPTED, VALIDATING, QUEUED, RESOURCE_CHECK, CANCEL_REQUESTED,
CANCELLED. The database-level CHECK(worker_started=0) is retained.
There is NO reachable RUNNING, RESULT_READY or COMPLETED state.

- SQLite migration starts from a known historical v0.10 jobs schema, uses
  BEGIN IMMEDIATE and copies all columns, drops/replaces the constrained
  table, runs PRAGMA foreign_key_check before commit and re-enables FK
  enforcement. Unknown schema and orphan references are denied.
- Child tables admissions, controls, admission_events, attempt_fences and
  outbox_intents retain their FK target name and all old rows.
- Internal advance_pre_dispatch applies central lifecycle.check_transition
  INSIDE the same SQLite transaction as job state/version update,
  idempotency receipt and monotonic event sequence.
- Parent-authorized abort already exists; it now applies to all four
  nonexecuting states and dynamically allocates event sequence numbers.
- No new MCP tool, worker process, shell, network, file capability or
  output access is introduced. The public MCP pilot still authenticates
  one locally configured operator, not an actual Unsloth parent session.

## Red/green observations

1. [Initial red CI #40945652938](https://github.com/Griggelor/MinionOne/actions/runs/40945652938):
   test-first checks failed while the canonical manager lacked
   transition/migration methods.
2. [Initial code run #40945791768](https://github.com/Griggelor/MinionOne/actions/runs/40945791768)
   found a test-contract error: the test incorrectly rejected integer
   state-version zero, which is legitimately the first CAS version.
   The test was corrected to accept version 0 but reject a numeric state.
   This was **not** counted as a fixed production bug.
3. [Green Windows/Linux #40945965760](https://github.com/Griggelor/MinionOne/actions/runs/40945965760)
   at commit 58aff093: **123/123 Windows PASS (21.712s)**,
   **123/123 Ubuntu PASS (13.243s)**.
4. Additional real MCP status/abort regression was added later and requires
   exact-head Windows/Linux CI. Do not count it as proven by 123-test run.

## Critical review findings and limits

**BLOCKER** — No authenticated external chat-to-manager session identity.
The manager-only preflight method must never be exposed to untrusted
client/worker tools; running the method requires already-trusted manager
code. An actual authenticated parent/tool boundary remains unproven.

**BLOCKER** — Only six persisted states. The future 17-state durable manager
must add verified attempt lease, provider-checked quiescence, model resource
reservation, file broker, result digest, immutable publication, external
effects, integration ACK and completion without bypassing the canonical
lifecycle policy.

**HIGH** — Migration is a high-impact schema rewrite even though the
transaction can roll back. Require offline/single-manager startup, a
restorable backup and target-machine migration rehearsal before touching
real project data. Concurrent multi-process manager operation is unproven.

**HIGH** — SQLite foreign_key_check establishes relational integrity, not
on-disk tamper resistance, Windows ACL confinement or signed provenance.
All user project files remain outside this scope.

**HIGH** — AdmissionStore.advance_pre_dispatch is internal-only and not
independently authenticated. Do NOT invoke based on raw model prompts.
Even a QUEUED record is not evidence that a worker has executed or that
the input file was sealed.

**PASS for limited scope** only when exact-head required CI, SDK and
additional reviews finish green. **Beta remains NO-GO.**
