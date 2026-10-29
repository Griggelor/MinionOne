# ADR: long-running manager jobs — experimental duration profile

Date: 2026-10-29. Scope: internal AgentLimits contract only.
No public worker execution or extra MCP method is introduced.

## Why

Long coding tasks need one to four hours to reason, edit and review.
Twenty minutes is a legacy v0.6 policy limit, not an LM Studio
technical limit. The revised policy deliberately preserves all v0.6
settings in standard mode and adds an explicitly selected extended
duration profile.

## Timeout hierarchy

- Job wall time covers the complete job with all model and tool calls.
  Standard: 900 seconds default, 1200 seconds maximum.
  Extended: 3600 seconds default/minimum, 14400 seconds maximum.
  The extended choice is made only by trusted manager configuration.
- Model call: 120 seconds default, maximum 300 seconds per completion,
  including model-side thinking. This is NOT whole-job time.
- Generic tool call: 30 seconds default, maximum configurable 300
  seconds. Specific broker tool types enforce stricter upper limits:
  list_files 10; read_file 30; create_work 60; publish_artifact 120;
  report_progress 5; ask_question 5 seconds.
- The effective per-call timeout is the minimum of the configured
  limit, the tool-specific cap and remaining job wall time.
- The existing two-second MCP HTTP socket reading limit is unrelated
  to asynchronous job execution.

At most 200 model turns, 60 tool requests, three security-denial
strikes and the existing token/no-progress budgets apply to BOTH
duration profiles. Extending elapsed time must not automatically
increase file rights, tool count or autonomy.

## Implementation and testing

AgentLimits remains immutable and defaults to legacy standard mode.
Untrusted model output cannot change the profile. AgentLoop checks
its monotonic clock before and after each model and broker call,
including the applicable individual timeout; a late final result is
not accepted. The new tests simulate one-hour and four-hour
lifetimes through a fake clock without actually waiting for hours.

## Unfinished operational gates

Real workers need durable deadline/loop/strike counters across restarts,
trusted per-job approvals, provider and process termination checks,
a proven Windows file broker, measured local-model residency,
checkpointing, and parent-chat integration. Python callback timeouts
alone cannot forcibly stop external inference. The live MCP pilot
still has no executable worker tool.

Extended mode is a proposed policy extension. Standard mode retains
the documented v0.6 ceiling and continues as the default. Beta NO-GO.
