# MinionOne Architecture & Release Gates

**Status:** Working requirements summary for the public pre-alpha. Historical source of truth is the sanitized v0.6 handoff; its complete contract suite has not yet been imported into this public repository. Do not treat this summary as an independent safety proof.

## Purpose

An independent extension for Unsloth Studio that delegates bounded jobs from an authenticated parent chat to specialist local LLM workers. MinionOne owns job status, resources, admission, audit and worker tool policy. A worker has neither OS shell privileges nor authority to merge results into the parent project.

## Trust boundaries

1. Human/operator chooses project and approves final integration.
2. Parent assistant is authenticated by an actual transport session and may use only its thread-bound jobs; model-provided principal/thread IDs never establish identity.
3. Manager is the sole authority for job state, admission, fencing, publication, persistent journal and outbox.
4. Tool broker validates every worker operation against a server-issued job/attempt capability and quota.
5. Worker output, instructions, supplied files and model assertions are **untrusted content**; do not execute or promote them merely because they pass through a broker.
6. Provider backends are transports, not identity providers.

## Per-job visible folders (required target contract)

- \`input/\`: parent-prepared, sealed, immutable and listed in trusted hash manifest; worker **read only**.
- \`work/\`: worker can list/read and create new files through the broker, **no overwrite**, rename, delete, append or execution.
- \`output/\`: exclusively manager-written result snapshots and a direct fsynced redacted job.log.jsonl; the opt-in sensitive worker-io.jsonl captures actual provider/tool bytes while the job runs. **No worker OS access**. Log files are intentionally append-only and differ from immutable published result snapshots; see ADR-029 v0.30. The target-host NTFS security gate is still NO-GO.

**Windows gate:** lexical path checks alone do not provide sandboxing. Handle-bound filesystem operations must defend against reparse points, hardlinks, ADS, case folding, path aliases, race/swap attacks and non-owner ACL abuse. If unproven, worker file access stays disabled. Running a test on \`windows-latest\` does not prove this boundary.

## Six worker tools (target)

\`list_files\`, \`read_file\`, \`create_work\`, \`publish_artifact\`, \`report_progress\`, \`ask_question\`.

\`publish_artifact\` nominates work for manager verification; it never grants direct write access to output. Arbitrary shell, network, browser, generic file API, Git, other MCP access and code/test execution are **not** available.

## Durability and terminal state

Persist job IDs before dispatch. Enforce scoped authentication, CAS state versions, attempt fencing, idempotency, exact source hashes and crash recovery. Externally ambiguous provider operations enter reconciliation; never blindly retry. \`COMPLETED\` requires verified immutable result, trusted delivery, parent restoration condition and authenticated integration acknowledgment, not worker \`DONE\`.

The public \`AdmissionStore\` is *deliberately incomplete*: it registers accepted jobs, supports authenticated status and abort-before-start, and cannot start workers or claim completion. It is not a real MCP server and cannot attest Unsloth chat/thread ownership. The admission event table is preliminary, not a tamper-evident end-to-end journal.

## Implementation, tests and CI

Owner decision, 2026-10-30: implement directly with tests and CI; no separate
review or review-approval stage. Issue #9 was canceled as not planned, not
reported as an audit pass. This supersedes historical handoff review gates.

Use sequence: failing positive/negative/adversarial test → implementation → actual
production-path integration → fault/restart/concurrency tests → exact-head CI →
merge. Report commands, host/OS, pass/fail/skip counts and non-reproducible limits.

### Gates

| Gate | Status | Acceptance |
|---|---|---|
| G0 | Offline partial | Normative contracts and schemas in full repository + clean-checkout CI |
| G1a | **BLOCKED** | Authenticated Unsloth MCP client binding and real same-parent-chat wakeup or accepted fallback |
| G1b | **BLOCKED** | Verified handle-scoped Windows broker, adversarial target tests |
| G1c | **BLOCKED** | Worker/provider feasibility, exact target resource model and no parent unload |
| G2 | In development | Durable manager, broker, audit, leases, outbox, recovery, invariants |
| G3 | **BLOCKED** | Actual synthetic Unsloth → MinionOne → LM Studio worker E2E |
| Beta | **NO-GO** | Full positive/negative/recovery suite, actual target integration and no unmitigated blocker |

### Independent-project disclaimer

This project is not an official Unsloth product and no implicit vendor support is claimed. No software redistribution license has yet been selected by the repository owner. Do not use it for real confidential data or safety-critical projects at pre-alpha status.

## Exact supported observations (2026-10-29)

- GitHub Actions ran offline SQLite/CAS tests on Linux and Windows.
- The first Windows run found a test cleanup/file-handle defect; it was fixed with explicit connection closing; a later 12-test Windows/Linux run passed.
- Local staged sanitized v0.8 source, including **existing contract tests**, was independently run with **142/142 passing** on Linux after adding the admission tests; those non-imported tests are **not** claimed as remote CI evidence.
- Target Windows sandbox and Unsloth provider integration are **not tested** in this environment.
