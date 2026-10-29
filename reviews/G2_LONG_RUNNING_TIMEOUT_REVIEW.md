# G2 Long-Running Duration Profile — security and architecture review

Date: 2026-10-29. PR #25. Separate critical review by the **same
development assistant**, not an independent external audit.

## Verified design

The original standard policy remains unmodified: 900 sec default and
1200 sec absolute wall ceiling, max 60 tools, max 200 model turns and
three security denials. The new explicit internal manager profile
extended defaults to 3600 sec and allows at most 14400 sec. No MCP
tool exposes the profile; no worker start or file operation is added.

Per-call model deadline remains <=300 sec, including Thinking.
The generic broker per-call limit remains <=300 sec, but static
operation-specific ceilings are stricter: list_files 10 sec,
read_file 30 sec, create_work 60 sec, publish_artifact 120 sec,
report_progress 5 sec and ask_question 5 sec. The smaller of that,
the configured tool cap and remaining job time is forwarded.
A late provider/broker callback is rejected even if the whole-job
deadline has not elapsed.

## Adversarial acceptance

- Extended cannot be implicitly activated by changing wall_seconds
  above v0.6's 1200. Invalid profile types/values and bool limits deny.
- Extended minimum/default is one hour, hard ceiling four hours.
- A synthetic sequence of short model/tool turns can complete shortly
  before an hour; no single five-minute model timeout is circumvented.
- A multi-turn task cannot publish a candidate at the four-hour deadline.
- Long profile cannot exceed 60 tools, 200 loops, three denials or the
  configured token, model and tool budgets.
- File read is capped at 30 sec despite a generic 300-sec tool limit.
- Unknown tool requests still count strikes without touching broker.
- Model/tool time overrun is detected independently of wall deadline.
- Windows, Linux, official MCP SDK and Windows API feasibility CI must
  be green for the exact PR head before merge.

## Critical residual risks

This is an internal **synthetic policy** only. There is no live
Unsloth->Flint workflow, secure NTFS worker file broker, manager
authentication for a future extended-job request, persistent deadline
or strike accounting across restarts, host process kill/quiescence,
checkpointing for long tasks or independent security assessment.
The existing MCP server reports worker_execution_enabled=false.
Python callback timing cannot forcibly interrupt a blocked provider.
Extended jobs must not be promised to users as a running capability.

Normative v0.6 standard rules remain. This new extended contract is
a proposed manager-selected v0.7 exception, not a silent policy change.
Full multi-agent Beta remains NO-GO.

Review verdict: provisionally PASS for an internal clock-and-budget
policy only, conditioned on exact-head green CI. No production GO.
