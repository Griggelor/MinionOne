# Development status — 2026-10-31

## v0.34 – Boxed tool gateway (logical boundary, NO OS execution sandbox)

- [PR #52](https://github.com/Griggelor/MinionOne/pull/52) merged into `main`, implementation HEAD `64eace18be7d91a653acf9568a131d300918d2bf`: **23/23 exact-commit GitHub Actions checks green** on Windows and Ubuntu, including native Work evidence, Windows full-system package, official MCP SDKs and full HTTP bridge.
- Untrusted LM Studio tool proposals now pass immutable trusted job-kind grants in AgentLoop **and** `BoxedToolGateway` before reaching any native/SQLite broker. Provider tool-schema exposure is driven by the same source-of-truth (6 legacy isolated / 11 actual Workspace tools).
- MCP `minionone_status` explicitly returns `worker_tool_boundary=MANAGER_BROKER_FILES_ONLY`, `boxed_execution_enabled=false`, `os_sandbox_attested=false`. No new executable tool, shell, arbitrary code, host filesystem or OS sandbox claim.
- Added failure tests with intentionally permissive brokers and forged tool requests, including repeated `execute_shell` and `run_tests` attempts. Full Windows CI revealed a tight synthetic HTTP deadline; the CI fixture now allows 6s for legitimate responses while keeping an intentional 9s timeout to verify quarantine.
- [ADR-034](docs/ADR_034_BOXED_TOOL_EXECUTION.md) specifies the future process-isolated tool runner with a separate Windows AppContainer/Job Object or VM design, sandbox admission tests, quotas, no-network default, durable receipts and target-host verification.
- Actual AppContainer/VM execution, crash-safe Work tool receipts, target-host OS isolation and real Unsloth/Flint same-chat confirmation remain **NO_GO for code execution and real data**.


## v0.33 – incomplete Work mutation quarantine, Windows operator self-test, product NO-GO

- [PR #51](https://github.com/Griggelor/MinionOne/pull/51) merged into `main`. Exact-head commit `dee4513476cc53f76645511884461e2801a9bff1`: **23/23 GitHub Actions checks passed** (Windows/Ubuntu). Native Work synthetic MCP/HTTP remains 9/9 on each OS.
- An interrupted `pending-*` Work write is quarantined before further mutations or `RESULT_READY`: remaining bytes are **preserved for reconciliation**, never silently deleted or published. Tool receipt uncertainty maps to `NEEDS_RECONCILIATION`; **not** a complete crash-replay/idempotency solution.
- Work inventory now limits 64 total filesystem entries across recursive subdirectories, preventing unbounded enumeration of empty directories. Internal output/log and pending names, and casefold-duplicate artifact destinations are blocked.
- Real workspace CLI `workspace-self-test` now verifies a **physical native Work file** with expected content, file type, single-link identity and SHA-256 before the output copy and ACK. A regression test removes Work after publishing and verifies **no ACK**.
- [Operator test procedure](docs/WINDOWS_NATIVE_WORK_SYSTEMTEST_V033.md); actual Unsloth/Flint/Windows target-host tests, adversarial ACL/races, safe Work journaling and original-chat wakeup remain unverified. **NO_GO for real data.**


## Development checkpoint — 2026-10-31 (v0.32 implementation, still NO-GO)

- PR #47 (v0.30), #48 (v0.31.1 requirements) and #49 (v0.31.2 POSIX Work) and #50 (v0.32 Win32 Work) are merged into `main`. The single current requirements entrypoint is `docs/REQUIREMENTS_CURRENT.md`.
- Both **Windows** and **Ubuntu** now pass all **9/9 scoped physical Work synthetic HTTP/MCP acceptance cases**. Win32 exact-head PR #50: **21/21 GitHub Actions checks passed**, including strict Work gate and Windows source/package CI. See `https://github.com/Griggelor/MinionOne/actions/runs/41243000929`.
- Windows implementation uses anchored, native relative NT handles; implementation and tests are *synthetic development evidence*, not audited Windows target-host sandbox/ACL, race or crash-idempotency guarantees.
- Still **NOT VERIFIED**: real Unsloth/Flint integration and same-chat wakeup, actual customer host, OS adversarial race/ACL, safe multi-attempt resume, advanced fault recovery, guided setup and full release gates. Customer beta and real data remain **NO-GO**.
- Installed Python package still identifies as `0.25.0.dev0` by design of existing wheel tests; **v0.32 refers to the implementation milestone, not a published versioned release**. Coordinate package/version markers before the next Windows system-test distribution.


**Version:** 0.25.0.dev0 (GitHub development; TARGET_PRODUCT NO-GO)
**Channel:** Public PRE-ALPHA — not a beta, release **NO-GO**.

## v0.18.0.dev0 — complete test-document job path

- Canonical path now supports an opt-in full test job using real LM Studio HTTP tool calls and the six scoped SQLite document operations. COMPLETED requires verified published files and authenticated parent ACK; default connection mode remains non-executing.
- The native Windows package is built and tested separately from the source suite. Frozen execution, spawned network client, DPAPI roundtrip, duplicate-instance guard and result export are tested before uploading a runnable artifact. See exact CI evidence, not just source-test counts.
- Local source suite: 246 tests after new full HTTP pipeline, timeout, broker, recovery, operator exercise, bundle and atomic-status cases. Executed result is recorded in evidence/full-system-pilot/TEST_RUN_SUMMARY.json.
- First complete-system backend deliberately uses manager-owned SQLite documents. It grants no native Windows project-folder rights and does not claim the NTFS broker gate. Job files can be returned/exported as verified ZIP data.
- Readiness requires an already-loaded configured worker and available host memory. Original Unsloth chat attestation/wakeup, GPU UMA/parent residency, native project import, general multi-attempt Resume and production release remain outside this test build. No review/approval stage is added.

## v0.17.0.dev0 — typed local-state/storage diagnostics

- v0.16 PR #32 is merged to main at `53e0bbb2333f357d3eb8a8f14963cbc2bb0fc255`.
- SQLite/OS numeric diagnostics now cross actual MCP and startup boundaries. Startup state-directory errors no longer escape as raw tracebacks; incompatible schema/reference faults are distinguished from invalid client input.
- Full local Linux/Python 3.12 clean-environment suite: **231/231 PASS**, 24.463s; 18 new storage tests. Exact evidence is in evidence/g2-storage-diagnostics/TEST_RUN_SUMMARY.json. Remote OS/SDK/wheel results require their own exact-head CI.
- Separate review/approval stages were removed at the owner's explicit instruction on 2026-10-30. Issue #9 is canceled, not represented as a completed audit. Historical review files do not impose current workflow gates.
- No new schema, retries, worker rights or autonomous Resume. Corrupt state is preserved; unavailable storage does not claim durable journaling. Issue #29 remains open; beta NO-GO.

## v0.16.0.dev0 — durable parent-control rejection journal

- Implemented the next scoped increment of issue #29: authorized ABORT/RESUME_REQUEST failures persist on the canonical timeline and project through events/status after restart.
- One AdmissionStore transaction owns command outcome, savepoint rollback and error persistence. No job-state reinterpretation, worker start or automatic retry.
- Local Linux/Python 3.12: **213/213 PASS**, including 20 new challenge tests. Exact commands, red baseline and durations are in evidence/g2-control-errors/TEST_RUN_SUMMARY.json. Windows/Linux remote CI is a separate checkpoint, not inferred from this local result.
- Error capacity is bounded at 128 distinct keys/job; accepted cancellation is not blocked by capacity. Store/commit failure reports ERROR_JOURNAL_UNAVAILABLE without claiming durability.
- Additive schema migration and stopped-process backup/restore guidance documented. Same-assistant critical review is not an independent external audit.
- G1a/G1b/G1c, live worker dispatch, actual multi-attempt Resume, full G2/G3 and real parent notifications remain open. **Issue #29 remains open; beta NO-GO.**

## v0.15.0.dev0 – first structured safe error diagnostics (NOT full error handling)

- [PR #30](https://github.com/Griggelor/MinionOne/pull/30) merged: typed provider auth, API status, connectivity, timeout and invalid-response failure codes with fixed `code/message/action/retryable` on the MCP tool, local probe CLI and LM Studio adapters. The real MCP 401 auth failure includes `MCP_AUTH_FAILED` without echoing credentials.
- Source exact-head [CI #40973935402](https://github.com/Griggelor/MinionOne/actions/runs/40973935402): **193/193 Windows and 193/193 Ubuntu PASS**, plus official MCP SDK and Win32 feasibility checks. Versioned wheel needs its own dual-OS clean-install workflow.
- Strict scope: no durable per-job fault journal, no real Unsloth error UI, no Win32 broker/worker fault translation, no external target fault injection. Full matrix tracked in [Issue #29](https://github.com/Griggelor/MinionOne/issues/29). **Beta NO-GO.**

## Ongoing error-handling implementation

- [Issue #29](https://github.com/Griggelor/MinionOne/issues/29): comprehensive end-to-end error handling with understandable messages for API failures, incorrect credentials/passwords, connection failures, timeouts, SQLite/file permissions, provider OOM, worker failures and parent delivery/ACK.
- [PR #30](https://github.com/Griggelor/MinionOne/pull/30): first code slice introduces fixed safe `code`, `message`, `action`, `retryable` for local provider errors and MCP authorization, plus CLI fault output. Tests must prove passwords, API keys, paths and raw provider responses never leak.
- **Still open:** durable per-job error events, tool/broker and filesystem failure mapping, original-chat UI/wakeup, checkpoint-resume error status, external LM Studio/Unsloth fault injection. The PR does not turn on real worker actions. Beta NO-GO.

## 0.14.0.dev0 – durable timeout and parent Resume request (non-executing)

- [PR #27](https://github.com/Griggelor/MinionOne/pull/27) merged: a canonical synthetic RUNNING job can be recorded as INTERRUPTED with reason TIMEOUT only after a matching old attempt is fenced and the manager attests provider quiescence. Otherwise NEEDS_RECONCILIATION reports unknown worker liveness and forbids Resume.
- Timeout reason/metadata, version and events survive SQLite restarts. Checkpoint digests stay manager-only. New MCP minionone_resume_request stores a CAS-protected, owner-scoped, idempotent **intent**, leaving state INTERRUPTED and resume_started=false. Parent may instead cancel the interrupted job.
- Historical 3/6-state job tables migrate with FK integrity validation; worker_started=0 remains SQL-enforced. The existing pilot still starts no worker.
- Exact functional PR head [CI #40970211921](https://github.com/Griggelor/MinionOne/actions/runs/40970211921): **181/181 Windows and 181/181 Ubuntu PASS**, SDK and API feasibility checks PASS. The v0.14 package must separately pass versioned CI and wheel installation.
- Missing: genuine provider quiescence proof, attempt epoch >=2, actual safe checkpoint restore, host process termination, live Unsloth original-chat ownership/wakeup and verified Win32 file broker. **Live Resume/Beta NO-GO**.

## 0.13.0.dev0 – extended duration contract (no live worker)

- PR #25: optional manager-selected 1–4h job duration; standard mode still 900s default/1200s max.
- Max 300s per model completion and stricter broker tool caps: list_files 10s, read_file 30s, create_work 60s, publish_artifact 120s, report_progress/ask_question 5s.
- No changes to 200 model turns, 60 tool calls, three security denials or token budget. Synthetic tests 168/168 per OS on [CI #40967495967](https://github.com/Griggelor/MinionOne/actions/runs/40967495967).
- ADR: [duration and deadline hierarchy](docs/ADR_G2_EXTENDED_WORKER_TIMEOUTS.md).
- No actual long-running worker, safe Windows file broker, durable budget persistence or Unsloth wakeup. **Beta NO-GO.**

## 0.12.0.dev0 bounded agent loop and LM Studio planner (PRE-ALPHA)

- [PR #21](https://github.com/Griggelor/MinionOne/pull/21) merged manager-only AgentLoop: default 50, hard 200 model turns; 3 denied-tool strikes then SECURITY_ABORT; finite overall/model/tool deadlines, token (reported Thinking included) and no-progress thresholds; model final -> AWAITING_REVIEW.
- [PR #23](https://github.com/Griggelor/MinionOne/pull/23) tightened the initial values against normative worker_policy v0.6: <=60 tool calls, <=1200s wall time, even with 200 model turns.
- [PR #22](https://github.com/Griggelor/MinionOne/pull/22) added LM Studio localhost tool-call message planner and synthetic multi-step conversation tests. Actual sandbox/worker calls remain disabled; no real live Flint, Unsloth wakeup or OS file authorizations.
- Source test evidence: PR21 [144/144 on each OS](https://github.com/Griggelor/MinionOne/actions/runs/40950294398), PR23 [145/145 on each OS](https://github.com/Griggelor/MinionOne/actions/runs/40951492562), PR22 [156/156 on each OS](https://github.com/Griggelor/MinionOne/actions/runs/40951411465). Combined main/version commit requires its own verified CI result; do not claim green in advance.
- Security reviews are in reviews/G2_AGENT_LOOP_SECURITY_REVIEW.md and PR #23/#22 review comments. These are separate passes by the same assistant, not independent third-party audits. **Beta NO-GO.**

## 0.11.0.dev0 canonical pre-dispatch milestone

- [PR #19](https://github.com/Griggelor/MinionOne/pull/19) merged at d135987: the **existing** canonical jobs table now persists VALIDATING, QUEUED and RESOURCE_CHECK with central lifecycle checks, versioned idempotent manager transitions and dynamic audit sequences. No parallel state owner added.
- The legacy v0.10 schema migration preserves existing admissions, controls, events, outbox and attempt-fence child relations. It checks foreign keys before commit and rolls back invalid legacy data. A production-path MCP test verifies status/abort correctly reflect the new states.
- GitHub CI [#40946389035](https://github.com/Griggelor/MinionOne/actions/runs/40946389035) at final feature commit 5db5ecfd: **124/124 Windows PASS; 124/124 Ubuntu PASS**. Official MCP SDK [#40946389029](https://github.com/Griggelor/MinionOne/actions/runs/40946389029) **4/4 PASS**; Win32 API feasibility [#40946389055](https://github.com/Griggelor/MinionOne/actions/runs/40946389055) PASS.
- No RUNNING, RESULT_READY or COMPLETED states are accessible via the persistent manager; SQLite still enforces worker_started=0. **No real worker execution, authenticated parent session, secure Windows file broker, output artifact integration or beta release.**
- Before applying schema changes to a pilot machine: stop all MinionOne processes and back up the private SQLite database (including WAL/SHM as appropriate using SQLite's backup API), then test first with synthetic data.

## 0.10.0.dev0 development checkpoint (not beta)

- [PR #15](https://github.com/Griggelor/MinionOne/pull/15): one-shot, manager-only worker attempt reservation and irrevocable epoch fencing; no provider start. Initial exact-head CI showed **90/90 per OS**.
- [PR #16](https://github.com/Griggelor/MinionOne/pull/16): actual MCP startup now quarantines uncertain outbox effects and fences reserved attempts **before socket binding**. Red/green production-path failure injection: [run #40924081134](https://github.com/Griggelor/MinionOne/actions/runs/40924081134) was red; subsequently **95/95 Windows and Linux PASS** at [run #40924172291](https://github.com/Griggelor/MinionOne/actions/runs/40924172291).
- [PR #17](https://github.com/Griggelor/MinionOne/pull/17): strict single-worker, parent-pinned UMA budget eligibility from synthetic trusted telemetry. No real resource measurement, reservation or model execution.
- Latest combined main-branch [CI #40925220168](https://github.com/Griggelor/MinionOne/actions/runs/40925220168) at commit 5b11c77: **109/109 on Windows** (24.604s) and **109/109 on Ubuntu** (13.023s).
- **Not proven:** actual Unsloth chat identity and wakeup; handle-bound Windows sandbox; live Flint worker with parent Rulith kept resident; immutable artifact publication; full multi-attempt recovery and independent third-party security review. The actual MCP pilot still exposes no worker/file tools. Full Beta remains **NO-GO**.

## Latest verified engineering evidence

The previous 37-test snapshot and 160-test private staging figures below are
historical; they are NOT the current GitHub code test totals.

- **G2 outbox:** [PR #12](https://github.com/Griggelor/MinionOne/pull/12)
  merged. Durable per-job intents, single-claim CAS and uncertainty quarantine;
  no send, delivery ACK, original-chat identity or worker rights. [77/77 per OS
  CI](https://github.com/Griggelor/MinionOne/actions/runs/40916174040).
- **G1b Windows API feasibility:** [PR #13](https://github.com/Griggelor/MinionOne/pull/13)
  merged. [6/6 real Win32/NTFS API tests](https://github.com/Griggelor/MinionOne/actions/runs/40916892418)
  on Windows build 26100; no production Windows file broker exists.
- **G1a official MCP SDK test:** [PR #14](https://github.com/Griggelor/MinionOne/pull/14)
  merged. [4/4 official SDK v1/v2 and Windows/Linux combinations](https://github.com/Griggelor/MinionOne/actions/runs/40917985128)
  passed. Real Unsloth Studio client, caller identity and same-chat wakeup
  are NOT verified.
- [GitHub Actions pilot wheel](https://github.com/Griggelor/MinionOne/actions/workflows/pilot-wheel.yml)
  is a temporary downloadable artifact after Windows and Linux test gates.
  It is NOT a beta or a signed production release.

**Still not implemented/verified:** handle-bound NTFS worker file broker,
complete persistent canonical state engine, provider-fenced worker attempts,
LM Studio/Flint real job execution, immutable artifact publication, target
resource/UMA admission, same-chat delivery and independent human review.
**Beta: NO-GO.** G0 full normative sanitized reference import also incomplete.

## New MCP-pilot implementation

- Production-path HTTP listener at src/minionone/mcp_server.py using 2025-06-18
  Streamable HTTP JSON response mode. Binds only 127.0.0.1 and checks Host,
  Origin and an operator-only secret before reading request bodies.
- Six deliberately narrow tools: gate status; read-only local model discovery;
  admission, state, redacted events and abort for jobs that NEVER execute workers.
- Admission quota defaults to 256 **lifetime jobs per configured principal**;
  transactional existing-key replay is allowed even at quota.
- A partial request timeout is enforced to avoid indefinitely blocked handlers.
- Installed module entrypoint: python -m minionone.mcp_cli; wheel created only
  by a successful CI build, with a SHA-256 checksum.
- Real Unsloth Studio interoperability, sender-chat attestation and Windows
  handle-based containment are **not tested**. This is a transport pilot, not beta.

**Verified CI evidence before latest version-consistency change:** GitHub
[dual-OS run 40903714400](https://github.com/Griggelor/MinionOne/actions/runs/40903714400)
at ed0e0f11b5d11ad9ee41df20e0a058a6f4f1b86a:
Ubuntu 24.04 **46/46 PASS**; Windows-latest **46/46 PASS**.
[Wheel build 40903714254](https://github.com/Griggelor/MinionOne/actions/runs/40903714254):
PASS, install smoke and SHA-256 artifact generated. A newer commit must
be checked independently; these numbers are commit-specific.

## Implemented in this public GitHub repository

- Editable/installable Python source package with `minionone` operator-only CLI (`status`, `probe-lmstudio`, `smoke`).
- AdmissionStore: SQLite persisted admission, thread-scoped configured bearer credentials, transactional idempotent receipt and optimistic state control for never-started jobs, limited preliminary event log.
- LMStudioProbe: strictly loopback GET `/v1/models`; limits URLs/redirects, response budgets, and malformed model IDs.
- LMStudioCompletion: bounded, **tool-free**, fixed-policy text generation via documented `POST /v1/chat/completions`. Never execute or auto-integrate model output.
- Explicit build metadata and a pinned Windows + Ubuntu test workflow.

## Reproducible test evidence

- Remote GitHub Actions [#40900180801](https://github.com/Griggelor/MinionOne/actions/runs/40900180801) at commit `c37645429f97820a8be320cb3925dd80dcceb41d`:
  - Python 3.13, Windows latest: **37 tests passed** (9.048 s).
  - Python 3.13, Ubuntu 24.04: **37 tests passed** (6.886 s).
- A **separate**, larger Linux staging tree with the sanitized 0.8 reference code plus new work:
  - `python -m unittest discover -s tests -q`: **160 tests passed**, on Linux with PYTHONPATH=src.
  - `python tools/validate_specs.py`: 50 FR, 16 NFR, 169 planned tests, eight validated fixtures.
  - `python scripts/check_git_manifest.py`: **97/97** tracked files; `python tools/verify_manifest.py`: **97/97** hashes.
- The 123 older specification/contract tests **are not yet imported or run in the public GitHub CI**. Do not add the 160 local count to remote CI claims.
- All provider/CLI smoke tests in CI are against **synthetic local HTTP mocks**, not real LM Studio, Flint or Unsloth.

## Missing mandatory beta gates

1. Complete sanitized normative sources/contracts/tests in GitHub and reproduce full CI on Windows/Linux (Issue #1).
2. Authenticated actual Unsloth MCP client/parent thread and true same-chat wakeup or owner-accepted fallback (Issue #2).
3. Verified real Windows NTFS handle-based file broker and adversarial external-process tests (Issue #3).
4. Real LM Studio/Flint/Rulith resource and residency measurements (Issue #4).
5. Full durable job state machine, attempt fencing, worker tool permissions, immutable artifact publication, recovery and outbox (Issues #5–6).
6. Synthetic *real-provider* end-to-end (Issue #7). Separate review gate #9 was canceled by owner instruction on 2026-10-30.
7. Approved license and provenance analysis before open-source distribution (Issue #8).

**None of these gates is waived by green mock-based tests.** The downloadable GitHub source archive is a development snapshot, not a beta installer.


## v0.20.0.dev0 – new working increment (not a product release)

- Actual POSIX MCP input seal, canonical job-ID admission, manager-pinned bytes and joblog projection: LOCAL only.
- No Windows filesystem-seal approval and no worker NTFS access; security `NO-GO` remains.
- T-082/T-102 local contract successful; T-163 worker-start contract still failing.
- Model backends from spec: LM Studio partially implemented; Unsloth-native and OpenAI cloud not implemented (`docs/MODEL_PROVIDER_ROADMAP.md`).

## v0.21.0.dev0 – GitHub-native Win32 read-only broker WIP

- 8/8 native Windows NTFS broker tests passed on GitHub Actions runner (plus 6 existing feasibility probes); no native runner test is a target-host acceptance.
- Windows MCP seal, worker read/write tools, CREATE-ONLY publication and full system-test remain blocked.
- Next: native `work/` CREATE-ONLY boundary, manager-only `output/`, mutation/fault injection on real target.

## v0.22.0.dev0 – Win32 native `work/` create-only proof

- Native NtCreateFile(FILE_CREATE) under pinned Windows `work/` handle, exclusive creation and bounded direct writes; no existing-file overwrite.
- 12 native Windows tests passed on GitHub runner (plus 1 POSIX-only skip), separate runner-level feasibility evidence.
- **Still blocked:** atomic publication, crash recovery, durable quotas/fencing, manager-only output, on-target ACL tests and real workspace worker execution. Product approval remains NO-GO.

## v0.24.0.dev0 – durable POSIX manager-only candidate publication

- Persistent SQLite receipt `PENDING`→`PUBLISHED`, exact idempotency, durable quotas and manager-pinned content.
- POSIX descriptor-relative `output/` publication, pending-file fsync, no-clobber atomic hardlink, replay on crash, directory locking and strict artifact hashes; 13 POSIX targeted tests.
- Windows manager output publisher, Flint execution, job start, actual target Windows ACL/fault evidence and parent ACK remain BLOCKED. Product target remains NO-GO.

## v0.24.0.dev0 – native Windows manager-only publication prototype

- SQLite journal shared with POSIX, native output directory handle anchoring, pending-file hash and atomic no-overwrite rename, replay after SQLite/NTFS interruption.
- Native tests seed ADMITTED status only in fixtures; Windows input seal remains fail-closed, worker filesystem permissions disabled. Real deployment G1b and E2E gates not met.

## v0.25.0.dev0 – native Windows manager input seal and canonical admission

- Same reserved JOB-ID admitted as ACCEPTED/version 1 under an immutable manager SQLite snapshot of validated Windows input bytes and hashes.
- Native held Windows file handles through DB transaction, SHA256 manifest validation, task/text/size rules, and admission-only manager log projection.
- Real MCP prepare/seal/verify contract tested on native Windows runner; no worker start, provider profiles, target-host ACL certification or Unsloth parent ACK. Product NO-GO.
