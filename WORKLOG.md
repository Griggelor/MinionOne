# MinionOne WORKLOG — 2026-10-29

## 2026-10-30 — full-system test target / v0.18.0.dev0

- User explicitly selected a complete Unsloth -> Flint -> test files -> returned-result test, rather than a connection-only package. Continue until a runnable Windows system-test artifact exists; no separate reviews.
- Base main: `3da7b5249a83db4602937b44ffa1763d92e7b326` (v0.17). Atomic status race reproduced with a red production-path test, then fixed with a shared read transaction. Existing suite plus that case passed 232/232.
- Implemented opt-in durable real-provider test jobs over a strictly scoped SQLite document backend. This is an explicit alternative to native host file access for the first system test; G1b NTFS rights remain ungranted. Full HTTP model/manager/result/ACK flow passed, with negative, timeout, duplicate-start, immutability, capability and restart cases.
- Added absolute HTTP deadlines via isolated network-client processes and uncertain-generation quarantine; stopping the client is never presented as provider quiescence. One process lock prevents competing startup recovery.
- Added native Windows launcher build with multiprocessing freeze_support, user-bound credential protection, guided setup, actual provider test, safe result export, reports and offline Python fallback. Frozen executable runs its own integration exercise in CI before packaging.
- Scope and user-machine procedure are documented in docs/SYSTEM_TEST_PILOT.md and system_test/README_DE.md. No target-device or actual Flint/Unsloth success is claimed until the user runs this package. Existing implementation/CI/merge workflow continues without review gates.


## 2026-10-30 — G2-E29-03 storage diagnostics / v0.17.0.dev0

- Intake main was still v0.15; PR #32 had no review comments. Owner then explicitly requested direct implementation without reviews. Removed that process gate, canceled issue #9 as not planned, and merged passing PR #32. New base: `53e0bbb2333f357d3eb8a8f14963cbc2bb0fc255` (tree `0f46bef64babb30566d94275dac1fa727da3a38c`).
- Wrote storage/startup/HTTP negative tests first. Final 18-test set also executed against untouched v0.16 source and failed as expected. Additional red case caught malformed attached diagnostic catalog keys before the guard was added.
- Implemented numeric SQLite/OS taxonomy, typed stored-schema/reference rejection, safe startup JSON/exit code, explicit-DB home independence, safe cause-specific journal actions and catalog reconstruction. No new job owner/state/store, no automatic retry or repair.
- A reused environment contained stale editable distribution metadata; the initial full suite failed only its installed-version check. A fresh isolated environment reports matching 0.17 versions and **231/231 tests PASS**, 24.463s. Preserve this distinction in evidence.
- Functional evidence includes real SQLite locking over HTTP, actual corrupt/cannot-open files, SQLite read-only and size-cap errors. Physical disk-full and Windows ACL cases are not claimed: errno cases are injected.
- Updated versioned artifact metadata and current process/gate documentation. No new review document/stage is created. Continue through exact-head OS/SDK/wheel CI and merge automatically when all pass.
- Next implementation: coherent combined job/timeout status projection, then persistent operator health and provider/worker outcome wiring at real canonical boundaries. Keep real target integration and worker file rights gated until implemented and demonstrated.


## 2026-10-30 — G2-E29-02 durable rejected controls / v0.16.0.dev0

- Source base rechecked against GitHub main: `49eb8804731dba58fd0ef69fda454fc4ceac6a92`; no open PRs at intake. Restored all 74 files and verified Git blob hashes. Handoff checksum verification: 140/140.
- Work uses attached handoff and GitHub only. The unrelated project-level PDF review file is outside this project scope.
- Reproduced missing durable RESUME_NOT_READY event through LocalMCP, then wrote production-path challenges before implementation. The final 20-test challenge set was rerun against an untouched baseline source snapshot and failed as expected.
- Added canonical transaction-bound control rejection metadata, fixed safe diagnostics, receipt replay, quota, authenticated projections, additive migration and rollback guidance. A commit-boundary test failed before the commit-error diagnostic fix and passed afterwards.
- Full local suite: `python -m unittest discover -s tests -v`, 213/213 PASS (19.668s). Python 3.12 / Linux; local editable venv install. Wheel smoke evidence is separate. Exact-head remote Windows/Linux/SDK/build evidence must be checked on the PR, not inferred.
- Versioned package/runtime/README/CI artifact at 0.16.0.dev0. Pilot-wheel workflow now also checks pull requests before merge.
- Critical self-review recorded in reviews/G2_DURABLE_CONTROL_ERRORS_REVIEW.md; this does not close the independent review gate.
- Next: typed storage/operator health, then durable provider/worker outcomes through the same canonical owner. Real Unsloth notifications, Windows broker and target resource/provider verification remain gated. No worker execution enabled, no license chosen, no beta approval.


**Project:** https://github.com/Griggelor/MinionOne
**Channel:** Public pre-alpha / MCP transport pilot. **Overall beta gate: NO-GO.**
**Normative basis:** handoff v0.7, architecture/security/test contracts v0.6.
Only source already cleared for public use is in this GitHub repository.

## This session, executed

- Confirmed GitHub has push permission, published authenticated localhost
  Streamable HTTP MCP pilot (JSON responses, 2025-06-18 protocol).
- Exposed six deliberately narrow tools for gate status, local LM Studio
  model discovery, non-executing job admission, status, redacted events,
  CAS cancellation. **No real worker tool or file access is enabled.**
- Test-first fixes: bounded AdmissionStore per-principal quota including
  idempotent replay; partial-HTTP-body timeout; eight active HTTP connection
  slots; serverInfo version synced with the installed package.
- Added on-wire HTTP tests on real loopback sockets and additional durable
  quota/restart tests. Deliberately red commits for each violation precede
  corresponding green fixes (see Git history).
- Added Windows/Unsloth pilot install guide; CI-gated wheel build with SHA256
  manifest and fresh-environment import smoke; second-pass security review.
- The target Windows/Unsloth/Rulith/LM Studio hardware is inaccessible to
  this execution environment. No target success was claimed.

## Verified evidence, pinned to commits

- Commit 5753985c4a8312c9052e1ec3acd0dab2b49f54e0:
  https://github.com/Griggelor/MinionOne/actions/runs/40904702751
  Ubuntu 24.04 47/47 PASS (12.607 s); Windows-latest 47/47 PASS (16.357 s).
- Same commit:
  https://github.com/Griggelor/MinionOne/actions/runs/40904702634
  installable wheel, clean import and SHA256 artifact: PASS.
- Further report-only commits require distinct CI status checks before
  their exact HEAD can be declared verified.
- Current public repo does **not** contain the complete 123-test normative
  v0.8 reference tree. Separate local staged runs of 160 tests are not
  represented as GitHub CI passes.

## Gate matrix

| Gate | Status | Next required proof |
|---|---|---|
| G0 spec baseline | PARTIAL | import sanitized v0.6 normative tree; run full Windows/Linux CI |
| G1a Unsloth | BLOCKED_TARGET | authenticated custom MCP, caller/thread attribution and same-chat callback |
| G1b Windows FS | NOT IMPLEMENTED/BLOCKED_TARGET | secure Win32 handle broker with external-user race adversarial suite |
| G1c LM Studio / Rulith | BLOCKED_TARGET | model ID, UMA headroom, no parent eviction, timeout/cancel |
| G2 runtime | PARTIAL | durable multi-attempt manager, sealed inputs, fencing, broker, publication, outbox |
| G3 end-to-end | BLOCKED | real safe synthetic Flint worker and parent acknowledgement |
| G6 community beta | NO-GO | all mandatory gates, external security review, license and provenance |

## Next three engineering issues

1. G1a: instrument the installed Unsloth Studio version's custom MCP
   configuration and live owner/thread/wakeup capabilities. Connection
   pilot is safe to try with synthetic data; **do not enable worker tools**.
2. G2: implement canonical durable multi-attempt state machine and
   Windows file broker under fail-closed switch; test untrusted worker
   adversarial cases and crash/recovery, independent role review.
3. G1c/G3: target hardware conservative resource tests and synthetic
   LM Studio/Flint integration, preserving parent_unload=forbid.

## Reproduction

    python -m pip install -e .
    python -m unittest discover -s tests -v
    python -m minionone status

For MCP only:
[Windows pilot guide](docs/WINDOWS_UNSLOTH_MCP_PILOT.md).
Stop the server with Ctrl+C. Do not use real customer data or shared network
interfaces in pilot mode.

## Independent review

[Second-pass MCP security review](reviews/MCP_PILOT_SECOND_PASS_2026-10-29.md).
Same assistant in a distinct adversarial role; third-party audit remains open.
Release judgement **FAIL for beta**, transport pilot only.


## Follow-on work: G2 guard integration and artifact gates (same day)

- PR #10 [lifecycle policy](https://github.com/Griggelor/MinionOne/pull/10)
  merged via ae60a926... after independent-role adversarial review. Includes
  immutable 17-state transition guard and strict evidence requirements for
  COMPLETED, CANCELLED, fencing, retry and provider reconciliation. The
  evidence itself must come from a future authenticated, durable manager.
- PR #11 [atomic policy enforcement](https://github.com/Griggelor/MinionOne/pull/11)
  merged via 1388eb569741... . The two existing real SQLite owner operations
  now call canonical lifecycle guard inside their transaction. Injection tests
  prove denied decisions leave state/version/events unchanged.
- CI after merge on commit 1388eb5697414399509f7dd71058548c7c79b1f3:
  [dual-OS tests #40906687884](https://github.com/Griggelor/MinionOne/actions/runs/40906687884):
  Windows-latest **64/64 PASS** and Ubuntu-24.04 **64/64 PASS**.
- [CI-gated wheel #40906687907](https://github.com/Griggelor/MinionOne/actions/runs/40906687907):
  required Windows+Ubuntu test jobs and dependent build-pilot all **PASS**.
  Includes wheel import smoke and SHA256SUMS.txt. This fixes a real CI gap in
  which artifact upload previously could happen despite an independent red
  Windows suite.
- Cross-OS negative socket test corrected to accept EOF on Linux OR
  ConnectionReset/ConnectionAborted on Windows for intentionally refused
  ninth client; CI confirmed both environments. Do not misrepresent the
  earlier Windows failure as production MCP defect.
- [Direct GitHub artifact installation guide](docs/INSTALL_GITHUB_ARTIFACT_WINDOWS.md).
  Existing exact build has 14-day artifact retention and is a **PILOT only**.
- Latest docs-only commits have to run their own CI. Do not infer PASS for
  a newer SHA solely from the prior commit.

### Still blocked for multi-agent Beta

Unsloth caller/chat identity attestation and same-chat wakeup; actual Win32
handle-based read-only/sealed input and create-only work broker under races;
real LM Studio/Flint worker execution with target resource admission; durable
multi-attempt dispatcher/fencing, outbox, explicit parent ACK and recovery;
independent human/third-party security audit, source provenance and approved
license. All release claims remain NO-GO until explicit evidence exists.


## Continued implementation milestone — 2026-10-29, G2/G1b/G1a

This record was added after merging the following reviewed feature branches.
All are incremental **pre-alpha components**; no multi-agent Beta released.

### PR #12 — conservative durable outbox (merged bfad84e833af6048670d96223c6f18b92f5ad5a9)

- [PR #12](https://github.com/Griggelor/MinionOne/pull/12) introduced
  manager-internal SQLite delivery intents with exact job/version eligibility,
  durable operation-key idempotency, bounded per-job quotas, single-winner
  dispatch claims and explicit quarantine of uncertain IN_FLIGHT effects.
- Does **not** send messages, trust provider ACKs, identify original chats,
  retry ambiguous side effects or expose tools to worker/parent.
- First red test commit 170db2be..., initial implementation exposed Windows
  sqlite3 fixture-handle failures (WinError 32), fixed by explicit closing.
- GitHub Actions [#40916174040](https://github.com/Griggelor/MinionOne/actions/runs/40916174040)
  at c278634a...: **77/77 Windows PASS**, **77/77 Ubuntu PASS**.
- Postmerge [package build #40916407802](https://github.com/Griggelor/MinionOne/actions/runs/40916407802):
  PASS, required Windows + Ubuntu jobs then installable wheel.
- [Outbox ADR and caveats](docs/G2_OUTBOX_SLICE_ADR022.md);
  [independent-role review](reviews/G2_OUTBOX_REVIEW_2026-10-29.md)
  (not a third-party review).

### PR #13 — real Windows API feasibility (merged 0e281ef2f1800eefadb5ae690b1827026b2eaee4)

- [PR #13](https://github.com/Griggelor/MinionOne/pull/13) contains six
  actual Win32 handle experiments; zero worker file rights enabled.
- [Windows run #40916892418](https://github.com/Griggelor/MinionOne/actions/runs/40916892418)
  at 0485f785...: **6/6 PASS, 0 skipped**, Windows build 26100,
  NTFS, filesystem flags 0x3E72EFF.
- Verified FileIdInfo identity, hardlink alias/NumberOfLinks, file/directory
  reparse flags, and CREATE_NEW refusing overwrite.
- **Does NOT** prove trusted root handle, handle-relative traversal, rename
  races, junction/mount attacks, hardlink containment, other Windows-user ACL
  isolation or target hardware. G1b remains **BLOCKED**.
- [G1b API threat analysis](docs/G1B_WINDOWS_API_FEASIBILITY.md);
  [review](reviews/G1B_WIN32_FEASIBILITY_REVIEW.md).

### PR #14 — official MCP SDK compatibility (merged 1b3173974cbd4752dd860d5e54b9d9de30753c1d)

- [PR #14](https://github.com/Griggelor/MinionOne/pull/14) uses the actual
  Python MCP SDK 1.30.0 and 2.3.0, both on Windows and Ubuntu, against a
  real localhost authenticated MinionOne server.
- [GitHub Actions #40917985128](https://github.com/Griggelor/MinionOne/actions/runs/40917985128)
  at 26937a1f...: **4/4 SDK/OS combinations PASS**.
- Real MCP initialize, tools/list and tools/call all succeed; unknown worker
  rights remain disabled. Earlier red run found v1/v2 CamelCase vs snake_case
  Python attribute differences in the test; corrected to canonical wire
  aliases via model_dump(by_alias=True).
- **Actual Unsloth Studio runtime, original-chat identity and same-chat
  callback/wakeup are STILL UNVERIFIED.** This proves standardized MCP
  transport interoperability, not parent authorization. G1a still BLOCKED.
- [SDK interoperability method and limits](docs/G1A_OFFICIAL_MCP_SDK_INTEROP.md).

### Ongoing release decision and critical blockers

The production Win32 handle-bound worker broker, complete durable 17-state
job engine, LM Studio/Flint resource-safe worker execution, actual Unsloth
parent/chat ownership and same-chat wakeup, manager-only immutable publication,
independently verified integration ACK/delivery, independent human security
review and public license/provenance audit are still incomplete.

**Beta verdict remains NO-GO.** The CI-built artifact is an installable
**local MCP-transport PILOT** for synthetic tests only, not a worker beta.

The mandatory complete sanitized v0.6 contract/reference tree is still not
fully imported into GitHub; G0 packaging/traceability remains PARTIAL.

## Milestone checkpoint — 0.10.0.dev0 (2026-10-29)

After PR #14, three additional reviewed changes are merged directly to main:

- PR #15: internal one-shot AttemptFenceStore, 256-bit capability only returned once, stored SHA-256 digest, durable single-winner CAS, fail-closed reboot fencing. Current public AdmissionStore remains ACCEPTED-only and cannot reserve a worker. Scope-only security review at reviews/G2_ATTEMPT_FENCING_SECURITY_REVIEW.md.
- PR #16: actual LocalMCP startup connects OutboxStore and AttemptFenceStore and quarantines uncertain side effects before HTTP socket bind. Unit/injection tests first went red on BOTH OSes (GitHub Actions 40924081134). After integration 95/95 Windows and Linux tests passed at 40924172291. Review: reviews/G2_MCP_STARTUP_RECOVERY_REVIEW.md.
- PR #17: pure parent-resident memory eligibility, strict telemetry consistency/freshness, no model load or memory reservation; 103/103 feature-branch CI per OS at run 40924809700. Review: reviews/G1C_RESOURCE_POLICY_REVIEW.md.

At merged functional commit 5b11c77ed82df9393141a6cba0dc22ca5a0ba82d:
- Core Windows CI 40925220168: 109/109 PASS (24.604s).
- Core Ubuntu CI 40925220168: 109/109 PASS (13.023s).
- Official MCP SDK and Windows API feasibility workflows on that commit: PASS.
- Packaging workflow on that commit started and must be checked for its own terminal conclusion.

Version checkpoint will be packaged as **0.10.0.dev0, public PRE-ALPHA, NO-GO for Beta** only after version-consistency tests and dual-OS gated wheel build succeed. This is not a claim that the 123-test sanitized normative v0.6 tree is imported. It remains absent from the public repository.

Critical blockers: no Win32 secure file broker, Unsloth real chat identity/wakeup, provider quiescence, worker tool broker, model residency checks on target, immutable published artifacts, completed/ACKed integration and independent external review. No real worker can run from the shipped MCP pilot.

## 0.11.0.dev0 migration/release checkpoint — 2026-10-29

[PR #19](https://github.com/Griggelor/MinionOne/pull/19) merged on main at d135987c107ee989daf185e7cc5c36504e410355.
Canonical SQLite owner now persists validated non-executing pre-dispatch stages; legacy schema migration is checked transactionally and preserves populated foreign-key children. Idempotent CAS state and audit events are atomic. An authenticated public MCP status/abort path is checked against a manager-staged QUEUED job; no new MCP or worker tool is exposed.
Red test commit 5acab5e... established missing methods; the first implementation CI #40945791768 caught an incorrect *test* assumption (version zero was treated as invalid). Fixed contract and repeated CI. Final feature commit 5db5ecf: [124/124 Windows + Ubuntu PASS](https://github.com/Griggelor/MinionOne/actions/runs/40946389035), [official SDK matrix 4/4](https://github.com/Griggelor/MinionOne/actions/runs/40946389029), and [Win32 API feasibility PASS](https://github.com/Griggelor/MinionOne/actions/runs/40946389055). [Architecture/release review](reviews/G2_CANONICAL_PREFLIGHT_REVIEW.md) is same-assistant second pass, not third-party audit.
**Worker_started CHECK remains 0, G1a/G1b/G1c live tests blocked, beta NO-GO.** A 0.11 wheel must be built from the exact version-tagged main commit after Windows/Linux CI and clean-install checks.

## v0.12.0.dev0 bounded agent planner checkpoint — 2026-10-29

PR #21 merged c8d0fccf7: internal synthetic AgentLoop with max 200 model turns, independent tool/token/time/no-progress budgets, three typed scope-denial strikes, fail-closed provider/broker faults and AWAITING_REVIEW on model final. [144/144 Windows and Ubuntu tests at exact PR head](https://github.com/Griggelor/MinionOne/actions/runs/40950294398). Review: reviews/G2_AGENT_LOOP_SECURITY_REVIEW.md.
Contract second pass caught an initial security ceiling mismatch with normative v0.6 worker_policy.schema.json. PR #23 merged 22cb738: hard tool cap 60, whole-job wall cap 1200s, still 200 model turns. [145/145 tests each OS](https://github.com/Griggelor/MinionOne/actions/runs/40951492562).
PR #22 merged 38c9fe30: LMStudioAgentProvider parses bounded localhost OpenAI-compatible tool calls, single-tool step, strict response/token/reasoning/argument validation, sanitized tool result history and fake broker multi-turn test; **not actual Flint execution or secure file tools**. [156/156 tests each OS](https://github.com/Griggelor/MinionOne/actions/runs/40951411465).
Current v0.12 version bump test was first red; source/package/CI labels updated atomically. Wait for v0.12 exact-commit GitHub Windows+Ubuntu+SDK+wheel before calling this artifact verified. Beta and production: NO-GO.
Remaining target blockers: real secure Win32 file broker, persistent per-attempt strike accounting and provider quiescence, actual LM Studio tool-call/model-residency testing, original Unsloth chat identity/wakeup, immutable artifacts, parent ACK and external security audit.

## v0.13.0.dev0 extended runtime policy — 2026-10-29

PR #25 implemented an expressly manager-selected internal 3600–14400
second duration profile. The prior standard v0.6 policy remains 900
seconds default, 1200 maximum. Individual model calls retain a 300
second upper bound; file-related broker calls have stricter ceilings
(read_file 30s, list_files 10s, create_work 60s, publish_artifact 120s).
200 model turns and 60 tool requests remain unchanged.
Test-first workflow initially red, then 168/168 on Windows and Ubuntu
at https://github.com/Griggelor/MinionOne/actions/runs/40967495967;
official MCP SDK and Windows API feasibility tests also passed.
The local fake-provider test harness required an observe_tool callback;
it was fixed before the successful evidence run. Review in
reviews/G2_LONG_RUNNING_TIMEOUT_REVIEW.md and ADR in
docs/ADR_G2_EXTENDED_WORKER_TIMEOUTS.md.
There is still NO real manager-authorized long-running worker,
durable strike/time accounting, safe file broker, actual target soak test,
process quiescence or Unsloth same-chat completion. Beta NO-GO.

## v0.14.0.dev0 timeout and Resume request slice — 2026-10-29

PR #27 merged at 0527f1158974217f88957a6b4ee6c0be30c83013.
A synthetic RUNNING job can be recorded as INTERRUPTED with reason
TIMEOUT only when the prior attempt fence is FENCED and trusted manager
quiescence is asserted. Unknown worker liveness is quarantined in
NEEDS_RECONCILIATION; the status response uses unknown/null rather than
misleading false for worker_running. The parent can cancel a timed-out
job or submit a durable, idempotent minionone_resume_request intent.
That request does NOT transition to RESUMING, create epoch 2, dispatch
Flint or publish any answer. The canonical SQLite schema migrates
known old 3/6-state constraints, retaining the worker_started=0 check.
Tests initially red; synthetic event-count assertion corrected and
false-unknown liveness reviewed. Exact functional PR head run
https://github.com/Griggelor/MinionOne/actions/runs/40970211921:
181/181 tests per OS PASS and separate official MCP SDK/Win32
feasibility runs PASS. Reference:
docs/G2_TIMEOUT_RESUME_INTENT.md and
reviews/G2_TIMEOUT_RESUME_SECURITY_REVIEW.md.
Beta and live resume remain NO-GO, pending secure new attempt epoch,
verified checkpoint/progress/budget recovery, provider quiescence,
Win32 file broker and Unsloth same-chat attestation.

## v0.15.0.dev0 actionable diagnostics checkpoint (2026-10-30)

Issue #29 is the mandatory comprehensive error-handling release gate:
API/auth/password, connections, SQLite/disk/ACL, provider model and
resource failures, worker file strikes, timeout/resume, parent wakeup,
delivery and ACK, with understandable and safely redacted guidance.
PR #30 merged the first isolated diagnostics slice. The real MCP
server, local CLI, LMStudioProbe, Completion and agent planner now
classify provider HTTP and transport failures plus job permission,
input/conflict and unsafe resume errors using fixed codes/actions
and retryability hints (never blind external-effect replay).
Synthetic secrets/HTTP error-body canaries and live localhost MCP wire
test prove no raw value leaks to the public output. First red run
found a test that over-restricted the generic word "password"; it was
corrected to reject concrete secret values instead.
Exact functional head 228a743...: [193/193 Windows and Ubuntu PASS](https://github.com/Griggelor/MinionOne/actions/runs/40973935402),
[official MCP SDK PASS](https://github.com/Griggelor/MinionOne/actions/runs/40973935484)
and [Win32 feasibility PASS](https://github.com/Griggelor/MinionOne/actions/runs/40973935438).
All worker/file/parent-delivery operations remain disabled. Full Issue #29,
real Windows/Unsloth fault injection, durable error journal and independent
human review are still open. Beta NO-GO.
