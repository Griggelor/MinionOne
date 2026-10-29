# MinionOne

**Unsloth Studio MCP extension for secure, local multi-agent delegation.**

> **PUBLIC PRE-ALPHA — NOT PRODUCTION-READY.** This is an independent community project, not an official Unsloth product. Current development contains specifications, test contracts and a limited experimental SQLite audit component, **not** a verified end-to-end MCP job manager.

MinionOne is designed to delegate bounded tasks from a trusted parent assistant to local LLM workers (initially via LM Studio), while limiting worker authority through a manager-enforced tool broker.

## Intended security model

- `input/`: sealed, read-only worker input.
- `work/`: worker read and **create-only** through an authorized broker; never overwrite or delete.
- `output/`: manager-only, immutable published artifacts and a redacted job log.
- Workers have **no shell, arbitrary code execution, network, browser, Git, unrestricted filesystem or self-integration rights**.
- Persisted job states, scoped authentication, idempotency, fencing, recovery and explicit parent acknowledgement are required for the future runtime.

**These are design requirements, not yet verified production guarantees.**

## Implementation status

A preliminary internal SQLite audit/journal prototype and offline contract/test suite exist. On a separate prepared public-source snapshot, **123/123 offline tests** and **87/87 transport manifest checks** passed on Linux on 2026-10-29. They do not establish a secure Windows file broker or functional Unsloth / LM Studio integration. GitHub Actions results are not yet available.

Major remaining gates:

1. **G1a:** authenticated Unsloth MCP attachment, parent ownership and real same-chat resumption.
2. **G1b:** Windows NTFS handle-bound confinement under reparse points, hardlinks, rename races and failure injection.
3. **G1c:** LM Studio / worker model resource and residency behavior on target hardware.
4. **G2–G3:** durable manager, six authorized worker tools, crash recovery and real synthetic end-to-end test.
5. Independent security review, reproducible release and explicit maintainer approval.

See [Issues](https://github.com/Griggelor/MinionOne/issues) for concrete gate work. No production use or security guarantee is implied.

## Public source import

The initial GitHub README is a bootstrap document. The cleaned full **v0.8-dev source** (specifications, code, test suite, CI workflow and SHA-256 manifest) is being prepared as a separate import artifact. Do **not** import prior Git bundles containing historical third-party reference archives.

## Security and participation

Please do not post secrets, private customer data or actionable vulnerability details in public issues. Use GitHub private vulnerability reporting if enabled.

**License:** No software license has yet been adopted by the owner. Public visibility is not a grant of permission to copy, modify or redistribute the code. Third-party provenance and an explicit license choice remain open decisions.

**Status:** Publicly developed experimental pre-alpha, **release NO-GO**.
