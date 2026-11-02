"""Real bounded control HTTP, native contracts and physical files; no live target claim."""
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from minionone.admission import InputRejected
from minionone.mcp_server import LocalMCP
from minionone.model_lifecycle import ModelLifecycle,MemoryReading
from minionone.pilot_cli import PilotClient
from minionone.pilot_jobs import PilotError
from minionone.unsloth_parent import NativeParentConfig,UnslothParent
from minionone.worker_profiles import WorkerProfile

spec=importlib.util.spec_from_file_location('native_fixture',Path(__file__).resolve().parents[1]/'integration_tests/native_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
TOKEN='native-fixture-authorization-'+('x'*48)

class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.project=self.root/'project';self.project.mkdir()
        self.host=fixture.NativeFixture().start();self.host.capacity=4000
        self.addCleanup(self.host.server_close);self.addCleanup(self.host.shutdown)
        self.env=patch.dict(os.environ,{'MINIONONE_NATIVE_TEST_KEY':'synthetic-key'});self.env.start();self.addCleanup(self.env.stop)
        self.parent=NativeParentConfig(fixture.THREAD,fixture.PARENT,self.host.url,'MINIONONE_NATIVE_TEST_KEY',
            memory_ram_mb=2000,inference_ram_mb=0,inference_vram_mb=0,allow_parent_unload=True)
        self.profiles={p:WorkerProfile(p,p,fixture.WORKER,self.host.url,auto_load=True,
            memory_ram_mb=1500,inference_ram_mb=0,inference_vram_mb=0,load_timeout_seconds=15,secret_env='MINIONONE_NATIVE_TEST_KEY') for p in ('unsloth','lmstudio')}
        self.manager=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.root/'jobs.sqlite',
            parent_config=self.parent,worker_profiles=self.profiles,workspace_projects={'demo':self.project},
            enable_workspace_jobs=True,min_free_mb=1)
        self.manager.model_lifecycle.measure=self.host.memory;self.manager.native_parent.measure=self.host.memory
        threading.Thread(target=self.manager.serve_forever,daemon=True).start()
        self.addCleanup(self.manager.server_close);self.addCleanup(self.manager.shutdown)
        self.client=PilotClient(self.manager.server_port,TOKEN)
    def submit(self,provider='unsloth'):
        return self.client.tool('minionone_delegate',{'project_id':'demo','task':'Perform this explicit Flash task.',
            'profile_id':provider,'operation_id':'native-operation-'+provider})
    def await_state(self,job,states=('RESULT_READY','FAILED','NEEDS_RECONCILIATION')):
        end=time.monotonic()+60
        while time.monotonic()<end:
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            if status['state'] in states:return status
            time.sleep(.05)
        self.fail('job did not finish '+str(status))
    def await_return(self,job,target='DELIVERED'):
        end=time.monotonic()+45
        while time.monotonic()<end:
            state=self.manager.parent_return.status(job['job_id'])
            if state['state']==target:return state
            time.sleep(.05)
        self.fail('return did not settle '+str(state))
    def test_invalid_native_credential_never_loads_or_runs_worker(self):
        with patch.dict(os.environ,{'MINIONONE_NATIVE_TEST_KEY':'wrong-key'}):
            with self.assertRaises(PilotError):self.submit()
        self.assertFalse(self.host.events)

    def test_legacy_profile_snapshot_remains_bound_without_enabling_new_auto_load_defaults(self):
        from dataclasses import asdict
        from minionone.admission import _digest
        from minionone.worker_selection import load
        config=asdict(replace(self.profiles['unsloth'],auto_load=False))
        for field in ('auto_load','memory_ram_mb','memory_vram_mb','inference_ram_mb','inference_vram_mb','load_timeout_seconds','load_options'):config.pop(field)
        admitted=self.manager.store.admit(TOKEN,fixture.THREAD,'a'*64,'legacy-profile-operation-001')
        digest=_digest(['a'*64,config,False])
        with self.manager.store._connection() as con:
            con.execute('INSERT INTO pilot_runs(job_id,task,model_id,worker_profile_sha256) VALUES(?,?,?,?)',
                (admitted['job_id'],'legacy task',fixture.WORKER,digest))
            con.execute('INSERT INTO pilot_worker_profiles VALUES(?,?,?)',(admitted['job_id'],json.dumps(config),0))
            profile=load(con,admitted['job_id'])
        self.assertFalse(profile.auto_load)
        self.assertFalse(self.host.events)

    def test_parent_can_ack_while_native_admission_response_is_still_in_flight(self):
        self.host.capacity=6000;acks=[]
        def ack_from_parent():
            try:
                with self.manager.store._connection() as con:
                    row=con.execute("SELECT job_id FROM jobs WHERE state='RESULT_READY'").fetchone()
                result=self.client.tool('minionone_workspace_result',{'job_id':row[0]})
                acks.append(self.client.tool('minionone_accept_workspace_result',dict(job_id=row[0],
                    expected_version=result['version'],result_digest=result['result_digest'],operation_id='early-native-ack-001')))
            except Exception as exc:acks.append(exc)
        self.host.early_ack=ack_from_parent
        job=self.submit('lmstudio');self.host.end_turn()
        state=self.await_state(job,('COMPLETED','FAILED','NEEDS_RECONCILIATION'))
        self.assertEqual(state['state'],'COMPLETED',acks)
        self.assertEqual(self.host.events.count('parent-run'),1)

    def test_native_binding_requires_the_actual_active_flash_run(self):
        self.host.end_turn()
        with self.assertRaises(PilotError):self.submit()
        self.assertFalse(self.host.events)

    def test_cancelled_wait_for_turn_never_unloads_flash_or_starts_inference(self):
        job=self.submit()
        status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
        self.client.tool('minionone_cancel_workspace_job',dict(job_id=job['job_id'],expected_version=status['version'],
            operation_id='native-cancel-wait-001'))
        self.host.end_turn()
        state=self.await_state(job,('CANCELLED','FAILED','NEEDS_RECONCILIATION'))
        self.assertEqual(state['state'],'CANCELLED',state)
        self.assertNotIn('unload:flash-original',self.host.events)
        self.assertFalse(self.host.requests)

    def test_swap_waits_for_turn_loads_worker_releases_it_restores_and_resumes_original_chat(self):
        job=self.submit();time.sleep(.3)
        self.assertEqual(self.host.events,[],'never unload a generating Flash')
        self.host.end_turn()
        status=self.await_state(job);self.assertEqual(status['state'],'RESULT_READY',status)
        delivery=self.await_return(job);self.assertTrue(delivery['restore_needed'])
        self.assertEqual(self.host.events[:2],['unload:flash-original','load:worker-key'])
        self.assertLess(self.host.events.index('unload:worker-key'),self.host.events.index('load:flash-original'))
        self.assertLess(self.host.events.index('load:flash-original'),self.host.events.index('parent-run'))
        self.assertEqual(self.host.events.count('parent-run'),1)
        restored=next(p for p in self.host.load_payloads if p['model_path']==fixture.PARENT)
        self.assertEqual(restored['max_seq_length'],4096)
        self.assertEqual(len(self.host.requests),3)
        self.assertNotIn('PRIVATE ORIGINAL FLASH HISTORY',json.dumps(self.host.requests))
        with self.manager.store._connection() as con:
            envelope=con.execute('SELECT envelope_json FROM native_parent_returns WHERE job_id=?',(job['job_id'],)).fetchone()[0]
        self.assertNotIn('PRIVATE ORIGINAL FLASH HISTORY',envelope)
        with self.manager.store._connection() as con:
            system=con.execute('SELECT system_json FROM native_parent_jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0]
        self.assertNotIn('ORIGINAL FLASH SYSTEM POLICY',system)
        run=next(r for k,r in self.host.runs.items() if k!='original-run')
        self.assertEqual(run['threadId'],fixture.THREAD)
        self.assertIn('PRIVATE ORIGINAL FLASH HISTORY',json.dumps(run['requestPayload']['messages']))
        self.assertEqual(run['requestPayload']['messages'][0]['content'],'ORIGINAL FLASH SYSTEM POLICY')
        result=self.client.tool('minionone_workspace_result',{'job_id':job['job_id']})
        ack=self.client.tool('minionone_accept_workspace_result',dict(job_id=job['job_id'],
            expected_version=result['version'],result_digest=result['result_digest'],operation_id='native-ack-operation-001'))
        self.assertEqual(ack['state'],'COMPLETED')
    def test_enough_memory_keeps_flash_resident_and_lm_studio_uses_instance_identity(self):
        self.host.capacity=6000;job=self.submit('lmstudio');self.host.end_turn()
        self.assertEqual(self.await_state(job)['state'],'RESULT_READY')
        self.await_return(job)
        self.assertNotIn('unload:flash-original',self.host.events)
        self.assertIn('lm-load',self.host.events);self.assertIn('lm-unload',self.host.events)
        self.assertTrue(all(r['model']=='worker-instance' for r in self.host.requests))
    def test_parent_unload_permission_is_operator_only(self):
        self.manager.native_parent.allow_unload=False
        job=self.submit();self.host.end_turn()
        self.assertEqual(self.await_state(job)['state'],'FAILED')
        self.assertNotIn('unload:flash-original',self.host.events)
        self.assertFalse(self.host.requests)
    def test_restore_failure_quarantines_model_lease_and_prevents_new_worker(self):
        self.host.reject_restore=True;job=self.submit();self.host.end_turn()
        status=self.await_state(job);self.assertEqual(status['state'],'NEEDS_RECONCILIATION',status)
        self.assertEqual(status['model_lifecycle']['state'],'NEEDS_RECONCILIATION')
        self.assertNotIn('parent-run',self.host.events)
        with self.assertRaises(PilotError):self.submit('lmstudio')
    def test_operator_reconciles_restored_models_and_delivers_existing_result_without_new_inference(self):
        from minionone.admission import _digest
        self.host.reject_restore=True;job=self.submit();self.host.end_turn()
        self.assertEqual(self.await_state(job)['state'],'NEEDS_RECONCILIATION')
        before=len(self.host.requests)
        with self.assertRaises(InputRejected):self.manager.pilot.reconcile_models_for_operator(TOKEN,fixture.THREAD,job['job_id'])
        self.host.reject_restore=False
        self.manager.native_parent.restore('local-operator',fixture.THREAD,fixture.PARENT,'parent-restore-'+_digest(job['job_id']))
        repaired=self.manager.pilot.reconcile_models_for_operator(TOKEN,fixture.THREAD,job['job_id'],
            confirm_worker_stopped=True,confirm_work_inspected=True)
        self.assertEqual(repaired['state'],'RESULT_READY')
        self.await_return(job)
        self.assertEqual(len(self.host.requests),before)
        result=self.client.tool('minionone_workspace_result',{'job_id':job['job_id']})
        self.assertEqual(result['files'][0]['text'],'NATIVE LIFECYCLE RESULT')

    def test_lost_native_run_response_is_read_reconciled_without_second_generation(self):
        self.host.capacity=6000;self.host.lose_return_response=True
        job=self.submit('lmstudio');self.host.end_turn()
        self.assertEqual(self.await_state(job)['state'],'RESULT_READY')
        self.await_return(job)
        self.assertEqual(self.host.events.count('parent-run'),1)
        for _ in range(3):self.manager.parent_return.tick()
        self.assertEqual(self.host.events.count('parent-run'),1)
    def test_branch_ambiguity_never_creates_another_chat(self):
        job=self.submit();self.host.end_turn()
        self.host.messages.extend([{'id':name,'threadId':fixture.THREAD,'parentId':'assistant-original',
            'role':'user','content':[{'type':'text','text':'another branch'}],'createdAt':3} for name in ('branch-a','branch-b')])
        self.assertEqual(self.await_state(job)['state'],'RESULT_READY')
        self.await_return(job,'WAITING_PARENT')
        self.assertNotIn('parent-run',self.host.events)
    def test_cleanup_cannot_unload_another_model_even_with_a_wrong_lease_instance(self):
        job=self.submit();self.host.end_turn();self.await_state(job);self.await_return(job)
        lease=self.manager.model_lifecycle
        lease._set(job['job_id'],state='ACTIVE',owned=fixture.PARENT)
        before=list(self.host.events)
        with self.assertRaises(Exception):lease.finish(job['job_id'],'FAILED')
        self.assertEqual(before,self.host.events)
        self.assertEqual(lease.status(job['job_id'])['state'],'NEEDS_RECONCILIATION')

    def test_restart_quarantines_external_model_effect_and_never_reloads_it(self):
        job=self.submit();self.host.end_turn();self.await_state(job);self.await_return(job)
        self.manager.model_lifecycle._set(job['job_id'],state='PREPARING',phase='WORKER_LOAD_IN_FLIGHT')
        before=list(self.host.events)
        recovered=ModelLifecycle(self.manager.store,parent=self.manager.native_parent,measure=self.host.memory)
        self.assertEqual(recovered.status(job['job_id'])['state'],'NEEDS_RECONCILIATION')
        self.assertEqual(before,self.host.events)
    def test_unsafe_or_unbounded_auto_load_configuration_is_rejected(self):
        for args in ({'auto_load':True},{'auto_load':True,'memory_ram_mb':2,'load_options':{'force_alongside':True}},
                     {'memory_ram_mb':True},{'load_options':{'device':'shell'}}):
            with self.assertRaises(InputRejected):WorkerProfile('worker','unsloth','model',self.host.url,**args)
    def _restart_notification(self,*,message_stored):
        from minionone.admission import _digest
        from minionone.parent_return import ParentReturnController
        self.manager.parent_return.close();self.host.capacity=6000
        job=self.submit('lmstudio');self.host.end_turn();self.await_state(job)
        with self.manager.store._connection() as con:
            digest=con.execute('SELECT result_digest FROM parent_returns WHERE job_id=?',(job['job_id'],)).fetchone()[0]
        operation='return-'+_digest([job['job_id'],digest])
        row,wire=self.manager.native_parent._prepare(job['job_id'],digest,operation)
        if message_stored:
            message=json.loads(row['message_json'])
            self.manager.native_parent.http.request('PUT',self.manager.native_parent.thread_path+'/messages/'+message['id'],message)
            self.manager.native_parent._phase(job['job_id'],'MESSAGE_IN_FLIGHT',operation)
        with self.manager.store._connection() as con:
            con.execute("UPDATE parent_returns SET state='IN_FLIGHT' WHERE job_id=?",(job['job_id'],))
        fresh=UnslothParent(self.manager.store,self.parent,measure=self.host.memory)
        recovered=ParentReturnController(self.manager.store,fresh)
        self.addCleanup(recovered.close)
        recovered.tick()
        self.assertEqual(recovered.status(job['job_id'])['state'],'DELIVERED')
        self.assertEqual(self.host.events.count('notification'),1)
        self.assertEqual(self.host.events.count('parent-run'),1)
        self.assertEqual(len(self.host.requests),3)

    def test_restart_of_prepared_unsent_notification_rebuilds_parent_data_without_duplication(self):
        self._restart_notification(message_stored=False)

    def test_restart_after_lost_message_put_only_starts_the_first_native_run(self):
        self._restart_notification(message_stored=True)

    def test_known_return_memory_precondition_waits_without_sending_and_recovers_once(self):
        returns=self.manager.parent_return
        returns.close()
        self.host.capacity=6000
        job=self.submit('lmstudio');self.host.end_turn()
        self.assertEqual(self.await_state(job)['state'],'RESULT_READY')
        self.manager.native_parent.measure=lambda *args:MemoryReading(500,None,time.monotonic())
        returns._stop.clear();returns.tick()
        self.assertEqual(returns.status(job['job_id'])['state'],'WAITING_PARENT')
        self.assertNotIn('parent-run',self.host.events)
        self.manager.native_parent.measure=self.host.memory
        returns.tick()
        self.assertEqual(returns.status(job['job_id'])['state'],'DELIVERED')
        self.assertEqual(self.host.events.count('parent-run'),1)
        self.assertEqual(len(self.host.requests),3)

    def test_loaded_models_still_reserve_incremental_KV_memory(self):
        lease=self.manager.model_lifecycle
        profile=replace(self.profiles['unsloth'],memory_vram_mb=1024,inference_vram_mb=512)
        lease.measure=lambda *args:MemoryReading(10000,768,time.monotonic())
        self.assertFalse(lease.fits(profile,already_loaded=True))
        lease.measure=lambda *args:MemoryReading(10000,1100,time.monotonic())
        self.assertTrue(lease.fits(profile,already_loaded=True))
        with self.assertRaises(InputRejected):WorkerProfile('missing-cache','unsloth','model',self.host.url,
            auto_load=True,memory_ram_mb=1500)

    def test_stale_or_missing_gpu_telemetry_never_authorizes_a_load(self):
        lease=self.manager.model_lifecycle
        lease.measure=lambda *args:MemoryReading(100000,None,time.monotonic()-6)
        with self.assertRaises(Exception):lease.fits(self.profiles['unsloth'])
        lease.measure=lambda *args:MemoryReading(100000,None,time.monotonic())
        self.assertFalse(lease.fits(replace(self.profiles['unsloth'],memory_vram_mb=1)))

if __name__=='__main__':unittest.main()
