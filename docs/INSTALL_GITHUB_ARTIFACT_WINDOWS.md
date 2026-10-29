# Direct GitHub Actions package — Windows MCP pilot

**Experimental MCP connection pilot; not a multi-agent beta or production release.**

The v0.12 package contains experimental agent-loop and LM Studio tool-call *code*, but no worker/file MCP tools are exposed. It is not an autonomous-agent beta. Only synthetic status/admission/abort tools are safe to exercise.

**UPGRADE NOTE:** v0.11 migrates the local pilot SQLite jobs table at startup. Stop all MinionOne instances and take a restorable SQLite backup before installing or launching the upgraded pilot. Never try this migration on private project data; use synthetic tests only.
Use only synthetic test data. Workers, secure file broker, same-chat callbacks
and artifact publication are disabled.

## Download the CI-gated wheel

1. Open https://github.com/Griggelor/MinionOne/actions/workflows/pilot-wheel.yml
2. Select the most recent successful run on the main branch.
3. Confirm three green jobs: Required tests (ubuntu-24.04), Required tests
   (windows-latest) and build-pilot. Build now depends on BOTH test jobs.
4. Under Artifacts, download MinionOne-MCP-PILOT-UNVERIFIED-0.13.0.dev0.
   GitHub may require signing in. Artifacts are retained for 14 days.
5. Extract the ZIP. It contains a Python wheel and SHA256SUMS.txt.

## Verify SHA256 and install (PowerShell)

    $wheel = Get-ChildItem -File *.whl | Select-Object -First 1
    if ($null -eq $wheel) { throw "Wheel missing" }
    $expected = ((Get-Content .\SHA256SUMS.txt -Raw).Trim() -split '\s+')[0]
    $actual = (Get-FileHash $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected.ToLowerInvariant()) { throw "SHA256 mismatch" }
    py -3.13 -m pip install --no-deps --no-index $wheel.FullName

SHA256 detects transport corruption. It is NOT a developer signature or
independent supply-chain attestation.

## Start and connect

    $env:MINIONONE_MCP_TOKEN = py -3.13 -c "import secrets; print(secrets.token_hex(32))"
    py -3.13 -m minionone.mcp_cli --port 8765

In Unsloth Studio (same host as the backend), add a custom HTTP MCP server:
  URL: http://127.0.0.1:8765/mcp
  Header name: Authorization
  Header value: Bearer followed by a space and your generated token

Never put credentials in URL or disable auth; never bind MinionOne to 0.0.0.0.
Detailed setup: docs/WINDOWS_UNSLOTH_MCP_PILOT.md.

Call minionone_status. Expected release_gate NO_GO, worker_execution_enabled
false and parent_thread_attested false. This tests MCP connectivity only.
Never send personal files or allow worker execution. Actual Unsloth Studio
interoperability and target Windows NTFS security are still unverified.

If it fails, report Unsloth version, backend topology, sanitized error code
and steps to reproduce. Do not share the token.
