"""Automatic return contracts with a trusted synthetic host, NOT live Unsloth."""
from dataclasses import replace
import threading
import time
import unittest

import test_text_workers_v044 as fixtures
import test_full_bridge_http_e2e_ci as file_bridge
from minionone.parent_return import ParentReturnController, ParentState, ParentReceipt
from minionone.pilot_jobs import PilotError


class SyntheticHost:
    def __init__(self):
        self.resident=True;self.busy=False;self.restore_failure=False;self.wrong_receipt=False
        self.private_parent_context='FLASH-CONTEXT-MUST-NOT-REACH-WORKER'
        self.restores=[];self.deliveries=[];self.delivered=threading.Event()
    def state(self,principal,thread):
        return ParentState(principal,thread,'original-flash-model',self.resident,
                           self.busy,True,True,True)
    def restore(self,principal,thread,model,operation):
        self.restores.append((model,operation))
        if self.restore_failure:raise RuntimeError('synthetic restore failure')
        self.resident=True
        return self.state(principal,thread)
    def deliver(self,principal,thread,job,digest,operation):
        self.deliveries.append((thread,job,digest,operation))
        self.delivered.set()
        return ParentReceipt(job,'other-chat' if self.wrong_receipt else thread,digest,operation)


class ParentReturnTests(unittest.TestCase):
    setUp=fixtures.TextMCPTests.setUp
    consult=fixtures.TextMCPTests.consult
    terminal=fixtures.TextMCPTests.terminal

    def attach(self,**settings):
        self.host=SyntheticHost()
        for key,value in settings.items():setattr(self.host,key,value)
        self.returns=ParentReturnController(self.server.store,self.host)
        self.server.parent_return=self.returns
        self.server.pilot.parent_return=self.returns
        self.returns.start()

    def await_return(self,job,target):
        until=time.monotonic()+40
        while time.monotonic()<until:
            status=self.returns.status(job['job_id'])
            if status['state']==target:return status
            time.sleep(.05)
        self.fail('parent return did not reach '+target+': '+str(status))

    def test_resident_flash_receives_automatic_return_without_status_polling(self):
        self.attach()
        job=self.consult()
        self.assertTrue(self.host.delivered.wait(40),'no automatic host notification')
        self.await_return(job,'DELIVERED')
        self.assertEqual(len(self.host.deliveries),1)
        self.assertFalse(self.host.restores)
        # Delivery itself does not fabricate an integration ACK.
        self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.assertNotIn(self.host.private_parent_context,str(self.http.requests))
        self.returns.tick();self.assertEqual(len(self.host.deliveries),1)

    def test_unloaded_flash_is_restored_before_same_chat_return_and_ack(self):
        self.attach(resident=False)
        job=self.consult()
        self.assertTrue(self.host.delivered.wait(40))
        state=self.await_return(job,'DELIVERED')
        self.assertTrue(state['restore_needed'])
        self.assertEqual(self.host.restores[0][0],'original-flash-model')
        self.assertEqual(self.host.deliveries[0][0],'configured-pilot')
        result=self.client.tool('minionone_consult_result',{'job_id':job['job_id']})
        ack=self.client.tool('minionone_accept_consult_result',{
            'job_id':job['job_id'],'expected_version':result['version'],
            'result_digest':result['result_digest'],'operation_id':'parent-restore-ack-001'})
        self.assertEqual(ack['state'],'COMPLETED')
        self.assertNotIn(self.host.private_parent_context,str(self.http.requests))

    def test_busy_parent_defers_then_returns_without_repeating_worker(self):
        self.attach(busy=True)
        job=self.consult();self.assertEqual(self.terminal(job)['state'],'RESULT_READY')
        self.await_return(job,'WAITING_PARENT')
        self.assertFalse(self.host.deliveries)
        self.host.busy=False
        self.assertTrue(self.host.delivered.wait(10))
        self.await_return(job,'DELIVERED')
        self.assertEqual(len(self.http.requests),1)

    def test_unverified_original_parent_prevents_worker_start(self):
        self.attach()
        original=self.host.state
        self.host.state=lambda principal,thread:replace(original(principal,thread),authenticated=False)
        with self.assertRaises(PilotError):self.consult()
        self.assertFalse(self.http.requests)
        self.assertFalse(self.host.deliveries)
        self.assertFalse(self.returns.verified)

    def test_restore_failure_never_delivers_or_completes_or_blindly_reloads(self):
        self.attach(resident=False,restore_failure=True)
        job=self.consult();self.await_return(job,'RESTORE_FAILED')
        self.returns.tick();self.returns.tick()
        self.assertEqual(len(self.host.restores),1)
        self.assertFalse(self.host.deliveries)
        result=self.client.tool('minionone_consult_result',{'job_id':job['job_id']})
        with self.assertRaises(PilotError):
            self.client.tool('minionone_accept_consult_result',{
                'job_id':job['job_id'],'expected_version':result['version'],
                'result_digest':result['result_digest'],'operation_id':'must-not-ack-001'})
        self.assertEqual(self.terminal(job)['state'],'RESULT_READY')

    def test_wrong_chat_receipt_is_quarantined_and_never_resent(self):
        self.attach(wrong_receipt=True)
        job=self.consult();self.await_return(job,'NEEDS_RECONCILIATION')
        self.returns.tick();self.returns.tick()
        self.assertEqual(len(self.host.deliveries),1)
        self.assertEqual(self.terminal(job)['state'],'RESULT_READY')

    def test_restart_after_inflight_fence_does_not_repeat_external_delivery(self):
        self.attach()
        job=self.consult();self.await_return(job,'DELIVERED')
        self.returns.close()
        with self.server.store._connection() as con:
            con.execute("UPDATE parent_returns SET state='IN_FLIGHT' WHERE job_id=?",(job['job_id'],))
        recovered=ParentReturnController(self.server.store,self.host)
        recovered.tick()
        self.assertEqual(recovered.status(job['job_id'])['state'],'NEEDS_RECONCILIATION')
        self.assertEqual(len(self.host.deliveries),1)


class FileParentReturnTests(unittest.TestCase):
    setUp=file_bridge.CompleteBridgeE2E.setUp
    rpc=file_bridge.CompleteBridgeE2E.rpc
    direct=file_bridge.CompleteBridgeE2E.direct

    def test_existing_physical_file_route_also_returns_automatically(self):
        host=SyntheticHost()
        returns=ParentReturnController(self.manager.store,host)
        self.manager.parent_return=returns
        self.manager.pilot.parent_return=returns
        returns.start()
        job=self.direct('file-auto-return-operation-001')
        self.assertTrue(host.delivered.wait(40))
        until=time.monotonic()+5
        while returns.status(job['job_id'])['state']!='DELIVERED' and time.monotonic()<until:
            time.sleep(.05)
        self.assertEqual(returns.status(job['job_id'])['state'],'DELIVERED')
        path=self.project/'.minionone'/'jobs'/job['job_id']/'output'/'result.txt'
        self.assertEqual(path.read_text(),'SYNTHETIC BRIDGE CONTENT')
        self.assertEqual(len(self.model.calls),4)
        self.assertEqual(len(host.deliveries),1)


if __name__=='__main__':unittest.main()
