# G2 Worker Agent Loop: budget and incident policy (internal-only)

**Status:** Experimental orchestration contract, NOT a live LM Studio/Flint worker
and NOT an externally exposed MCP capability. Model text never executes OS,
network, file or integration actions by itself. Only the future trusted manager
broker may carry out the six bounded worker tools after real Windows confinement.

## Requested agent behavior

The worker may internally reason (Thinking depends on the actual model/provider),
request approved tools, observe scoped tool results and continue until it
produces a candidate final answer. The parent need not receive every
intermediate message. Internal progress will remain manager-side; an
ask_question event pauses the task for the parent to answer.

**Critical:** finish means AWAITING_REVIEW, never COMPLETED or DELIVERED.
The future manager must verify an immutable published artifact, provider
quiescence, original-chat recipient and integration ACK. Model prose is
never proof of successful completion.

## Enforced loop-policy defaults and hard limits

| Control | Default | Hard policy |
|---|---:|---|
| Agent model turns | 50 | max 200 |
| Worker tool requests (including denied) | 400 | max 2,000 |
| Total tokens reported by provider | 150,000 | max 2,000,000 |
| Whole job wall budget | 1,800 seconds | max 86,400 seconds |
| Model request timeout forwarded to provider | 120 seconds | max 300 seconds |
| Tool request timeout forwarded to broker | 30 seconds | max 300 seconds |
| Forbidden/out-of-scope access strikes | 3 | max 3 |
| Consecutive no-progress model turns | 8 | max 200 |

All limits are strict integers; booleans, zero and invalid values fail.
A model step with missing or zero reported token usage is invalid.
Reasoning tokens must be nonnegative and <= total tokens; they are
already included in total usage and are not double-counted. The private
reasoning text is neither journaled nor exposed.

One loop = one model completion/decision. The current synthetic
adapter represents at most one tool request per decision. A future
real adapter must normalize multi-tool requests to individually
authorized/budgeted operations, with no parallel authorization bypass.

## Denied file access and incident escalation

The future trusted Win32 file broker, NOT model prose, must validate
each file operation by canonical job, attempt fence, root handle,
authorization and requested action. On denial it raises a typed
BrokerScopeDenied signal.

- Strike 1: operation denied, neutral DENIED feedback.
- Strike 2: same; worker may correct its plan.
- Strike 3: SECURITY_ABORT; stop further model and tool calls; future
  manager must fence the attempt and persist a redacted security event.
- Unknown tool names are denied BEFORE broker execution and counted.
  Allowlist: list_files, read_file, create_work, publish_artifact,
  report_progress, ask_question.
- Broker exceptions, invalid results, and provider faults abort the
  run without fabricating success.

The counter is currently in-memory for the bounded synthetic run,
NOT a production durable security ledger. Before actual workers it
must become a transactional persisted strike/fencing record.
No real file access has been granted by this module.

## Timeouts and cancellation

AgentLoop forwards the smaller of the remaining wall budget and
the model/tool timeout to trusted downstream callbacks. It checks a
monotonic clock before and after each call and refuses late output.

**Important:** This cannot forcibly terminate a blocked Python
callback or independently confirm that a provider stopped. Actual
LM Studio and Win32 broker adapters must enforce the timeouts at
transport/OS level, revoke stale capabilities, terminate hung workers,
and verify quiescence. A TIMEOUT return code is not termination proof.

Exit codes: AWAITING_REVIEW, WAITING_FOR_PARENT, CANCELLED, TIMEOUT,
LOOP_LIMIT, TOOL_LIMIT, TOKEN_LIMIT, NO_PROGRESS, SECURITY_ABORT,
BROKER_FAILURE, PROVIDER_INVALID, PROVIDER_FAILURE. Result contains no
prompt, private reasoning, file path or raw output; the candidate
digest is a SHA256 of untrusted model text, NOT an approved artifact.

## Test plan and scope

- The worker may finish on loop 200, but never starts loop 201.
- Separate tool count, token budget and no-progress threshold.
- Three denied file requests or three unlisted tool requests cause
  SECURITY_ABORT, without executing an unauthorized OS operation.
- Invalid token/Thinking usage, invalid steps, malformed broker results
  and provider/broker exceptions fail closed.
- Model- and tool-call deadlines are forwarded and wall-time is checked
  after calls. Cancellation prevents calls.
- A model final answer never directly publishes or claims COMPLETED.

No real LM Studio native tool-calling session, target hardware, NTFS
handle broker, persistence of loop/strike state, original Unsloth chat
identity, wakeup or integration ACK is proven by these unit tests.

**G2 partial policy only. G1a/G1b/G1c/G3 and beta: NO-GO.**
