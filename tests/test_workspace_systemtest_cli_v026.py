"""Real CLI + real MCP workspace systemtest; deterministic provider responses.

Runs on Windows and POSIX. This does NOT attest real Flint/Unsloth or
target hardware, and uses only synthetic, non-sensitive test text.
"""
from contextlib import redirect_stderr,redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from minionone.agent_loop import ModelStep
from minionone.mcp_server import LocalMCP
from minionone.pilot_cli import PilotClient,main,workspace_exercise

TOKEN='workspace-standalone-test-'+('Q'*48)


class DeterministicFlintFixture:
    def __init__(self,*args,**kwargs):
        self._provider=SimpleNamespace(_opener=None)
        self.sink=kwargs.get("io_sink")
        self.turn=0
        self.steps=iter((
            ModelStep('tool',name='read_file',arguments={'path':'input/sample.txt'},total_tokens=2),
            ModelStep('tool',name='create_work',
                      arguments={'name':'result.txt','content':'MINIONONE WORKSPACE OK'},total_tokens=2),
            ModelStep('tool',name='publish_artifact',
                      arguments={'name':'result.txt'},total_tokens=2),
            ModelStep('finish',text='finished',total_tokens=2),
        ))
    def next_step(self,*,timeout_seconds):
        step=next(self.steps)
        self.turn+=1
        if self.sink is not None:
            self.sink("MODEL_REQUEST",json.dumps({"synthetic":True,"turn":self.turn}))
            self.sink("MODEL_RESPONSE",json.dumps({"synthetic":True,"action":step.action,
                 "tool":step.name if step.action=="tool" else "none"}))
        return step
    def observe_tool(self,name,reply):
        if self.sink is not None:
            self.sink("TOOL_RESPONSE",json.dumps({"tool":name,"data":reply.data}))
        if reply.status!='ok':
            raise RuntimeError('unexpected denied synthetic broker operation')


class WorkspaceExerciseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project=self.root/'operator-selected-project'
        self.project.mkdir()
        self.server=LocalMCP(('127.0.0.1',0),token=TOKEN,
            database_path=self.root/'manager.sqlite',
            workspace_projects={'demo':self.project},
            enable_workspace_jobs=True,worker_model='model-loaded',
            min_free_mb=1,lmstudio_url='http://127.0.0.1:2')
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client=PilotClient(self.server.server_port,TOKEN)

    def test_synthetic_project_to_mcp_to_broker_to_native_publication_to_ack(self):
        with patch('minionone.pilot_jobs.PilotJobs._readiness',return_value=None), \
             patch('minionone.pilot_jobs.LMStudioAgentProvider',DeterministicFlintFixture):
            report=workspace_exercise(self.client,project_id='demo',
                                      project_root=self.project,model='model-loaded',timeout=12)
        self.assertTrue(report['passed'],report)
        self.assertEqual([r['step'] for r in report['steps']],[
            'opt_in_server_gate','real_manager_input_seal',
            'bounded_worker_result_ready','real_native_work_file_and_digest_verified',
            'published_file_and_digest_verified','exact_parent_ack'])
        self.assertEqual(report['release_gate'],'NO_GO')
        self.assertEqual(report['worker_file_backend'],
                         'native_win32_work_sqlite_output' if os.name=='nt'
                         else 'native_posix_work_sqlite_output')
        self.assertFalse(report['target_parent_attested'])
        from minionone.workspace_preparation import _JOB
        self.assertRegex(report['job_id'],_JOB)
        folder=self.project/'.minionone'/'jobs'/report['job_id']
        self.assertEqual((folder/'output'/'result.txt').read_text(encoding='utf-8'),
                         'MINIONONE WORKSPACE OK')
        self.assertEqual((folder/'work'/'result.txt').read_text(encoding='utf-8'),
                         'MINIONONE WORKSPACE OK')
        status=self.client.tool('minionone_job_status',{'job_id':report['job_id']})
        self.assertEqual(status['state'],'COMPLETED')
        self.assertIn('workspace_job',status)

    def test_missing_native_work_file_blocks_parent_ack(self):
        # The Worker completed and the immutable output exists; after
        # RESULT_READY, simulate external removal of the physical Work source.
        # The operator's real-system CLI must fail instead of acknowledging.
        original=self.client.tool
        sabotaged={'done':False}
        def remove_after_result(name,args):
            result=original(name,args)
            if name=='minionone_workspace_result':
                path=self.project/'.minionone'/'jobs'/args['job_id']/'work'/'result.txt'
                path.unlink()
                sabotaged['done']=True
            return result
        with patch('minionone.pilot_jobs.PilotJobs._readiness',return_value=None), \
             patch('minionone.pilot_jobs.LMStudioAgentProvider',DeterministicFlintFixture), \
             patch.object(self.client,'tool',side_effect=remove_after_result):
            report=workspace_exercise(self.client,project_id='demo',
                                      project_root=self.project,model='model-loaded',timeout=12)
        self.assertTrue(sabotaged['done'])
        self.assertFalse(report['passed'],report)
        self.assertEqual(report['error']['code'],'PILOT_FILE_CHANGED')
        self.assertNotIn('exact_parent_ack',[step['step'] for step in report['steps']])
        status=original('minionone_job_status',{'job_id':report['job_id']})
        self.assertEqual(status['state'],'RESULT_READY')

    def test_mismatching_explicit_root_rejected_before_parent_file_writes(self):
        decoy=self.root/'other-project';decoy.mkdir()
        report=workspace_exercise(self.client,project_id='demo',
                                  project_root=decoy,model='model-loaded',timeout=3)
        self.assertFalse(report['passed'],report)
        self.assertFalse((decoy/'.minionone').exists())

    def test_worker_disabled_without_explicit_switch(self):
        other=self.root/'inactive-project';other.mkdir()
        server=LocalMCP(('127.0.0.1',0),token=TOKEN,
            database_path=self.root/'inactive.sqlite',
            workspace_projects={'demo':other},
            lmstudio_url='http://127.0.0.1:2')
        try:
            self.assertNotIn('minionone_start_workspace_job',server.tool_map)
        finally:server.server_close()


class ServeOptionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.state=self.root/'state'
        self.project=self.root/'project'
        self.project.mkdir()

    def _invoke(self,*args):
        stderr=StringIO();stdout=StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            result=main(['--state',str(self.state),'serve','--model','loaded-model',
                         '--port','18874',*args])
        return result,stdout.getvalue(),stderr.getvalue()

    def test_explicit_switch_passes_operator_approved_project(self):
        calls=[]
        class FakeServer:
            server_port=18874
            def __init__(self,*args,**kwargs):calls.append(kwargs)
            def serve_forever(self,*,poll_interval):pass
            def server_close(self):pass
        with patch('minionone.pilot_cli.LocalMCP',FakeServer):
            rc,out,err=self._invoke('--experimental-workspace-jobs',
                        '--workspace-project','demo='+str(self.project))
        self.assertEqual(rc,0,err)
        self.assertEqual(len(calls),1)
        self.assertTrue(calls[0]['enable_workspace_jobs'])
        self.assertEqual(calls[0]['workspace_projects'],{'demo':self.project})
        self.assertIn('EXPERIMENTAL WORKSPACE',out)

    def test_unpaired_flags_and_duplicate_project_rejected_without_listener(self):
        class UnexpectedServer:
            def __init__(self,*args,**kwargs):
                raise AssertionError('bad configuration reached LocalMCP')
        with patch('minionone.pilot_cli.LocalMCP',UnexpectedServer):
            cases=(
                ('--experimental-workspace-jobs',),
                ('--workspace-project','demo='+str(self.project)),
                ('--experimental-workspace-jobs','--workspace-project','demo='+str(self.project),
                 '--workspace-project','demo='+str(self.project)),
                ('--experimental-workspace-jobs','--workspace-project','demo=relative'),
            )
            for args in cases:
                with self.subTest(args=args):
                    rc,out,err=self._invoke(*args)
                    self.assertEqual(rc,2,err)
                    self.assertIn('ok',err)
                    self.assertNotIn('EXPERIMENTAL WORKSPACE',out)

    def test_default_server_configuration_remains_nonexecuting_workspace(self):
        calls=[]
        class FakeServer:
            server_port=18874
            def __init__(self,*args,**kwargs):calls.append(kwargs)
            def serve_forever(self,*,poll_interval):pass
            def server_close(self):pass
        with patch('minionone.pilot_cli.LocalMCP',FakeServer):
            rc,_,err=self._invoke()
        self.assertEqual(rc,0,err)
        self.assertIsNone(calls[0]['workspace_projects'])
        self.assertFalse(calls[0]['enable_workspace_jobs'])


if __name__=='__main__':
    unittest.main()
