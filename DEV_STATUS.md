# Development status — 2026-10-29

**Version:** 0.14.0.dev0  
**Channel:** Public PRE-ALPHA — not a beta, release **NO-GO**.

## Ongoing error-handling review (mandatory before Beta)

- [Issue #29](https://github.com/Griggelor/MinionOne/issues/29): comprehensive end-to-end error handling with understandable messages for API failures, incorrect credentials/passwords, connection failures, timeouts, SQLite/file permissions, provider OOM, worker failures and parent delivery/ACK.
- [PR #30](https://github.com/Griggelor/MinionOne/pull/30): first code slice introduces fixed safe `code`, `message`, `action`, `retryable` for local provider errors and MCP authorization, plus CLI fault output. Tests must prove passwords, API keys, paths and raw provider responses never leak.
- **Still open:** durable per-job error events, tool/broker and filesystem failure mapping, original-chat UI/wakeup, checkpoint-resume error status, external LM Studio/Unsloth fault injection and independent security review. The PR does not turn on real worker actions. Beta NO-GO.

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
6. Synthetic *real-provider* end-to-end and independent external security review (Issues #7, #9).
7. Approved license and provenance analysis before open-source distribution (Issue #8).

**None of these gates is waived by green mock-based tests.** The downloadable GitHub source archive is a development snapshot, not a beta installer.
