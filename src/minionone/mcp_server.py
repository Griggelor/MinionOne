"""Local-only MCP Streamable HTTP pilot. No worker execution or chat attestation."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit
from . import __version__
from .admission import AdmissionStore, InputRejected, PermissionDenied, IdempotencyConflict, VersionConflict
from .lmstudio_probe import LMStudioProbe, ProbeError
from .outbox import OutboxStore
from .attempt_fence import AttemptFenceStore
from .timeout_control import TimeoutControlStore, TimeoutControlDenied
from .diagnostics import diagnostic, safe_tool_fault

PROTOCOL = "2025-06-18"
TOOLS = [
 {"name":"minionone_status","description":"Report actual implemented safety gates; does not run workers.","inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
 {"name":"minionone_models","description":"Read-only discovery on configured loopback LM Studio.","inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
 {"name":"minionone_admit","description":"Register a durable non-executing job with a previously approved SHA256.","inputSchema":{"type":"object","properties":{"input_digest":{"type":"string"},"operation_id":{"type":"string"}},"required":["input_digest","operation_id"],"additionalProperties":False}},
 {"name":"minionone_job_status","description":"Read an admitted job state.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}},
 {"name":"minionone_job_events","description":"Read redacted manager event metadata.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}},
 {"name":"minionone_abort","description":"Request cancellation of a non-executing job with CAS.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"},"expected_version":{"type":"integer"},"operation_id":{"type":"string"}},"required":["job_id","expected_version","operation_id"],"additionalProperties":False}},
 {"name":"minionone_resume_request","description":"Record a parent-authorized resume INTENT for a safely interrupted timed-out job. DOES NOT launch a worker.","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"},"expected_version":{"type":"integer"},"operation_id":{"type":"string"}},"required":["job_id","expected_version","operation_id"],"additionalProperties":False}}
]
TOOL_MAP = {t["name"]: t for t in TOOLS}

WORKSPACE_TOOLS = [
 {'name':'minionone_prepare_workspace',
  'description':'Opt-in manager-only PREPARING workspace in an operator-approved project. Not admitted or executable.',
  'inputSchema':{'type':'object','properties':{'project_id':{'type':'string'},'operation_id':{'type':'string'}},
                 'required':['project_id','operation_id'],'additionalProperties':False}},
 {'name':'minionone_seal_workspace',
  'description':'POSIX/Win32 manager-only development: verify pinned input manifest, store exact bytes in SQLite, admit SAME job ID. No worker launch or target-host approval.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'operation_id':{'type':'string'}},
                 'required':['job_id','expected_version','operation_id'],'additionalProperties':False}},
 {'name':'minionone_verify_workspace_input',
  'description':'Verify that admitted POSIX/Win32 staging input still matches the pinned manager snapshot. NOT worker-start or target-host attestation.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'}},
                 'required':['job_id'],'additionalProperties':False}},
 {'name':'minionone_workspace_snapshot',
  'description':'Authenticated manager metadata for sealed SQLite input (names, hashes, sizes). No content, worker access or on-disk recheck.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'}},
                 'required':['job_id'],'additionalProperties':False}},
 {'name':'minionone_start_workspace_job',
  'description':'EXPERIMENTAL opt-in: dispatch a sealed UTF-8 workspace to the bounded LM Studio document worker; no host write capability, not target-host approved.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'operation_id':{'type':'string'},'model_id':{'type':'string'}},
                 'required':['job_id','expected_version','operation_id','model_id'],'additionalProperties':False}},
 {'name':'minionone_workspace_result',
  'description':'Read verified bounded worker document results, only after manager publication.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'}},
                 'required':['job_id'],'additionalProperties':False}},
 {'name':'minionone_accept_workspace_result',
  'description':'Explicit parent acknowledgement of an exact delivered workspace result digest.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'result_digest':{'type':'string'},'operation_id':{'type':'string'}},
                 'required':['job_id','expected_version','result_digest','operation_id'],'additionalProperties':False}},
 {'name':'minionone_cancel_workspace_job',
  'description':'Fence an experimental running workspace worker by authenticated CAS.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'operation_id':{'type':'string'}},
                 'required':['job_id','expected_version','operation_id'],'additionalProperties':False}},
 {'name':'minionone_workspace_status',
  'description':'Read an owned PREPARING or admitted workspace; never launches worker execution.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'}},
                 'required':['job_id'],'additionalProperties':False}}
]

PILOT_TOOLS = [
 {'name':'minionone_wait_test_job','description':'Wait up to 20 seconds for a test-job outcome, then return one consistent status snapshot. Repeat while still running.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'wait_seconds':{'type':'integer','minimum':1,'maximum':20}},'required':['job_id'],'additionalProperties':False}},
 {'name':'minionone_submit_test_job','description':'Submit bounded UTF-8 test documents to the isolated manager document store. Never reads host paths.',
  'inputSchema':{'type':'object','properties':{'task':{'type':'string','maxLength':3500},'files':{'type':'object','maxProperties':4,'additionalProperties':{'type':'string','maxLength':3500}},'operation_id':{'type':'string'}},'required':['task','files','operation_id'],'additionalProperties':False}},
 {'name':'minionone_run_test_job','description':'Start one real bounded LM Studio test job. Poll minionone_job_status until RESULT_READY, then read and accept the result.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'operation_id':{'type':'string'}},'required':['job_id','expected_version','operation_id'],'additionalProperties':False}},
 {'name':'minionone_test_result','description':'Read the authorized immutable result files and digest; model output remains untrusted data.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'}},'required':['job_id'],'additionalProperties':False}},
 {'name':'minionone_accept_test_result','description':'Explicit parent acceptance of the exact delivered test result. Never executes or merges output into project files.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'result_digest':{'type':'string'},'operation_id':{'type':'string'}},'required':['job_id','expected_version','result_digest','operation_id'],'additionalProperties':False}},
 {'name':'minionone_cancel_test_job','description':'Fence test-job tools and request cancellation. Provider uncertainty remains quarantined.',
  'inputSchema':{'type':'object','properties':{'job_id':{'type':'string'},'expected_version':{'type':'integer'},'operation_id':{'type':'string'}},'required':['job_id','expected_version','operation_id'],'additionalProperties':False}},
]

def _error(ident, code, message):
    return {"jsonrpc":"2.0","id":ident,"error":{"code":code,"message":message}}

class LocalMCP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, token, database_path, principal="local-operator",
                 thread="configured-pilot", lmstudio_url="http://127.0.0.1:1234",
                 enable_test_jobs=False,worker_model=None,worker_api_key=None,
                 min_free_mb=4096,model_call_seconds=60,workspace_projects=None,
                 enable_workspace_jobs=False):
        if (type(address) is not tuple or len(address) != 2 or
            address[0] != "127.0.0.1" or type(address[1]) is not int or
            not 0 <= address[1] <= 65535):
            raise ValueError("bind to literal IPv4 loopback only")
        if (type(token) is not str or not 32 <= len(token) <= 512 or
            not token.isascii() or any(ord(c)<33 or ord(c)>126 for c in token)):
            raise ValueError("strong bearer credential required")
        if type(enable_test_jobs) is not bool:raise ValueError('invalid test-job switch')
        if type(enable_workspace_jobs) is not bool:raise ValueError('invalid workspace execution switch')
        if enable_workspace_jobs and workspace_projects is None:
            raise ValueError('workspace execution requires configured projects')
        self.pilot=None;self._pilot_lock=None;self.workspace=None;self.workspace_jobs=None
        self.enable_workspace_jobs=enable_workspace_jobs
        self.tools=TOOLS+(PILOT_TOOLS if enable_test_jobs else [])+(WORKSPACE_TOOLS if workspace_projects is not None else [])
        if not enable_workspace_jobs:
            self.tools=[x for x in self.tools if x['name'] not in (
                'minionone_start_workspace_job','minionone_workspace_result',
                'minionone_accept_workspace_result','minionone_cancel_workspace_job')]
        self.tool_map={t['name']:t for t in self.tools}
        if enable_test_jobs or enable_workspace_jobs:
            from .pilot_lock import PilotLock
            self._pilot_lock=PilotLock(database_path)
        self.credential = token
        self.thread_id = thread
        try:
            self.store = AdmissionStore(database_path, credentials={token:(principal,{thread})})
            if workspace_projects is not None:
                from .workspace_preparation import WorkspacePreparation
                self.workspace = WorkspacePreparation(self.store,workspace_projects)
            # Recover external-effect uncertainty BEFORE opening the listening socket.
            # Partial recovery failures propagate; never serve requests in that case.
            self.outbox = OutboxStore(database_path)
            self.attempt_fences = AttemptFenceStore(database_path)
            self.timeout_control = TimeoutControlStore(self.store)
            outbox_count = self.outbox.quarantine_in_flight()
            attempt_count = self.attempt_fences.fence_unreconciled()
            self.recovery_summary = {
                "outbox_quarantined": outbox_count,
                "attempts_fenced": attempt_count,
            }
            if enable_test_jobs or enable_workspace_jobs:
                from .pilot_jobs import PilotJobs,PilotConfig
                self.pilot=PilotJobs(self.store,PilotConfig(worker_model,lmstudio_url,worker_api_key,
                                    model_call_seconds=model_call_seconds,min_free_mb=min_free_mb))
                if enable_workspace_jobs:
                    from .workspace_jobs import WorkspaceJobs
                    self.workspace_jobs=WorkspaceJobs(self.workspace,self.pilot,token,thread)
                    self.pilot.workspace_execution=self.workspace_jobs
                count=self.pilot.recover()
                if enable_test_jobs:self.recovery_summary['test_jobs_quarantined']=count
                if enable_workspace_jobs:self.recovery_summary['workspace_jobs_recovery_checked']=True
            self.probe = LMStudioProbe(lmstudio_url,api_key=worker_api_key)
            self._slots=threading.BoundedSemaphore(8)
            super().__init__(address, MCPHandler)
        except BaseException:
            if self._pilot_lock is not None:self._pilot_lock.close()
            raise

    def server_close(self):
        try:
            if self.pilot is not None:self.pilot.close()
        finally:
            try:super().server_close()
            finally:
                if self._pilot_lock is not None:self._pilot_lock.close()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self._slots.release()

    def call_tool(self, name, args):
        if type(args) is not dict or name not in self.tool_map:
            raise InputRejected("unknown tool or arguments")
        spec = self.tool_map[name]["inputSchema"]
        if not set(spec.get("required",())) <= set(args) or set(args) - set(spec["properties"]):
            raise InputRejected("missing or unexpected arguments")
        token, thread = self.credential, self.thread_id
        if name == "minionone_status":
            return {"phase":"system-test-pilot" if self.pilot else "mcp-pilot","release_gate":"NO_GO",
                    "test_readiness":"READY_FOR_LOCAL_SYSTEM_TEST" if self.pilot else "CONNECTION_ONLY",
                    "mcp_transport_available":True,"unsloth_live_verified":False,
                    "parent_thread_attested":False,"worker_execution_enabled":self.pilot is not None,
                    "worker_file_backend":"sqlite_documents" if self.pilot else None,
                    "host_filesystem_tools_enabled":False,
                    "windows_file_broker_verified":False,"auto_wakeup_enabled":False,
                    "workspace_preparation_enabled":self.workspace is not None,
                    "workspace_execution_enabled":self.workspace_jobs is not None,
                    "workspace_execution_mode":"OPT_IN_DEV_SQLITE_SANDBOX" if self.workspace_jobs else None}
        if name == "minionone_models":
            return {"models":self.probe.models(),"provider_identity_verified":False}
        if name == "minionone_admit":
            return self.store.admit(token,thread,args["input_digest"],args["operation_id"])
        if name == "minionone_job_status":
            return self.store.status(token,thread,args['job_id'],include_timeout=True)
        if name == "minionone_job_events":
            return {"events":self.store.events(token,thread,args["job_id"])}
        if name == "minionone_abort":
            return self.store.request_abort(token,thread,args["job_id"],
                                            args["expected_version"],args["operation_id"])
        if name == "minionone_resume_request":
            return self.timeout_control.request_resume(token,thread,args["job_id"],
                                      args["expected_version"],args["operation_id"])
        if self.workspace is not None:
            if name == 'minionone_prepare_workspace':
                receipt=self.workspace.reserve(token,thread,args['project_id'],args['operation_id'])
                # Model discovery is optional. Never roll back a durable reservation
                # because the provider happens to be unavailable.
                try:
                    ids=self.probe.models()
                    receipt['models']=[{'model_id':x,'description':'Loaded LM Studio model; worker capabilities unverified'} for x in ids]
                    receipt['model_discovery']='AVAILABLE_UNVERIFIED'
                except ProbeError:
                    receipt['models']=[]
                    receipt['model_discovery']='UNAVAILABLE'
                return receipt
            if name == 'minionone_seal_workspace':
                return self.workspace.seal(token,thread,args['job_id'],args['expected_version'],args['operation_id'])
            if name == 'minionone_verify_workspace_input':
                return self.workspace.verify_input(token,thread,args['job_id'])
            if name == 'minionone_workspace_snapshot':
                from .workspace_input_snapshot import SealedWorkspaceInput
                return SealedWorkspaceInput(self.workspace).metadata(token,thread,args['job_id'])
            if name == 'minionone_workspace_status':
                return self.workspace.status(token,thread,args['job_id'])
            if self.workspace_jobs is not None:
                if name == 'minionone_start_workspace_job':
                    return self.workspace_jobs.start(token,thread,args['job_id'],
                        args['expected_version'],args['operation_id'],args['model_id'])
                if name == 'minionone_workspace_result':
                    return self.workspace_jobs.result(token,thread,args['job_id'])
                if name == 'minionone_accept_workspace_result':
                    return self.workspace_jobs.acknowledge(token,thread,args['job_id'],
                        args['expected_version'],args['result_digest'],args['operation_id'])
                if name == 'minionone_cancel_workspace_job':
                    return self.workspace_jobs.cancel(token,thread,args['job_id'],
                        args['expected_version'],args['operation_id'])
        if self.pilot is not None:
            if name=='minionone_wait_test_job':
                seconds=args.get('wait_seconds',10)
                if type(seconds) is not int or not 1<=seconds<=20:raise InputRejected('invalid bounded wait')
                deadline=time.monotonic()+seconds
                while True:
                    status=self.store.status(token,thread,args['job_id'],include_timeout=True)
                    if 'test_job' not in status:raise InputRejected('not a test job')
                    if status['state'] not in ('QUEUED','RESOURCE_CHECK','RUNNING','CANCEL_REQUESTED') or time.monotonic()>=deadline:return status
                    time.sleep(min(.25,max(0,deadline-time.monotonic())))
            if name=='minionone_submit_test_job':
                return self.pilot.submit(token,thread,args['task'],args['files'],args['operation_id'])
            if name=='minionone_run_test_job':
                return self.pilot.start(token,thread,args['job_id'],args['expected_version'],args['operation_id'])
            if name=='minionone_test_result':return self.pilot.result(token,thread,args['job_id'])
            if name=='minionone_accept_test_result':
                return self.pilot.acknowledge(token,thread,args['job_id'],args['expected_version'],args['result_digest'],args['operation_id'])
            if name=='minionone_cancel_test_job':
                return self.pilot.cancel(token,thread,args['job_id'],args['expected_version'],args['operation_id'])
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
            self._send(403,{"error":diagnostic("MCP_REQUEST_DENIED").to_dict()})
            return False
        if self.path != "/mcp":
            self._send(404)
            return False
        authorization=self.headers.get('Authorization','')
        if not authorization.isascii() or not hmac.compare_digest(authorization,"Bearer "+self.server.credential):
            self._send(401,{"error":diagnostic("MCP_AUTH_FAILED").to_dict()})
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
                        "serverInfo":{"name":"minionone-pilot","version":__version__}}
            elif method=="ping":
                result={}
            elif method=="tools/list":
                if params.get("cursor") is not None: raise InputRejected("bad cursor")
                result={"tools":self.server.tools}
            elif method=="tools/call":
                name=params.get("name")
                if type(name) is not str or name not in self.server.tool_map:
                    self._send(200,_error(ident,-32602,"Unknown tool"));return
                try:
                    value=self.server.call_tool(name,params.get("arguments",{}))
                    result={"content":[{"type":"text","text":json.dumps(value,sort_keys=True)}]}
                except Exception as exc:
                    # Fixed taxonomy only: never reflect raw provider responses,
                    # client content, credentials or paths in the MCP result.
                    fault=safe_tool_fault(exc)
                    result={"isError":True,"content":[{"type":"text",
                        "text":json.dumps({"ok":False,"error":fault.to_dict()},
                                          sort_keys=True,separators=(",",":"))}]}
            else:
                self._send(200,_error(ident,-32601,"Method not found"));return
            self._send(200,{"jsonrpc":"2.0","id":ident,"result":result})
        except (InputRejected,ValueError,TypeError):
            self._send(200,_error(ident,-32602,"Invalid params"))
