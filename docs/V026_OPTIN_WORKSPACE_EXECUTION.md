# v0.26 — opt-in sealed workspace execution (development-only)

**Gate: NO-GO** for real Unsloth/Flint/Windows production usage. This
increment introduces an **experimental executable path**, not G1a/G1b approval.

## Operator configuration

The LocalMCP constructor accepts enable_workspace_jobs=False (default).
With an explicit True, configured workspace_projects and worker_model are
required. The existing CLI deliberately does NOT enable this switch.
No workspace start MCP tool is advertised by default.

Opt-in tools:

- minionone_start_workspace_job(job_id, expected_version, operation_id, model_id)
- minionone_workspace_result(job_id)
- minionone_accept_workspace_result(job_id, expected_version, result_digest, operation_id)
- minionone_cancel_workspace_job(job_id, expected_version, operation_id)

## Positive slice

1. Trusted parent reserves/seals input (TASK.md + manifest + bounded UTF-8
   documents) to the canonical JOB ID.
2. MCP Start checks principal/thread, CAS, configured model, fresh on-disk
   seal and the canonical SQLite snapshot.
3. The same canonical ID changes ACCEPTED -> QUEUED -> RESOURCE_CHECK.
   The explicit WORKSPACE job kind is added by an FK-verified v0.25 migration.
4. The bounded existing LM Studio document-worker path checks loaded worker
   model and RAM, then uses only job-scoped SQLite documents. No worker host
   paths or unrestricted tools.
5. The manager re-verifies the input seal and publishes bounded immutable
   output via the existing Windows/POSIX publication journal.
   RESULT_READY occurs only after publication. Publication uncertainty
   triggers NEEDS_RECONCILIATION.
6. An authenticated parent fetches result digests and sends an explicit
   CAS acknowledgement. Only then can the job be COMPLETED.

## Explicit limitations

- The actual project work/ directory is not exposed to the model.
  create_work is an immutable SQLite document operation. Native Win32
  CREATE-ONLY worker work handles are still NOT integrated.
- Only 1–4 small additional UTF-8 input documents are supported.
  Binary project files and patches are rejected.
- Parent-model residency and true Unsloth thread identity are NOT attested.
- No auto wakeup: use polling and explicit ACK.
- The worker feature requires explicit operator opt-in and is NOT
  enabled by the existing CLI. Do NOT claim beta or release readiness.
- Recovery after ambiguous publication is quarantined; a reviewed
  operator reconciliation path is still required.

## Reproducible test commands

python -m unittest discover -s tests -p test_workspace_dispatch_v026.py -v

python -m unittest discover -s tests -v

The dedicated test uses actual MCP HTTP, canonical SQLite CAS, bounded
document broker and native OS output publication, with synthetic provider
responses. It is NOT real Flint + Unsloth target-host proof.
The intentionally-red product acceptance cases remain a separate gate.
