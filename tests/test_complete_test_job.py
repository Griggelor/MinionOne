"""Real MCP HTTP -> actual provider adapter -> scoped documents -> parent ACK.

The HTTP model server is deterministic synthetic test infrastructure. The exact
same manager/adapter path is used with real LM Studio in the packaged pilot.
"""
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from minionone.mcp_server import LocalMCP
from minionone.pilot_jobs import DocumentBroker,PilotConfig
from minionone.pilot_cli import exercise,PilotClient,export_result
from minionone.agent_loop import BrokerScopeDenied
from minionone.lifecycle import Evidence

TOKEN='synthetic-complete-job-'+'x'*48

class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,value):
        body=json.dumps(value).encode();self.send_response(200)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass  # deliberate client deadline test
    def do_GET(self):
        if self.path!='/api/v0/models':self.send_error(404);return
        self.send({'data':[{'id':'synthetic-worker','state':'loaded' if self.server.loaded else 'not-loaded'}]})
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path!='/v1/chat/completions':self.send_error(404);return
        self.server.calls+=1
        if self.server.delay:time.sleep(self.server.delay)
        messages=data['messages'];tools=[m for m in messages if m.get('role')=='tool']
        n=len(tools)
        if self.server.mode=='deny':name,args='execute_shell',{'command':'synthetic denied'}
        elif n==0:name,args='read_file',{'path':'input/sample.txt'}
        elif n==1:
            text=json.loads(tools[0]['content'])['data']['content']
            name,args='create_work',{'name':'result.txt','content':text.upper()}
        elif n==2:name,args='publish_artifact',{'name':'result.txt'}
        else:
            self.send({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'Synthetic result is published.'}}],
                       'usage':{'total_tokens':20}});return
        self.send({'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'call-'+str(n),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}],
            'usage':{'total_tokens':20}})

class CompleteTestJobTests(unittest.TestCase):
    def test_operator_provider_url_is_canonicalized_before_requests(self):
        config=PilotConfig('synthetic-worker','http://127.0.0.1:1234/')
        self.assertEqual(config.base_url,'http://127.0.0.1:1234')
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'state.sqlite'
        self.model=ThreadingHTTPServer(('127.0.0.1',0),ModelHandler)
        self.model.daemon_threads=True;self.model.calls=0;self.model.loaded=True;self.model.mode='normal';self.model.delay=0
        worker=threading.Thread(target=self.model.serve_forever,daemon=True);worker.start()
        self.addCleanup(self.model.server_close);self.addCleanup(self.model.shutdown)
        self.server=self.open_server();threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def open_server(self):
        return LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.db,
            lmstudio_url=f'http://127.0.0.1:{self.model.server_port}',
            enable_test_jobs=True,worker_model='synthetic-worker',min_free_mb=1,
            model_call_seconds=3)

    def rpc(self,name,args):
        with closing(http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)) as c:
            c.request('POST','/mcp',json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call',
                      'params':{'name':name,'arguments':args}}),headers={
                      'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json',
                      'Accept':'application/json, text/event-stream'})
            r=c.getresponse();self.assertEqual(r.status,200);result=json.loads(r.read())['result']
        value=json.loads(result['content'][0]['text']);return result,value

    def submit(self,op='complete-submit-0001'):
        r,v=self.rpc('minionone_submit_test_job',{'task':'Read sample.txt and publish its uppercase text in result.txt.',
                  'files':{'sample.txt':'hello minion'},'operation_id':op})
        self.assertFalse(r.get('isError',False),v);return v

    def start(self,job,op='complete-start-0001'):
        r,v=self.rpc('minionone_run_test_job',{'job_id':job['job_id'],'expected_version':job['version'],'operation_id':op})
        self.assertFalse(r.get('isError',False),v);return v

    def status(self,job):return self.rpc('minionone_job_status',{'job_id':job['job_id']})[1]

    def wait(self,job,limit=20):
        deadline=time.monotonic()+limit
        while time.monotonic()<deadline:
            value=self.status(job)
            if value['state'] in ('RESULT_READY','COMPLETED','FAILED','NEEDS_RECONCILIATION','CANCELLED','WAITING_FOR_PARENT'):return value
            time.sleep(.1)
        self.fail('synthetic production-path job did not reach a bounded outcome')

    def test_full_http_document_job_and_authenticated_result_ack(self):
        job=self.submit();self.assertEqual(self.submit(),job)
        started=self.start(job);self.assertEqual(started['state'],'RESOURCE_CHECK')
        ready=self.wait(job);self.assertEqual(ready['state'],'RESULT_READY',ready)
        self.assertEqual(ready['test_job']['tool_calls'],3)
        self.assertEqual(ready['test_job']['model_turns'],4)
        self.assertEqual(self.model.calls,4)
        # Even a known status digest is not enough before result delivery is issued.
        r,v=self.rpc('minionone_accept_test_result',{'job_id':job['job_id'],'expected_version':ready['version'],
                    'result_digest':ready['test_job']['result_digest'],'operation_id':'premature-ack-0001'})
        self.assertTrue(r['isError']);self.assertEqual(v['error']['code'],'PILOT_RESULT_NOT_DELIVERED')
        r,result=self.rpc('minionone_test_result',{'job_id':job['job_id']})
        self.assertEqual(result['files'][0]['text'],'HELLO MINION')
        self.assertTrue(result['untrusted_model_output'])
        args={'job_id':job['job_id'],'expected_version':result['version'],
              'result_digest':result['result_digest'],'operation_id':'complete-ack-0001'}
        r,ack=self.rpc('minionone_accept_test_result',args);self.assertEqual(ack['state'],'COMPLETED',ack)
        self.assertEqual(self.rpc('minionone_accept_test_result',args)[1],ack)
        self.assertEqual(self.start(job),started);self.assertEqual(self.model.calls,4)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT worker_started FROM jobs').fetchone()[0],1)
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_three_unknown_tools_stop_without_any_file_write(self):
        self.model.mode='deny';job=self.submit();self.start(job);value=self.wait(job)
        self.assertEqual(value['state'],'FAILED',value)
        self.assertEqual(value['test_job']['denials'],3)
        self.assertEqual(self.model.calls,3)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM pilot_files WHERE zone!='input'").fetchone()[0],0)

    def test_not_loaded_model_never_sends_generation_request(self):
        self.model.loaded=False;job=self.submit();self.start(job);value=self.wait(job)
        self.assertEqual(value['state'],'FAILED',value)
        self.assertEqual(value['test_job']['error']['code'],'PILOT_MODEL_NOT_LOADED')
        self.assertEqual(self.model.calls,0)

    def test_absolute_timeout_quarantines_and_blocks_another_generation(self):
        self.model.delay=5;job=self.submit();self.start(job);value=self.wait(job)
        self.assertEqual(value['state'],'NEEDS_RECONCILIATION',value)
        next_job=self.submit('second-submit-0001')
        r,v=self.rpc('minionone_run_test_job',{'job_id':next_job['job_id'],'expected_version':next_job['version'],'operation_id':'second-start-0001'})
        self.assertTrue(r['isError']);self.assertEqual(v['error']['code'],'PILOT_JOB_BUSY')
        self.assertEqual(self.model.calls,1)

    def test_document_input_is_immutable_and_output_read_is_scoped(self):
        job=self.submit()
        with closing(sqlite3.connect(self.db)) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE pilot_files SET content='tamper' WHERE job_id=?",(job['job_id'],))
        r,v=self.rpc('minionone_test_result',{'job_id':job['job_id']})
        self.assertTrue(r['isError']);self.assertEqual(v['error']['code'],'PILOT_RESULT_NOT_READY')
        r,v=self.rpc('minionone_test_result',{'job_id':'JOB-unowned'})
        self.assertTrue(r['isError']);self.assertEqual(v['error']['code'],'ACCESS_DENIED')

    def test_input_paths_and_reserved_names_rejected_before_admission(self):
        for name in ('../secret','C:secret','output/a.txt','CON.txt','a:stream','a..txt'):
            with self.subTest(name=name):
                r,v=self.rpc('minionone_submit_test_job',{'task':'Synthetic task','files':{name:'synthetic'},'operation_id':'bad-submit-'+str(len(name))})
                self.assertTrue(r['isError']);self.assertEqual(v['error']['code'],'INVALID_INPUT')
        with closing(sqlite3.connect(self.db)) as con:self.assertEqual(con.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_packaged_operator_exercise_and_result_export_follow_production_path(self):
        report=exercise(PilotClient(self.server.server_port,TOKEN),timeout=20)
        self.assertTrue(report['passed'],report)
        self.assertEqual(report['worker_metrics']['tool_calls'],3)
        self.assertNotIn(TOKEN,json.dumps(report));self.assertNotIn(str(self.db),json.dumps(report))
        result=self.server.pilot.result(TOKEN,'configured-pilot',report['job_id'])
        path=Path(self.tmp.name)/'result.zip';export_result(result,path)
        import zipfile
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('output/result.txt'),b'MINIONONE SYSTEMTEST OK')
        with self.assertRaises(FileExistsError):export_result(result,path)

    def test_second_active_pilot_cannot_recover_or_fence_this_instance(self):
        with self.assertRaises(Exception) as raised:self.open_server()
        from minionone.diagnostics import safe_tool_fault
        self.assertEqual(safe_tool_fault(raised.exception).code,'PILOT_INSTANCE_BUSY')

    def _scoped_broker(self):
        job=self.submit();captured=[]
        def capture(job_id,cap):captured.append(cap)
        with patch.object(self.server.pilot,'_run',side_effect=capture):self.start(job)
        deadline=time.monotonic()+2
        while not captured and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(captured)
        with self.server.store._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()
            self.server.store._transition(con,row,'RUNNING',Evidence(attempt_no=1,active_lease=True,worker_outcome='RUNNING'),'synthetic-active-0001')
        return job,DocumentBroker(self.server.pilot,job['job_id'],captured[0])

    def test_broker_capabilities_deny_paths_overwrite_output_and_stale_writes(self):
        job,broker=self._scoped_broker()
        for name,args in [('read_file',{'path':'../secret'}),('read_file',{'path':'output/result.txt'}),
                          ('list_files',{'zone':'output'}),('create_work',{'name':'a:stream','content':'x'}),
                          ('shell',{'command':'synthetic'})]:
            with self.subTest(name=name,args=args),self.assertRaises(BrokerScopeDenied):
                broker.execute(name,args,timeout_seconds=1)
        broker.execute('create_work',{'name':'Result.txt','content':'safe synthetic text'},timeout_seconds=1)
        with self.assertRaises(BrokerScopeDenied):broker.execute('create_work',{'name':'result.TXT','content':'replace'},timeout_seconds=1)
        broker.execute('publish_artifact',{'name':'result.txt'},timeout_seconds=1)
        with self.assertRaises(BrokerScopeDenied):broker.execute('publish_artifact',{'name':'result.txt'},timeout_seconds=1)
        status=self.status(job)
        self.server.pilot.cancel(TOKEN,'configured-pilot',job['job_id'],status['version'],'cap-revoke-0001')
        with self.assertRaises(BrokerScopeDenied):broker.execute('create_work',{'name':'late.txt','content':'late'},timeout_seconds=1)

    def test_restart_quarantines_unfinished_generation_and_operator_resolves(self):
        job,_=self._scoped_broker()
        with self.server.store._connection() as con:
            con.execute('UPDATE pilot_runs SET provider_inflight=1 WHERE job_id=?',(job['job_id'],))
        self.server.shutdown();self.server.server_close()
        restarted=self.open_server()
        try:
            status=restarted.call_tool('minionone_job_status',{'job_id':job['job_id']})
            self.assertEqual(status['state'],'NEEDS_RECONCILIATION')
            self.assertTrue(status['test_job']['provider_state_uncertain'])
            result=restarted.pilot.resolve_for_operator(job['job_id'])
            self.assertEqual(result['state'],'FAILED')
            self.assertFalse(restarted.call_tool('minionone_job_status',{'job_id':job['job_id']})['test_job']['provider_state_uncertain'])
        finally:restarted.server_close()

if __name__=='__main__':unittest.main()
