"""Actual MCP, HTTP planner, durable worker checkpoints and physical Work."""
from dataclasses import replace
from hashlib import sha256
import json
import threading
import time
import unittest
from unittest.mock import patch

from minionone.mcp_server import LocalMCP
from minionone.pilot_cli import PilotClient
from minionone.pilot_jobs import PilotError,PilotJobs
from minionone.agent_loop import BrokerScopeDenied
from test_text_workers_v044 import WorkerMCPFixture,TOKEN
import test_native_lifecycle_v046 as native


def tool(data,name,args):
    number=sum(m['role']=='tool' for m in data['messages'])
    return {'model':data['model'],'choices':[{'finish_reason':'tool_calls','message':{
        'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(number),'type':'function',
        'function':{'name':name,'arguments':json.dumps(args)}}]}}],'usage':{'total_tokens':12}}


def question_script(data,mode='ok',*,questions=1):
    answers=[m for m in data['messages'] if m.get('role')=='user' and
             m.get('content','').startswith('Explicit parent answer')]
    calls=[c['function']['name'] for m in data['messages'] for c in m.get('tool_calls',[])]
    if not calls:return tool(data,'create_work',{'name':'draft.txt','content':'DRAFT'})
    if len(answers)<questions:return tool(data,'ask_question',{'question':'Which version should be published?'})
    if 'write_work' not in calls:return tool(data,'write_work',{
        'name':'draft.txt','content':'ANSWERED RESULT','expected_sha256':sha256(b'DRAFT').hexdigest()})
    if 'publish_artifact' not in calls:return tool(data,'publish_artifact',{'name':'draft.txt'})
    return {'model':data['model'],'choices':[{'finish_reason':'stop','message':{
        'role':'assistant','content':'Published.'}}],'usage':{'total_tokens':12}}


class QuestionResumeTests(WorkerMCPFixture,unittest.TestCase):
    def answer(self,status,operation='answer-operation-001',text='Publish the revised version.'):
        return self.client.tool('minionone_answer_question',{'job_id':status['job_id'],
            'expected_version':status['version'],'question_digest':status['workspace_job']['question_digest'],
            'answer':text,'operation_id':operation})

    def wait_changed(self,job,attempt,state):
        end=time.monotonic()+60
        while time.monotonic()<end:
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            if status['workspace_job']['attempt_epoch']>=attempt and status['state']==state:return status
            if status['state'] in ('FAILED','NEEDS_RECONCILIATION','CANCELLED'):self.fail(str(status))
            time.sleep(.05)
        self.fail(str(status))

    def test_same_job_both_handoffs_retains_work_history_and_cumulative_budgets(self):
        with patch('test_text_workers_v044.completion',question_script):
            for path in ('direct','file'):
                job=self.submit(op='question-'+path) if path=='direct' else self.staged(op='question-'+path)[0]
                paused=self.terminal(job)
                self.assertEqual(paused['state'],'WAITING_FOR_PARENT',paused)
                self.assertTrue(paused['workspace_job']['answer_available'])
                self.assertEqual(paused['workspace_job']['model_turns'],2)
                before=self.client.tool('minionone_worker_io',{'job_id':job['job_id']})
                receipt=self.answer(paused,'answer-'+path)
                self.assertEqual(self.answer(paused,'answer-'+path),receipt)
                self.assertEqual(receipt['job_id'],job['job_id'])
                final=self.wait_changed(job,2,'RESULT_READY')
                self.assertEqual(final['workspace_job']['model_turns'],5)
                self.assertEqual(final['workspace_job']['tokens_used'],60)
                folder=self.project/'.minionone'/'jobs'/job['job_id']
                self.assertEqual((folder/'work'/'draft.txt').read_text(),'ANSWERED RESULT')
                self.assertEqual((folder/'output'/'draft.txt').read_text(),'ANSWERED RESULT')
                after=self.client.tool('minionone_worker_io',{'job_id':job['job_id']})
                self.assertEqual(after['events'][:len(before['events'])],before['events'])
                with self.server.store._connection() as con:
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM pilot_parent_answers WHERE job_id=?',(job['job_id'],)).fetchone()[0],1)
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0],1)
                requests=[r for r in self.http.requests if 'Explicit parent answer' in json.dumps(r)]
                self.assertTrue(requests)

    def test_stale_changed_and_competing_answers_never_dispatch_twice(self):
        with patch('test_text_workers_v044.completion',question_script):
            job=self.submit();paused=self.terminal(job)
            bad=json.loads(json.dumps(paused));bad['workspace_job']['question_digest']='0'*64
            with self.assertRaises(PilotError):self.answer(bad)
            result=[];gate=threading.Barrier(3)
            def send(op):
                gate.wait()
                try:result.append(('ok',self.answer(paused,op)))
                except PilotError:result.append(('rejected',None))
            threads=[threading.Thread(target=send,args=('competing-answer-'+str(i),)) for i in range(2)]
            for thread in threads:thread.start()
            gate.wait()
            for thread in threads:thread.join(30)
            self.assertEqual(sum(k=='ok' for k,_ in result),1,result)
            self.wait_changed(job,2,'RESULT_READY')
            with self.assertRaises(PilotError):self.answer(paused,'competing-answer-0','Changed answer.')
            self.assertEqual(len(self.http.requests),5)

    def test_changed_paused_files_or_empty_directory_are_rejected_before_dispatch(self):
        with patch('test_text_workers_v044.completion',question_script):
            job=self.submit();paused=self.terminal(job)
            work=self.project/'.minionone'/'jobs'/job['job_id']/'work'
            (work/'unexpected').mkdir()
            with self.assertRaises(PilotError):self.answer(paused)
            self.assertEqual(len(self.http.requests),2)
            (work/'unexpected').rmdir();(work/'draft.txt').write_text('CHANGED')
            with self.assertRaises(PilotError):self.answer(paused)
            self.assertEqual(len(self.http.requests),2)
            (work/'draft.txt').write_text('DRAFT')
            self.answer(paused);self.wait_changed(job,2,'RESULT_READY')

    def test_paused_checkpoint_and_log_append_survive_manager_restart(self):
        with patch('test_text_workers_v044.completion',question_script):
            job=self.submit();paused=self.terminal(job)
            self.server.shutdown();self.server.server_close()
            self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.root/'jobs.sqlite',
                worker_profiles=self.profiles,min_free_mb=1,enable_workspace_jobs=True,
                workspace_projects={'demo':self.project},worker_model='unused-default-model')
            threading.Thread(target=self.server.serve_forever,daemon=True).start()
            self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
            self.client=PilotClient(self.server.server_port,TOKEN)
            self.answer(paused);self.wait_changed(job,2,'RESULT_READY')
            self.assertEqual(len(self.http.requests),5)

    def test_previous_attempt_capability_cannot_mutate_during_the_new_attempt(self):
        entered=threading.Event();release=threading.Event();brokers=[]
        old_make=self.server.pilot.make_broker
        def remember(*args):
            broker=old_make(*args);brokers.append(broker);return broker
        def wait_for_parent(data,mode='ok'):
            if any(m.get('role')=='user' and m.get('content','').startswith('Explicit parent answer') for m in data['messages']):
                entered.set();release.wait(8)
            return question_script(data,mode)
        with patch('test_text_workers_v044.completion',wait_for_parent),patch.object(self.server.pilot,'make_broker',side_effect=remember):
            job=self.submit();paused=self.terminal(job);old_broker=brokers[0]
            self.answer(paused)
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises(BrokerScopeDenied):
                    old_broker.execute('create_work',{'name':'stale.txt','content':'STALE'},timeout_seconds=1)
            finally:release.set()
            self.wait_changed(job,2,'RESULT_READY')
            self.assertFalse((self.project/'.minionone'/'jobs'/job['job_id']/'work'/'stale.txt').exists())

    def test_restart_after_answer_intent_never_blindly_repeats_dispatch(self):
        from types import SimpleNamespace
        class UnstartedThread:
            def __init__(self,*args,**kw):pass
            def start(self):pass
            def join(self,timeout=None):pass
        with patch('test_text_workers_v044.completion',question_script):
            job=self.submit();paused=self.terminal(job)
            with patch('minionone.question_resume.threading',SimpleNamespace(Thread=UnstartedThread)):
                receipt=self.answer(paused)
            recovered=PilotJobs(self.server.store,self.server.pilot.config)
            self.assertEqual(recovered.recover(),1)
            self.assertEqual(self.answer(paused),receipt)
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            self.assertEqual(status['state'],'FAILED',status)
            self.assertEqual(len(self.http.requests),2)

    def test_consumed_active_time_is_not_reset_by_answer_or_configuration_change(self):
        from minionone.question_resume import finished
        def almost_exhausted(manager,job,cap,run,provider,broker,elapsed_ms,**kw):
            return finished(manager,job,cap,run,provider,broker,899500,**kw)
        with patch('test_text_workers_v044.completion',question_script),patch('minionone.question_resume.finished',side_effect=almost_exhausted):
            job=self.submit();paused=self.terminal(job)
            self.assertEqual(paused['state'],'WAITING_FOR_PARENT',paused)
            self.assertFalse(paused['workspace_job']['answer_available'])
            self.server.pilot.config=replace(self.server.pilot.config,wall_seconds=1200)
            with self.assertRaises(PilotError):self.answer(paused)
            self.assertEqual(len(self.http.requests),2)
            with self.server.store._connection() as con:
                self.assertEqual(con.execute('SELECT wall_limit_ms FROM pilot_runs WHERE job_id=?',(job['job_id'],)).fetchone()[0],900000)


class NativeQuestionReturnTests(unittest.TestCase):
    setUp=native.NativeLifecycleTests.setUp
    submit=native.NativeLifecycleTests.submit
    await_state=native.NativeLifecycleTests.await_state
    await_return=native.NativeLifecycleTests.await_return

    def test_flash_can_answer_while_question_delivery_http_response_is_in_flight(self):
        self.host.worker_reply=question_script
        answers=[]
        job=self.submit();self.host.end_turn()
        def early_answer():
            self.host.early_ack=None
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            self.assertEqual(status['state'],'WAITING_FOR_PARENT',status)
            self.host.begin_parent_turn()
            answers.append(self.client.tool('minionone_answer_question',{'job_id':job['job_id'],
                'expected_version':status['version'],'question_digest':status['workspace_job']['question_digest'],
                'answer':'Publish the revised version.','operation_id':'native-early-answer'}))
            self.host.end_turn()
        self.host.early_ack=early_answer
        final=self.await_state(job)
        self.assertEqual(final['state'],'RESULT_READY',final);self.await_return(job)
        self.assertEqual(len(answers),1)
        self.assertEqual(len(self.host.requests),5)
        self.assertEqual(self.host.events.count('parent-run'),2)

    def test_two_identical_questions_three_attempts_and_three_distinct_original_chat_returns(self):
        self.host.worker_reply=lambda data:question_script(data,questions=2)
        job=self.submit();self.host.end_turn()
        for attempt in (1,2):
            paused=self.await_state(job,('WAITING_FOR_PARENT','FAILED','NEEDS_RECONCILIATION'))
            self.assertEqual(paused['state'],'WAITING_FOR_PARENT',paused)
            self.assertEqual(paused['workspace_job']['attempt_epoch'],attempt)
            self.await_return(job)
            self.host.begin_parent_turn()
            self.client.tool('minionone_answer_question',{'job_id':job['job_id'],
                'expected_version':paused['version'],'question_digest':paused['workspace_job']['question_digest'],
                'answer':'Use the revised version.','operation_id':'native-answer-'+str(attempt)})
            self.host.end_turn()
        final=self.await_state(job)
        self.assertEqual(final['state'],'RESULT_READY',final);self.await_return(job)
        self.assertEqual(final['workspace_job']['attempt_epoch'],3)
        self.assertEqual(final['workspace_job']['tokens_used'],72)
        self.assertEqual(self.host.events.count('parent-run'),3)
        self.assertEqual(len(self.host.requests),6)
        self.assertNotIn('PRIVATE ORIGINAL FLASH HISTORY',json.dumps(self.host.requests))
        with self.manager.store._connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM parent_return_history').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM native_parent_return_history').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM model_lease_history').fetchone()[0],2)
