"""Native authenticated Unsloth Studio same-thread resumption, API contract f9cecc99.

Only the original parent is given its own history. Worker inputs are never read
or constructed here. All effects have stable IDs; reconciliation only reads.
"""
from dataclasses import dataclass,field
from hashlib import sha256
import json
import time
from urllib.parse import quote
from .admission import InputRejected,_digest
from .control_http import ControlHTTP
from .model_runtime import ModelRuntime
from .parent_return import ParentState,ParentReceipt
from .worker_profiles import WorkerProfile,WorkerProviderError,load_profiles

CONTRACT='unsloth-studio-f9cecc99'
_OPTIONS={'temperature','top_p','top_k','min_p','max_tokens','repetition_penalty','presence_penalty',
          'seed','reasoning_effort','enable_thinking','enable_tools','enabled_tools','mcp_enabled',
          'context_policy','context_overflow','compaction_headroom_ratio','permission_mode','max_tool_calls_per_message'}

@dataclass(frozen=True)
class NativeParentConfig:
    thread_id:str
    model_id:str
    base_url:str
    secret_env:str
    memory_ram_mb:int
    memory_vram_mb:int=0
    allow_parent_unload:bool=False
    load_options:dict=field(default_factory=dict)
    resume_options:dict=field(default_factory=dict)
    contract:str=CONTRACT
    def __post_init__(self):
        if (type(self.thread_id) is not str or not 1<=len(self.thread_id)<=256 or
                any(ord(c)<32 for c in self.thread_id) or self.contract!=CONTRACT or
                type(self.allow_parent_unload) is not bool or not self.secret_env or
                type(self.resume_options) is not dict or set(self.resume_options)-_OPTIONS):
            raise InputRejected('invalid native parent binding/configuration')
        self.profile()
        if len(json.dumps(self.resume_options))>16384:raise InputRejected('parent options too large')
    def profile(self):
        return WorkerProfile('original-flash','unsloth',self.model_id,self.base_url,secret_env=self.secret_env,
            auto_load=True,memory_ram_mb=self.memory_ram_mb,memory_vram_mb=self.memory_vram_mb,load_options=self.load_options)


def load_parent_config(path):
    from pathlib import Path
    p=Path(path)
    if p.stat().st_size>32768:raise InputRejected('parent configuration too large')
    def unique(items):
        result={}
        for k,v in items:
            if k in result:raise InputRejected('duplicate parent configuration key')
            result[k]=v
        return result
    try:return NativeParentConfig(**json.loads(p.read_text(encoding='utf-8'),object_pairs_hook=unique))
    except (ValueError,TypeError):raise InputRejected('invalid native parent configuration') from None


class UnslothParent:
    def __init__(self,owner,config,*,principal='local-operator',http=None,runtime=None,measure=None):
        self.owner=owner;self.config=config;self.profile=config.profile();self.thread_id=config.thread_id
        self.principal=principal;self.allow_unload=config.allow_parent_unload
        self.http=http or ControlHTTP(self.profile);self.runtime=runtime or ModelRuntime(self.profile,http=self.http)
        from .model_lifecycle import measure_memory
        self.measure=measure or measure_memory
        with owner._connection() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS native_parent_jobs(
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), anchor_id TEXT NOT NULL,
                options_json TEXT NOT NULL, system_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_parent_returns(
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), operation_id TEXT NOT NULL,
                digest TEXT NOT NULL, envelope_json TEXT NOT NULL, message_json TEXT NOT NULL,
                phase TEXT NOT NULL);''')
            if 'restore_json' not in {r[1] for r in con.execute('PRAGMA table_info(native_parent_jobs)')}:
                con.execute("ALTER TABLE native_parent_jobs ADD COLUMN restore_json TEXT NOT NULL DEFAULT '{}'")
    @property
    def thread_path(self):return '/api/chat/threads/'+quote(self.thread_id,safe='')
    def _identity(self,principal,thread):
        if principal!=self.principal or thread!=self.thread_id:raise InputRejected('foreign parent binding')
        chat=self.http.request('GET',self.thread_path)
        if (chat.get('id')!=thread or chat.get('modelId')!=self.profile.model_id or chat.get('archived') is not False
                or chat.get('pairId')):raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        return chat
    def _messages(self):
        result=self.http.request('GET',self.thread_path+'/messages')
        messages=result.get('messages')
        if (type(messages) is not list or len(messages)>2048 or any(type(m) is not dict or
                type(m.get('id')) is not str or m.get('threadId')!=self.thread_id for m in messages)
                or len({m['id'] for m in messages})!=len(messages)):
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        return messages
    def _active(self):
        runs=self.http.request('GET','/api/inference/chat-runs/active?threadId='+quote(self.thread_id,safe='')).get('runs')
        if type(runs) is not list or any(r.get('threadId')!=self.thread_id for r in runs):
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        return runs
    def state(self,principal,thread):
        self._identity(principal,thread);runs=self._active();snapshot=self.runtime.snapshot()
        # A legacy stream is not an attested ended durable turn.
        messages=self._messages()
        stale=False
        candidates=[m for m in messages if isinstance(m.get('metadata'),dict) and
            m['metadata'].get('generationStatus') in ('running','queued','cancelling')]
        if not runs:
            if len(candidates)>4:stale=True
            else:
                for message in candidates:
                    run_id=message['metadata'].get('generationRunId')
                    if not isinstance(run_id,str):stale=True;break
                    prior=self.http.request('GET','/api/inference/chat-runs/'+quote(run_id,safe=''),missing=True)
                    if (prior is None or prior.get('threadId')!=thread or
                            prior.get('status') not in ('completed','failed','cancelled','interrupted')):
                        stale=True;break
        return ParentState(principal,thread,self.profile.model_id,self.runtime.ready(snapshot),
                           bool(runs or stale or snapshot['loading']),True,True,True)
    @staticmethod
    def _leaf(messages,anchor=None):
        by_id={m['id']:m for m in messages};parents={m.get('parentId') for m in messages}
        leaves=[m for m in messages if m['id'] not in parents]
        def descends(m):
            seen=set()
            while m is not None:
                if m['id'] in seen:raise InputRejected('cyclic parent history')
                seen.add(m['id'])
                if m['id']==anchor:return True
                m=by_id.get(m.get('parentId'))
            return anchor is None
        leaves=[m for m in leaves if descends(m)]
        if len(leaves)!=1:raise WorkerProviderError('NATIVE_CHAT_AMBIGUOUS')
        return leaves[0]
    def capture(self,job,principal,thread):
        self._identity(principal,thread)
        with self.owner._connection() as con:
            if con.execute('SELECT 1 FROM native_parent_jobs WHERE job_id=?',(job,)).fetchone():return
        messages=self._messages();runs=self._active()
        if len(runs)>1:raise WorkerProviderError('NATIVE_CHAT_AMBIGUOUS')
        options=dict(self.config.resume_options);system=[]
        if runs:
            run=runs[0];anchor=run['assistantMessageId'];request=run.get('requestPayload',{})
            if request.get('model')!=self.profile.model_id:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
            options={k:v for k,v in request.items() if k in _OPTIONS}|options
            system=[m for m in request.get('messages',[]) if m.get('role')=='system']
            message=next((m for m in messages if m['id']==anchor),None)
            if (message is None or message.get('role')!='assistant' or
                    message.get('parentId')!=run.get('userMessageId') or
                    message.get('metadata',{}).get('generationRunId')!=run.get('id') or
                    message.get('metadata',{}).get('serverManaged') is not True):
                raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        else:
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        if options.get('mcp_enabled') is not True:
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        status=self.http.request('GET','/api/inference/status')
        if status.get('model_identifier')!=self.profile.model_id or type(status.get('requested_context_length')) is not int:
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        restore=dict(self.profile.load_options)
        for source,target in (('requested_context_length','max_seq_length'),('load_in_4bit','load_in_4bit'),('gguf_variant','gguf_variant')):
            value=status.get(source)
            if value is None:continue
            if target in restore and restore[target]!=value:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
            restore[target]=value
        from dataclasses import replace
        replace(self.profile,load_options=restore)  # validate native facts before sealing
        with self.owner._connection() as con:
            con.execute('INSERT INTO native_parent_jobs(job_id,anchor_id,options_json,system_json,restore_json) VALUES(?,?,?,?,?)',
                        (job,anchor,json.dumps(options,sort_keys=True),json.dumps({'source_run_id':runs[0]['id'],'system_digest':_digest(system)}),json.dumps(restore,sort_keys=True)))
    def unload(self,operation):
        state=self.state(self.principal,self.thread_id)
        if not self.allow_unload or state.busy or not state.authenticated:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        self.runtime.unload(self.profile.model_id)
    def _restore_options(self,operation):
        with self.owner._connection() as con:
            bindings=con.execute('SELECT job_id,restore_json FROM native_parent_jobs').fetchall()
        for binding in bindings:
            if operation in ('parent-restore-'+_digest(binding['job_id']),'restore-'+_digest(binding['job_id'])):
                return json.loads(binding['restore_json'])
        return dict(self.profile.load_options)
    def verify_restoration(self,job):
        options=self._restore_options('parent-restore-'+_digest(job))
        self._verify_restore_options(options)
    def _verify_restore_options(self,options):
        status=self.http.request('GET','/api/inference/status')
        if status.get('model_identifier')!=self.profile.model_id:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        for source,target in (('requested_context_length','max_seq_length'),('load_in_4bit','load_in_4bit'),('gguf_variant','gguf_variant')):
            if target in options and status.get(source)!=options[target]:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')

    def restore(self,principal,thread,model,operation):
        self._identity(principal,thread)
        if model!=self.profile.model_id:raise InputRejected('wrong original model')
        restore_options=self._restore_options(operation)
        state=self.state(principal,thread)
        if state.resident:
            self._verify_restore_options(restore_options)
            return state
        if state.busy:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        if self.measure is not None:
            from .model_lifecycle import MemoryReading
            s=self.measure(0,self.profile.memory_vram_mb>0)
            loaded=self.runtime.present(self.runtime.snapshot())
            if (type(s) is not MemoryReading or time.monotonic()-s.measured>5 or
                    s.ram_mb<(0 if loaded else self.profile.memory_ram_mb)+1024 or
                    (self.profile.memory_vram_mb and (s.vram_mb is None or
                     s.vram_mb<(0 if loaded else self.profile.memory_vram_mb)+512))):
                raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        self.runtime.load(operation,load_options=restore_options)
        restored=self.state(principal,thread)
        if not restored.resident:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        self._verify_restore_options(restore_options)
        return restored
    @staticmethod
    def _content(message):
        if message.get('attachments'):raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
        content=message.get('content',[])
        if type(content) is str:return content
        if type(content) is not list:raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
        text=[]
        for part in content:
            if type(part) is not dict:raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
            if part.get('type')=='text' and type(part.get('text')) is str:text.append(part['text'])
            elif any(x in str(part.get('type','')).lower() for x in ('image','audio','video','file')):
                raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
            else:text.append('Recorded parent-chat part (data, do not re-execute): '+json.dumps(part,ensure_ascii=True,sort_keys=True))
        return '\n'.join(text)
    def _prepare(self,job,digest,operation):
        with self.owner._connection() as con:
            prior=con.execute('SELECT * FROM native_parent_returns WHERE job_id=?',(job,)).fetchone()
            binding=con.execute('SELECT * FROM native_parent_jobs WHERE job_id=?',(job,)).fetchone()
        if prior:
            if prior['digest']!=digest or prior['operation_id']!=operation:raise InputRejected('return binding changed')
            return prior,None
        if binding is None:raise InputRejected('original parent branch missing')
        messages=self._messages();leaf=self._leaf(messages,binding['anchor_id']);by_id={m['id']:m for m in messages}
        chain=[];seen=set();node=leaf
        while node is not None:
            if node['id'] in seen:raise InputRejected('cyclic history')
            seen.add(node['id']);chain.append(node)
            parent_id=node.get('parentId')
            if parent_id is not None and parent_id not in by_id:raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
            node=by_id.get(parent_id)
        system_ref=json.loads(binding['system_json'])
        original=self.http.request('GET','/api/inference/chat-runs/'+quote(system_ref['source_run_id'],safe=''))
        history=[m for m in original.get('requestPayload',{}).get('messages',[]) if m.get('role')=='system']
        if (original.get('threadId')!=self.thread_id or original.get('assistantMessageId')!=binding['anchor_id'] or
                original.get('requestPayload',{}).get('model')!=self.profile.model_id or
                _digest(history)!=system_ref['system_digest']):raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        for m in reversed(chain):
            if m.get('role') not in ('user','assistant'):raise WorkerProviderError('NATIVE_CHAT_UNSUPPORTED')
            history.append({'role':m['role'],'content':self._content(m)})
        run='minionone_'+_digest([job,digest,operation]);user=run+'_user';assistant=run+'_assistant'
        notice=('MinionOne job notification (manager metadata, not worker instructions): '+
                json.dumps({'job_id':job,'notification_digest':digest},sort_keys=True)+
                '. Read minionone_job_status; if RESULT_READY, retrieve minionone_workspace_result, review '
                'and integrate it, then acknowledge its exact digest. Otherwise report the job status/question. '
                'Do not start the same worker job again.')
        message={'id':user,'threadId':self.thread_id,'parentId':leaf['id'],'role':'user',
                 'content':[{'type':'text','text':notice}],'createdAt':int(time.time()*1000),
                 'metadata':{'minionone':{'job_id':job,'digest':digest,'operation_id':operation}}}
        payload={**json.loads(binding['options_json']),'model':self.profile.model_id,
                 'messages':history+[{'role':'user','content':notice}],'stream':True,
                 'thread_id':self.thread_id}
        envelope={'runId':run,'threadId':self.thread_id,'userMessageId':user,
                  'assistantMessageId':assistant,'requestPayload':payload,'stopWhenUnattended':False}
        # Parent history is transient: journal only its digest, IDs and control options.
        stored={**envelope,'requestPayload':{k:v for k,v in payload.items() if k!='messages'},
                'messages_digest':_digest(payload['messages'])}
        with self.owner._connection() as con:
            con.execute('INSERT INTO native_parent_returns VALUES(?,?,?,?,?,?)',
                (job,operation,digest,json.dumps(stored,sort_keys=True),json.dumps(message,sort_keys=True),'PREPARED'))
            return con.execute('SELECT * FROM native_parent_returns WHERE job_id=?',(job,)).fetchone(),envelope
    def _phase(self,job,phase):
        with self.owner._connection() as con:con.execute('UPDATE native_parent_returns SET phase=? WHERE job_id=?',(phase,job))
    def reconcile(self,principal,thread,job,digest,operation):
        self._identity(principal,thread)
        with self.owner._connection() as con:row=con.execute('SELECT * FROM native_parent_returns WHERE job_id=?',(job,)).fetchone()
        if row is None or row['digest']!=digest or row['operation_id']!=operation:return None
        envelope=json.loads(row['envelope_json']);message=json.loads(row['message_json'])
        run=self.http.request('GET','/api/inference/chat-runs/'+envelope['runId'],missing=True)
        if run is None:return None
        if any(run.get(k)!=v for k,v in {'id':envelope['runId'],'threadId':thread,
            'userMessageId':message['id'],'assistantMessageId':envelope['assistantMessageId']}.items()):
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        if run.get('status') not in ('queued','running','completed'):return None
        request=run.get('requestPayload',{})
        if request.get('model')!=self.profile.model_id or _digest(request.get('messages'))!=envelope['messages_digest']:
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        observed=self.http.request('GET',self.thread_path+'/messages/'+quote(message['id'],safe=''))
        if any(observed.get(k)!=message[k] for k in ('id','threadId','parentId','role','content','metadata')):
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        assistant=self.http.request('GET',self.thread_path+'/messages/'+quote(envelope['assistantMessageId'],safe=''))
        if (assistant.get('parentId')!=message['id'] or assistant.get('threadId')!=thread or
            assistant.get('role')!='assistant' or assistant.get('metadata',{}).get('generationRunId')!=run['id']):
            raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        self._phase(job,'ACCEPTED')
        return ParentReceipt(job,thread,digest,operation)
    def deliver(self,principal,thread,job,digest,operation):
        state=self.state(principal,thread)
        if not state.resident or state.busy:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        row,wire=self._prepare(job,digest,operation)
        if row['phase']!='PREPARED':
            receipt=self.reconcile(principal,thread,job,digest,operation)
            if receipt:return receipt
            raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        if wire is None:raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        envelope=wire;message=json.loads(row['message_json'])
        self._phase(job,'MESSAGE_IN_FLIGHT')
        self.http.request('PUT',self.thread_path+'/messages/'+quote(message['id'],safe=''),message)
        self._phase(job,'RUN_IN_FLIGHT')
        self.http.request('POST','/api/inference/chat-runs',envelope)
        receipt=self.reconcile(principal,thread,job,digest,operation)
        if receipt is None:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
        return receipt
