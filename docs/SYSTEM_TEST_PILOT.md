# v0.18 — executable full-job system-test pilot

Owner goal: a runnable test build for Unsloth → loaded Flint/LM Studio → job
documents → returned result. Development follows implementation/tests/CI/merge,
without a separate review stage. This document defines this test build's actual
capabilities; it does not declare the original native-filesystem product complete.

## Complete supported test flow

`minionone_submit_test_job` commits a new opaque job ID, sealed task and up to
four small UTF-8 input documents. `minionone_run_test_job` stores a start receipt
and active capability before starting one background worker. Parent tools use
bounded `minionone_wait_test_job`, ordinary status, `minionone_test_result` and
`minionone_accept_test_result`. A matching authorized ACK is required for
`COMPLETED`; a model finishing only makes verified files `RESULT_READY`.

The Windows executable and offline-wheel fallback run the same package. The
operator self-test independently checks the expected uppercase document, hashes
and completed state. It is a real configured-provider call on the user's machine.
Windows CI substitutes a deterministic HTTP model server and labels that scope;
it also runs the actual frozen executable, spawned network process, DPAPI
credential, second-instance rejection and ZIP export.

## File-backend decision

For this first complete test, worker files are **SQLite documents**, not native
host files. The six worker functions are list_files, read_file, create_work,
publish_artifact, report_progress and ask_question. Arguments are allowlisted,
bounded and interpreted as names in the current job namespace only.

| Zone | Rights |
|---|---|
| input | Sealed manager-created UTF-8 documents, read-only to worker |
| work | Read and create-only; case-insensitive flat ASCII names |
| output | No direct worker access; publisher snapshots nominated work |

The broker never converts worker names into OS paths or shell/network calls.
SQL statements are fixed and parameterized. Capability + RUNNING state are
checked in the same write transaction as each broker action. Duplicate names,
traversal, drive/ADS syntax, output access and stale capabilities are denied.
Immutable SQL triggers protect file rows, task and configured model identity.
Per-job limits are 32 documents / 128 KiB, with small per-document/tool budgets.
The manager verifies sealed input and file/result digests before use/delivery.

An explicit local operator export validates each document/digest and creates a
new ZIP with safe member names. It does not overwrite an existing output, run
model text or write into project files. This backend therefore does not require
granting a worker native Windows file rights. **It does not mark G1b NTFS-handle
confinement implemented**; native project-folder import remains a separate backend.

## State and transaction ownership

AdmissionStore owns the canonical jobs table and `_transition` boundary.
The v0.18 migration rebuilds known v0.10/v0.13/v0.17 schemas transactionally,
preserving existing child rows and validating foreign keys. NON_EXECUTING jobs
still enforce worker_started=0 in SQL. Explicit ISOLATED_TEST jobs allow a worker
start and RESULT_READY/COMPLETED/FAILED/WAITING_FOR_PARENT states. The default
connection-only server exposes no new worker start tools.

Pilot metadata stores capabilities, budgets, receipts, documents and diagnostics;
it is not a second job-state table. Canonical transitions and their event rows
commit together. Job/timeout/test status is read from one SQLite snapshot.
Successful command replay returns its original receipt and does not relaunch.
Canceled/finished jobs revoke document capabilities. Terminal results cannot be
replaced and ACK checks the delivered verified digest plus expected version.

One process lock is acquired before pilot recovery/listener binding, and one
partial unique SQL index permits only one active test attempt. A second active
pilot cannot fence or recover the first instance. Standard operator state is
separate from the older connection-pilot database; back up any explicitly reused
older database while stopped before migration.

## Provider execution, limits and recovery

The exact configured model must be reported `loaded` by GET /api/v0/models.
The operator loads it beforehand. No load/unload endpoints are available to this
client. A minimum available-host-memory check runs first; neither this nor a
loaded-model response proves GPU UMA calibration or parent residency.

The existing finite AgentLoop enforces turn/tool/token/denial/wall budgets.
Document transactions use bounded SQLite waits. The provider's HTTP operation
runs in a disposable spawned **network-client** process, with a parent-enforced
absolute deadline and termination/join. That proves the client stopped, not the
LM server. A timeout/uncertain provider result persists NEEDS_RECONCILIATION,
revokes authority and blocks another model generation. Startup quarantines an
unfinished running attempt; it never blindly replays the provider request.

Only the offline local operator recovery command can confirm a stopped provider
and close quarantine as FAILED. It requires the server to be stopped and an
explicit --confirm-provider-stopped flag after actually stopping generation in
LM Studio. It is not an untrusted MCP tool. A new job is then a new operation.

ask_question produces WAITING_FOR_PARENT and exposes a scoped question. This
test build can cancel that job; answering/resuming it with a new attempt is
not implemented. Model-only completion without a published file is a failure.
Provider accounting uses reported token totals; actual thinking/resource behavior
is part of the target test, not assumed from a synthetic fixture.

## Identity, UI and secrets

Parent authority comes from the configured operator bearer over literal
127.0.0.1 with Host/Origin checks. This is not attested Unsloth per-chat identity.
Results return through ordinary tool replies and bounded waiting in the current
chat, not an invented asynchronous wakeup API. The target test verifies the real
installed Unsloth HTTP-MCP/header support.

On Windows the locally saved token is DPAPI-protected for the user account;
Linux fallback state uses private directory/file permissions. Connection output
reveals the token only with the explicit local --show-token helper. Optional
LM Studio credentials are environment/hidden-console input and are not saved.
Reports contain codes, version/build, counters and hashes, not tokens, raw model
responses, chain-of-thought, private paths or hostnames.

## Remaining production scope

This build is ready to exercise complete bounded test-document jobs. It is not
a production beta, native NTFS worker, general binary-document importer, arbitrary
code runner, automatic project merger, true multi-attempt Resume, attested
original-chat callback or proven target UMA controller. Actual user-machine
LM Studio/Flint/Unsloth compatibility is established by running the supplied
system-test steps. No license choice or public release tag is made here.

Primary interface references: LM Studio REST v0 model state and OpenAI-compatible
tool use; Microsoft GlobalMemoryStatusEx and CryptProtectData; PyInstaller 6.16
freeze_support documentation. These interfaces are used as documented, not as
evidence that the target integrations have already been run.
