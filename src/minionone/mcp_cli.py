"""Start the opt-in localhost MCP pilot. No file broker, worker execution or auto-wakeup."""
from __future__ import annotations
import argparse
import errno
import json
import os
from pathlib import Path
import sys
from .mcp_server import LocalMCP
from .diagnostics import diagnostic, safe_tool_fault

def main(argv=None):
    p=argparse.ArgumentParser(description="MinionOne local MCP transport pilot (NOT production-ready)")
    p.add_argument("--port",type=int,default=8765)
    p.add_argument("--db",default=None,help="Private state database (operator-controlled path)")
    p.add_argument("--lmstudio-url",default="http://127.0.0.1:1234")
    args=p.parse_args(argv)
    token=os.environ.get("MINIONONE_MCP_TOKEN")
    if token is None or len(token)<32:
        p.error("MINIONONE_MCP_TOKEN must be provided as a strong secret in the environment")
    if type(args.port) is not int or not 1024 <= args.port <= 65535:
        p.error("use a local unprivileged TCP port 1024-65535")
    try:
        if args.db is None:
            root=Path.home()/".minionone"
            root.mkdir(mode=0o700,parents=True,exist_ok=True)
            os.chmod(root,0o700) if os.name=="posix" else None
            db=root/"pilot-jobs.sqlite"
        else:
            db=Path(args.db)
        server=LocalMCP(("127.0.0.1",args.port),token=token,
                        database_path=db,lmstudio_url=args.lmstudio_url)
    except Exception as exc:
        fault=safe_tool_fault(exc)
        if isinstance(exc,OSError) and exc.errno==errno.EADDRINUSE:
            fault=diagnostic('MCP_ADDRESS_IN_USE')
        elif isinstance(exc,ValueError) and fault.code in ('INVALID_INPUT','INTERNAL_ERROR'):
            fault=diagnostic('CONFIGURATION_INVALID')
        print(json.dumps({'ok':False,'started':False,'error':fault.to_dict()},
                         sort_keys=True,separators=(',',':')),file=sys.stderr)
        return 2
    print(f"MinionOne PILOT: http://127.0.0.1:{server.server_port}/mcp",flush=True)
    print("PRE-ALPHA: no worker execution, no verified Unsloth chat ownership, no Windows sandbox.",flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

if __name__=="__main__":
    raise SystemExit(main())
