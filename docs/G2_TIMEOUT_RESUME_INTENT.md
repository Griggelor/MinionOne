# G2 timeout state and resume request

This component is a non-executing pilot. A timeout is represented
by canonical state INTERRUPTED with reason code TIMEOUT.
If the former worker has not been verified as quiescent,
the state must be NEEDS_RECONCILIATION instead.

A trusted manager records a timeout under SQLite BEGIN IMMEDIATE
only for an expected-version RUNNING job with a fenced attempt
at the matching epoch. Canonical state, version, audit event and
timeout metadata commit atomically. Restart preserves the record.
The checkpoint digest is stored privately, never returned to a client.

The MCP job status includes a timeout section when relevant:
error_code TIMEOUT; resume_available; resume_requested;
resume_started false. The worker_running value is false only if
quiescence was manager-attested; it is null (unknown) while
NEEDS_RECONCILIATION. Neither status can invent provider termination. Resume is available
only for an interrupted, quiescent attempt with a checkpoint.

The minionone_resume_request tool records a parent-authorized
idempotent request with CAS and a durable journal event.
It does NOT launch or restart a worker; canonical state remains
INTERRUPTED while the request is pending. The existing abort tool
also supports an INTERRUPTED job, so the parent can cancel.

An actual transition INTERRUPTED -> RESUMING requires a newly
verified checkpoint, a new fenced attempt epoch, separate budget
approval, confirmed termination of the previous provider and
safe Windows file broker operations. None is implemented here.
The current static MCP credential is not proof of Unsloth
original-chat ownership.

Known old job-table schemas are migrated transactionally; the
worker_started=0 database constraint remains. All RUNNING jobs
in the tests are synthetic fixtures, never actual workers.

Beta and live autonomous resume remain NO-GO.
