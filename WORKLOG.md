# MinionOne WORKLOG — 2026-10-29

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
