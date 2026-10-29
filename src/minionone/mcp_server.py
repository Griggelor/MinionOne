"""Local-only MCP Streamable HTTP pilot. No worker execution or chat attestation."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit
from .admission import AdmissionStore, InputRejected, PermissionDenied, IdempotencyConflict, VersionConflict
from .lmstudio_probe import LMStudioProbe, ProbeError

PROTOCOL = "2025-06-18"
TOOLS = [
 {"name":"minionone_status","description":"Report actual implemented safety gates; does not run workers.","inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
 {"name":"minionone_models","description":"Read-only discovery on configured loopback LM Studio.","inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
 {"name":"minionone_admit","description":"Register a durable non-executing job with a previously approved SHA256.","inputSchema":{"type":"object","properties":{"input_digest":{"type":"string"},"operation_id":{"type":"string"}},"required":["input_digest","operation_id"],"additionalProperties":False}},
 {"name":"minionone_job_status","description":"Read an admitted job state.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}},
 {"name":"minionone_job_events","description":"Read redacted manager event metadata.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}},
 {"name":"minionone_abort","description":"Request cancellation of a non-executing job with CAS.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"},"expected_version":{"type":"integer"},"operation_id":{"type":"string"}},"required":["job_id","expected_version","operation_id"],"additionalProperties":False}}
]
TOOL_MAP = {t["name"]: t for t in TOOLS}

def _error(ident, code, message):
    return {"jsonrpc":"2.0","id":ident,"error":{"code":code,"message":message}}

class LocalMCP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, token, database_path, principal="local-operator",
                 thread="configured-pilot", lmstudio_url="http://127.0.0.1:1234"):
        if (type(address) is not tuple or len(address) != 2 or
            address[0] != "127.0.0.1" or type(address[1]) is not int or
            not 0 <= address[1] <= 65535):
            raise ValueError("bind to literal IPv4 loopback only")
        if (type(token) is not str or not 32 <= len(token) <= 512 or
            not token.isascii() or any(ord(c)<33 or ord(c)>126 for c in token)):
            raise ValueError("strong bearer credential required")
        self.credential = token
        self.thread_id = thread
        self.store = AdmissionStore(database_path, credentials={token:(principal,{thread})})
        self.probe = LMStudioProbe(lmstudio_url)
        super().__init__(address, MCPHandler)

    def call_tool(self, name, args):
        if type(args) is not dict or name not in TOOL_MAP:
            raise InputRejected("unknown tool or arguments")
        spec = TOOL_MAP[name]["inputSchema"]
        if not set(spec.get("required",())) <= set(args) or set(args) - set(spec["properties"]):
            raise InputRejected("missing or unexpected arguments")
        token, thread = self.credential, self.thread_id
        if name == "minionone_status":
            return {"phase":"mcp-pilot","release_gate":"NO_GO",
                    "mcp_transport_available":True,"unsloth_live_verified":False,
                    "parent_thread_attested":False,"worker_execution_enabled":False,
                    "windows_file_broker_verified":False,"auto_wakeup_enabled":False}
        if name == "minionone_models":
            return {"models":self.probe.models(),"provider_identity_verified":False}
        if name == "minionone_admit":
            return self.store.admit(token,thread,args["input_digest"],args["operation_id"])
        if name == "minionone_job_status":
            return self.store.status(token,thread,args["job_id"])
        if name == "minionone_job_events":
            return {"events":self.store.events(token,thread,args["job_id"])}
        if name == "minionone_abort":
            return self.store.request_abort(token,thread,args["job_id"],
                                            args["expected_version"],args["operation_id"])
        raise InputRejected("unknown tool")

class MCPHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    server_version="MinionOne-Pilot"
    sys_version=""

    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *_):
        return # Never log secrets, body, paths or client-supplied messages.

    def _send(self, status, payload=None):
        data=b"" if payload is None else json.dumps(payload,ensure_ascii=True,separators=(",",":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type","application/json" if payload is not None else "text/plain")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Connection","close")
        self.end_headers()
        if data: self.wfile.write(data)
        self.close_connection=True

    def _preflight(self):
        port=self.server.server_port
        if self.headers.get("Host","") not in (f"127.0.0.1:{port}",f"localhost:{port}") or self.headers.get("Origin") is not None:
            self._send(403)
            return False
        if self.path != "/mcp":
            self._send(404)
            return False
        if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+self.server.credential):
            self._send(401)
            return False
        return True

    def do_GET(self):
        if self._preflight(): self._send(405)
    def do_DELETE(self):
        if self._preflight(): self._send(405)
    def do_POST(self):
        if not self._preflight(): return
        if self.headers.get("Content-Type","").split(";",1)[0].strip()!="application/json":
            self._send(415);return
        accept=self.headers.get("Accept","")
        if "application/json" not in accept or "text/event-stream" not in accept:
            self._send(406);return
        length=self.headers.get("Content-Length")
        if (length is None or not length.isdecimal() or not 1 <= int(length) <= 16384
                or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding")):
            self._send(413);return
        try:
            raw=self.rfile.read(int(length))
        except (TimeoutError,OSError):
            self._send(408)
            return
        try:
            msg=json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            self._send(200,_error(None,-32700,"Parse error"));return
        if type(msg) is not dict or msg.get("jsonrpc")!="2.0" or type(msg.get("method")) is not str:
            self._send(200,_error(None,-32600,"Invalid Request"));return
        ident=msg.get("id")
        if ident is not None and (type(ident) not in (str,int) or
                                   (type(ident) is str and len(ident)>128)):
            self._send(200,_error(None,-32600,"Invalid Request"));return
        method=msg["method"]
        if ident is None:
            self._send(202);return
        params=msg.get("params",{})
        if type(params) is not dict:
            self._send(200,_error(ident,-32602,"Invalid params"));return
        if method!="initialize" and self.headers.get("MCP-Protocol-Version") not in (None,PROTOCOL):
            self._send(400);return
        try:
            if method=="initialize":
                if type(params.get("protocolVersion")) is not str:
                    raise InputRejected("protocolVersion missing")
                result={"protocolVersion":PROTOCOL,"capabilities":{"tools":{"listChanged":False}},
                        "serverInfo":{"name":"minionone-pilot","version":"0.9.1-dev"}}
            elif method=="ping":
                result={}
            elif method=="tools/list":
                if params.get("cursor") is not None: raise InputRejected("bad cursor")
                result={"tools":TOOLS}
            elif method=="tools/call":
                name=params.get("name")
                if type(name) is not str or name not in TOOL_MAP:
                    self._send(200,_error(ident,-32602,"Unknown tool"));return
                try:
                    value=self.server.call_tool(name,params.get("arguments",{}))
                    result={"content":[{"type":"text","text":json.dumps(value,sort_keys=True)}]}
                except (InputRejected,PermissionDenied,IdempotencyConflict,VersionConflict,
                        ProbeError,ValueError,TypeError):
                    result={"isError":True,"content":[{"type":"text","text":"Operation denied or unavailable."}]}
            else:
                self._send(200,_error(ident,-32601,"Method not found"));return
            self._send(200,{"jsonrpc":"2.0","id":ident,"result":result})
        except (InputRejected,ValueError,TypeError):
            self._send(200,_error(ident,-32602,"Invalid params"))
