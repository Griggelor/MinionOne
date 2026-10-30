# MinionOne

**Independent MCP-based multi-agent job manager for Unsloth Studio.**

> **Public PRE-ALPHA · 0.22.0.dev0 · NOT A BETA · NOT PRODUCTION-READY**

**New v0.19 WIP:** opt-in operator-approved project workspace staging with a durable PREPARING job ID (no seal, no worker file access, no Windows NTFS sandbox). See [target workflow](docs/PRODUCT_TARGET_WORKFLOW.md) and [live status evidence](evidence/product-status/PRODUCT_STATUS.md).

MinionOne aims to delegate bounded tasks from a primary local assistant to specialist workers (initially via LM Studio), while keeping authorization, state, resource budgets, files and final integration under the trusted manager's control. It is **not** an official Unsloth project or an endorsed Unsloth extension.

**New in v0.15:** The local MCP pilot reports fixed, safe error codes and corrective actions rather than an undifferentiated "Operation denied or unavailable": incorrect MCP token, LM Studio API credentials, refused connection, timeout, unexpected HTTP status, stale job state and unavailable resume. A `retryable` hint NEVER authorizes blind retry of an ambiguous side effect. See [diagnostic taxonomy](docs/G2_ACTIONABLE_ERRORS.md) and [full release-gate issue #29](https://github.com/Griggelor/MinionOne/issues/29). The complete error handling and real-chat delivery are NOT finished.

**New in v0.16:** Valid, authorized abort/resume requests that are rejected now leave a
bounded, durable error event on the canonical job timeline. Errors survive
restart and are visible through job events and `latest_control_error` in job
status. Failed-command replay preserves its outcome; the error is historical
metadata, not a new job state. See [transaction and recovery contract](docs/ADR_G2_DURABLE_CONTROL_ERRORS.md).
Issue #29 remains open: provider/worker/storage-wide error journaling and real
Unsloth notification are not implemented by this slice.

**New in v0.17:** SQLite and local-state failures now have distinct fixed diagnostics
for busy/locked, corrupt, unavailable, full, read-only, I/O, permission,
schema/integrity and database-memory failures. Startup reports a redacted JSON
error and exits without claiming a listener started; default-state-directory
failures are handled too. See [storage diagnostic contract](docs/G2_STORAGE_DIAGNOSTICS.md).
Implementation proceeds with tests and CI, then merge; no separate review or
review-approval step is required by the owner. Worker execution remains disabled.

## Complete system-test pilot (v0.18)

An opt-in **real LM Studio job path** is now implemented: submit sealed test
text files, start a bounded worker, read/create/publish through six scoped
manager tools, fetch the immutable result and send an explicit parent ACK.
Only then does the canonical job become COMPLETED. All tools operate on an
isolated SQLite document namespace; no worker host-filesystem, shell or code
execution is exposed. The native Windows project-folder broker remains a
separate unimplemented feature, not a claimed property of this backend.

The Windows package contains a console executable and start/connection/system-test
launchers. No Python installation is needed for that package. The executable is
itself exercised in Windows CI, including spawned network clients, protected
credentials and an exported result ZIP, against a synthetic HTTP model server.
Use the [German system-test instructions](system_test/README_DE.md) for a real
Flint/LM Studio and Unsloth run on your machine. Actual target-model/Unsloth
interoperability is established by that target test, not by the CI fixture.

Run from source: `python -m minionone.pilot_cli serve --model EXACT_LOADED_MODEL_ID`.
Use `python -m minionone.pilot_cli connection --show-token` locally for MCP setup,
then `python -m minionone.pilot_cli self-test` in another terminal. The worker
model must already be loaded; MinionOne does not send load/unload commands.

The default `minionone.mcp_cli` remains a connection/admission-only server.
Complete jobs are explicitly enabled by the system-test launcher, with a
separate local state directory. This is a test build, not a production beta.

## New: opt-in local MCP transport pilot

A **real, authenticated Streamable HTTP MCP endpoint** is now implemented, using the
2025-06-18 JSON-response protocol on literal IPv4 loopback only. This is **NOT**
the full MinionOne multi-agent worker manager. Its tools permit redacted status,
read-only LM Studio model discovery, and persisted registration/status/events/
cancellation of *non-executing* jobs. The configured bearer token is not evidence
of an actual Unsloth chat identity.

- [**Download installable CI-tested wheel directly from GitHub**](docs/INSTALL_GITHUB_ARTIFACT_WINDOWS.md)
- [Windows install and Unsloth UI connection test](docs/WINDOWS_UNSLOTH_MCP_PILOT.md)
- [GitHub Actions: MCP transport tests on Windows and Linux](https://github.com/Griggelor/MinionOne/actions/workflows/admission-ci.yml)
- [GitHub Actions: installable wheel build](https://github.com/Griggelor/MinionOne/actions/workflows/pilot-wheel.yml)
- [Official MCP SDK interop (v1.30.0 / v2.3.0, Windows + Ubuntu)](docs/G1A_OFFICIAL_MCP_SDK_INTEROP.md)
- [Windows NTFS handle API feasibility — not a secure sandbox](docs/G1B_WINDOWS_API_FEASIBILITY.md)
- [G2 conservative delivery outbox — no network sending](docs/G2_OUTBOX_SLICE_ADR022.md)
- [G2 one-shot worker attempt fencing — no worker start](docs/G2_ATTEMPT_FENCING.md)
- [G2 recovery before MCP socket startup](docs/G2_STARTUP_RECOVERY.md)
- [G2 canonical pre-dispatch state and v0.10 DB migration](docs/G2_CANONICAL_PREFLIGHT_MIGRATION.md)
- [G2 durable timeout and safe resume-request pilot](docs/G2_TIMEOUT_RESUME_INTENT.md)
- [G2 safe actionable API/auth/connectivity errors (initial slice)](docs/G2_ACTIONABLE_ERRORS.md)
- [G2 bounded agent loops, security strikes, Thinking-token accounting](docs/G2_AGENT_LOOP_BUDGETS.md)
- [G2 ADR: optional one-to-four-hour job duration with per-tool deadlines](docs/ADR_G2_EXTENDED_WORKER_TIMEOUTS.md)
- [G2 local LM Studio tool-call planner — no file broker](docs/G2_LMSTUDIO_TOOL_PLANNER.md)
- [G1c parent-pinned UMA eligibility — no model loading](docs/G1C_RESOURCE_ELIGIBILITY.md)

The pilot now persists manager-only VALIDATING, QUEUED and RESOURCE_CHECK transitions, with an automatic atomic migration of v0.10 pilot databases. **Back up the stopped private database before upgrading**; this is not a real worker run. The pilot does not expose workers, file tools, a verified input seal, publication or wakeup.
**Community beta remains NO-GO.**


**New timeout/resume control-plane prototype:** A timed-out, manager-attested quiescent attempt becomes canonical `INTERRUPTED` with reason `TIMEOUT`. Uncertain termination becomes `NEEDS_RECONCILIATION` and cannot be resumed. The job-status MCP tool reports whether a checkpoint and fenced attempt make Resume eligible. `minionone_resume_request` accepts an authenticated idempotent **request only**; it does not start another worker or enter `RESUMING`. Real safe restart and original Unsloth identity remain blocked.

**New optional long-job policy (v0.13):** The trusted manager may explicitly select an extended profile with a 1-hour default and 4-hour ceiling. The original standard 15–20-minute profile remains. Individual LM Studio model requests still max at 300s, with stricter tool-specific limits such as read_file 30s. Model turns (200 maximum), tool requests (60 maximum), denial strikes and token limits do not increase. No live worker or writable file tool is enabled. See [runtime ADR](docs/ADR_G2_EXTENDED_WORKER_TIMEOUTS.md).

**New internal v0.12 agent planner:** A finite agent-loop coordinator supports 50 default/model-turns up to a hard cap of 200, a three-strike policy for repeated broker-denied tool requests, and distinct token/model/tool/wall-clock/no-progress limits. The default **standard** profile preserves the normative v0.6 ceilings of **60 tool calls and 1,200 seconds total runtime**. An explicitly trusted-manager-selected **extended** policy is now modeled with a **one-hour default and four-hour hard ceiling**, retaining the 60-call/200-turn limits and adding stricter per-tool timeouts (e.g. file reads at most 30 seconds). This policy is not yet exposed through MCP, is not a running worker, and cannot be requested by model text. The LM Studio tool-call adapter parses approved tool requests and sanitized tool results on localhost; it is not wired to a real worker/file broker. Candidate results are **AWAITING_REVIEW**, never automatically sent to the parent. Real file/worker tools remain disabled.

## What is implemented

- Installable **experimental** Python package in `src/minionone/`.
- `AdmissionStore`: SQLite-backed, persisted job admission with authenticated caller-scoped threads from **trusted local configuration**, idempotency receipts, transactional admission event recording, optimistic state versions, authenticated abort requests and safe cancellation for jobs never started.
- Positive, negative, adversarial, concurrency and restart tests.
- `LMStudioProbe`: strict literal-loopback read-only model discovery using documented `GET /v1/models`.
- `LMStudioCompletion`: tool-free, bounded local text inference using documented `POST /v1/chat/completions`; worker output is untrusted and is never executed or automatically integrated.
- Operator diagnostic CLI (`minionone` or `python -m minionone`) with explicit NO-GO status, read-only model probe and fixed synthetic smoke prompt; it never launches a worker, reads user projects or exposes model responses by default.
- New G2 manager pre-dispatch integration: [PR #19](https://github.com/Griggelor/MinionOne/pull/19), CI [#40946389035](https://github.com/Griggelor/MinionOne/actions/runs/40946389035) **124/124 tests per OS** at feature commit 5db5ecfd, including real MCP status/abort, legacy foreign-key migration and rollback. No worker starts.
- GitHub Actions Python 3.13 main-branch suite: [run #40925220168](https://github.com/Griggelor/MinionOne/actions/runs/40925220168) confirmed **109/109 tests on Windows and 109/109 on Ubuntu** at commit 5b11c77. The [package workflow](https://github.com/Griggelor/MinionOne/actions/workflows/pilot-wheel.yml) requires both OS test jobs before uploading a wheel. Separate [Win32 API experiments](https://github.com/Griggelor/MinionOne/actions/runs/40916892418) and [official MCP SDK interoperability](https://github.com/Griggelor/MinionOne/actions/runs/40917985128) also passed, but these do not establish target Unsloth or secure file-broker behavior.

**Not implemented:** real Unsloth UI integration evidence or parent/thread attestation or same-chat notification, LM Studio worker invocation, secure Windows filesystem broker, six executable worker tools, artifact publisher, complete canonical worker-state engine, production provider-fenced worker attempts, trusted host resource telemetry and full end-to-end job execution. This repository is not a working multi-agent automation system yet.

## Develop and test

Prerequisites: Python 3.11+; 3.13 is the CI target.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m minionone status
python -m minionone probe-lmstudio
# Optional future target test with a known local model only:
# python -m minionone smoke --model YOUR_LOADED_MODEL_ID
```

The current GitHub Actions coverage includes authenticated admission, pure lifecycle invariants, and real-socket MCP transport plus mocked local-provider contracts. It does not include target Unsloth or NTFS tests. A larger sanitized v0.8 reference/design source exists outside this repository: its legacy 123 tests are **not** represented as remote GitHub CI unless actually imported and rerun.

## Security model and gates

**No untrusted worker is allowed direct shell, network, generic file APIs, Git, other MCP capabilities, execution or project integration.** Required target folders:

- `input/`: sealed, verified, read-only worker input.
- `work/`: broker-scoped create-only worker output (no overwrite/delete).
- `output/`: manager-only immutable publication and redacted log.

These are **requirements**, not implemented Windows security guarantees. A fail-closed Windows NTFS handle broker, authenticated Unsloth MCP ownership and real LM Studio/Unsloth E2E evidence must be provided before a usable beta. Learn more in [Architecture and release gates](docs/ARCHITECTURE_AND_RELEASE_GATES.md).

## Project management

- [Roadmap and issues](https://github.com/Griggelor/MinionOne/issues)
- [CI results](https://github.com/Griggelor/MinionOne/actions)
- [Security disclosure](SECURITY.md)
- [Public GitHub source archive](https://github.com/Griggelor/MinionOne/archive/refs/heads/main.zip) — **source only, not an installer or beta**.

**License:** No software license has been approved yet; public visibility is not permission to redistribute. External source provenance and license choice are tracked in [Issue #8](https://github.com/Griggelor/MinionOne/issues/8).

**Release verdict:** NO-GO. Do not process sensitive real-world documents or assume secure sandboxing at this stage.


### New local-only v0.20 development capability

With `--project demo=<trusted-root>` enabled, Flash can reserve a job, write `input/TASK.md`, a flat `manifest.json` of SHA-256 digests and other declared inputs, and call `minionone_seal_workspace`. POSIX-only: creates canonical `ACCEPTED` with same job ID and manager-projected `output/job.log.jsonl`; **no Worker start**. Windows fails closed until secure native handles are verified. See `docs/V020_POSIX_SEAL_SCOPE.md`. Provider roadmap: `docs/MODEL_PROVIDER_ROADMAP.md`.

### v0.21 native Windows input broker (G1b development, not product-ready)

A native read-only handle-rooted `input/` broker prototype is tested on a Windows NTFS runner. There is **no Windows MCP seal or worker filesystem permission**, no work/ writer and no real Flint/Unsloth job. See `docs/V021_WINDOWS_READ_BROKER_SCOPE.md`. Target-product acceptance remains NO-GO.

### v0.22 Windows `work/` CREATE-ONLY proof (not enabled)

The isolated native `WindowsWorkCreateProbe` can create new flat files under a held NTFS `work/` directory handle without overwriting an existing name. Native Windows CI tests cover collisions, links, disallowed paths and basic quotas. **It is not wired to the MCP service or any worker**, and partial-file recovery, durable quotas, atomic publication and target-host ACL checks remain open. See `docs/V022_WINDOWS_WORK_CREATE_SCOPE.md`. Target product remains NO-GO.
