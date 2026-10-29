# Changelog

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
