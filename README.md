# MinionOne

**Independent MCP-based multi-agent job manager for Unsloth Studio.**

> **Public PRE-ALPHA · 0.9.0.dev0 · NOT A BETA · NOT PRODUCTION-READY**

MinionOne aims to delegate bounded tasks from a primary local assistant to specialist workers (initially via LM Studio), while keeping authorization, state, resource budgets, files and final integration under the trusted manager's control. It is **not** an official Unsloth project or an endorsed Unsloth extension.

## New: opt-in local MCP transport pilot

A **real, authenticated Streamable HTTP MCP endpoint** is now implemented, using the
2025-06-18 JSON-response protocol on literal IPv4 loopback only. This is **NOT**
the full MinionOne multi-agent worker manager. Its tools permit redacted status,
read-only LM Studio model discovery, and persisted registration/status/events/
cancellation of *non-executing* jobs. The configured bearer token is not evidence
of an actual Unsloth chat identity.

- [Windows install and Unsloth UI connection test](docs/WINDOWS_UNSLOTH_MCP_PILOT.md)
- [GitHub Actions: MCP transport tests on Windows and Linux](https://github.com/Griggelor/MinionOne/actions/workflows/admission-ci.yml)
- [GitHub Actions: installable wheel build](https://github.com/Griggelor/MinionOne/actions/workflows/pilot-wheel.yml)

The pilot does not expose workers, file tools, a verified input seal, publication or wakeup.
**Community beta remains NO-GO.**

## What is implemented

- Installable **experimental** Python package in `src/minionone/`.
- `AdmissionStore`: SQLite-backed, persisted job admission with authenticated caller-scoped threads from **trusted local configuration**, idempotency receipts, transactional admission event recording, optimistic state versions, authenticated abort requests and safe cancellation for jobs never started.
- Positive, negative, adversarial, concurrency and restart tests.
- `LMStudioProbe`: strict literal-loopback read-only model discovery using documented `GET /v1/models`.
- `LMStudioCompletion`: tool-free, bounded local text inference using documented `POST /v1/chat/completions`; worker output is untrusted and is never executed or automatically integrated.
- Operator diagnostic CLI (`minionone` or `python -m minionone`) with explicit NO-GO status, read-only model probe and fixed synthetic smoke prompt; it never launches a worker, reads user projects or exposes model responses by default.
- GitHub Actions on Windows and Linux using Python 3.13. The current remote CI suite has **32 tests per OS**, with an independently reviewed [PASS run](https://github.com/Griggelor/MinionOne/actions/runs/40899172333) before the later provider timeout and documentation changes.

**Not implemented:** real Unsloth UI integration evidence or parent/thread attestation or same-chat notification, LM Studio worker invocation, secure Windows filesystem broker, six executable worker tools, artifact publisher, full durable state machine/outbox, resource accounting and full end-to-end job execution. This repository is not a working multi-agent automation system yet.

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

The repository's current GitHub Actions coverage is for **admission only**. A larger sanitized v0.8 reference/design source exists outside this repository: its legacy 123 tests are **not** represented as remote GitHub CI unless actually imported and rerun.

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
