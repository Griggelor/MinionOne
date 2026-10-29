# Development status — 2026-10-29

**Version:** 0.9.0.dev0  
**Channel:** Public PRE-ALPHA — not a beta, release **NO-GO**.

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
