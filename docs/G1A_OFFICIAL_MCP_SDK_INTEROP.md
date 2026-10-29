# G1a MCP protocol interoperability — official clients, NOT Unsloth live

This PR adds a **real official Python MCP SDK** interop matrix on localhost
against MinionOne's existing authenticated Streamable HTTP pilot.

## Tested integration boundary

- MCP Python SDK 1.30.0, with supported v1 streamablehttp_client transport.
- MCP Python SDK 2.3.0, with supported v2 streamable_http_client plus httpx2.
- Both versions on Ubuntu 24.04 and windows-latest using Python 3.13.
- Initialize, list six tools, invoke minionone_status with a bearer secret.
- Check release_gate NO_GO and false flags for worker execution, parent chat
  attestation, and Windows broker verification.
- Real TCP loopback and real SDK protocols; all data synthetic. The server
  runs and shuts down inside the test process.

Official SDK migration guidance:
https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md

SDK distribution release history:
https://pypi.org/project/mcp/

## Independent-role review issues

The first red CI run exposed an error in the integration **test harness**:
SDK v1 exposes some CamelCase Pydantic attribute names, while v2 exposes
SnakeCase. The test was corrected to assert the serialized JSON-RPC wire
aliases with model_dump(by_alias=True), which is the actual interoperability
contract rather than incidental SDK Python field names.

Any SDK failure is a hard failed test, not a timeout-derived success. No
Unsloth Studio product claim is made from these external SDK tests.

## Outstanding release gates

- Does the **actual installed** Unsloth Studio build attach our custom
  Authorization header to the correct backend-host localhost address?
- Does Unsloth expose a verified parent/chat identity in incoming MCP tool
  calls? Our current static token does **not** prove it.
- Does an authenticated same-chat wakeup/notification API exist? What are
  its busy-thread, retry and stale-identity semantics?
- If not, keep callback and worker operations OFF and require a reviewed
  explicit polling/manual protocol ADR instead.
- Windows file broker and resource/worker execution remain unimplemented.

The G1a and community beta release gates remain BLOCKED regardless of
four green official-SDK protocol tests. This is transport compatibility,
not authentication of a real Unsloth chat.
