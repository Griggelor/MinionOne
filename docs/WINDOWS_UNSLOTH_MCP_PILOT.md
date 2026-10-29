# Windows / Unsloth MCP connection pilot — v0.9.1-dev

> **Experimental only, NOT an operational multi-agent beta.** Six tools exercise the
> real local MCP HTTP transport plus durable *non-executing* job registration. Worker
> execution, sandboxing, output publication and automatic same-chat wakeup remain off.

## Environment

- Windows 11 with Python 3.11+ and Unsloth Studio supporting custom HTTP MCP servers.
- Run the MinionOne server on the **same host as the Unsloth Studio backend**.
  A VM/WSL/remote backend does not share Windows loopback.
- Optional LM Studio OpenAI-compatible HTTP server on 127.0.0.1:1234.

## Install from GitHub

Download and extract https://github.com/Griggelor/MinionOne/archive/refs/heads/main.zip .
Open PowerShell inside the extracted folder containing pyproject.toml.

    py -3.13 -m pip install .

Do not treat this as a verified or signed release.

## Start

In the same PowerShell session generate an operator-only dedicated bearer secret:

    $env:MINIONONE_MCP_TOKEN = py -3.13 -c "import secrets; print(secrets.token_hex(32))"
    py -3.13 -m minionone.mcp_cli --port 8765

It should display:

    MinionOne PILOT: http://127.0.0.1:8765/mcp

Never commit, publish or print the secret in logs/screenshots. This is NOT an Unsloth
API key. Use a fresh token for every operator session and keep the terminal open.

## Connect in Unsloth Studio

In Manage MCP servers, add an HTTP custom MCP server:

- Name: MinionOne Pilot
- URL: http://127.0.0.1:8765/mcp (no trailing slash for this endpoint)
- Request header named Authorization, value: Bearer followed by a space and the
  value of MINIONONE_MCP_TOKEN
- Turn off OAuth for this static-token pilot if prompted.
- Test connection, then enable the server for a tool-capable chat.

If that Unsloth build cannot configure a custom Authorization header, STOP.
Never remove authentication, put the key in the URL, or bind to 0.0.0.0.

## Six exposed tools

1. minionone_status — truthful NO_GO release and gate values.
2. minionone_models — optional read-only LM Studio discovery.
3. minionone_admit — persists a **non-executing** synthetic job record.
4. minionone_job_status — reads that record.
5. minionone_job_events — redacted event metadata.
6. minionone_abort — versioned cancellation request, no worker started.

Synthetic chat smoke test: call minionone_status and check that the result
includes release_gate NO_GO, parent_thread_attested false, worker_execution_enabled
false and windows_file_broker_verified false. Use no private project data.

The supplied input_digest is **NOT a verified sealed input**: job creation is only
an idempotent database admission, not successful attachment or worker execution.
One configured bearer credential is mapped to one local operator identity. This is
not authentication of the actual originating Unsloth chat.

## Required production gates still OPEN

- Authenticated per-chat identity and asynchronous same-chat wakeup.
- Windows handle-bound NTFS sandbox, create-only work and immutable publication.
- Real fenced worker attempts with process/provider recovery.
- LM Studio/Flint real execution and parent model residency on target hardware.
- Independent security assessment, provenance/license review and beta signoff.

If the connection fails: report Unsloth build/version, server log's category only,
MCP Test connection response code and host topology — never send the secret.
Stop the pilot with Ctrl+C.
