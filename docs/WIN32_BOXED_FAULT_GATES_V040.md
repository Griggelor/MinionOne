# v0.40: Native Windows AppContainer IPC fault-injection slice

**Status:** executable test-only child; run_tests and generic shell tools
remain denied. **No product GO, no target-host security attestation.**

## Purpose

v0.39 demonstrated a fixed synthetic PROBE_PING/PONG over a real
AppContainer child process using two inherited anonymous pipe handles
and a pre-dispatch SQLite no-replay fence. v0.40 attacks that **same actual
native transport path**, not just an in-memory framing codec.

All fault modes are selected solely by a trusted CI harness and compiled
into the fixed, MSVC-built C helper. The public zero-argument probe API
remains unchanged; none of the fault modes can be requested through MCP,
Flint, model prompts, AgentLoop or a worker tool.

## Real Windows negative test matrix

| Internal mode | Child behavior | Expected durable manager state |
| --- | --- | --- |
| crash | Terminates after request, without a reply | IN_FLIGHT |
| hang | Sleeps longer than the 4s process deadline | IN_FLIGHT |
| truncated | Returns length header and one body byte | NEEDS_RECONCILIATION |
| oversized | Returns invalid 0xffffffff length | NEEDS_RECONCILIATION |
| wrong_nonce | Returns canonical PONG with a changed nonce | NEEDS_RECONCILIATION |
| trailing | Returns valid PONG plus one extra byte | NEEDS_RECONCILIATION |

Every case must fail closed with automatic replay disabled and
worker_tool_exposed=false, os_sandbox_attested=false, product NO_GO.
Success in any hostile mode fails the test. The manager verifies journal
state while the temporary test database is still available. The hang
case requires Job Object termination; it cannot be reported as a normal
exit or completion. Child is created suspended and assigned to the Job
Object before resume.

For invalid frames, the manager now supplies actual bounded untrusted
reply bytes directly to the durable journal's validation boundary. The
journal records NEEDS_RECONCILIATION on malformed or wrong-attempt
replies. Process crash and timeout remain uncertain IN_FLIGHT.

## Evidence and classification

Dedicated Windows CI builds and runs the native v0.39 success path, six
hostile cases, v0.38 protocol/journal tests, and the separate v0.37
real AppContainer manager/sibling/output canary-denial regression.
Linux executes contract-level fail-closed checks only.

The workflow writes redacted evidence/v040/HOSTILE_IPC_EVIDENCE.json,
including commit SHA and observed journal states. This is Windows CI
synthetic evidence, not validation on the operator target host.

## Still open / next gates

- Hostile process-tree breakaway, unexpected/duplicated handle probing,
  explicit DNS/TCP network denial and hostile untrusted workloads.
- v0.37 loopback timeout remains a timeout, not network attestation.
- Production database integration, durable after-crash recovery in the
  running manager and repeated fault-injection across process boundaries.
- Safe input/scratch/output transport, target Windows G1b attestation,
  real Flint/Unsloth job flow and explicit approval for executable tools.

**boxed_execution_enabled=false; os_sandbox_attested=false; NO_GO.**
