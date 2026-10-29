# Critical second-pass review — 2026-10-29

**Scope:** public MinionOne commit `2863720681ea5342d5bb66de18316b4ee90e1dff`.
**Reviewer:** separate adversarial review pass by the same development assistant, **NOT** a third-party or organizationally independent security audit.
**Judgement:** **FAIL FOR BETA / PRODUCTION**. The present modules may remain in public pre-alpha.

## Verified evidence

- GitHub Actions run [#40899172333](https://github.com/Griggelor/MinionOne/actions/runs/40899172333): **Ubuntu 24.04: 32/32 PASS**, **Windows latest: 32/32 PASS**, both Python 3.13, editable package installed.
- Test-first issue observed: test replay after a valid abort originally returned updated job state rather than the original admission receipt; receipt persisted transactionally, then regression test passed.
- Initial Windows run found `WinError 32` when test SQLite connections were not closed. A second independent negative test exhibited the same problem; explicit closure fixed it. These are **test-harness** defects, not a proof of secure Windows filesystem brokerage.
- Bounded 120-second inference timeout was first rejected by the short discovery-timeout validator; failing test was added and a separate bounded timeout (1–300 s) implemented.
- Separate sanitized local staging tree: earlier 123 contract/audit tests and 32 new runtime/adversarial/provider tests run together, **155/155 PASS locally on Linux**. This is *not* proof that those 123 tests ran on GitHub.

## What the production path can actually do

Current public production module entry points are only:
- `AdmissionStore` for configured parent-token/thread admission, status, abort-before-start and limited event log.
- `LMStudioProbe.models()` for a strictly loopback-only, GET-based catalog discovery, no provider state change.
- `LMStudioCompletion.complete()` for one-shot, plain-text, tool-free local OpenAI-compatible model inference. Returned text is explicitly untrusted.

**No installed or runnable MCP server.** No real worker dispatcher, Windows filesystem broker, progress/ask-question broker, artifact publication, parent resumption, outbox, checkpoint, resource controller, or integration acknowledgement. No code path can legitimately assert `COMPLETED` for a worker job.

## Findings / priority

| Severity | Finding | Release treatment |
|---|---|---|
| BLOCKER | Windows handle-bound, race-resistant worker filesystem confinement not implemented or tested against live NTFS | Fail closed; no worker file tools |
| BLOCKER | Unsloth MCP true parent identity, per-thread authority and same-chat wakeup unverified | No automatic chat resumption |
| BLOCKER | Durable full manager, resource admission, six broker tools, outbox and verified artifact integration not implemented | No delegated jobs/production release |
| BLOCKER | Real LM Studio/Flint and Rulith resource residency tests not run | No claim of model compatibility |
| HIGH | Current admission events are preliminary; they do not share the prior private audit prototype's canonical hash-chain/transaction boundaries | Unify into authoritative journal before runtime |
| HIGH | Admission digest is not a verified sealed input snapshot; auth mapping is trusted test/admin configuration, not live Unsloth attestation | Do not expose AdmissionStore directly to public MCP |
| MEDIUM | Token rotation, scoped administrative issuance, rate limits, DB ACLs, disk-full/WAL corruption recovery and multi-process behavior remain untested | Expand independent fault-injection and recovery matrix |
| MEDIUM | Current GitHub repository lacks most normative v0.8 source/schemas/reference tests and a full SHA-256 manifest | Import sanitized files and rerun complete GitHub CI |
| MEDIUM | No explicit redistribution license/third-party provenance decision | Block official Community release |

## Test architecture assessment

Strengths: verified actual GitHub CI on two OSes, test-first regressions, ID/ownership checks, deterministic CAS failure, concurrent admission/abort, restart/replay, malformed provider payloads, forbidden external URLs and redirects, absence of worker tool forwarding.

Limitations: HTTP tests use **synthetic local servers**; current repository's tests cover small internal slices, **not** a real end-to-end job production path. An empty successful provider test cannot prove safe output integration. Source snapshot is not yet fully imported.

## Release and acceptance

**NO-GO** for beta. GitHub `main` is a community pre-alpha development branch only. A beta candidate requires G1a/G1b/G1c, full G2/G3, complete in-repo source and tests, independent security review, reproducible package, clear license and synthetic target-hardware acceptance evidence. No release tag or beta package should be created before those conditions are satisfied.

Useful references: [Architecture](../docs/ARCHITECTURE_AND_RELEASE_GATES.md), [issue #1](https://github.com/Griggelor/MinionOne/issues/1), [issue #2](https://github.com/Griggelor/MinionOne/issues/2), [issue #3](https://github.com/Griggelor/MinionOne/issues/3), [issue #4](https://github.com/Griggelor/MinionOne/issues/4), [issue #9](https://github.com/Griggelor/MinionOne/issues/9).
