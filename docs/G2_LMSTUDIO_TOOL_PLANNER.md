# G2 LM Studio tool-call planner — protocol-only pilot

**PR #22 is not a worker executor or a full agentic beta.**

The manager-internal LMStudioAgentProvider sends a bounded, previously
approved text task and exactly six tool names over an OpenAI-compatible
POST /v1/chat/completions request to a *literal loopback-only* LM Studio
endpoint, with tool_choice=auto. It parses either a single tool request
or a final answer. A tool request is returned to the separately tested
AgentLoop; the adapter itself never touches OS/files/network beyond the
specific local model HTTP request and never runs the requested tool.

Tool inventory: list_files, read_file, create_work, publish_artifact,
report_progress, ask_question. Requests outside this list are returned
to AgentLoop to accrue security strikes and MUST NOT be sent to any broker.

Tool-call history uses the official assistant function-call plus tool
reply sequence, sanitized to status/result code only. A live read_file
implementation would need a reviewed secure bounded content channel;
**that is NOT implemented here**. The adapter therefore cannot yet
allow a worker to read real project input or produce a real artifact.

No multiple tool calls in one response are accepted: they are rejected
rather than silently bypassing per-operation budgets. Response bodies,
request histories, prompt/model IDs, tool JSON and tool IDs are bounded.
The provider must report total token usage. Optional reported reasoning
tokens are checked as a subset; private reasoning text is never stored.

A model saying 'finished' returns ModelStep.finish, which AgentLoop
converts to AWAITING_REVIEW and a SHA-256 candidate-text digest. Only a
trusted future manager with immutable artifact, recipient authorization,
and parent integration acknowledgment may ever set COMPLETED.

## Hard limits and security contract

Normative v0.6 worker policy requires <= 60 actual tool requests and
<= 1200 seconds total runtime, regardless of a maximum of 200 model
turns. The separate PR #23 corrects the manager loop so this planner
will inherit those stricter boundaries. Missing caller identity,
secure Win32 file broker, target residency and durable tool/strike
accounting keep agent execution disabled in the public MCP server.

## Test evidence vs. release gates

Tests use synthetic OpenAI-format LM Studio responses and a fake
manager-side broker. They check real-shaped HTTP request serialization,
sanitized function-call/tool-response conversation history, accounting
including reasoning tokens, constrained endpoint, malformed tool args,
excessive or parallel tool calls, and 3 strikes for unknown/out-of-scope
requests. They do NOT execute a real Flint/LM Studio model or call a
real Windows file broker. No user project files are involved.

*Green CI on its exact branch commit is required before merge.*
External Windows target, real provider compatibility/Thinking reporting,
Win32 handle security, Unsloth same-chat identity and final integration
remain G1a/G1b/G1c/G3 BLOCKED. **Beta NO-GO.**
