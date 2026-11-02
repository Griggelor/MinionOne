"""Deterministic HTTP protocol fixture, never an actual Unsloth/LM Studio host."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit,unquote

PARENT='flash-original'
WORKER='worker-key'
THREAD='original-thread'

class NativeFixture(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self):
        super().__init__(('127.0.0.1',0),Handler)
        self.lock=threading.RLock();self.loaded={PARENT};self.active=PARENT;self.lm_loaded={}
        self.load_payloads=[];self.busy=True;self.events=[];self.requests=[];self.runs={};self.reject_restore=False
        self.lose_return_response=False;self.branch=False;self.early_ack=None;self.worker_reply=None
        self.messages=[{'id':'user-original','threadId':THREAD,'parentId':None,'role':'user',
            'content':[{'type':'text','text':'PRIVATE ORIGINAL FLASH HISTORY'}],'createdAt':1},
            {'id':'assistant-original','threadId':THREAD,'parentId':'user-original','role':'assistant',
            'content':[{'type':'text','text':'Delegating.'}],'createdAt':2,
            'metadata':{'serverManaged':True,'generationRunId':'original-run','generationStatus':'running'}}]
        self.original={'id':'original-run','threadId':THREAD,'userMessageId':'user-original',
            'assistantMessageId':'assistant-original','status':'running','requestPayload':{
            'model':PARENT,'messages':[{'role':'system','content':'ORIGINAL FLASH SYSTEM POLICY'},
                {'role':'user','content':'PRIVATE ORIGINAL FLASH HISTORY'}],
            'enable_tools':True,'enabled_tools':[],'mcp_enabled':True}}
        self.runs['original-run']=self.original
    @property
    def url(self):return f'http://127.0.0.1:{self.server_port}'
    def start(self):threading.Thread(target=self.serve_forever,daemon=True).start();return self
    def end_turn(self):
        with self.lock:
            self.busy=False
            for run in self.runs.values():
                if run['status'] in ('running','queued'):run['status']='completed'
            for message in self.messages:
                if message.get('metadata',{}).get('generationStatus') in ('running','queued'):
                    message['metadata']['generationStatus']='completed'
    def begin_parent_turn(self):
        with self.lock:
            run=list(self.runs.values())[-1];run['status']='running';self.busy=True
            message=next(m for m in self.messages if m['id']==run['assistantMessageId'])
            message['metadata']['generationStatus']='running'

    def memory(self,*args):
        from minionone.model_lifecycle import MemoryReading
        used=(2000 if PARENT in self.loaded else 0)+(1500 if WORKER in self.loaded else 0)+(1500 if self.lm_loaded else 0)
        return MemoryReading(self.capacity-used,None,time.monotonic())

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,value,status=200):
        raw=json.dumps(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        with self.server.lock:self.get()
    def get(self):
        if self.path.startswith(('/api/inference/','/api/chat/')) and self.headers.get('Authorization')!='Bearer synthetic-key':
            return self.respond({},401)
        s=self.server;path=unquote(urlsplit(self.path).path)
        if path=='/api/inference/status':return self.respond({'model_identifier':s.active,
            'loaded':sorted(s.loaded),'loading':[],'serving':sorted(s.loaded),'requested_context_length':4096,'load_in_4bit':True})
        if path=='/api/v1/models':return self.respond({'models':[{'key':WORKER,'loaded_instances':
            [{'id':i,'config':{'context_length':4096}} for i in s.lm_loaded]}]})
        if path=='/api/v0/models':return self.respond({'data':[{'id':i,'state':'loaded'} for i in s.lm_loaded]})
        if path==f'/api/chat/threads/{THREAD}':return self.respond({'id':THREAD,'modelId':PARENT,'archived':False,'pairId':None})
        if path==f'/api/chat/threads/{THREAD}/messages':return self.respond({'messages':s.messages})
        if path.startswith(f'/api/chat/threads/{THREAD}/messages/'):
            id=path.rsplit('/',1)[1];message=next((m for m in s.messages if m['id']==id),None)
            return self.respond(message or {},200 if message else 404)
        if path=='/api/inference/chat-runs/active':
            return self.respond({'runs':[r for r in s.runs.values() if r['status'] in ('queued','running')]})
        if path.startswith('/api/inference/chat-runs/'):
            run=s.runs.get(path.rsplit('/',1)[1]);return self.respond(run or {},200 if run else 404)
        return self.respond({},404)
    def read(self):return json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    def do_PUT(self):
        if self.headers.get('Authorization')!='Bearer synthetic-key':return self.respond({},401)
        message=self.read()
        with self.server.lock:
            s=self.server;s.events.append('notification')
            if any(m['id']==message['id'] for m in s.messages):return self.respond({},409)
            s.messages.append(message);self.respond(message)
    def do_POST(self):
        if self.path.startswith('/api/inference/') and self.headers.get('Authorization')!='Bearer synthetic-key':
            return self.respond({},401)
        data=self.read();s=self.server;path=self.path
        if path=='/api/inference/chat-runs':
            with s.lock:
                s.events.append('parent-run');run={'id':data['runId'],'threadId':data['threadId'],
                    'userMessageId':data['userMessageId'],'assistantMessageId':data['assistantMessageId'],
                    'requestPayload':data['requestPayload'],'status':'completed'}
                s.runs[run['id']]=run
                s.messages.append({'id':data['assistantMessageId'],'threadId':data['threadId'],
                    'parentId':data['userMessageId'],'role':'assistant','content':[{'type':'text','text':'Native parent resumed.'}],
                    'createdAt':int(time.time()*1000),'metadata':{'generationRunId':data['runId'],
                    'generationStatus':'completed','serverManaged':True}})
            if s.early_ack:s.early_ack()
            if s.lose_return_response:
                self.connection.shutdown(2);self.connection.close();return
            return self.respond(run,202)
        with s.lock:
            if path=='/api/inference/unload':
                if s.busy or data.get('force_cancel_active'):return self.respond({},409)
                s.events.append('unload:'+data['model_path']);s.loaded.discard(data['model_path'])
                if s.active==data['model_path']:s.active=next(iter(s.loaded),None)
                return self.respond({'status':'unloaded','model':data['model_path']})
            if path=='/api/inference/load':
                model=data['model_path']
                if s.reject_restore and model==PARENT:return self.respond({},503)
                if s.busy:return self.respond({},409)
                if data.get('alongside') is not True or data.get('force_alongside') is not False:return self.respond({},400)
                s.load_payloads.append(data);s.events.append('load:'+model);s.loaded.add(model);s.active=model
                return self.respond({'status':'loaded','model':model,'evicted':[],'evicted_slots':[]})
            if path=='/api/v1/models/load':
                s.events.append('lm-load');s.lm_loaded={'worker-instance':WORKER}
                return self.respond({'status':'loaded','instance_id':'worker-instance'})
            if path=='/api/v1/models/unload':
                s.events.append('lm-unload');s.lm_loaded.pop(data['instance_id'],None)
                return self.respond({'instance_id':data['instance_id']})
            if path!='/v1/chat/completions':return self.respond({},404)
            s.requests.append(data);s.events.append('worker-inference')
            if s.worker_reply is not None:return self.respond(s.worker_reply(data))
            count=sum(m['role']=='tool' for m in data['messages'])
            if count==0:name,args='create_work',{'name':'result.txt','content':'NATIVE LIFECYCLE RESULT'}
            elif count==1:name,args='publish_artifact',{'name':'result.txt'}
            else:return self.respond({'model':data['model'],'choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':'Published.'}}],'usage':{'total_tokens':12}})
            return self.respond({'model':data['model'],'choices':[{'finish_reason':'tool_calls',
                'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(count),
                'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}],
                'usage':{'total_tokens':12}})


def question_worker_reply(data):
    """Deterministic paused/resumed native worker, used by the frozen app check."""
    count=sum(m['role']=='tool' for m in data['messages'])
    answered=any(m.get('role')=='user' and m.get('content','').startswith('Explicit parent answer') for m in data['messages'])
    if count==0:name,args='create_work',{'name':'result.txt','content':'NATIVE LIFECYCLE RESULT'}
    elif not answered:name,args='ask_question',{'question':'May the existing result be published?'}
    elif count==2:name,args='publish_artifact',{'name':'result.txt'}
    else:return {'model':data['model'],'choices':[{'finish_reason':'stop',
        'message':{'role':'assistant','content':'Published.'}}],'usage':{'total_tokens':12}}
    return {'model':data['model'],'choices':[{'finish_reason':'tool_calls',
        'message':{'role':'assistant','content':None,'tool_calls':[{'id':'question-call-'+str(count),
        'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}],
        'usage':{'total_tokens':12}}
