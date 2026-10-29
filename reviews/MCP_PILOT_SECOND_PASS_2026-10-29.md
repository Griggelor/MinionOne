# Independent-role second-pass: MCP transport pilot — 2026-10-29

**Assessment:** **PASS for a restricted, local-only MCP transport smoke pilot;
FAIL / NO-GO for multi-agent beta, production or sensitive data.**

**Independence note:** This is a separate adversarial review pass by the same
development assistant, not an external third-party security audit. Real Windows
NTFS and Unsloth/LM Studio are not available to this review environment.

## Ground truth / production path

Installed entry point: python -m minionone.mcp_cli.

- mcp_cli.py reads MINIONONE_MCP_TOKEN from the operator environment, constructs
  a LocalMCP instance on literal 127.0.0.1 with a local SQLite path, then serves.
- mcp_server.py verifies Host, forbids Origin, requires exact Bearer token, applies
  strict request size limits, 2-second request-body timeout and JSON-RPC framing.
- tools/list advertises only six tools, no generic file, shell, browser, network,
  arbitrary MCP invocation, output mutation, code execution or worker activation.
- The state owner is AdmissionStore in admission.py: jobs, admissions, controls and
  safe event metadata in SQLite. No worker ever starts; CANCELLED is only an
  explicitly internal quiescence confirmation operation not exposed as an MCP tool.
- minionone_models performs read-only GET to a configured literal-loopback
  provider through LMStudioProbe. It is not a worker model lifecycle test.
- No code path advertises integration acknowledgement or completion.

## Reproduced defect -> independent negative test -> fix

| ID | Reproducer / expected | Actual result / mitigation |
|---|---|---|
| MCP-001 | unauthenticated or wrong-token POST/GET | 401, no tools exposed |
| MCP-002 | forged Origin / Host / query token | denied 403/404 |
| MCP-003 | arbitrary shell or file tool name | JSON-RPC unknown tool, no execution |
| MCP-004 | invalid job digest, operation, stale abort | bounded tool error; no state transition |
| MCP-005 | multi-request job replay / restart | same durable admission receipt |
| MCP-006 | per-principal quota exhaustion | transactionally reject new admission; old key still returns receipt |
| MCP-007 | send a declared-length HTTP body then hold socket open | initial request hung indefinitely; new test failed, handler read timeout fixes it |
| MCP-008 | MCP serverInfo claims version not matching installed package | new failing test; now imports package version directly |
| MCP-009 | Windows + Ubuntu package test then wheel smoke | GitHub Actions with real tests and isolated wheel install |

## Verified CI

- Windows and Ubuntu 24.04: [Actions #40903714400](https://github.com/Griggelor/MinionOne/actions/runs/40903714400) at commit ed0e0f11b5d11ad9ee41df20e0a058a6f4f1b86a: **46/46 PASS per OS** including partial-request timeout regression and quota contract.
- [Pilot-wheel build #40903714254](https://github.com/Griggelor/MinionOne/actions/runs/40903714254) at same commit: **PASS**, wheel build, clean import, SHA256 file and upload.
- Version-consistency regression at commit 6a570eb was deliberately red. Follow-on source fix at 4699ed6: [CI #40904062907](https://github.com/Griggelor/MinionOne/actions/runs/40904062907) and [wheel #40904062848](https://github.com/Griggelor/MinionOne/actions/runs/40904062848) **PASS**. Later documentation commits do not change runtime but require own checks.
- These prove local-HTTP protocol handling in CI, **not Unsloth Studio interoperability or endpoint security against hostile local users**.

## Remaining findings by severity

### BLOCKER — G1a: real caller/chat ownership is not attested
Bearer token is associated with one operator-configured identity and an artificial
pilot thread. A caller with the secret can operate the entire pilot namespace;
no per-chat authorization proof, same-chat resume, callback authenticity or wakeup.
Never enable worker execution or automatic parent delivery based solely on this
credential. Must verify supported Unsloth callback/session APIs on real target.

### BLOCKER — G1b: no Windows secure file broker or verified input seal
minionone_admit accepts client-supplied input_digest; it does not seal real
input files. No worker read/create-only/publish tool exists. NTFS reparse,
hardlink/rename races, ACL escape, source snapshot and recovery gates unmet.
No real files or sensitive projects permitted.

### BLOCKER — G2/G3: no workers or end-to-end integration
No LM Studio worker loop, tool capability fencing, resource planner, outbox,
integration receipt, checkpoint/recovery or immutable artifact publication.
No completion state from worker text. Beta remains NO-GO.

### HIGH — actual Unsloth version/transport compatibility unverified
The implementation supports a 2025-06-18 Streamable HTTP JSON-response subset.
Modern Unsloth code supports HTTP MCP server configuration and custom headers,
but installed product/version and same-chat behavior must be tested. No live
handshake evidence from user target exists.

### MEDIUM — local-only transport is not a hardened multi-user Windows service
The Python ThreadingHTTPServer model can create many short-lived request threads.
A 2-second body read timeout and 16KiB cap reduce but do not eliminate resource
abuse by processes holding the configured secret. Service isolation, strict ACLs,
key lifecycle, thread/request concurrency cap and durable quota retention need
review before privileged deployment.

### MEDIUM — public-source completeness and license
The normative v0.6 sanitized source/contract test suite from the handoff has not
all been imported into this GitHub repository. Existing GitHub contract CI counts
are smaller than the separate 160-test Linux staging environment. No public
redistribution license has been chosen. Do not claim a community release.

## Gate decision

- G0: *partial* — executable pilot contracts, dual-OS CI, installed-wheel test.
- G1a: **BLOCKED** until real Unsloth attestation/wakeup evidence.
- G1b: **BLOCKED** until handle-based Windows containment exists and is tested.
- G1c: **BLOCKED** until provider/model memory residency measured on target.
- G2/G3: **NOT COMPLETE**.
- Community beta: **NO-GO**. Pilot download/connection check is allowed only
  with synthetic data and no worker/file privileges.

Production gates must not be waived merely because this pilot's tests are green.
