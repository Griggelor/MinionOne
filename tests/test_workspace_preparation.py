"""v0.19 two-phase PREPARING workspace: real MCP socket tests and negative cases.

These tests do NOT establish a secure Windows handle-relative file broker.
"""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor

from minionone.admission import (AdmissionStore, IdempotencyConflict,
                                  InputRejected, PermissionDenied)
from minionone.mcp_server import LocalMCP
from minionone.workspace_preparation import WorkspacePreparation

TOKEN='integration-workspace-'+('Q'*48)

class WorkspaceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=self.root/'real-project'
        self.project.mkdir()
        self.second=self.root/'other-project'
        self.second.mkdir()
        self.database=self.root/'manager.sqlite'
        self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.database,
                   workspace_projects={'demo':self.project,'other':self.second},lmstudio_url='http://127.0.0.1:2')
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self,name,args,token=TOKEN):
        message={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}}
        body=json.dumps(message).encode()
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        try:
            conn.request('POST','/mcp',body,headers={
                'Authorization':'Bearer '+token,
                'Host':'127.0.0.1:'+str(self.server.server_port),
                'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
            resp=conn.getresponse()
            raw=resp.read()
            return resp.status,json.loads(raw)
        finally:
            conn.close()

    def call(self,name,args):
        status,msg=self.request(name,args)
        self.assertEqual(status,200)
        result=msg['result']
        return result.get('isError',False),json.loads(result['content'][0]['text'])

    def prepared(self,op='prepare-0000001'):
        fail,data=self.call('minionone_prepare_workspace',{'project_id':'demo','operation_id':op})
        self.assertFalse(fail,data)
        return data

    def test_t170_prepare_returns_durable_id_and_exact_three_directories(self):
        receipt=self.prepared()
        self.assertRegex(receipt['job_id'],r'^JOB-[0-9a-f]{32}$')
        self.assertEqual((receipt['state'],receipt['version']),('PREPARING',0))
        self.assertEqual(receipt['next_action'],'PARENT_PREPARES_INPUT_THEN_SEAL_REQUIRED')
        directory=Path(receipt['job_dir'])
        self.assertEqual({x.name for x in directory.iterdir()},{'input','work','output'})
        self.assertFalse(receipt['worker_execution_enabled'])
        self.assertFalse(receipt['input_sealed'])
        self.assertFalse(receipt['file_broker_verified'])

    def test_t171_retry_and_restart_preserve_job_id(self):
        first=self.prepared()
        again=self.prepared()
        self.assertEqual(first['job_id'],again['job_id'])
        # Reopen the durable state through an independent new manager instance.
        other_store=AdmissionStore(self.database,credentials={TOKEN:('local-operator',{'configured-pilot'})})
        resumed=WorkspacePreparation(other_store,{'demo':self.project,'other':self.second})
        status=resumed.status(TOKEN,'configured-pilot',first['job_id'])
        self.assertEqual(status['job_id'],first['job_id'])
        self.assertTrue(Path(first['input_dir']).is_dir())

    def test_t172_mutated_retry_denied_and_unchanged(self):
        first=self.prepared()
        fail,_=self.call('minionone_prepare_workspace',{'project_id':'other','operation_id':'prepare-0000001'})
        self.assertTrue(fail)
        self.assertFalse((self.second/'.minionone').exists())
        fail,_=self.call('minionone_prepare_workspace',{'project_id':'demo','operation_id':'bad'})
        self.assertTrue(fail)
        self.assertEqual(self.prepared()['job_id'],first['job_id'])

    def test_t173_unapproved_host_path_cannot_be_passed(self):
        fail,_=self.call('minionone_prepare_workspace',{'project_id':'C:/Windows','operation_id':'prepare-0000002'})
        self.assertTrue(fail)
        fail,_=self.call('minionone_prepare_workspace',{'project_id':'demo','operation_id':'prepare-0000002','path':'C:/Windows'})
        self.assertTrue(fail)
        self.assertFalse((self.project/'.minionone').exists())

    def test_t174_preparing_cannot_be_run_or_claimed_admitted(self):
        receipt=self.prepared()
        self.assertTrue(self.call('minionone_job_status',{'job_id':receipt['job_id']})[0])
        self.assertNotIn('minionone_run_test_job',self.server.tool_map)
        fail,gate=self.call('minionone_status',{})
        self.assertFalse(fail)
        self.assertTrue(gate['workspace_preparation_enabled'])
        self.assertFalse(gate['host_filesystem_tools_enabled'])
        self.assertFalse(gate['workspace_execution_enabled'])

    def test_t175_only_token_holder_can_reserve(self):
        status,_=self.request('minionone_prepare_workspace',{'project_id':'demo',
                        'operation_id':'prepare-0000003'},token='not-authorized')
        self.assertEqual(status,401)
        self.assertFalse((self.project/'.minionone').exists())

    def test_t176_missing_provider_returns_id_with_unavailable_catalog(self):
        receipt=self.prepared()
        self.assertEqual(receipt['model_discovery'],'UNAVAILABLE')
        self.assertEqual(receipt['models'],[])
        self.assertEqual(receipt['state'],'PREPARING')

    def test_t177_link_substitution_is_rejected_at_staging_prefilter(self):
        if not hasattr(os,'symlink'):
            self.skipTest('symlink unavailable')
        self.prepared()
        target=self.root/'other';target.mkdir()
        path=self.project/'.minionone'/'jobs'
        # A prepared job with a replaced jobs-dir must not be reported healthy.
        # This is a prefilter test, NOT a race-safe Win32 security proof.
        try:
            path.rename(self.project/'.minionone'/'jobs-old')
            os.symlink(target,path,target_is_directory=True)
        except OSError:
            self.skipTest('symlink permission denied')
        fail,_=self.call('minionone_prepare_workspace',{'project_id':'demo','operation_id':'prepare-0000004'})
        self.assertTrue(fail)
        self.assertEqual(list(target.iterdir()),[])
        fail,_=self.call('minionone_workspace_status',{'job_id':self.server.workspace.reserve(TOKEN,'configured-pilot','other','other-prepare-1')['job_id']})
        self.assertFalse(fail) # other project remains safe and usable

    def test_t178_unavailable_job_status_fails_closed(self):
        self.assertTrue(self.call('minionone_workspace_status',{'job_id':'JOB-'+'a'*32})[0])

    def test_t179_concurrent_same_id_returns_single_reservation(self):
        def call(_):return self.prepared('parallel-00000001')['job_id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(call,range(8)))
        self.assertEqual(len(set(ids)),1)
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM workspace_reservations').fetchone()[0],1)

class WorkspaceDisabledTests(unittest.TestCase):
    def test_t180_original_production_mcp_does_not_offer_workspace_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            server=LocalMCP(('127.0.0.1',0),token=TOKEN,
                            database_path=Path(directory)/'db.sqlite')
            try:
                self.assertNotIn('minionone_prepare_workspace',server.tool_map)
                self.assertIsNone(server.workspace)
                self.assertFalse(server.call_tool('minionone_status',{})['workspace_preparation_enabled'])
            finally:server.server_close()

class WorkspaceAuthorizationTests(unittest.TestCase):
    def test_t181_other_principal_cannot_read_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            project=directory/'project';project.mkdir()
            cred_a='a'*40;cred_b='b'*40
            store=AdmissionStore(directory/'db.sqlite',credentials={cred_a:('owner',{'threadA'}),
                                                 cred_b:('other',{'threadB'})})
            work=WorkspacePreparation(store,{'demo':project})
            job=work.reserve(cred_a,'threadA','demo','owner-prepare-1')['job_id']
            with self.assertRaises(PermissionDenied):work.status(cred_b,'threadB',job)
            with self.assertRaises(PermissionDenied):work.reserve(cred_b,'threadA','demo','other-prepare-1')

class WorkspaceQuotaTests(unittest.TestCase):
    def test_t182_reservation_quota_rejects_new_jobs_but_preserves_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            project=directory/'project';project.mkdir()
            store=AdmissionStore(directory/'jobs.db',credentials={TOKEN:('owner',{'thread'})})
            manager=WorkspacePreparation(store,{'demo':project},max_prepared_per_principal=2)
            first=manager.reserve(TOKEN,'thread','demo','reserve-quota-001')
            manager.reserve(TOKEN,'thread','demo','reserve-quota-002')
            with self.assertRaises(InputRejected):
                manager.reserve(TOKEN,'thread','demo','reserve-quota-003')
            self.assertEqual(first['job_id'],manager.reserve(TOKEN,'thread','demo','reserve-quota-001')['job_id'])
            with closing(sqlite3.connect(directory/'jobs.db')) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM workspace_reservations').fetchone()[0],2)

if __name__=='__main__':unittest.main()
