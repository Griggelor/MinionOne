"""Local operator launcher, real-system exercise and artifact export.

No model output is executed. Secrets live outside the distributable bundle.
The synthetic self-test verifies returned content before sending parent ACK.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
import http.client
import getpass
import errno
import json
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import sys
import time
import zipfile
from . import __version__
from .diagnostics import diagnostic,safe_tool_fault
from .mcp_server import LocalMCP
from .admission import AdmissionStore,_digest
from .pilot_jobs import PilotJobs,PilotConfig,PilotError,_name,available_memory_mb
from .pilot_lock import PilotLock

def default_state():
    if os.name=='nt':
        base=os.environ.get('LOCALAPPDATA')
        if not base:raise PilotError('PILOT_NOT_CONFIGURED')
        return Path(base)/'MinionOne'/'SystemTest'
    return Path.home()/'.minionone'/'system-test'

def _dpapi(data,decrypt=False):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise PilotError('PILOT_CREDENTIAL_UNAVAILABLE')
    try:return ctypes.string_at(out.data,out.length)
    finally:kernel.LocalFree(out.data)

def _token(state,create=False):
    path=state/'credential.bin'
    if not path.exists():
        if not create:raise PilotError('PILOT_NOT_CONFIGURED')
        raw=secrets.token_hex(32).encode('ascii')
        encoded=b'DP1'+_dpapi(raw) if os.name=='nt' else b'PX1'+raw
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as file:file.write(encoded)
    data=path.read_bytes()
    if os.name=='nt' and data[:3]==b'DP1':raw=_dpapi(data[3:],True)
    elif os.name!='nt' and data[:3]==b'PX1':raw=data[3:]
    else:raise PilotError('PILOT_CREDENTIAL_UNAVAILABLE')
    try:token=raw.decode('ascii')
    except UnicodeError:raise PilotError('PILOT_CREDENTIAL_UNAVAILABLE') from None
    if re.fullmatch(r'[a-f0-9]{64}',token) is None:raise PilotError('PILOT_CREDENTIAL_UNAVAILABLE')
    return token

def _settings(state):
    try:config=json.loads((state/'settings.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):raise PilotError('PILOT_NOT_CONFIGURED') from None
    if (type(config) is not dict or type(config.get('port')) is not int or
        not 1024<=config['port']<=65535 or type(config.get('model')) is not str):
        raise PilotError('PILOT_NOT_CONFIGURED')
    return config

class PilotClient:
    def __init__(self,port,token):self.port=port;self.token=token;self.counter=0
    def request(self,method,params,*,credential=None,timeout=25):
        self.counter+=1
        body=json.dumps({'jsonrpc':'2.0','id':self.counter,'method':method,'params':params}).encode()
        try:
            with closing(http.client.HTTPConnection('127.0.0.1',self.port,timeout=timeout)) as conn:
                conn.request('POST','/mcp',body,headers={'Authorization':'Bearer '+(self.token if credential is None else credential),
                    'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
                response=conn.getresponse();raw=response.read(1024*1024+1)
                if response.status==401:raise PilotError('MCP_AUTH_FAILED')
                if response.status!=200 or len(raw)>1024*1024:raise PilotError('PILOT_SERVER_UNAVAILABLE')
        except (TimeoutError,OSError):raise PilotError('PILOT_SERVER_UNAVAILABLE') from None
        try:return json.loads(raw)
        except ValueError:raise PilotError('PILOT_SERVER_UNAVAILABLE') from None
    def tool(self,name,args):
        reply=self.request('tools/call',{'name':name,'arguments':args})
        try:
            result=reply['result'];value=json.loads(result['content'][0]['text'])
            if result.get('isError'):raise PilotError(value['error']['code'])
            return value
        except (KeyError,IndexError,ValueError,TypeError):raise PilotError('PILOT_SERVER_UNAVAILABLE') from None

def verify_result(value):
    if type(value) is not dict or type(value.get('files')) is not list or not 1<=len(value['files'])<=32:
        raise PilotError('PILOT_FILE_CHANGED')
    seen=set();pairs=[]
    for file in value['files']:
        if type(file) is not dict:raise PilotError('PILOT_FILE_CHANGED')
        try:name=_name(file['name']);content=file['text']
        except (KeyError,PermissionError):raise PilotError('PILOT_FILE_CHANGED') from None
        if name in seen or type(content) is not str or len(content.encode())>8192:raise PilotError('PILOT_FILE_CHANGED')
        seen.add(name);digest=sha256(content.encode()).hexdigest()
        if digest!=file.get('sha256'):raise PilotError('PILOT_FILE_CHANGED')
        pairs.append((name,digest))
    if _digest(sorted(pairs))!=value.get('result_digest'):raise PilotError('PILOT_FILE_CHANGED')
    return value

def export_result(value,destination):
    verify_result(value)
    with open(destination,'xb') as output:
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for file in value['files']:
                info=zipfile.ZipInfo('output/'+_name(file['name']));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,file['text'].encode())
            info=zipfile.ZipInfo('RESULT_MANIFEST.json');info.compress_type=zipfile.ZIP_DEFLATED
            manifest={k:value[k] for k in ('job_id','result_digest','untrusted_model_output')}
            manifest['files']=[{'name':f['name'],'sha256':f['sha256']} for f in value['files']]
            archive.writestr(info,json.dumps(manifest,sort_keys=True,indent=2).encode())

def facts():
    return {'version':__version__,'python':platform.python_version(),'os':platform.system(),
            'windows_build':sys.getwindowsversion().build if os.name=='nt' else None,
            'available_memory_mb':available_memory_mb(),
            'worker_file_backend':'sqlite_documents','native_windows_project_file_broker':False,
            'unsloth_chat_identity_attested':False,'auto_wakeup_enabled':False}

def exercise(client,*,timeout=900):
    report={'schema_version':1,'started_utc':datetime.now(timezone.utc).isoformat(),
            'facts':facts(),'steps':[],'passed':False,'provider':'configured-loopback-http',
            'provider_identity_verified':False}
    job=None
    try:
        client.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'minionone-systemtest','version':__version__}})
        gate=client.tool('minionone_status',{})
        if not gate.get('worker_execution_enabled') or gate.get('worker_file_backend')!='sqlite_documents':raise PilotError('PILOT_TEST_MODE_DISABLED')
        report['steps'].append({'step':'authenticated_mcp','passed':True})
        try:client.request('ping',{},credential='synthetic-deliberately-wrong-credential')
        except PilotError as exc:
            if exc.diagnostic.code!='MCP_AUTH_FAILED':raise
        else:raise PilotError('PILOT_STATE_INVALID')
        report['steps'].append({'step':'wrong_token_rejected','passed':True})
        operation='systemtest-'+secrets.token_hex(12)
        task=('Read input/sample.txt. Use create_work to create result.txt containing exactly the uppercase '
              'text of sample.txt, no additional prose. Then call publish_artifact for result.txt and finish.')
        job=client.tool('minionone_submit_test_job',{'task':task,'files':{'sample.txt':'minionone systemtest ok'},'operation_id':operation})
        report['job_id']=job['job_id'];report['steps'].append({'step':'sealed_test_documents','passed':True})
        receipt=client.tool('minionone_run_test_job',{'job_id':job['job_id'],'expected_version':job['version'],'operation_id':operation+'-run'})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=client.tool('minionone_wait_test_job',{'job_id':job['job_id'],
                    'wait_seconds':max(1,min(10,int(deadline-time.monotonic())))})
            if state['state']=='RESULT_READY':break
            if state['state'] in ('FAILED','NEEDS_RECONCILIATION','CANCELLED','WAITING_FOR_PARENT'):
                error=state.get('test_job',{}).get('error',{}).get('code','PILOT_JOB_FAILED');raise PilotError(error)
        else:raise PilotError('PROVIDER_TIMEOUT')
        report['steps'].append({'step':'real_worker_result_ready','passed':True})
        report['worker_metrics']={k:state['test_job'][k] for k in
                  ('model_turns','tool_calls','tokens_used','denials','worker_model')}
        report['worker_metrics']['token_accounting']='provider-reported'
        result=verify_result(client.tool('minionone_test_result',{'job_id':job['job_id']}))
        output=[f for f in result['files'] if f['name']=='result.txt']
        if len(output)!=1 or output[0]['text'].strip()!='MINIONONE SYSTEMTEST OK':raise PilotError('PILOT_RESULT_UNEXPECTED')
        report['result_digest']=result['result_digest']
        report['steps'].append({'step':'result_content_and_digest_verified','passed':True})
        ack=client.tool('minionone_accept_test_result',{'job_id':job['job_id'],'expected_version':result['version'],
                    'result_digest':result['result_digest'],'operation_id':operation+'-ack'})
        if ack['state']!='COMPLETED':raise PilotError('PILOT_STATE_INVALID')
        report['steps'].append({'step':'authenticated_parent_ack','passed':True});report['passed']=True
    except Exception as exc:
        report['error']=safe_tool_fault(exc).to_dict()
        if job is not None:
            try:
                current=client.tool('minionone_job_status',{'job_id':job['job_id']})
                report['last_state']=current['state']
                if current['state'] in ('QUEUED','RESOURCE_CHECK','RUNNING','WAITING_FOR_PARENT'):
                    client.tool('minionone_cancel_test_job',{'job_id':job['job_id'],'expected_version':current['version'],
                                'operation_id':'systemtest-cancel-'+secrets.token_hex(8)})
            except Exception:pass
    report['finished_utc']=datetime.now(timezone.utc).isoformat();return report


def _synthetic_workspace_folder(project_root, job):
    """Operator-side path check; never trust an arbitrary MCP-returned path."""
    from .workspace_preparation import _JOB
    root=Path(project_root)
    if not root.is_absolute() or not root.is_dir():
        raise PilotError('CONFIGURATION_INVALID')
    # Do not compare Windows string canonicalization: volume aliases or
    # runner drive spelling can differ without making the directory a link.
    # The native manager pins and verifies the actual root handle identity.
    root_stat=root.lstat()
    if (not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink() or
            (os.name=='nt' and root_stat.st_file_attributes &
             getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400))):
        raise PilotError('PILOT_FILE_CHANGED')
    if type(job) is not str or _JOB.fullmatch(job) is None:
        raise PilotError('PILOT_FILE_CHANGED')
    folder=root/'.minionone'/'jobs'/job
    for p in (root,root/'.minionone',root/'.minionone'/'jobs',folder,
              folder/'input',folder/'work',folder/'output'):
        item=p.lstat()
        if not stat.S_ISDIR(item.st_mode) or p.is_symlink():
            raise PilotError('PILOT_FILE_CHANGED')
        if os.name=='nt' and item.st_file_attributes & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise PilotError('PILOT_FILE_CHANGED')
    return folder


def _create_synthetic_file(folder,name,blob):
    """Create one fixed test document, never overwrite existing job input."""
    if type(blob) is not bytes or name not in ('TASK.md','sample.txt','manifest.json'):
        raise PilotError('CONFIGURATION_INVALID')
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(folder/name,flags,0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())


def workspace_exercise(client, *, project_id, project_root, model, timeout=900):
    """Synthetic parent-side end-to-end run with real sealed project folders.

    Does not certify native worker handle confinement or true Unsloth identity.
    No project data is submitted, and the output is never executed/merged.
    """
    from .workspace_preparation import _PROJECT
    report={'schema_version':1,
            'started_utc':datetime.now(timezone.utc).isoformat(),
            'passed':False,'release_gate':'NO_GO',
            'test':'synthetic_sealed_workspace',
            'provider_identity_verified':False,
            'target_parent_attested':False,
            'steps':[]}
    try:
        if (type(project_id) is not str or _PROJECT.fullmatch(project_id) is None or
                type(model) is not str or not model or
                type(timeout) is not int or not 1<=timeout<=1800):
            raise PilotError('CONFIGURATION_INVALID')
        # Check the trusted operator-selected directory BEFORE reserving a job.
        root=Path(project_root)
        if not root.is_absolute() or not root.is_dir():
            raise PilotError('CONFIGURATION_INVALID')
        root_stat=root.lstat()
        if (not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink() or
                (os.name=='nt' and root_stat.st_file_attributes &
                 getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400))):
            raise PilotError('CONFIGURATION_INVALID')
        gate=client.tool('minionone_status',{})
        expected_backend=('native_win32_work_sqlite_output' if os.name=='nt'
                          else 'native_posix_work_sqlite_output')
        if (gate.get('release_gate')!='NO_GO' or
                gate.get('workspace_execution_enabled') is not True or
                gate.get('workspace_execution_mode')!='OPT_IN_DEV_SQLITE_SANDBOX' or
                gate.get('worker_file_backend')!=expected_backend or
                gate.get('host_filesystem_tools_enabled') is not False):
            raise PilotError('PILOT_TEST_MODE_DISABLED')
        report['steps'].append({'step':'opt_in_server_gate','passed':True})
        operation='ws-synthetic-'+secrets.token_hex(12)
        prepared=client.tool('minionone_prepare_workspace',{
            'project_id':project_id,'operation_id':operation+'-prepare'})
        job=prepared.get('job_id')
        folder=_synthetic_workspace_folder(root,job)
        actual=Path(prepared.get('input_dir',''))
        if (os.path.normcase(str(actual.absolute())) !=
                os.path.normcase(str((folder/'input').absolute())) or
                prepared.get('state')!='PREPARING' or prepared.get('version')!=0):
            raise PilotError('PILOT_FILE_CHANGED')
        report['job_id']=job
        task=(b'Read input/sample.txt and create output/result.txt with exactly '
              b'the uppercase version of the input text, no extra text. '
              b'Use create_work, publish_artifact, and finish.')
        sample=b'minionone workspace ok'
        manifest=json.dumps({'files':{
            'TASK.md':sha256(task).hexdigest(),
            'sample.txt':sha256(sample).hexdigest()}},
            sort_keys=True,separators=(',',':')).encode('utf-8')
        for name,blob in (('TASK.md',task),('sample.txt',sample),
                          ('manifest.json',manifest)):
            _create_synthetic_file(folder/'input',name,blob)
        sealed=client.tool('minionone_seal_workspace',{
            'job_id':job,'expected_version':0,'operation_id':operation+'-seal'})
        if sealed.get('job_id')!=job or sealed.get('state')!='ACCEPTED' or sealed.get('input_sealed') is not True:
            raise PilotError('PILOT_STATE_INVALID')
        report['steps'].append({'step':'real_manager_input_seal','passed':True})
        client.tool('minionone_start_workspace_job',{
            'job_id':job,'expected_version':sealed['version'],
            'operation_id':operation+'-start','model_id':model})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            snapshot=client.tool('minionone_job_status',{'job_id':job})
            state=snapshot.get('state')
            if state=='RESULT_READY':break
            if state in ('FAILED','CANCELLED','NEEDS_RECONCILIATION','WAITING_FOR_PARENT'):
                raise PilotError('PILOT_JOB_FAILED')
            if state not in ('QUEUED','RESOURCE_CHECK','RUNNING'):
                raise PilotError('PILOT_STATE_INVALID')
            time.sleep(min(0.5,max(0,deadline-time.monotonic())))
        else:raise PilotError('PROVIDER_TIMEOUT')
        report['steps'].append({'step':'bounded_worker_result_ready','passed':True})
        result=verify_result(client.tool('minionone_workspace_result',{'job_id':job}))
        if result.get('job_id') != job or len(result['files'])!=1:
            raise PilotError('PILOT_RESULT_UNEXPECTED')
        record=result['files'][0]
        expected=b'MINIONONE WORKSPACE OK'
        if record['name']!='result.txt' or record['text'].encode('utf-8')!=expected:
            raise PilotError('PILOT_RESULT_UNEXPECTED')
        # Native Work is not just an SQLite document mirror: validate the
        # actual physical Worker bytes BEFORE accepting the result/ACK.
        work=folder/'work'/'result.txt'
        work_details=work.lstat()
        work_blob=work.read_bytes()
        if (not stat.S_ISREG(work_details.st_mode) or work.is_symlink() or
                work_details.st_nlink!=1 or
                (os.name=='nt' and work_details.st_file_attributes &
                 getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)) or
                work_blob!=expected or
                sha256(work_blob).hexdigest()!=record['sha256']):
            raise PilotError('PILOT_FILE_CHANGED')
        report['worker_file_backend']=expected_backend
        report['steps'].append({'step':'real_native_work_file_and_digest_verified','passed':True})
        artifact=folder/'output'/'result.txt'
        details=artifact.lstat()
        if (not stat.S_ISREG(details.st_mode) or artifact.is_symlink() or
                (os.name=='nt' and details.st_file_attributes &
                getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)) or
                sha256(artifact.read_bytes()).hexdigest()!=record['sha256'] or
                artifact.read_bytes()!=work_blob):
            raise PilotError('PILOT_FILE_CHANGED')
        report['result_digest']=result['result_digest']
        report['steps'].append({'step':'published_file_and_digest_verified','passed':True})
        accepted=client.tool('minionone_accept_workspace_result',{
            'job_id':job,'expected_version':result['version'],
            'result_digest':result['result_digest'],
            'operation_id':operation+'-ack'})
        if accepted.get('state')!='COMPLETED' or accepted.get('job_id')!=job:
            raise PilotError('PILOT_STATE_INVALID')
        report['steps'].append({'step':'exact_parent_ack','passed':True})
        report['passed']=True
    except Exception as exc:
        report['error']=safe_tool_fault(exc).to_dict()
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    return report


def diagnostic_report(client,job_id):
    """Build a bounded support report; NEVER include task text or raw MCP params."""
    snapshot=client.tool('minionone_job_status',{'job_id':job_id})
    safe={k:snapshot[k] for k in ('job_id','state','version','created_utc','updated_utc')
          if k in snapshot}
    worker=snapshot.get('workspace_job',snapshot.get('test_job',{}))
    if type(worker) is dict:
        safe['worker_metrics']={k:worker[k] for k in
            ('attempt_epoch','model_turns','tool_calls','denials',
             'tokens_used','progress_percent','provider_state_uncertain')
             if k in worker}
        fault=worker.get('error')
        if type(fault) is dict and type(fault.get('code')) is str:
            safe['error_code']=safe_tool_fault(PilotError(fault['code'])).code
    entries=[]
    after=0
    previous_trace=None
    for _ in range(22):  # Up to 2048 durable entries, 100 per MCP page.
        page=client.tool('minionone_job_trace',
                         {'job_id':job_id,'after_seq':after,'limit':100})
        if (page.get('integrity')!='VERIFIED_HASH_CHAIN' or
                type(page.get('events')) is not list or len(page['events'])>100 or
                page.get('job_id')!=job_id or
                (previous_trace is not None and previous_trace!=page.get('trace_id'))):
            raise PilotError('PILOT_FILE_CHANGED')
        previous_trace=page['trace_id']
        for item in page['events']:
            if type(item) is not dict or item.get('seq')!=after+1:
                raise PilotError('PILOT_FILE_CHANGED')
            entries.append(item)
            after+=1
        if not page.get('has_more'):
            if page.get('total_events')!=len(entries):
                raise PilotError('PILOT_FILE_CHANGED')
            return {'schema_version':1,'application_version':__version__,
                'release_gate':'NO_GO','redaction':'SAFE_METADATA_ONLY',
                'generated_utc':datetime.now(timezone.utc).isoformat(),
                'job':safe,'trace_id':previous_trace,'trace_events':entries,
                'verified_events':len(entries)}
        if not page['events']:raise PilotError('PILOT_FILE_CHANGED')
    raise PilotError('PILOT_FILE_CHANGED')


def export_diagnostics(client,job_id,destination):
    report=diagnostic_report(client,job_id)
    payload=json.dumps(report,indent=2,sort_keys=True,ensure_ascii=True).encode('ascii')
    if len(payload)>1024*1024:raise PilotError('PILOT_FILE_CHANGED')
    handle=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(handle,'wb',closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(handle)
    return {'report':str(destination),'event_count':report['verified_events']}


def main(argv=None):
    parser=argparse.ArgumentParser(description='MinionOne complete local-system test pilot')
    parser.add_argument('--state',type=Path,default=None)
    sub=parser.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--model');serve.add_argument('--provider-url',default=None)
    serve.add_argument('--port',type=int,default=None);serve.add_argument('--min-free-mb',type=int,default=4096)
    serve.add_argument('--model-call-seconds',type=int,default=120)
    serve.add_argument('--ask-api-key',action='store_true')
    serve.add_argument('--experimental-workspace-jobs',action='store_true',
        help='Explicit development-only sealed workspace worker; NOT production approval')
    serve.add_argument('--workspace-project',action='append',default=[],metavar='ID=ABSOLUTE_DIR',
        help='Trusted local folder approved for synthetic, sealed workspace test only')
    connect=sub.add_parser('connection');connect.add_argument('--show-token',action='store_true')
    check=sub.add_parser('self-test');check.add_argument('--timeout',type=int,default=900)
    workspace_test=sub.add_parser('workspace-self-test',
        help='Run a synthetic task end-to-end using the explicit workspace test server')
    workspace_test.add_argument('--project-id',required=True)
    workspace_test.add_argument('--project-root',required=True,type=Path)
    workspace_test.add_argument('--timeout',type=int,default=900)
    export=sub.add_parser('export');export.add_argument('--job',required=True);export.add_argument('--out',type=Path,required=True)
    diag=sub.add_parser('diagnostics',help='Export durable redacted job trace as JSON (create-only)')
    diag.add_argument('--job',required=True);diag.add_argument('--out',type=Path,required=True)
    resolve=sub.add_parser('resolve');resolve.add_argument('--job',required=True);resolve.add_argument('--confirm-provider-stopped',action='store_true',required=True)
    sub.add_parser('facts')
    args=parser.parse_args(argv)
    try:
        if args.command=='facts':print(json.dumps(facts(),indent=2));return 0
        state=args.state or default_state()
        if args.command=='serve':
            state.mkdir(parents=True,exist_ok=True,mode=0o700)
            if os.name!='nt':os.chmod(state,0o700)
            previous={}
            if (state/'settings.json').exists():previous=_settings(state)
            model=args.model or previous.get('model')
            if not model:
                if not sys.stdin.isatty():raise PilotError('PILOT_NOT_CONFIGURED')
                model=input('Exact loaded LM Studio worker model ID: ').strip()
            port=args.port or previous.get('port',8765)
            if type(port) is not int or not 1024<=port<=65535:raise PilotError('CONFIGURATION_INVALID')
            provider=args.provider_url or previous.get('provider_url','http://127.0.0.1:1234')
            key=getpass.getpass('LM Studio API key (not saved): ') if args.ask_api_key else os.environ.get('MINIONONE_LMSTUDIO_KEY')
            if bool(args.workspace_project) != args.experimental_workspace_jobs:
                raise PilotError('CONFIGURATION_INVALID')
            projects={}
            for entry in args.workspace_project:
                if type(entry) is not str or '=' not in entry:
                    raise PilotError('CONFIGURATION_INVALID')
                project_id,raw_root=entry.split('=',1)
                if (not project_id or project_id in projects or not raw_root or
                        not Path(raw_root).is_absolute()):
                    raise PilotError('CONFIGURATION_INVALID')
                projects[project_id]=Path(raw_root)
            config=PilotConfig(model,provider,key,
                               model_call_seconds=args.model_call_seconds,min_free_mb=args.min_free_mb)
            token=_token(state,True)
            server=LocalMCP(('127.0.0.1',port),token=token,database_path=state/'jobs.sqlite',
                lmstudio_url=config.base_url,enable_test_jobs=not args.experimental_workspace_jobs,worker_model=model,worker_api_key=config.api_key,
                min_free_mb=config.min_free_mb,model_call_seconds=config.model_call_seconds,
                workspace_projects=projects or None,
                enable_workspace_jobs=args.experimental_workspace_jobs)
            try:
                (state/'settings.json').write_text(json.dumps({'port':port,'model':model,'provider_url':config.base_url}),encoding='utf-8')
                print('MinionOne SYSTEM TEST '+__version__+' listening at http://127.0.0.1:'+str(port)+'/mcp',flush=True)
                print('Worker files: isolated manager documents. Use connection and self-test in another window.',flush=True)
                if args.experimental_workspace_jobs:
                    print('EXPERIMENTAL WORKSPACE: synthetic files only; NOT G1b/Unsloth identity proof.',flush=True)
                server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:pass
            finally:server.server_close()
            return 0
        config=_settings(state);token=_token(state)
        if args.command=='connection':
            print(json.dumps({'url':'http://127.0.0.1:'+str(config['port'])+'/mcp','header_name':'Authorization',
                  'header_value':'Bearer '+token if args.show_token else '[hidden; use --show-token locally]',
                  'worker_model':config['model'],'copy_into':'Unsloth custom HTTP MCP server'},indent=2));return 0
        if args.command=='resolve':
            with_lock=PilotLock(state/'jobs.sqlite')
            try:
                owner=AdmissionStore(state/'jobs.sqlite',credentials={token:('local-operator',{'configured-pilot'})})
                manager=PilotJobs(owner,PilotConfig(config['model'],config['provider_url']))
                manager.recover();print(json.dumps(manager.resolve_for_operator(args.job),indent=2))
            finally:with_lock.close()
            return 0
        client=PilotClient(config['port'],token)
        if args.command=='workspace-self-test':
            if not 1<=args.timeout<=1800:raise PilotError('CONFIGURATION_INVALID')
            report=workspace_exercise(client,project_id=args.project_id,
                                      project_root=args.project_root,model=config['model'],
                                      timeout=args.timeout)
            folder=state/'reports';folder.mkdir(exist_ok=True)
            path=folder/('workspace-test-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2));print('REPORT: '+str(path))
            return 0 if report['passed'] else 2
        if args.command=='diagnostics':
            receipt=export_diagnostics(client,args.job,args.out)
            print(json.dumps({'ok':True,'diagnostics':receipt},sort_keys=True))
            return 0
        if args.command=='export':
            result=client.tool('minionone_test_result',{'job_id':args.job});export_result(result,args.out)
            print('RESULT_ZIP_CREATED');return 0
        if not 1<=args.timeout<=1800:raise PilotError('CONFIGURATION_INVALID')
        report=exercise(client,timeout=args.timeout)
        folder=state/'reports';folder.mkdir(exist_ok=True)
        path=folder/('system-test-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2));print('REPORT: '+str(path))
        return 0 if report['passed'] else 2
    except KeyboardInterrupt:return 130
    except Exception as exc:
        fault=safe_tool_fault(exc)
        if isinstance(exc,OSError) and exc.errno==errno.EADDRINUSE:
            fault=diagnostic('MCP_ADDRESS_IN_USE')
        elif isinstance(exc,ValueError) and fault.code in ('INTERNAL_ERROR','INVALID_INPUT'):
            fault=diagnostic('CONFIGURATION_INVALID')
        print(json.dumps({'ok':False,'error':fault.to_dict()}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
