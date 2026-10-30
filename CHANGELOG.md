# Changelog

## 0.17.0.dev0 — 2026-10-30 (PUBLIC PRE-ALPHA, NOT BETA)

- Distinct fixed SQLite/OS diagnostics for busy/locked, corrupt, cannot-open, full, read-only, I/O, permissions, schema/integrity and database-memory failures. Numeric codes only; no raw-message guessing.
- MCP startup now returns redacted JSON plus exit code 2 for state-directory, database, configuration and bind failures. Explicit --db does not require the default home state directory.
- Journal failures retain ERROR_JOURNAL_UNAVAILABLE and add a safe cause-specific action. Attached diagnostics are rebuilt from the fixed catalog, including malformed-key handling.
- 18 new tests, including actual SQLite lock/corruption/read-only/size-cap failures and real MCP HTTP/startup boundaries. Local clean-environment suite: 231/231 PASS (24.463s).
- PR #32 (v0.16) merged. Owner instruction: direct implementation, tests, CI, merge; separate review gate #9 canceled as not planned. Existing capability/target-evidence boundaries remain.
- Full error UX under #29 and live worker/provider/Unsloth integration remain unfinished. Worker execution stays disabled; beta NO-GO.


## 0.16.0.dev0 — 2026-10-30 (PUBLIC PRE-ALPHA, NOT BETA)

- Durable, authorized ABORT/RESUME_REQUEST rejection events on the canonical job timeline; historical error projection in job status, fixed safe diagnostic snapshots and observed state/version/attempt metadata.
- Failed-command replay survives restart without duplicate events or later execution. Accepted receipts retain priority over conflicting requests.
- Savepoint rollback and atomic journal commit; explicit ERROR_JOURNAL_UNAVAILABLE on failed error writes/commits or 128/job capacity exhaustion. Valid abort remains available at capacity.
- 20 new production-path/transaction/crash/security tests; full local suite 213/213 PASS. New PR must pass Windows/Linux, official MCP SDK and wheel checks at its own exact head.
- Additive database metadata; back up the stopped private pilot DB before upgrade. See docs/ADR_G2_DURABLE_CONTROL_ERRORS.md.
- Provider/worker/storage-wide error journaling and actual Unsloth error delivery remain open under #29. Worker execution stays disabled; **Beta NO-GO**.


## 0.15.0.dev0 — 2026-10-30 (PUBLIC PRE-ALPHA, NOT BETA)

- [PR #30](https://github.com/Griggelor/MinionOne/pull/30): safe, operator-actionable error codes for wrong MCP password/token, provider 401/403 authentication, connection refused, timeout, 400/404/429/5xx API responses, malformed provider response, unauthorized job, stale version, input and resume denial. MCP and local CLI return fixed `code/message/action/retryable` without raw credentials or URLs.
- [Issue #29](https://github.com/Griggelor/MinionOne/issues/29): comprehensive worker, Windows files, SQLite, original Unsloth chat, provider and delivery error UX is still required before beta.
- Test-first synthetic source CI: 193/193 Windows and Ubuntu passed at PR #30; official MCP SDK 4/4 matrix and Win32 feasibility also passed. New version requires its own clean-wheel package CI.
- No automatic retries of ambiguous side effects. No actual autonomous worker, secure file broker or same-chat delivery. **Beta NO-GO.**

## 0.14.0.dev0 — 2026-10-29 (PUBLIC PRE-ALPHA; NOT BETA)

- PR #27: durable canonical INTERRUPTED + TIMEOUT reason, or NEEDS_RECONCILIATION when old worker liveness is unknown. No invented new lifecycle state.
- Restart-safe idempotent authenticated minionone_resume_request records **request only**. Old attempt must be fenced; trusted checkpoint and quiescence required for availability. It does NOT launch a second worker or claim completion.
- Synthetic Windows/Linux 181/181 tests each OS passed at functional source head, including historical SQLite schema migration, concurrency, stale CAS, cancel/resume, strict tool scope and official MCP SDK interop.
- Older project DBs require backup and offline stop before v0.14 schema migration. No real Flint work, OS file sandbox, verified Unsloth original chat or multi-attempt restart exists. Beta NO-GO.

## 0.13.0.dev0 — 2026-10-29 (PRE-ALPHA; NOT BETA)

- PR #25: internal extended-duration profile, one hour default/minimum, four hours hard ceiling. Legacy standard 900s default/1200s maximum unchanged.
- Per-model 300s ceiling still applies. Tool-specific caps: list_files 10s, read_file 30s, create_work 60s, publish_artifact 120s, progress/question 5s.
- Tool-call 60, model-turn 200, token budget and three security denials unchanged. Late individual calls are rejected.
- Synthetic fake-clock and full CI: 168/168 per OS at PR #25. No real Flint worker, NTFS file broker, persistent time or process termination. Beta NO-GO.

## 0.12.0.dev0 — 2026-10-29 (PUBLIC PRE-ALPHA, NOT BETA)

- [PR #21](https://github.com/Griggelor/MinionOne/pull/21): internal bounded model loop, maximum 200 turns, three forbidden tool/file-scope denial strikes, deadline/token/no-progress limits and untrusted candidate review gate.
- [PR #23](https://github.com/Griggelor/MinionOne/pull/23): security correction — retain original v0.6 hard limits **60 tool calls and 1200 seconds runtime**, independent of model-turn count.
- [PR #22](https://github.com/Griggelor/MinionOne/pull/22): strict loopback LM Studio function-call *planner* with bounded one-tool-per-model-step, token/reasoning accounting and synthetic conversation tests. No real file broker, worker execution or parent callback.
- Versioned CI pilot wheel only after dual-OS tests; sample models/Windows hardware have not been tested. Pre-alpha installation is synthetic MCP smoke only.
- **G1a/G1b/G1c/G2/G3/Beta remain NO-GO** for real multi-agent usage.

## 0.11.0.dev0 — 2026-10-29 (PUBLIC PRE-ALPHA, NOT BETA)

- [PR #19](https://github.com/Griggelor/MinionOne/pull/19): migrate the same canonical SQLite jobs table with atomic FK verification; persist VALIDATING, QUEUED, RESOURCE_CHECK manager-only states and dynamic audit/CAS/idempotency receipts.
- Existing v0.10 pilot jobs/admissions/outbox/attempt-fence references preserved; corruption rejects migration. **Stop and back up pilot DB before upgrade**. The database CHECK(worker_started=0) and six old MCP tools remain unchanged.
- New adversarial tests: legacy child preservation including populated effect tables, rollback after broken FK, concurrent CAS, old idempotency receipt/restart, real MCP status and abort. Final branch CI: Windows 124/124 and Ubuntu 124/124 PASS at 5db5ecfd; official MCP SDK matrix 4/4 PASS.
- No worker launch, sealed files, real Unsloth parent-chat identity, provider quiescence or integration ACK. Still a synthetic connection pilot. **Beta NO-GO.**

## 0.10.0.dev0 — 2026-10-29 (PUBLIC PRE-ALPHA, NOT BETA)

This is a developer test package for MCP protocol attachment with **zero worker execution rights**.

- PR #15: one-shot internal job/attempt fencing ledger, transactionally tied to canonical job state/version, permanent crash quarantine and no implicit retry.
- PR #16: real local MCP server performs outbox quarantine and attempt fencing **before** network socket binding; recovery exceptions fail closed.
- PR #17: pure conservative UMA resource eligibility policy for a resident parent plus one worker. Without real target telemetry, no worker may be admitted.
- PR #12–14 retained: conservative outbox, Windows handle API feasibility (not a broker), and official MCP SDK compatibility matrix.
- 109/109 Windows and 109/109 Ubuntu source tests verified at GitHub Actions run #40925220168 (commit 5b11c77), before this release metadata update.
- Versioned installable wheel built only when dual-OS CI passes; SHA256SUMS produced by the same workflow. A GitHub Actions artifact is **not** a signed release.

**Release gate:** NO-GO for Multi-Agent Beta / production / sensitive files.
Outstanding: real Unsloth parent chat and same-chat wakeup, Windows NTFS handle-root broker, real Flint LM Studio worker workflow and parent memory residency, verified immutable artifacts, full canonical durable lifecycle, source provenance/license and independent human audit.

## 0.9.0.dev0 — historical experimental MCP pilot

Authenticated loopback Streamable HTTP endpoint, non-executing SQLite admission, read-only local LM Studio discovery, limited diagnostic CLI, conservative outbox and official MCP transport tests. No real worker execution.
