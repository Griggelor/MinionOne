"""Intentionally RED target-contract tests; require actual manager seam, not a mock.

Run separately from green pilot suite. These tests may only turn green when
actual production behavior exists. This local suite still does NOT replace
Windows NTFS, LM Studio/Flint or Unsloth E2E acceptance.
"""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from minionone.mcp_server import LocalMCP

TOKEN='target-contract-'+('Y'*48)

class TargetProductContract(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base=Path(tmp.name)
        self.project=base/'project'
        self.project.mkdir()
        self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,
            database_path=base/'manager.sqlite',workspace_projects={'demo':self.project},
            lmstudio_url='http://127.0.0.1:2')
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.job=self.call('minionone_prepare_workspace',{'project_id':'demo',
                                  'operation_id':'contract-prep-0001'})
        self.job_id=self.job['job_id']
        self.task=Path(self.job['input_dir'])/'TASK.md'
        self.task.write_text('Read sample.txt and produce a summary.',encoding='utf-8')
        content=b'example file\n'
        (Path(self.job['input_dir'])/'sample.txt').write_bytes(content)
        manifest={'files':{'TASK.md':hashlib.sha256(self.task.read_bytes()).hexdigest(),
                           'sample.txt':hashlib.sha256(content).hexdigest()}}
        (Path(self.job['input_dir'])/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')

    def call(self,name,args):
        msg={'jsonrpc':'2.0','id':1,'method':'tools/call',
             'params':{'name':name,'arguments':args}}
        body=json.dumps(msg).encode('utf-8')
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request('POST','/mcp',body,headers={
               'Host':'127.0.0.1:'+str(self.server.server_port),
               'Authorization':'Bearer '+TOKEN,
               'Content-Type':'application/json',
               'Accept':'application/json, text/event-stream'})
            response=conn.getresponse()
            self.assertEqual(response.status,200)
            reply=json.loads(response.read())
            self.assertIn('result',reply,'MCP tool unavailable')
            result=reply['result']
            self.assertFalse(result.get('isError',False),'MCP tool refused action')
            return json.loads(result['content'][0]['text'])
        finally:conn.close()

    def seal(self):
        self.assertIn('minionone_seal_workspace',self.server.tool_map,
                      'FAIL_TARGET: two-phase input-seal MCP tool not implemented')
        receipt=self.call('minionone_seal_workspace',{'job_id':self.job_id,
                  'expected_version':0,'operation_id':'seal-contract-0001'})
        self.assertEqual(receipt['job_id'],self.job_id)
        self.assertEqual(receipt['state'],'ACCEPTED')
        self.assertRegex(receipt['input_digest'],r'^[0-9a-f]{64}$')
        self.assertTrue(receipt['input_sealed'])
        return receipt

    def test_t082_real_seal_accepts_task_and_manifest_under_same_id(self):
        self.seal()

    def test_t163_mutated_input_after_seal_cannot_start(self):
        sealed=self.seal()
        self.assertIn('minionone_start_workspace_job',self.server.tool_map,
                      'FAIL_TARGET: start tool not implemented')
        # Negative-only testing could go green with an adapter that rejects ALL
        # starts. Require a second identical, unmodified job to start first.
        control=self.call('minionone_prepare_workspace',{
            'project_id':'demo','operation_id':'contract-control-0002'})
        input_dir=Path(control['input_dir'])
        task=b'Read sample.txt and produce a summary.'
        sample=b'example file\n'
        (input_dir/'TASK.md').write_bytes(task)
        (input_dir/'sample.txt').write_bytes(sample)
        (input_dir/'manifest.json').write_text(json.dumps({'files':{
            'TASK.md':hashlib.sha256(task).hexdigest(),
            'sample.txt':hashlib.sha256(sample).hexdigest()}}),encoding='utf-8')
        good=self.call('minionone_seal_workspace',{
            'job_id':control['job_id'],'expected_version':0,
            'operation_id':'contract-control-seal-0002'})
        allowed=self.call('minionone_start_workspace_job',{
            'job_id':control['job_id'],'expected_version':good['version'],
            'operation_id':'contract-control-start-0002','model_id':'configured-model'})
        self.assertIn(allowed['state'],('RESOURCE_CHECK','RUNNING','QUEUED'))
        self.task.write_text('CHANGED AFTER SEAL',encoding='utf-8')
        # A modified sealed input MUST fail even with a modified manifest.
        msg={'job_id':self.job_id,'expected_version':sealed['version'],
             'operation_id':'start-contract-0001','model_id':'configured-model'}
        request={'jsonrpc':'2.0','id':1,'method':'tools/call',
                 'params':{'name':'minionone_start_workspace_job','arguments':msg}}
        body=json.dumps(request).encode()
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request('POST','/mcp',body,headers={
               'Host':'127.0.0.1:'+str(self.server.server_port),
               'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json',
               'Accept':'application/json, text/event-stream'})
            response=conn.getresponse()
            result=json.loads(response.read())['result']
            self.assertTrue(result.get('isError'), 'tampered sealed input was accepted')
        finally:conn.close()

    def test_t102_journal_projected_into_output_log_after_admission(self):
        self.seal()
        path=Path(self.job['output_dir'])/'job.log.jsonl'
        self.assertTrue(path.is_file(),'FAIL_TARGET: manager log projection missing')
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(rows,'no projected journal events')
        self.assertEqual([r['seq'] for r in rows],list(range(1,len(rows)+1)))
        self.assertTrue(any(r['status']=='ACCEPTED' for r in rows))

if __name__=='__main__':unittest.main()
