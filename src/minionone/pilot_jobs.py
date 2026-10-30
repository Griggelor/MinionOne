"""Opt-in complete test jobs with SQLite document capabilities, no host file tools.

All canonical job transitions go through AdmissionStore._transition in its
transaction. Worker files are immutable job-scoped documents, never OS paths.
"""
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import os
import re
import secrets
import threading
import time
from urllib.request import Request
from .admission import InputRejected, PermissionDenied, VersionConflict, IdempotencyConflict, _digest, _utc, _field, _OP
from .agent_loop import AgentLoop, AgentLimits, BrokerReply, BrokerScopeDenied
from .diagnostics import diagnostic, safe_tool_fault
from .lifecycle import Evidence
from .lmstudio_agent import LMStudioAgentProvider, PlannerRejected
from .pilot_transport import DeadlineOpener
from .worker_contract import SYSTEM_POLICY, task_message
from .job_telemetry import append as log_event
from .worker_io import append as append_worker_io

_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z',re.ASCII)
_RESERVED={'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}

class PilotError(RuntimeError):
    def __init__(self,code):
        super().__init__('test job operation unavailable')
        self.diagnostic=diagnostic(code)

def _name(value):
    if (type(value) is not str or _NAME.fullmatch(value) is None or
        value.endswith('.') or value.split('.')[0].lower() in _RESERVED or '..' in value):
        raise BrokerScopeDenied('invalid document name')
    return value.lower()

def _text(value,limit):
    if type(value) is not str or not value or len(value)>3500 or '\x00' in value:
        raise InputRejected('invalid bounded UTF-8 document')
    try:size=len(value.encode('utf-8'))
    except UnicodeError:raise InputRejected('invalid UTF-8 document') from None
    if size>limit:raise InputRejected('document byte budget exhausted')
    return value

def available_memory_mb():
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                      *[(n,ctypes.c_ulonglong) for n in ('total','available','page','freepage','virtual','freevirtual','extended')]]
        info=Memory();info.length=ctypes.sizeof(info)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):
            raise PilotError('PILOT_RESOURCE_UNVERIFIED')
        return info.available//(1024*1024)
    try:return os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')//(1024*1024)
    except (ValueError,OSError,AttributeError):raise PilotError('PILOT_RESOURCE_UNVERIFIED') from None

@dataclass(frozen=True)
class PilotConfig:
    model_id:str
    base_url:str='http://127.0.0.1:1234'
    api_key:str|None=None
    model_call_seconds:int=60
    wall_seconds:int=900
    min_free_mb:int=4096
    def __post_init__(self):
        adapter=LMStudioAgentProvider(self.base_url,model_id=self.model_id,approved_prompt='configuration check',api_key=self.api_key)
        object.__setattr__(self,'base_url',adapter._provider.base_url)
        for value,low,high in ((self.model_call_seconds,1,300),(self.wall_seconds,1,1200),(self.min_free_mb,1,131072)):
            if type(value) is not int or not low<=value<=high:raise InputRejected('invalid test configuration')

class PilotJobs:
    def __init__(self,owner,config):
        self.owner=owner;self.config=config;self._threads=set();self._lock=threading.Lock()
        self.workspace_execution=None  # Explicit opt-in manager-side attachment, never model-supplied.
        with owner._connection() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS pilot_runs(
              job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), task TEXT NOT NULL, model_id TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
              provider_inflight INTEGER NOT NULL DEFAULT 0 CHECK(provider_inflight IN (0,1)),
              epoch INTEGER NOT NULL DEFAULT 1 CHECK(epoch=1), capability TEXT,
              turns INTEGER NOT NULL DEFAULT 0 CHECK(turns BETWEEN 0 AND 200),
              tools INTEGER NOT NULL DEFAULT 0 CHECK(tools BETWEEN 0 AND 60),
              denials INTEGER NOT NULL DEFAULT 0 CHECK(denials BETWEEN 0 AND 3),
              tokens INTEGER NOT NULL DEFAULT 0 CHECK(tokens BETWEEN 0 AND 4000000),
              percent INTEGER NOT NULL DEFAULT 0, error_code TEXT, result_digest TEXT,
              issued_digest TEXT, question TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS pilot_one_active ON pilot_runs(active) WHERE active=1;
            CREATE TRIGGER IF NOT EXISTS pilot_task_sealed BEFORE UPDATE OF task,model_id ON pilot_runs
              BEGIN SELECT RAISE(ABORT,'sealed job task'); END;
            CREATE TABLE IF NOT EXISTS pilot_files(
              job_id TEXT NOT NULL REFERENCES jobs(job_id),zone TEXT NOT NULL CHECK(zone IN ('input','work','output')),
              name TEXT NOT NULL,content TEXT NOT NULL,digest TEXT NOT NULL,
              PRIMARY KEY(job_id,zone,name));
            CREATE TRIGGER IF NOT EXISTS pilot_files_immutable_update BEFORE UPDATE ON pilot_files
              BEGIN SELECT RAISE(ABORT,'immutable job document'); END;
            CREATE TRIGGER IF NOT EXISTS pilot_files_immutable_delete BEFORE DELETE ON pilot_files
              BEGIN SELECT RAISE(ABORT,'immutable job document'); END;
            CREATE TABLE IF NOT EXISTS pilot_receipts(
              job_id TEXT NOT NULL REFERENCES jobs(job_id),kind TEXT NOT NULL,
              operation_digest TEXT NOT NULL,request_hash TEXT NOT NULL,receipt TEXT NOT NULL,
              PRIMARY KEY(job_id,kind,operation_digest));
            ''')

    def recover(self):
        count=0
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            for row in con.execute("SELECT j.* FROM jobs j JOIN pilot_runs p USING(job_id) WHERE (p.active=1 OR p.provider_inflight=1) AND j.state IN ('RESOURCE_CHECK','RUNNING','CANCEL_REQUESTED')").fetchall():
                target='FAILED' if row['state']=='RESOURCE_CHECK' else 'NEEDS_RECONCILIATION'
                code='PILOT_START_FAILED' if target=='FAILED' else 'PILOT_PROVIDER_UNCERTAIN'
                con.execute('UPDATE pilot_runs SET active=0,capability=NULL,provider_inflight=1,error_code=? WHERE job_id=?',
                            (code,row['job_id']))
                if target=='FAILED':con.execute('UPDATE pilot_runs SET provider_inflight=0 WHERE job_id=?',(row['job_id'],))
                self.owner._transition(con,row,target,
                    Evidence(reconciliation_required=True,error_code='PILOT_PROVIDER_UNCERTAIN') if target=='NEEDS_RECONCILIATION' else
                    Evidence(worker_outcome='FAILED',error_code='PILOT_START_FAILED'),
                    'pilot-recovery-'+secrets.token_hex(8))
                count+=1
        return count

    @staticmethod
    def project(con,row):
        p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(row['job_id'],)).fetchone()
        if p is None:raise PilotError('PILOT_STATE_INVALID')
        return {'backend':'sqlite_documents','attempt_epoch':p['epoch'],'worker_model':p['model_id'],
                'model_turns':p['turns'],'tool_calls':p['tools'],'denials':p['denials'],
                'tokens_used':p['tokens'],'progress_percent':p['percent'],
                'provider_state_uncertain':bool(p['provider_inflight'] and not p['active']),
                'result_available':row['state'] in ('RESULT_READY','COMPLETED'),
                'result_digest':p['result_digest'],
                **({'error':diagnostic(p['error_code']).to_dict()} if p['error_code'] else {}),
                **({'question':p['question']} if row['state']=='WAITING_FOR_PARENT' else {})}

    def _receipt(self,con,job,kind,operation,request,store=None):
        key=_digest(operation);request_hash=_digest(request)
        old=con.execute('SELECT * FROM pilot_receipts WHERE job_id=? AND kind=? AND operation_digest=?',
                        (job,kind,key)).fetchone()
        if old is not None:
            if old['request_hash']!=request_hash:raise IdempotencyConflict('test operation reused')
            return json.loads(old['receipt'])
        if store is not None:
            con.execute('INSERT INTO pilot_receipts VALUES(?,?,?,?,?)',
                        (job,kind,key,request_hash,json.dumps(store,sort_keys=True)))
        return None

    def submit(self,token,thread,task,files,operation_id):
        principal=self.owner._principal(token,thread);_field(_OP,operation_id,'operation')
        task=_text(task,4096)
        if type(files) is not dict or not 1<=len(files)<=4:raise InputRejected('one to four test files required')
        clean={}
        for name,content in files.items():
            try:key=_name(name)
            except BrokerScopeDenied:raise InputRejected('invalid input document name') from None
            if key=='task.md' or key in clean:raise InputRejected('duplicate or reserved document')
            clean[key]=_text(content,4096)
        if sum(len(x.encode('utf-8')) for x in clean.values())>8192:raise InputRejected('input budget exhausted')
        digest=_digest({'task':task,'files':clean});request_hash=_digest([thread,digest])
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            old=con.execute('SELECT * FROM admissions WHERE principal=? AND operation_id=?',(principal,operation_id)).fetchone()
            if old:
                row=self.owner._owned_row(con,principal,thread,old['job_id'])
                if old['request_hash']!=request_hash or row['job_kind']!='ISOLATED_TEST':raise IdempotencyConflict('test admission conflict')
                return json.loads(old['receipt'])
            if con.execute('SELECT count(*) FROM jobs WHERE principal=?',(principal,)).fetchone()[0]>=self.owner.max_jobs_per_principal:
                raise InputRejected('admission quota exhausted')
            job='JOB-'+secrets.token_hex(16);now=_utc()
            con.execute("INSERT INTO jobs(job_id,principal,thread_id,input_digest,state,version,job_kind,created_utc,updated_utc) VALUES(?,?,?,?,'ACCEPTED',0,'ISOLATED_TEST',?,?)",
                        (job,principal,thread,digest,now,now))
            con.execute('INSERT INTO admission_events VALUES(?,?,?,?,?,?)',(job,1,'ACCEPTED','pilot-submit-'+_digest(operation_id),'AUTHENTICATED_PARENT',now))
            con.execute('INSERT INTO pilot_runs(job_id,task,model_id) VALUES(?,?,?)',(job,task,self.config.model_id))
            log_event(con,job,'AUDIT','JOB_ACCEPTED',{'state':'ACCEPTED','version':0})
            log_event(con,job,'JOB','JOB_ACCEPTED',{'state':'ACCEPTED','version':0})
            for name,content in {'task.md':task,**clean}.items():
                con.execute('INSERT INTO pilot_files VALUES(?,?,?,?,?)',(job,'input',name,content,sha256(content.encode()).hexdigest()))
            row=self.owner._owned_row(con,principal,thread,job)
            row=self.owner._transition(con,row,'QUEUED',Evidence(),'pilot-sealed-'+secrets.token_hex(8))
            receipt=self.owner._snapshot(row)
            con.execute('INSERT INTO admissions VALUES(?,?,?,?,?)',(principal,operation_id,request_hash,job,json.dumps(receipt,sort_keys=True)))
            return receipt

    def start(self,token,thread,job_id,expected_version,operation_id,*,kind='ISOLATED_TEST'):
        principal=self.owner._principal(token,thread);_field(_OP,operation_id,'operation')
        if type(expected_version) is not int or expected_version<0:raise InputRejected('invalid expected version')
        cap=secrets.token_hex(32)
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=self.owner._owned_row(con,principal,thread,job_id)
            old=self._receipt(con,job_id,'START',operation_id,[expected_version])
            if old is not None:return old
            if row['job_kind']!=kind or kind not in ('ISOLATED_TEST','WORKSPACE') or row['state']!='QUEUED' or row['version']!=expected_version:
                raise VersionConflict('test job not queued at expected version')
            if con.execute('SELECT 1 FROM pilot_runs WHERE active=1 OR provider_inflight=1').fetchone():raise PilotError('PILOT_JOB_BUSY')
            con.execute('UPDATE pilot_runs SET active=1,capability=? WHERE job_id=?',(sha256(cap.encode()).hexdigest(),job_id))
            row=self.owner._transition(con,row,'RESOURCE_CHECK',Evidence(),'pilot-start-'+_digest(operation_id))
            receipt=self.owner._snapshot(row)
            self._receipt(con,job_id,'START',operation_id,[expected_version],receipt)
        worker=threading.Thread(target=self._run,args=(job_id,cap),daemon=True)
        with self._lock:self._threads.add(worker)
        try:worker.start()
        except Exception:
            self._finish(job_id,'FAILED','PILOT_START_FAILED');raise PilotError('PILOT_START_FAILED') from None
        return receipt

    def _current(self,con,job,cap):
        row=con.execute('SELECT j.*,p.active,p.capability FROM jobs j JOIN pilot_runs p USING(job_id) WHERE j.job_id=?',(job,)).fetchone()
        if row is None or row['state']!='RUNNING' or not row['active'] or not row['capability'] or not hmac.compare_digest(row['capability'],sha256(cap.encode()).hexdigest()):
            raise BrokerScopeDenied('inactive job capability')
        return row

    def _readiness(self):
        if available_memory_mb()<self.config.min_free_mb:raise PilotError('PILOT_RESOURCE_UNVERIFIED')
        request=Request(self.config.base_url+'/api/v0/models',method='GET')
        with DeadlineOpener(self.config.base_url,self.config.api_key).open(request,timeout=5) as response:
            data=json.loads(response.read())
        if type(data) is not dict or type(data.get('data')) is not list:raise PilotError('PILOT_MODEL_NOT_LOADED')
        matches=[x for x in data['data'] if type(x) is dict and x.get('id')==self.config.model_id]
        if len(matches)!=1 or matches[0].get('state')!='loaded':raise PilotError('PILOT_MODEL_NOT_LOADED')

    def _run(self,job,cap):
        publication_started=False
        io_published=False
        workspace_job=False
        live_logs=None
        try:
            with self.owner._connection() as con:
                configured=con.execute('SELECT model_id FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
                if configured['model_id']!=self.config.model_id:raise PilotError('PILOT_MODEL_NOT_LOADED')
            self._readiness()
            if self.workspace_execution is not None:
                with self.owner._connection() as con:
                    kind=con.execute('SELECT job_kind FROM jobs WHERE job_id=?',(job,)).fetchone()
                if kind is not None and kind['job_kind']=='WORKSPACE':
                    self.workspace_execution.workspace.verify_input(
                        self.workspace_execution.token,self.workspace_execution.thread,job)
            with self.owner._connection() as con:
                con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job,)).fetchone()
                if row['state']=='CANCEL_REQUESTED':
                    self._finish_in(con,row,'CANCELLED',None);return
                if row['state']!='RESOURCE_CHECK':raise PilotError('PILOT_STATE_INVALID')
                workspace_job=row['job_kind']=='WORKSPACE'
                self.owner._transition(con,row,'RUNNING',Evidence(attempt_no=1,active_lease=True,worker_outcome='RUNNING'),'pilot-running-'+secrets.token_hex(8))
                task=con.execute('SELECT task FROM pilot_runs WHERE job_id=?',(job,)).fetchone()[0]
                names=[r[0] for r in con.execute("SELECT name FROM pilot_files WHERE job_id=? AND zone='input' ORDER BY name",(job,))]
                sealed={r['name']:r['content'] for r in con.execute("SELECT name,content FROM pilot_files WHERE job_id=? AND zone='input'",(job,))}
                if sealed.pop('task.md',None)!=task:
                    raise PilotError('PILOT_FILE_CHANGED')
                if row['job_kind']=='WORKSPACE':
                    manager=self.workspace_execution
                    if manager is None:raise PilotError('PILOT_STATE_INVALID')
                    # Read source snapshot using manager identity; compare actual
                    # admitted document text, not the pilot's different digest format.
                    _, original=manager.reader._load(con,manager.token,manager.thread,job)
                    canonical={_name(k):v.decode('utf-8') for k,v in original.items()
                               if k not in ('TASK.md','manifest.json')}
                    if (canonical!=sealed or original['TASK.md'].decode('utf-8')!=task):
                        raise PilotError('PILOT_FILE_CHANGED')
                elif _digest({'task':task,'files':sealed})!=row['input_digest']:
                    raise PilotError('PILOT_FILE_CHANGED')
            if workspace_job:
                opening=self.workspace_execution.open_live_logs(job)
                opening.__enter__()
                live_logs=opening
            if getattr(self,"process_logs",None) is not None:
                self.process_logs.event("WORKER_STARTED",job_id=job,attempt=1)
            instructions=task_message(task,names)
            def worker_io_sink(kind,body):
                # SENSITIVE exact external model JSON request/response bodies
                # and exact broker tool replies, never HTTP auth headers.
                with self.owner._connection() as io_con:
                    io_con.execute('BEGIN IMMEDIATE')
                    self._current(io_con,job,cap)
                    append_worker_io(io_con,job,kind,body,live_writer=live_logs)
            provider=LMStudioAgentProvider(self.config.base_url,model_id=self.config.model_id,
                        approved_prompt=instructions,system_prompt=SYSTEM_POLICY,
                        max_output_tokens=2048,api_key=self.config.api_key,
                        io_sink=worker_io_sink if workspace_job else None)
            provider._provider._opener=DeadlineOpener(self.config.base_url,self.config.api_key)
            observed=ObservedProvider(self,job,cap,provider,live_logs=live_logs)
            broker=DocumentBroker(self,job,cap)
            run=AgentLoop(AgentLimits(wall_seconds=self.config.wall_seconds,
                    model_call_seconds=self.config.model_call_seconds)).run(observed,broker,
                    cancelled=lambda:self._cancelled(job))
            if workspace_job:
                # Each exact event has already been fsynced into output/;
                # no end-of-run publication from SQLite is needed.
                with self.owner._connection() as io_con:
                    io_con.execute('BEGIN')
                    from .worker_io import jsonl as verified_io_jsonl
                    verified=verified_io_jsonl(io_con,job)
                try:
                    if not verified or not live_logs.verify_worker_bytes(verified):
                        raise PilotError('PILOT_FILE_CHANGED')
                except InputRejected as exc:
                    raise PilotError('PILOT_FILE_CHANGED') from exc
                live_logs.job_event('JOB_OUTCOME',{'outcome':run.status})
                io_published=True
                # On Windows, a terminal job state MUST NOT become visible
                # while any live output file handle is still open. Otherwise
                # a parent ACK/cleanup can race pinned NTFS handles.
                live_logs.__exit__(None,None,None)
                live_logs=None
            if run.status=='AWAITING_REVIEW':
                if row['job_kind']=='WORKSPACE':
                    if self.workspace_execution is None:raise PilotError('PILOT_STATE_INVALID')
                    publication_started=True
                    self.workspace_execution.publish_finished(job,cap)
                self._finish(job,'RESULT_READY',None)
            elif run.status=='CANCELLED':self._finish(job,'CANCELLED',None)
            elif run.status=='WAITING_FOR_PARENT':self._finish(job,'WAITING_FOR_PARENT',None)
            else:
                uncertain=observed.uncertain or run.status=='TIMEOUT'
                code=observed.error_code or broker.error_code or ('PILOT_TOOL_DENIED' if run.status=='SECURITY_ABORT' else 'PILOT_JOB_FAILED')
                self._finish(job,'NEEDS_RECONCILIATION' if uncertain else 'FAILED',code)
            if getattr(self,'process_logs',None) is not None:
                self.process_logs.event("WORKER_LOOP_FINISHED",job_id=job,outcome=run.status)
        except Exception as exc:
            code=safe_tool_fault(exc).code
            # A mismatched / corrupted exact transcript cannot be a clean
            # worker failure. Its on-disk side effects need operator
            # reconciliation even if the SQLite state itself remains valid.
            if workspace_job and code in (
                    'PILOT_FILE_CHANGED','DB_INTEGRITY_ERROR',
                    'STORAGE_IO_ERROR','DB_CORRUPT'):
                publication_started=True
            if workspace_job and not io_published:
                try:
                    if live_logs is not None:
                        live_logs.job_event('JOB_LOG_ERROR',{'error_code':code})
                        with self.owner._connection() as io_con:
                            io_con.execute('BEGIN IMMEDIATE')
                            append_worker_io(io_con,job,'CAPTURE_ERROR',
                                json.dumps({'error_code':code},sort_keys=True),
                                live_writer=live_logs)
                    else:
                        # Pre-open failure: old manager-publisher recovery
                        # handles any absent transcript without clobbering.
                        with self.owner._connection() as io_con:
                            io_con.execute('BEGIN IMMEDIATE')
                            append_worker_io(io_con,job,'CAPTURE_ERROR',
                                json.dumps({'error_code':code},sort_keys=True))
                        self.workspace_execution.publish_worker_io(job)
                    io_published=True
                except Exception:
                    publication_started=True
            if getattr(self,'process_logs',None) is not None:
                try:self.process_logs.failure('WORKER_FAILED',exc,job_id=job,error_code=code)
                except Exception:publication_started=True
            # Always release pinned Windows output handles before publishing
            # FAILED / NEEDS_RECONCILIATION as a visible terminal state.
            if live_logs is not None:
                try:live_logs.__exit__(None,None,None)
                except Exception:publication_started=True
                live_logs=None
            try:self._finish(job,'NEEDS_RECONCILIATION' if publication_started else 'FAILED',code)
            except Exception:pass  # DB outage leaves the durable active intent for restart quarantine.
        finally:
            if live_logs is not None:
                try:live_logs.__exit__(None,None,None)
                except Exception as log_error:
                    if getattr(self,'process_logs',None) is not None:
                        self.process_logs.failure('JOB_LOG_CLOSE_FAILED',log_error)
            with self._lock:self._threads.discard(threading.current_thread())

    def _cancelled(self,job):
        with self.owner._connection() as con:
            row=con.execute('SELECT state FROM jobs WHERE job_id=?',(job,)).fetchone()
            return row is None or row['state']=='CANCEL_REQUESTED'

    def close(self):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            rows=con.execute("SELECT j.* FROM jobs j JOIN pilot_runs p USING(job_id) WHERE p.active=1 AND j.state IN ('RUNNING','RESOURCE_CHECK')").fetchall()
            for row in rows:
                self.owner._transition(con,row,'CANCEL_REQUESTED',Evidence(),'pilot-stop-'+secrets.token_hex(8))
                con.execute('UPDATE pilot_runs SET active=0,capability=NULL WHERE job_id=?',(row['job_id'],))
        with self._lock:threads=list(self._threads)
        # Cancellation revokes every live capability, but OS handles are
        # owned by worker threads until they actually unwind. A 200 ms
        # join is insufficient on Windows, leading to locked job logs and
        # unusable test/recovery directories. One shared bounded grace period.
        deadline=time.monotonic()+max(2,min(10,self.config.model_call_seconds))
        for thread in threads:
            thread.join(timeout=max(0,deadline-time.monotonic()))

    def resolve_for_operator(self,job_id):
        # Not an MCP tool. Caller must independently confirm provider stopped.
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute("SELECT * FROM jobs WHERE job_id=? AND job_kind='ISOLATED_TEST'",(job_id,)).fetchone()
            if row is None or row['state']!='NEEDS_RECONCILIATION':raise PilotError('PILOT_RESULT_NOT_READY')
            con.execute("UPDATE pilot_runs SET provider_inflight=0,active=0,capability=NULL,error_code='PILOT_JOB_FAILED' WHERE job_id=?",(job_id,))
            row=self.owner._transition(con,row,'FAILED',Evidence(reconciliation_verified=True,worker_outcome='FAILED',
                    error_code='PILOT_PROVIDER_UNCERTAIN'),'pilot-operator-resolved-'+secrets.token_hex(8),actor='LOCAL_OPERATOR')
            return self.owner._snapshot(row)

    @staticmethod
    def _outputs(con,job):
        rows=con.execute("SELECT name,content,digest FROM pilot_files WHERE job_id=? AND zone='output' ORDER BY name",(job,)).fetchall()
        if not rows:raise PilotError('PILOT_RESULT_MISSING')
        for r in rows:
            if sha256(r['content'].encode()).hexdigest()!=r['digest']:raise PilotError('PILOT_FILE_CHANGED')
        return rows,_digest([(r['name'],r['digest']) for r in rows])

    def _finish(self,job,state,code):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job,)).fetchone()
            self._finish_in(con,row,state,code)

    def _finish_in(self,con,row,state,code):
        if row['state'] not in ('RESOURCE_CHECK','RUNNING','CANCEL_REQUESTED'):return
        if row['state']=='CANCEL_REQUESTED' and state!='NEEDS_RECONCILIATION':state='CANCELLED';code=None
        result_digest=None
        if state=='RESULT_READY':
            try:_,result_digest=self._outputs(con,row['job_id'])
            except PilotError:state='FAILED';code='PILOT_RESULT_MISSING'
        con.execute('UPDATE pilot_runs SET active=0,capability=NULL,provider_inflight=?,error_code=?,result_digest=? WHERE job_id=?',
                    (int(state=='NEEDS_RECONCILIATION'),code,result_digest,row['job_id']))
        if state=='RESULT_READY':con.execute('UPDATE pilot_runs SET percent=100 WHERE job_id=?',(row['job_id'],))
        evidence={'RESULT_READY':Evidence(worker_outcome='DONE',result_digest=result_digest,worker_quiescent=True),
                  'CANCELLED':Evidence(worker_outcome='CANCELLED',worker_quiescent=True,attempt_no=1),
                  'WAITING_FOR_PARENT':Evidence(worker_outcome='QUESTION',worker_quiescent=True),
                  'NEEDS_RECONCILIATION':Evidence(reconciliation_required=True,error_code=code or 'PILOT_PROVIDER_UNCERTAIN'),
                  'FAILED':Evidence(worker_outcome='FAILED',error_code=code or 'PILOT_JOB_FAILED')}[state]
        self.owner._transition(con,row,state,evidence,'pilot-outcome-'+secrets.token_hex(8))

    def result(self,token,thread,job_id,*,kind='ISOLATED_TEST'):
        principal=self.owner._principal(token,thread)
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=self.owner._owned_row(con,principal,thread,job_id)
            if row['job_kind']!=kind or row['state'] not in ('RESULT_READY','COMPLETED'):raise PilotError('PILOT_RESULT_NOT_READY')
            files,digest=self._outputs(con,job_id)
            meta=con.execute('SELECT result_digest FROM pilot_runs WHERE job_id=?',(job_id,)).fetchone()
            if meta['result_digest']!=digest:raise PilotError('PILOT_FILE_CHANGED')
            con.execute('UPDATE pilot_runs SET issued_digest=? WHERE job_id=?',(digest,job_id))
            log_event(con,job_id,'AUDIT','RESULT_DELIVERED',{'result_digest':digest})
            return {'job_id':job_id,'state':row['state'],'version':row['version'],
                    'result_digest':digest,'untrusted_model_output':True,
                    'files':[{'name':r['name'],'text':r['content'],'sha256':r['digest']} for r in files]}

    def acknowledge(self,token,thread,job_id,expected_version,result_digest,operation_id,*,kind='ISOLATED_TEST'):
        principal=self.owner._principal(token,thread);_field(_OP,operation_id,'operation')
        if type(expected_version) is not int or expected_version<0:raise InputRejected('invalid expected version')
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=self.owner._owned_row(con,principal,thread,job_id)
            request=[expected_version,result_digest]
            old=self._receipt(con,job_id,'ACK',operation_id,request)
            if old is not None:return old
            p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(job_id,)).fetchone()
            if p is None or row['job_kind']!=kind or row['state']!='RESULT_READY' or row['version']!=expected_version:raise VersionConflict('result no longer ready at expected version')
            _,verified=self._outputs(con,job_id)
            if result_digest!=verified or p['issued_digest']!=verified or p['result_digest']!=verified:
                raise PilotError('PILOT_RESULT_NOT_DELIVERED')
            row=self.owner._transition(con,row,'COMPLETED',Evidence(worker_outcome='DONE',result_digest=verified,
                     worker_quiescent=True,integration_ack='ACKED',notification_delivery='DELIVERED'),
                     'pilot-ack-'+_digest(operation_id),actor='AUTHENTICATED_PARENT')
            receipt=self.owner._snapshot(row)
            self._receipt(con,job_id,'ACK',operation_id,request,receipt)
            return receipt

    def cancel(self,token,thread,job_id,expected_version,operation_id,*,kind='ISOLATED_TEST'):
        principal=self.owner._principal(token,thread);_field(_OP,operation_id,'operation')
        if type(expected_version) is not int or expected_version<0:raise InputRejected('invalid expected version')
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=self.owner._owned_row(con,principal,thread,job_id)
            old=self._receipt(con,job_id,'CANCEL',operation_id,[expected_version])
            if old is not None:return old
            if row['job_kind']!=kind or row['version']!=expected_version or row['state'] not in ('QUEUED','RESOURCE_CHECK','RUNNING','WAITING_FOR_PARENT'):
                raise VersionConflict('test job cannot be cancelled at expected version')
            previous=row['state']
            row=self.owner._transition(con,row,'CANCEL_REQUESTED',Evidence(),'pilot-cancel-'+_digest(operation_id),actor='AUTHENTICATED_PARENT')
            con.execute('UPDATE pilot_runs SET active=0,capability=NULL WHERE job_id=?',(job_id,))
            if previous in ('QUEUED','WAITING_FOR_PARENT'):self._finish_in(con,row,'CANCELLED',None)
            receipt=self.owner._snapshot(con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone())
            self._receipt(con,job_id,'CANCEL',operation_id,[expected_version],receipt)
            return receipt

class DocumentBroker:
    def __init__(self,manager,job,cap):self.m=manager;self.job=job;self.cap=cap;self.error_code=None
    def execute(self,name,args,*,timeout_seconds):
        try:return self._execute(name,args,timeout_seconds=timeout_seconds)
        except BrokerScopeDenied:raise
        except Exception as exc:
            self.error_code=safe_tool_fault(exc).code
            raise
    def _execute(self,name,args,*,timeout_seconds):
        if type(args) is not dict:raise BrokerScopeDenied('invalid arguments')
        required={'list_files':{'zone'},'read_file':{'path'},'create_work':{'name','content'},
                  'publish_artifact':{'name'},'report_progress':{'percent'},'ask_question':{'question'}}
        if name not in required or set(args)!=required[name]:raise BrokerScopeDenied('unknown tool or fields')
        with self.m.owner._connection(timeout_seconds=min(10,max(.001,timeout_seconds))) as con:
            con.execute('BEGIN IMMEDIATE');self.m._current(con,self.job,self.cap)
            if name=='list_files':
                if args['zone'] not in ('input','work'):raise BrokerScopeDenied('zone denied')
                data={'files':[{'path':args['zone']+'/'+r['name'],'sha256':r['digest']} for r in con.execute(
                    'SELECT name,digest FROM pilot_files WHERE job_id=? AND zone=? ORDER BY name',(self.job,args['zone']))]}
            elif name=='read_file':
                path=args['path']
                if type(path) is not str or path.count('/')!=1:raise BrokerScopeDenied('path denied')
                zone,key=path.split('/')
                if zone not in ('input','work'):raise BrokerScopeDenied('zone denied')
                key=_name(key)
                row=con.execute('SELECT content,digest FROM pilot_files WHERE job_id=? AND zone=? AND name=?',(self.job,zone,key)).fetchone()
                if row is None:raise BrokerScopeDenied('unknown document')
                if sha256(row['content'].encode()).hexdigest()!=row['digest']:raise PilotError('PILOT_FILE_CHANGED')
                data={'path':zone+'/'+key,'content':row['content'],'sha256':row['digest']}
            elif name in ('create_work','publish_artifact'):
                key=_name(args['name']);zone='work' if name=='create_work' else 'output'
                if key in ('worker-io.jsonl','job.log.jsonl'):
                    raise BrokerScopeDenied('manager-only output name denied')
                if name=='create_work':
                    try:content=_text(args['content'],8192)
                    except InputRejected:raise BrokerScopeDenied('document size denied') from None
                    digest=sha256(content.encode()).hexdigest()
                else:
                    source=con.execute("SELECT content,digest FROM pilot_files WHERE job_id=? AND zone='work' AND name=?",(self.job,key)).fetchone()
                    if source is None:raise BrokerScopeDenied('publication source denied')
                    content,digest=source['content'],source['digest']
                    if sha256(content.encode()).hexdigest()!=digest:raise PilotError('PILOT_FILE_CHANGED')
                if con.execute('SELECT 1 FROM pilot_files WHERE job_id=? AND zone=? AND name=?',(self.job,zone,key)).fetchone():raise BrokerScopeDenied('immutable document already exists')
                sizes=con.execute('SELECT count(*),COALESCE(sum(length(CAST(content AS BLOB))),0) FROM pilot_files WHERE job_id=?',(self.job,)).fetchone()
                if sizes[0]>=32 or sizes[1]+len(content.encode())>128*1024:raise BrokerScopeDenied('document quota exhausted')
                con.execute('INSERT INTO pilot_files VALUES(?,?,?,?,?)',(self.job,zone,key,content,digest))
                data={'path':zone+'/'+key,'sha256':digest}
            elif name=='report_progress':
                value=args['percent']
                if type(value) is not int or not 0<=value<=100:raise BrokerScopeDenied('invalid progress')
                con.execute('UPDATE pilot_runs SET percent=? WHERE job_id=?',(value,self.job));data={'percent':value}
            else:
                try:question=_text(args['question'],1000)
                except InputRejected:raise BrokerScopeDenied('question budget denied') from None
                con.execute('UPDATE pilot_runs SET question=? WHERE job_id=?',(question,self.job));data={'question_recorded':True}
            return BrokerReply('ok','OK',data)

class ObservedProvider:
    def __init__(self,manager,job,cap,provider,live_logs=None):
        self.m=manager;self.job=job;self.cap=cap;self.provider=provider;self.uncertain=False;self.error_code=None
        self.live_logs=live_logs
    def next_step(self,*,timeout_seconds):
        with self.m.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');self.m._current(con,self.job,self.cap)
            con.execute('UPDATE pilot_runs SET turns=turns+1,provider_inflight=1 WHERE job_id=?',(self.job,))
            turn=con.execute('SELECT turns FROM pilot_runs WHERE job_id=?',(self.job,)).fetchone()[0]
            log_event(con,self.job,'DEBUG','MODEL_REQUEST_STARTED',{'turn':turn,'attempt':1})
        if self.live_logs is not None:
            self.live_logs.job_event('MODEL_REQUEST_STARTED',{'turn':turn,'attempt':1})
        started=time.monotonic_ns()
        try:step=self.provider.next_step(timeout_seconds=timeout_seconds)
        except Exception as exc:
            self.error_code=safe_tool_fault(exc).code
            self.uncertain=self.error_code not in ('PROVIDER_AUTH_FAILED','PROVIDER_CONNECTION_REFUSED','PROVIDER_ENDPOINT_NOT_FOUND','PROVIDER_REQUEST_REJECTED')
            elapsed=min(4_000_000_000,max(0,(time.monotonic_ns()-started)//1_000_000))
            with self.m.owner._connection() as con:
                con.execute('BEGIN IMMEDIATE')
                log_event(con,self.job,'DEBUG','MODEL_REQUEST_FAILED',
                          {'turn':turn,'duration_ms':elapsed,'error_code':self.error_code,'attempt':1})
            if self.live_logs is not None:
                self.live_logs.job_event('MODEL_REQUEST_FAILED',
                    {'turn':turn,'duration_ms':elapsed,'error_code':self.error_code})
            raise
        elapsed=min(4_000_000_000,max(0,(time.monotonic_ns()-started)//1_000_000))
        with self.m.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('UPDATE pilot_runs SET provider_inflight=0,tokens=tokens+? WHERE job_id=?',(step.total_tokens,self.job))
            log_event(con,self.job,'DEBUG','MODEL_REQUEST_FINISHED',
                      {'turn':turn,'duration_ms':elapsed,'tokens':step.total_tokens,'attempt':1})
        if self.live_logs is not None:
            self.live_logs.job_event('MODEL_REQUEST_FINISHED',
                {'turn':turn,'duration_ms':elapsed,'tokens':step.total_tokens})
        return step
    def observe_tool(self,name,reply):
        from .agent_loop import ALLOWED_WORKER_TOOLS
        safe_name=name if name in ALLOWED_WORKER_TOOLS else 'FORBIDDEN_TOOL'
        with self.m.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('UPDATE pilot_runs SET tools=tools+1,denials=denials+? WHERE job_id=?',(int(reply.status=='DENIED'),self.job))
            log_event(con,self.job,'DEBUG','WORKER_TOOL_COMPLETED',
                      {'tool':safe_name,'result_code':reply.result_code,'attempt':1})
        if self.live_logs is not None:
            self.live_logs.job_event('WORKER_TOOL_COMPLETED',
                {'tool':safe_name,'result_code':reply.result_code})
        self.provider.observe_tool(name,reply)
