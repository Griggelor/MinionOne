"""G1a external protocol test using real official MCP SDK client transports.

This is NOT a live Unsloth Studio connection or original-chat proof.
Run in a separate CI job after installing an EXACT pinned mcp version.
No raw user prompts, worker actions, project files or network egress.
"""
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import tempfile
import threading
from minionone.mcp_server import LocalMCP


TOKEN = "sdk-interop-private-test-" + "f" * 48


async def exercise(url):
    from mcp import ClientSession
    major = int(version("mcp").split(".")[0])
    if major == 1:
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(
                url, headers={"Authorization": "Bearer " + TOKEN},
                timeout=10, sse_read_timeout=10) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await verify_tools(session)
    elif major == 2:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        async with httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + TOKEN},
                timeout=httpx2.Timeout(10.0)) as http_client:
            async with streamable_http_client(
                    url, http_client=http_client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await verify_tools(session)
    else:
        raise AssertionError("SDK major version was not pinned for this test")


async def verify_tools(session):
    init = await session.initialize()
    assert init.server_info.name == "minionone-pilot", "wrong MCP server identity"
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert names == {
        "minionone_status", "minionone_models", "minionone_admit",
        "minionone_job_status", "minionone_job_events", "minionone_abort",
    }, "unexpected MCP capability escalation or missing tool"
    response = await session.call_tool("minionone_status", arguments={})
    assert not response.isError, "server rejected official MCP client call"
    result = json.loads(response.content[0].text)
    assert result["release_gate"] == "NO_GO"
    assert result["worker_execution_enabled"] is False
    assert result["parent_thread_attested"] is False
    assert result["windows_file_broker_verified"] is False
    print("MCP_OFFICIAL_CLIENT_PASS version=" + version("mcp") +
          " tools=" + str(len(names)) + " release_gate=NO_GO")


def main():
    with tempfile.TemporaryDirectory(prefix="minionone-sdk-") as root:
        server = LocalMCP(("127.0.0.1", 0), token=TOKEN,
                          database_path=Path(root) / "jobs.sqlite")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(asyncio.wait_for(
                exercise("http://127.0.0.1:%d/mcp" % server.server_port),
                timeout=20))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
