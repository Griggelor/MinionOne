# G2 Agent Loop Budgets — independent-role second pass

**Date:** 2026-10-29 | **PR:** #21 | **Scope verdict:** PASS only for
synthetic, manager-internal finite loop policy. **Worker beta: NO-GO.**
Reviewer: same development assistant in a separate adversarial role,
NOT an external independent security auditor.

## Actual execution boundary

The new AgentLoop accepts only explicit trusted provider and broker callback
objects supplied by the *future manager*. It does not instantiate LM Studio,
read/write any file, call the network, run a shell, publish an artifact or add
MCP tools. The public LocalMCP worker_execution_enabled remains false.
Consequently the tests validate safety policy, NOT a deployable Flint agent.

Exactly six worker tool names are recognized, not arbitrary model-requested
functions. Any unknown name is denied BEFORE broker invocation. A trusted
broker's BrokerScopeDenied event counts as one security strike. At three
strikes the loop returns SECURITY_ABORT, with no more provider or broker calls.
This still does NOT fence the actual persisted AttemptFenceStore or enforce
Windows NTFS paths; that integration remains G1b/G2 blocker.

## Test-first evidence

- Initial RED tests: #40949464198, 18 new test cases failed to import
  missing agent_loop implementation.
- First implementation #40949607511 exposed two issues:
  (a) a test mistakenly used 199 idle model turns while expecting to reach
  turn 200, despite the independent no-progress stop at eight;
  (b) a real bug misclassified broker-thrown scope denials as BROKER_FAILURE
  rather than security strikes. The test was corrected to use productive
  progress turns; the broker catch-branch bug was fixed.
- Later RED #40949918814 introduced per-tool timeout and missing-token
  usage requirements; production run forwards the remaining time budget
  downstream and rejects zero model usage as PROVIDER_INVALID.
- Green source commit dba7efe3286cf998627c86454b27cee69bdb6189:
  [GitHub Actions #40949962541](https://github.com/Griggelor/MinionOne/actions/runs/40949962541)
  **144/144 Ubuntu PASS (13.926 s), 144/144 Windows PASS (49.978 s)**.
  Both official MCP SDK and Windows feasibility workflows passed on this
  source commit. A documentation-only follow-up needs exact-head checks.

## Threat-driven tests

- Hard maximum of 200 model turns, last-turn completion and no 201st call;
  default 50 turns.
- Separate tool-operation count, token usage including reported reasoning
  tokens and no-progress threshold.
- Invalid/unknown tool name denied before trusted broker call; three
  denied attempts stop a run, no raw path/secret in result.
- Trusted broker denial is not mistaken for a general error, but an
  unexpected broker crash aborts immediately.
- Model completion is UNTRUSTED candidate digest / AWAITING_REVIEW,
  NEVER a parent notification or durable job COMPLETED.
- Bounded per-model and per-tool timeout forwarded; whole-job monotonic
  deadline checked before and after callbacks. Late final not published.
- Question pauses rather than fabricating completion; cancellation checks
  before calls; malformed model or token accounting fails closed.

## Residual BLOCKER findings

1. No real tool-call provider loop is connected to LM Studio/Flint.
   LM Studio supports tool_calls, but actual version/model compatibility,
   thinking token reporting, context compaction and parent residency
   have not been verified on target hardware.
2. No secure Windows file broker is implemented. Simulated BrokerScopeDenied
   has no OS effect, so unauthorized file attempts in a real worker are
   **not yet observed, counted, blocked or persisted**.
3. Strike counters, loop index and token consumption exist only in this
   synchronous in-memory run. A process restart can reset them. Real
   execution needs per-job durable CAS accounting and epoch fencing.
4. Python callbacks cannot be forcibly interrupted. A per-call timeout
   argument and a post-call wall-clock check are not a hard external
   process timeout; future adapters must enforce cancellation at transport,
   subprocess/OS and provider quiescence layers.
5. No real same-chat Unsloth identity/callback; final result awaits manager
   artifact verification and explicit parent integration ACK.
6. Future multi-tool provider responses require separate per-operation
   budgets; this simulation accepts one tool per model decision.
7. No independent human security review, actual NTFS race/ACL proof,
   or sensitive-data permission.

**Merge recommendation:** merge as an internal contract only after
exact-head Windows/Linux CI and official MCP SDK tests PASS.
Never describe the resulting downloadable wheel as agent-capable.
Beta remains NO-GO.
