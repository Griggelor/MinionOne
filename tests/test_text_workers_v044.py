"""Real MCP + bounded HTTP text workers; cloud uses a synthetic transport only."""
from contextlib import closing
from dataclasses import replace
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
from minionone.worker_profiles import WorkerProfile, TextWorker, load_profiles

TOKEN='text-worker-fixture-'+('t'*48)
MODEL='synthetic-text-model'


class HTTPModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,value,status=200):
        data=json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
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
        message={'role':'assistant','content':'Synthetic review: check the assumptions.'}
        if self.server.mode=='tools':message['tool_calls']=[{'function':{'name':'execute_shell'}}]
        self.send({'model':MODEL,'choices':[{'finish_reason':'stop','message':message}],
                   'usage':{'total_tokens':32}})


class TextMCPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        (self.root/'parent-private-history.txt').write_text('NEVER-SEND-PARENT-HISTORY')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),HTTPModel)
        self.http.daemon_threads=True;self.http.requests=[];self.http.mode='ok'
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
        endpoint=f'http://127.0.0.1:{self.http.server_port}'
        self.profiles={name:WorkerProfile(name,provider,MODEL,endpoint,
            'Return a concise critical review.',timeout_seconds=15)
            for name,provider in (('fast','lmstudio'),('native','unsloth'))}
        self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.root/'jobs.sqlite',
                             worker_profiles=self.profiles,min_free_mb=1)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.client=PilotClient(self.server.server_port,TOKEN)

    def consult(self,profile='fast',op='text-review-operation-001',**extra):
        return self.client.tool('minionone_consult',dict(profile_id=profile,
            prompt='Review only this supplied proposal.',operation_id=op,**extra))

    def terminal(self,job):
        until=time.monotonic()+40
        while time.monotonic()<until:
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            if status['state'] in ('RESULT_READY','FAILED','NEEDS_RECONCILIATION'):
                return status
            time.sleep(.05)
        self.fail('bounded text fixture did not finish')

    def test_two_local_provider_roles_one_call_no_files_no_implicit_context_and_ack(self):
        for profile in ('fast','native'):
            with self.subTest(profile=profile):
                job=self.consult(profile,op='text-'+profile+'-operation-001')
                status=self.terminal(job)
                self.assertEqual(status['state'],'RESULT_READY',status)
                self.assertEqual(status['text_job']['backend'],'bounded_text_no_worker_tools')
                self.assertEqual(status['text_job']['model_turns'],1)
                self.assertEqual(status['text_job']['tool_calls'],0)
                result=self.client.tool('minionone_consult_result',{'job_id':job['job_id']})
                self.assertTrue(result['untrusted_model_output'])
                self.assertIn('Synthetic review',result['text'])
                ack=self.client.tool('minionone_accept_consult_result',{
                    'job_id':job['job_id'],'expected_version':result['version'],
                    'result_digest':result['result_digest'],'operation_id':'ack-'+profile+'-operation-001'})
                self.assertEqual(ack['state'],'COMPLETED')
        self.assertEqual(len(self.http.requests),2)
        for request in self.http.requests:
            self.assertNotIn('tools',request)
            self.assertEqual(len(request['messages']),2)
            supplied=json.loads(request['messages'][1]['content'])
            self.assertEqual(supplied['context'],'')
            self.assertNotIn('NEVER-SEND-PARENT-HISTORY',json.dumps(request))
        self.assertFalse(any(self.root.rglob('work')))

    def test_replay_does_not_repeat_inference_and_changed_explicit_context_conflicts(self):
        job=self.consult(context='Only this explicit background.')
        self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.assertEqual(self.consult(context='Only this explicit background.'),job)
        with self.assertRaises(PilotError) as failure:self.consult(context='Changed background')
        self.assertEqual(failure.exception.diagnostic.code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.http.requests),1)

    def test_provider_tool_request_never_executes_or_creates_a_result(self):
        self.http.mode='tools'
        job=self.consult()
        self.assertEqual(self.terminal(job)['state'],'NEEDS_RECONCILIATION')
        with self.assertRaises(PilotError):
            self.client.tool('minionone_consult_result',{'job_id':job['job_id']})
        self.assertFalse(any(self.root.rglob('work')))

    def test_local_text_worker_respects_manager_memory_floor_before_inference(self):
        with patch('minionone.pilot_jobs.available_memory_mb',return_value=0):
            job=self.consult()
            status=self.terminal(job)
        self.assertEqual(status['state'],'FAILED')
        self.assertEqual(status['text_job']['error']['code'],'PILOT_RESOURCE_UNVERIFIED')
        self.assertFalse(self.http.requests)

    def test_uncertain_post_is_fenced_and_blocks_next_worker(self):
        self.http.mode='error';job=self.consult()
        status=self.terminal(job)
        self.assertEqual(status['state'],'NEEDS_RECONCILIATION')
        self.assertTrue(status['text_job']['provider_state_uncertain'])
        with self.assertRaises(PilotError) as failure:
            self.consult(op='text-second-operation-001')
        self.assertEqual(failure.exception.diagnostic.code,'PILOT_JOB_BUSY')
        self.assertEqual(len(self.http.requests),1)

    def test_explicit_context_optional_and_no_profile_endpoint_can_be_supplied(self):
        reply=self.client.request('tools/call',{'name':'minionone_consult','arguments':{
            'profile_id':'fast','prompt':'Allowed text','operation_id':'extra-field-operation-001',
            'base_url':'https://unapproved.invalid'}})
        self.assertTrue(reply['result']['isError'])
        catalog=self.client.tool('minionone_workers',{})
        self.assertEqual({x['provider'] for x in catalog['workers']},{'lmstudio','unsloth'})
        self.assertTrue(all(x['worker_tools']==[] for x in catalog['workers']))
        self.assertTrue(all(x['readiness']=='CONFIGURED_NOT_PROBED' for x in catalog['workers']))
        self.assertNotIn('base_url',json.dumps(catalog))

    def test_cloud_mcp_needs_explicit_permission_and_never_logs_header_credentials(self):
        profile=WorkerProfile('cloud','cloud','synthetic-cloud','https://cloud.invalid',
            'Review only supplied input.',secret_env='MINIONONE_CLOUD_FIXTURE_KEY',cloud_approved=True)
        self.server.pilot.text_profiles['cloud']=profile
        with self.assertRaises(PilotError) as failure:self.consult('cloud')
        self.assertEqual(failure.exception.diagnostic.code,'INVALID_INPUT')
        calls=[]
        def fake_transport(url,body,key,timeout):
            calls.append(json.loads(body))
            self.assertEqual(key,'cloud-key-never-in-transcript')
            return json.dumps({'choices':[{'finish_reason':'stop','message':{
                'role':'assistant','content':'Synthetic cloud review'}}],
                'usage':{'total_tokens':20}}).encode()
        original=TextWorker
        with patch.dict(os.environ,{'MINIONONE_CLOUD_FIXTURE_KEY':'cloud-key-never-in-transcript'}), \
             patch('minionone.worker_profiles.TextWorker',side_effect=lambda p:original(p,transport=fake_transport)):
            job=self.consult('cloud',allow_cloud=True)
            self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
            io=self.client.tool('minionone_consult_io',{'job_id':job['job_id']})
        self.assertEqual(len(calls),1)
        self.assertNotIn('cloud-key-never-in-transcript',json.dumps(io))
        self.assertEqual(json.loads(calls[0]['messages'][1]['content'])['context'],'')


class ProfileAndCloudTests(unittest.TestCase):
    def profile(self,**changes):
        values=dict(profile_id='review',provider='cloud',model_id='test-cloud-model',
            base_url='https://provider.invalid/v1',instructions='Critically review supplied text.',
            secret_env='MINIONONE_SYNTHETIC_CLOUD_KEY',cloud_approved=True)
        return WorkerProfile(**(values|changes))

    def test_cloud_contract_only_explicit_payload_and_credentials_never_in_io(self):
        requests=[];events=[];key='synthetic-cloud-key-canary'
        def fake_https(url,body,credential,timeout):
            self.assertEqual(url,'https://provider.invalid/v1/chat/completions')
            self.assertEqual(credential,key)
            requests.append(json.loads(body))
            return json.dumps({'choices':[{'finish_reason':'stop','message':{
                'role':'assistant','content':'A bounded synthetic cloud review.'}}],
                'usage':{'total_tokens':20}}).encode()
        with patch.dict(os.environ,{'MINIONONE_SYNTHETIC_CLOUD_KEY':key}):
            worker=TextWorker(self.profile(),transport=fake_https);worker.preflight()
            text,tokens=worker.complete('EXPLICIT INPUT ONLY',sink=lambda k,b:events.append((k,b)))
        self.assertEqual(len(requests),1)
        self.assertEqual(tokens,20)
        self.assertIn('review',text)
        self.assertNotIn(key,json.dumps(events))
        self.assertEqual(requests[0]['messages'][1]['content'],'EXPLICIT INPUT ONLY')

    def test_cloud_without_operator_approval_or_https_and_local_nonloopback_rejected(self):
        for changes in ({'cloud_approved':False},{'base_url':'http://provider.invalid'},
                        {'provider':'lmstudio','base_url':'http://host.invalid:1234'}):
            with self.subTest(changes=changes),self.assertRaises(InputRejected):self.profile(**changes)

    def test_profile_file_rejects_duplicate_keys_names_and_raw_credential_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profiles.json'
            for text in ('{"profiles":[],"profiles":[]}',
                         '{"profiles":[{"api_key":"must-not-be-configured-here"}]}'):
                path.write_text(text)
                with self.assertRaises(InputRejected):load_profiles(path)

    def test_local_readiness_rejects_unloaded_or_inactive_models_before_inference(self):
        for provider,response in (
            ('lmstudio',{'data':[{'id':'chosen','state':'not-loaded'}]}),
            ('unsloth',{'model_identifier':'other','loaded':['chosen'],'loading':[]})):
            with self.subTest(provider=provider):
                profile=WorkerProfile('local',provider,'chosen','http://127.0.0.1:1234','Review')
                worker=TextWorker(profile,transport=lambda *args:json.dumps(response).encode())
                with self.assertRaises(Exception) as failure:worker.preflight()
                self.assertEqual(failure.exception.diagnostic.code,'PILOT_MODEL_NOT_LOADED')

    def test_v043_database_migration_retains_job_identity_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'jobs.sqlite'
            with closing(sqlite3.connect(path)) as con:
                con.execute(_JOB_TABLE.format(name='jobs').replace(",'TEXT'",''))
                con.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('JOB-existing','owner','thread','a'*64,'COMPLETED',7,'WORKSPACE',1,'old','old'))
                con.execute('CREATE TABLE preserved_ref(job TEXT REFERENCES jobs(job_id))')
                con.execute("INSERT INTO preserved_ref VALUES('JOB-existing')");con.commit()
            owner=AdmissionStore(path,credentials={TOKEN:('owner',{'thread'})})
            with owner._connection() as con:
                self.assertEqual(con.execute('SELECT job_kind FROM jobs').fetchone()[0],'WORKSPACE')
                self.assertEqual(con.execute('SELECT job FROM preserved_ref').fetchone()[0],'JOB-existing')
                self.assertIsNone(con.execute('PRAGMA foreign_key_check').fetchone())
                self.assertIn("'TEXT'",con.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0])


if __name__=='__main__':unittest.main()
