"""Regression fixtures: backend configuration now uses the universal workspace job."""
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from minionone.admission import AdmissionStore, InputRejected, _JOB_TABLE
from minionone.mcp_server import LocalMCP
from minionone.pilot_cli import PilotClient
from minionone.pilot_jobs import PilotError
from minionone.worker_profiles import WorkerProfile, ConfiguredWorker, load_profiles

TOKEN='universal-worker-fixture-'+('t'*48)
MODEL='synthetic-worker-model'


def completion(data, mode='ok'):
    n=sum(m['role']=='tool' for m in data['messages'])
    if mode=='forbidden':name,args='execute_shell',{'command':'never execute'}
    elif n==0:name,args='create_work',{'name':'result.txt','content':'SYNTHETIC RESULT'}
    elif n==1:name,args='publish_artifact',{'name':'result.txt'}
    else:
        return {'model':MODEL,'choices':[{'finish_reason':'stop',
            'message':{'role':'assistant','content':'Published.'}}],'usage':{'total_tokens':12}}
    return {'model':MODEL,'choices':[{'finish_reason':'tool_calls','message':{
        'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(n),'type':'function',
        'function':{'name':name,'arguments':json.dumps(args)}}]}}],'usage':{'total_tokens':12}}


class HTTPModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,value,status=200):
        data=json.dumps(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/api/inference/status':
            return self.send({'model_identifier':MODEL,'loaded':[MODEL],'loading':[]})
        if self.path!='/api/v0/models':return self.send({},404)
        self.send({'data':[{'id':MODEL,'state':'loaded'}]})
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(data)
        if self.server.mode=='error':return self.send({},503)
        value=completion(data,self.server.mode)
        if self.server.mode=='wrong_model':value['model']='another-model'
        self.send(value)


class WorkerMCPFixture:
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.project=self.root/'project';self.project.mkdir()
        (self.root/'parent-private-history.txt').write_text('NEVER-SEND-PARENT-HISTORY')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),HTTPModel)
        self.http.daemon_threads=True;self.http.requests=[];self.http.mode='ok'
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
        endpoint=f'http://127.0.0.1:{self.http.server_port}'
        self.profiles={name:WorkerProfile(name,provider,MODEL,endpoint,timeout_seconds=15)
            for name,provider in (('local','lmstudio'),('native','unsloth'))}
        self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.root/'jobs.sqlite',
            worker_profiles=self.profiles,min_free_mb=1,enable_workspace_jobs=True,
            workspace_projects={'demo':self.project},worker_model='unused-default-model')
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.client=PilotClient(self.server.server_port,TOKEN)

    def submit(self,profile='local',op='universal-job-operation-001',**extra):
        return self.client.tool('minionone_delegate',dict(profile_id=profile,project_id='demo',
            task='Perform exactly the task supplied by Flash.',operation_id=op,**extra))

    def terminal(self,job):
        until=time.monotonic()+40
        while time.monotonic()<until:
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            if status['state'] in ('RESULT_READY','FAILED','NEEDS_RECONCILIATION','WAITING_FOR_PARENT'):
                return status
            time.sleep(.05)
        self.fail('bounded worker did not finish')

    def staged(self,profile='local',op='staged-start-operation-001',**extra):
        job=self.client.tool('minionone_prepare_workspace',{'project_id':'demo','operation_id':'prepare-'+op})
        folder=self.project/'.minionone'/'jobs'/job['job_id']/'input'
        payload={'TASK.md':b'Perform exactly the task supplied by Flash.'}
        for name,body in payload.items():(folder/name).write_bytes(body)
        (folder/'manifest.json').write_text(json.dumps({'files':{n:sha256(b).hexdigest() for n,b in payload.items()}}))
        sealed=self.client.tool('minionone_seal_workspace',{'job_id':job['job_id'],
            'expected_version':0,'operation_id':'seal-'+op})
        args=dict(job_id=job['job_id'],expected_version=sealed['version'],operation_id=op,profile_id=profile,**extra)
        return self.client.tool('minionone_start_workspace_job',args),args


class UniversalWorkerTests(WorkerMCPFixture,unittest.TestCase):
    def test_task_only_quick_job_also_works_with_the_existing_default_worker(self):
        self.server.pilot.config=replace(self.server.pilot.config,model_id=MODEL,
            base_url=f'http://127.0.0.1:{self.http.server_port}')
        job=self.client.tool('minionone_delegate',{'project_id':'demo','task':'Flash supplies this task.',
            'operation_id':'default-quick-operation-001'})
        self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.assertEqual(len(self.http.requests),3)

    def test_configured_input_budget_applies_before_any_staging_or_request(self):
        self.server.pilot.worker_profiles['tiny']=replace(self.profiles['local'],profile_id='tiny',max_input_bytes=5)
        with self.assertRaises(PilotError):self.submit('tiny')
        self.assertFalse((self.project/'.minionone').exists())
        self.assertFalse(self.http.requests)

    def test_both_handoffs_use_same_workspace_lifecycle_for_both_local_backends(self):
        for profile in ('local','native'):
            for path in ('direct','file'):
                with self.subTest(profile=profile,path=path):
                    op='universal-'+profile+'-'+path+'-001'
                    job=self.submit(profile,op) if path=='direct' else self.staged(profile,op)[0]
                    status=self.terminal(job);self.assertEqual(status['state'],'RESULT_READY',status)
                    self.assertNotIn('text_job',status)
                    self.assertEqual(status['workspace_job']['worker_model'],MODEL)
                    result=self.client.tool('minionone_workspace_result',{'job_id':job['job_id']})
                    self.assertEqual(result['files'][0]['text'],'SYNTHETIC RESULT')
                    folder=self.project/'.minionone'/'jobs'/job['job_id']
                    self.assertEqual((folder/'input'/'TASK.md').read_text(),'Perform exactly the task supplied by Flash.')
                    self.assertEqual((folder/'output'/'result.txt').read_text(),'SYNTHETIC RESULT')
                    ack=self.client.tool('minionone_accept_workspace_result',dict(job_id=job['job_id'],
                        expected_version=result['version'],result_digest=result['result_digest'],operation_id='ack-'+op))
                    self.assertEqual(ack['state'],'COMPLETED')
        self.assertEqual(len(self.http.requests),12)
        for request in self.http.requests:
            self.assertNotIn('NEVER-SEND-PARENT-HISTORY',json.dumps(request))
            self.assertNotIn(str(self.project),json.dumps(request))
        native=self.http.requests[6]
        self.assertFalse(native['enable_tools']);self.assertFalse(native['mcp_enabled'])
        self.assertFalse(native['run_tools_locally']);self.assertIsNone(native['thread_id'])
        self.assertEqual(native['max_tool_calls_per_message'],1)
        self.assertIsNone(native['session_id']);self.assertIsNone(native['rag_scope'])
        self.assertEqual(native['tool_choice'],'auto')
        self.assertIn('create_work',{t['function']['name'] for t in native['tools']})

    def test_no_separate_review_consult_or_role_surface(self):
        self.assertNotIn('minionone_consult',self.server.tool_map)
        self.assertFalse(any('consult' in x for x in self.server.tool_map))
        workers=self.client.tool('minionone_workers',{})
        self.assertEqual(workers['handoff_paths'],['direct','prepared_workspace'])
        info=self.client.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'flash','version':'1'}})['result']['instructions']
        self.assertNotIn('minionone_consult',info)
        self.assertNotIn('Third text-only',info)

    def test_direct_replay_and_backend_change_are_fenced(self):
        job=self.submit();self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.assertEqual(self.submit(),job)
        with self.assertRaises(PilotError):self.submit(profile='native')
        self.assertEqual(len(self.http.requests),3)

    def test_staged_replay_and_backend_change_are_fenced(self):
        job,args=self.staged();self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.assertEqual(self.client.tool('minionone_start_workspace_job',args),job)
        with self.assertRaises(PilotError):self.client.tool('minionone_start_workspace_job',{**args,'profile_id':'native'})
        self.assertEqual(len(self.http.requests),3)

    def test_missing_or_ambiguous_task_or_injected_endpoint_is_rejected_before_staging(self):
        base={'project_id':'demo','operation_id':'reject-ambiguous-task-001','profile_id':'local'}
        for fields in ({},{'task':'task','goal':'other'},{'task':'task','base_url':'https://evil.example'},
                       {'task':'task','profile_id':'missing'}):
            with self.assertRaises(PilotError):self.client.tool('minionone_delegate',{**base,**fields})
        self.assertFalse((self.project/'.minionone').exists())

    def test_provider_uncertainty_blocks_both_handoff_paths(self):
        self.http.mode='error';job=self.submit()
        self.assertEqual(self.terminal(job)['state'],'NEEDS_RECONCILIATION')
        with self.assertRaises(PilotError):self.staged('native')
        self.assertEqual(len(self.http.requests),1)

    def test_native_wrong_model_never_publishes_output(self):
        self.http.mode='wrong_model';job=self.submit('native')
        self.assertEqual(self.terminal(job)['state'],'NEEDS_RECONCILIATION')
        self.assertFalse((self.project/'.minionone'/'jobs'/job['job_id']/'output'/'result.txt').exists())

    def test_resource_floor_prevents_local_inference(self):
        with patch('minionone.pilot_jobs.available_memory_mb',return_value=0):
            job=self.submit();self.assertEqual(self.terminal(job)['state'],'FAILED')
        self.assertFalse(self.http.requests)

    def test_forbidden_tool_is_never_executed(self):
        self.http.mode='forbidden';job=self.submit()
        self.assertEqual(self.terminal(job)['state'],'FAILED')
        self.assertFalse((self.project/'.minionone'/'jobs'/job['job_id']/'output'/'result.txt').exists())

    def test_cloud_approval_and_exact_private_io_on_both_paths(self):
        cloud=WorkerProfile('cloud','cloud',MODEL,'https://synthetic.invalid/v1',
            secret_env='MINIONONE_SYNTHETIC_CLOUD_KEY',cloud_approved=True,token_parameter='max_completion_tokens')
        self.server.pilot.worker_profiles['cloud']=cloud
        seen=[]
        def transport(url,body,key,timeout):
            self.assertEqual(key,'SYNTHETIC-SECRET');self.assertEqual(url,'https://synthetic.invalid/v1/chat/completions')
            data=json.loads(body);seen.append(data)
            self.assertIn('max_completion_tokens',data);self.assertNotIn('max_tokens',data)
            return json.dumps(completion(data)).encode()
        with self.assertRaises(PilotError):self.submit('cloud')
        with patch.dict(os.environ,{'MINIONONE_SYNTHETIC_CLOUD_KEY':'SYNTHETIC-SECRET'}),patch('minionone.worker_profiles.bounded_request',transport):
            for path in ('direct','file'):
                job=(self.submit('cloud','cloud-direct-operation-001',allow_cloud=True) if path=='direct'
                     else self.staged('cloud','cloud-file-operation-001',allow_cloud=True)[0])
                self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
                io=self.client.tool('minionone_worker_io',{'job_id':job['job_id']})
                self.assertNotIn('SYNTHETIC-SECRET',json.dumps(io))
        self.assertEqual(len(seen),6)

    def test_worker_profile_snapshot_is_immutable_and_bound_to_admitted_input(self):
        from minionone.worker_selection import load
        job=self.submit();self.terminal(job)
        with self.server.store._connection() as con:
            with self.assertRaises(sqlite3.IntegrityError):con.execute('DELETE FROM pilot_worker_profiles WHERE job_id=?',(job['job_id'],))
            con.execute('DROP TRIGGER worker_profile_no_update')
            bad=json.dumps(replace(self.profiles['local'],base_url='http://127.0.0.1:9').__dict__)
            con.execute('UPDATE pilot_worker_profiles SET profile_json=? WHERE job_id=?',(bad,job['job_id']))
            with self.assertRaises(InputRejected):load(con,job['job_id'])


class ProfileValidationTests(unittest.TestCase):
    def test_no_role_instruction_is_required_and_credentials_cannot_be_embedded(self):
        p=WorkerProfile('model','lmstudio',MODEL,'http://127.0.0.1:1234')
        self.assertEqual(p.instructions,'')
        for url in ('http://localhost:1234','http://127.0.0.1:1234?key=secret','http://a:b@127.0.0.1:1234'):
            with self.assertRaises(InputRejected):replace(p,base_url=url)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profiles.json';path.write_text('{"profiles":[],"profiles":[]}')
            with self.assertRaises(InputRejected):load_profiles(path)
            path.write_text(json.dumps({'profiles':[dict(p.__dict__,api_key='forbidden')]}))
            with self.assertRaises(InputRejected):load_profiles(path)

    def test_unloaded_or_inactive_local_models_are_not_implicitly_loaded(self):
        for provider,state in (('lmstudio',{'data':[{'id':MODEL,'state':'unloaded'}]}),
                               ('unsloth',{'model_identifier':'other','loaded':[MODEL],'loading':[]})):
            p=WorkerProfile('worker',provider,MODEL,'http://127.0.0.1:1234')
            worker=ConfiguredWorker(p,transport=lambda *a:json.dumps(state).encode())
            with self.assertRaises(Exception):worker.preflight()

    def test_v043_schema_migration_preserves_jobs_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.sqlite'
            with closing(sqlite3.connect(path)) as con:
                con.execute(_JOB_TABLE.format(name='jobs').replace(",'TEXT'",''));con.commit()
            store=AdmissionStore(path,credentials={TOKEN:('parent',{'thread'})})
            job=store.admit(TOKEN,'thread','a'*64,'schema-job-operation-001')
            store=AdmissionStore(path,credentials={TOKEN:('parent',{'thread'})})
            self.assertEqual(store.status(TOKEN,'thread',job['job_id'])['state'],'ACCEPTED')
            with store._connection() as con:self.assertFalse(con.execute('PRAGMA foreign_key_check').fetchall())


if __name__=='__main__':unittest.main()
