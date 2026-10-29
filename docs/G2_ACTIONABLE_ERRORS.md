# G2 error diagnostics — first API/auth/connection slice

**Scope:** experimental local MCP-pilot and local diagnostic CLI only.
This is NOT a real agent/worker error-reporting pipeline or a Beta release.

## Public error object (fixed, non-sensitive)

```json
{"code":"PROVIDER_AUTH_FAILED",
 "message":"LM Studio rejected the API credentials.",
 "action":"Check the configured LM Studio API key/password and server authentication settings.",
 "retryable":false}
```

The server returns public `error` fields in MCP `tools/call` text content
(with `isError: true`). The CLI emits the same fixed taxonomy. Wrong or
missing local MCP bearer tokens produce HTTP 401 and `MCP_AUTH_FAILED`.
This does **not** establish the caller's Unsloth chat identity.

HTTP 400/422 => PROVIDER_REQUEST_REJECTED, 401/403 =>
PROVIDER_AUTH_FAILED, 404 => PROVIDER_ENDPOINT_NOT_FOUND, 408/504 =>
PROVIDER_TIMEOUT, 429 => PROVIDER_RATE_LIMITED, 5xx =>
PROVIDER_SERVER_ERROR. Socket refusal => PROVIDER_CONNECTION_REFUSED,
other network failure => PROVIDER_CONNECTION_FAILED, and unparseable
payload => PROVIDER_RESPONSE_INVALID.

Job-owner errors are distinguished: INVALID_INPUT, ACCESS_DENIED,
STALE_VERSION, IDEMPOTENCY_CONFLICT, RESUME_NOT_READY. Unrecognized
internal faults return INTERNAL_ERROR. The error output is deliberately
created from fixed code/message/action pairs, never by interpolating
exceptions, HTTP response bodies, URL, raw prompts, tokens, credentials,
file paths or DB messages. No external provider error body is stored or
returned. Python exception chaining remains available only to trusted
debuggers; client responses must NEVER serialize exception tracebacks.

**Retryable does not authorize automatic replay.** A network failure
can occur after an external side effect. The manager must first inspect
job state, fencing, provider liveness and reconciliation status.

## Required independent acceptance matrix still open (#29)

- Real locally installed LM Studio and Unsloth MCP client error UI;
  distinguish wrong password, invalid key, service down, wrong endpoint,
  slow inference and unsupported model.
- Actual provider and tool-call adapter failures, loaded-model residency,
  resource exhaustion, context overflow, malformed tool replies.
- Windows filesystem broker denial (count strikes), disk full/ACL,
  corrupt/locked SQLite, migration failure, resumed job checkpoint,
  wrong chat owner, failed delivery and missing parent ACK.
- Persist code, safe retry guidance and terminal status in a durable,
  authorized per-job audit event, with safe log retention and redaction.
- Accessibility/localization of messages and a clear action for the
  original parent chat (reconnect, correct credentials, inspect/retry,
  request Resume, or reconcile before retry).
- Real fault injection on the target Windows machine. Synthetic CI
  is not a proof of live connection or autonomous worker behavior.

## Test evidence

Synthetic urllib failures, raw-message canaries and loopback MCP
HTTP tests must pass on Windows and Linux with official MCP SDK
compatibility. Do not declare this slice completed before exact-head
CI is green.

**Beta remains NO-GO.** See issue #29.
