"""Real manager jobs, immutable IO/cost journals, offline CLI and restart fencing."""
from contextlib import closing, redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

from minionone.admission import AdmissionStore, InputRejected, PermissionDenied, VersionConflict, IdempotencyConflict
from minionone.mcp_server import LocalMCP
from minionone.pilot_jobs import PilotJobs, PilotConfig
from minionone.pilot_lock import PilotLock
from minionone.pilot_cli import main, PilotClient
from minionone.workspace_jobs import WorkspaceJobs
from minionone.workspace_preparation import WorkspacePreparation
from minionone.worker_profiles import WorkerProfile, WorkerProviderError
from test_text_workers_v044 import WorkerMCPFixture, MODEL, TOKEN
from test_cloud_egress_v047 import policy


class CloudRecoveryTests(WorkerMCPFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cloud=WorkerProfile('cloud','cloud',MODEL,'https://synthetic.invalid/v1',
            secret_env='MINIONONE_CLOUD_RECOVERY_KEY',cloud_approved=True,cloud_policy=policy())
        self.server.pilot.worker_profiles['cloud']=self.cloud
        self.env=patch.dict(os.environ,{'MINIONONE_CLOUD_RECOVERY_KEY':'synthetic-recovery-key-value'})
        self.env.start();self.addCleanup(self.env.stop)
        self.calls=0
        from minionone.worker_profiles import bounded_request
        def uncertain(*args):
            if not args[0].startswith('https://synthetic.invalid/'):
                return bounded_request(*args)
            self.calls+=1
            raise WorkerProviderError('PROVIDER_TIMEOUT')
        self.transport=patch('minionone.worker_profiles.bounded_request',uncertain)
        self.transport.start();self.addCleanup(self.transport.stop)

    def uncertain_job(self, prepared=False):
        job=self.staged('cloud',allow_cloud=True)[0] if prepared else self.submit('cloud',allow_cloud=True)
        status=self.terminal(job)
        self.assertEqual(status['state'],'NEEDS_RECONCILIATION',status)
        self.job=job['job_id'];self.status=status
        self.folder=self.project/'.minionone'/'jobs'/self.job
        return status

    def offline(self):
        self.server.shutdown();self.server.server_close()
        owner=AdmissionStore(self.root/'jobs.sqlite',credentials={TOKEN:('local-operator',{'configured-pilot','foreign-thread'})})
        pilot=PilotJobs(owner,PilotConfig('unused'))
        pilot.workspace_execution=WorkspaceJobs(WorkspacePreparation(owner,{'demo':self.project}),pilot,TOKEN,'configured-pilot')
        self.offline_pilot=pilot
        return pilot

    def resolve(self, pilot, **changes):
        return pilot.resolve_cloud_for_operator(TOKEN,'configured-pilot',self.job,
            **dict(confirm_provider_stopped=True,confirm_work_inspected=True,**changes))

    def db_rows(self, table):
        with self.server.store._connection() as con:
            return [dict(r) for r in con.execute('SELECT * FROM '+table+' ORDER BY 1')]

    def file_bytes(self):
        return {p.relative_to(self.folder).as_posix():p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}

    def test_prepared_handoff_closes_without_retry_refund_or_file_writes(self):
        self.uncertain_job(prepared=True)
        (self.folder/'work'/'kept.txt').write_bytes(b'partially completed work')
        pilot=self.offline();files=self.file_bytes();charges=self.db_rows('cloud_request_reservations')
        with closing(PilotLock(self.root/'jobs.sqlite')):
            receipt=self.resolve(pilot,expected_version=self.status['version'])
            self.assertEqual(self.resolve(pilot,expected_version=self.status['version']),receipt)
        self.assertEqual(receipt['outcome'],'FAILED_NO_RETRY')
        self.assertEqual(receipt['cloud_budget']['requests_reserved'],1)
        self.assertEqual(self.file_bytes(),files)
        self.assertEqual(self.db_rows('cloud_request_reservations'),charges)
        self.assertEqual(self.calls,1)
        self.assertEqual(len(self.db_rows('cloud_operator_resolutions')),1)
        with pilot.owner._connection() as con:
            run=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(self.job,)).fetchone()
            self.assertFalse(run['active']);self.assertFalse(run['provider_inflight']);self.assertIsNone(run['capability'])
            self.assertIsNone(run['result_digest']);self.assertIsNone(run['issued_digest'])
            with self.assertRaises(sqlite3.IntegrityError):con.execute('DELETE FROM cloud_operator_resolutions')

    def test_restart_unblocks_dispatch_but_does_not_reset_global_budget(self):
        reserve=(262144*1000+2048*1000+999999)//1000000
        self.server.pilot.worker_profiles['cloud']=replace(self.cloud,cloud_policy=policy(
            max_job_cost_microusd=reserve,max_total_cost_microusd=reserve))
        self.uncertain_job();self.resolve(self.offline())
        restarted=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=self.root/'jobs.sqlite',
            worker_profiles={'cloud':self.server.pilot.worker_profiles['cloud']},min_free_mb=1,
            enable_workspace_jobs=True,workspace_projects={'demo':self.project},worker_model='unused')
        threading.Thread(target=restarted.serve_forever,daemon=True).start()
        self.addCleanup(restarted.server_close);self.addCleanup(restarted.shutdown)
        self.client=PilotClient(restarted.server_port,TOKEN)
        next_job=self.submit('cloud',op='after-cloud-resolution',allow_cloud=True)
        status=self.terminal(next_job)
        self.assertEqual(status['state'],'FAILED',status)
        self.assertEqual(status['workspace_job']['error']['code'],'CLOUD_BUDGET_EXHAUSTED')
        self.assertEqual(self.calls,1)
        self.assertEqual(status['workspace_job']['cloud_budget']['principal_reserved_upper_bound_microusd'],reserve)

    def test_stop_inspection_cas_scope_and_idempotency_are_required(self):
        self.uncertain_job();pilot=self.offline()
        for kwargs in ({'confirm_provider_stopped':False},{'confirm_work_inspected':False},{'expected_version':True}):
            args=dict(confirm_provider_stopped=True,confirm_work_inspected=True);args.update(kwargs)
            with self.assertRaises(InputRejected):pilot.resolve_cloud_for_operator(TOKEN,'configured-pilot',self.job,**args)
        with self.assertRaises(VersionConflict):self.resolve(pilot,expected_version=self.status['version']+1)
        with self.assertRaises(PermissionDenied):pilot.resolve_cloud_for_operator(TOKEN,'foreign-thread',self.job,
            confirm_provider_stopped=True,confirm_work_inspected=True)
        self.assertFalse(self.db_rows('cloud_operator_resolutions'))
        self.resolve(pilot)
        with self.assertRaises(IdempotencyConflict):self.resolve(pilot,expected_version=self.status['version'])
        self.assertEqual(self.calls,1)

    def test_tampered_io_and_partial_manager_log_leave_quarantine_unchanged(self):
        self.uncertain_job();pilot=self.offline()
        for name in ('worker-io.jsonl','job.log.jsonl'):
            path=self.folder/'output'/name;original=path.read_bytes()
            path.write_bytes(original[:-1])
            with self.assertRaises(InputRejected):self.resolve(pilot)
            self.assertFalse(self.db_rows('cloud_operator_resolutions'))
            self.assertEqual(self.db_rows('jobs')[0]['state'],'NEEDS_RECONCILIATION')
            path.write_bytes(original)
        self.resolve(pilot)

    def test_changed_sealed_input_is_not_repaired(self):
        self.uncertain_job();pilot=self.offline()
        path=self.folder/'input'/'TASK.md';path.write_text('changed input')
        with self.assertRaises(InputRejected):self.resolve(pilot)
        self.assertEqual(path.read_text(),'changed input')
        self.assertFalse(self.db_rows('cloud_operator_resolutions'))

    def test_work_hardlink_is_rejected_without_touching_its_target(self):
        self.uncertain_job();pilot=self.offline()
        outside=self.root/'outside.txt';outside.write_bytes(b'outside')
        os.link(outside,self.folder/'work'/'linked.txt')
        with self.assertRaises((InputRejected,PermissionError)):self.resolve(pilot)
        self.assertEqual(outside.read_bytes(),b'outside')
        self.assertFalse(self.db_rows('cloud_operator_resolutions'))

    def test_target_model_lease_cannot_be_bypassed(self):
        self.uncertain_job();pilot=self.offline()
        # Deliberately inconsistent legacy/future model scope: refuse regardless
        # of lifecycle state instead of initializing/mutating the model runtime.
        with pilot.owner._connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS model_leases(job_id TEXT PRIMARY KEY)')
            con.execute('INSERT INTO model_leases VALUES(?)',(self.job,))
        with self.assertRaises(InputRejected):self.resolve(pilot)
        self.assertFalse(self.db_rows('cloud_operator_resolutions'))

    def test_stale_crashed_target_is_fenced_without_global_recovery(self):
        self.uncertain_job();pilot=self.offline()
        # Simulate persisted state immediately before an unclean process exit.
        with pilot.owner._connection() as con:
            con.execute("UPDATE jobs SET state='RUNNING' WHERE job_id=?",(self.job,))
            con.execute('UPDATE pilot_runs SET active=1,provider_inflight=1 WHERE job_id=?',(self.job,))
        receipt=self.resolve(pilot)
        self.assertEqual(receipt['source_state'],'RUNNING')
        self.assertEqual(receipt['state'],'FAILED')
        self.assertEqual(self.calls,1)

    def test_offline_cli_refuses_running_instance_and_replays_identical_receipt(self):
        self.uncertain_job()
        config={'port':8765,'model':'unused','workspace_projects':{'demo':str(self.project)}}
        command=['--state',str(self.root),'resolve-cloud','--job',self.job,
            '--confirm-provider-stopped','--confirm-work-inspected']
        with patch('minionone.pilot_cli._settings',return_value=config),patch('minionone.pilot_cli._token',return_value=TOKEN):
            with redirect_stderr(io.StringIO()) as output:self.assertEqual(main(command),2)
            self.assertIn('PILOT_INSTANCE_BUSY',output.getvalue())
            self.offline()
            with redirect_stdout(io.StringIO()) as output:self.assertEqual(main(command),0)
            receipt=json.loads(output.getvalue())
            with redirect_stdout(io.StringIO()) as output:self.assertEqual(main(command),0)
            self.assertEqual(json.loads(output.getvalue()),receipt)
        self.assertEqual(self.calls,1)

    def test_local_ready_result_is_never_reclassified_as_uncertain_cloud(self):
        job=self.submit('local');status=self.terminal(job)
        self.assertEqual(status['state'],'RESULT_READY')
        self.job=job['job_id'];pilot=self.offline()
        with self.assertRaises(InputRejected):self.resolve(pilot)
        self.assertEqual(self.db_rows('jobs')[0]['state'],'RESULT_READY')

    def test_pending_publication_cannot_be_published_by_cloud_resolution(self):
        self.uncertain_job();pilot=self.offline()
        pilot.workspace_execution.publisher.stage(TOKEN,'configured-pilot',self.job,
            'result.txt',b'unpublished result','pending-cloud-publication')
        with self.assertRaises(InputRejected):self.resolve(pilot)
        self.assertFalse((self.folder/'output'/'result.txt').exists())
        self.assertEqual(self.db_rows('workspace_publications')[0]['state'],'PENDING')

    def test_published_files_are_verified_without_republication(self):
        self.uncertain_job();pilot=self.offline()
        publisher=pilot.workspace_execution.publisher
        publisher.stage(TOKEN,'configured-pilot',self.job,'result.txt',b'retained result','verified-cloud-publication')
        publisher.publish(TOKEN,'configured-pilot',self.job,'result.txt','verified-cloud-publication')
        path=self.folder/'output'/'result.txt';path.write_bytes(b'changed result')
        with self.assertRaises(InputRejected):self.resolve(pilot)
        path.write_bytes(b'retained result');before=self.file_bytes()
        self.resolve(pilot)
        self.assertEqual(self.file_bytes(),before)
        self.assertEqual(self.calls,1)


class NativeCloudRecoveryTests(unittest.TestCase):
    # Reuse fixture setup only; do not inherit or rediscover its existing tests.
    def setUp(self):
        import test_native_lifecycle_v046 as native
        native.NativeLifecycleTests.setUp(self)

    def test_restart_returns_failure_to_original_native_chat_exactly_once(self):
        import test_native_lifecycle_v046 as native
        cloud=WorkerProfile('cloud','cloud',native.fixture.WORKER,'https://synthetic.invalid/v1',
            secret_env='MINIONONE_NATIVE_TEST_KEY',cloud_approved=True,cloud_policy=policy())
        self.manager.pilot.worker_profiles['cloud']=cloud
        with patch('minionone.worker_profiles.bounded_request',side_effect=WorkerProviderError('PROVIDER_TIMEOUT')) as transport:
            job=self.client.tool('minionone_delegate',{'project_id':'demo','task':'Review the synthetic material.',
                'profile_id':'cloud','allow_cloud':True,'operation_id':'native-cloud-recovery'})
            self.host.end_turn()
            status=native.NativeLifecycleTests.await_state(self,job)
            self.assertEqual(status['state'],'NEEDS_RECONCILIATION',status)
            self.manager.shutdown();self.manager.server_close()
            self.assertEqual(self.host.events.count('parent-run'),0)
            owner=AdmissionStore(self.root/'jobs.sqlite',credentials={native.TOKEN:('local-operator',{native.fixture.THREAD})})
            pilot=PilotJobs(owner,PilotConfig('unused'))
            pilot.workspace_execution=WorkspaceJobs(WorkspacePreparation(owner,{'demo':self.project}),pilot,native.TOKEN,native.fixture.THREAD)
            before=list(self.host.events)
            with closing(PilotLock(self.root/'jobs.sqlite')):
                pilot.resolve_cloud_for_operator(native.TOKEN,native.fixture.THREAD,job['job_id'],
                    confirm_provider_stopped=True,confirm_work_inspected=True)
            self.assertEqual(self.host.events,before)  # Offline closure has no native/model effects.
            self.manager=LocalMCP(('127.0.0.1',0),token=native.TOKEN,database_path=self.root/'jobs.sqlite',
                parent_config=self.parent,worker_profiles={'cloud':cloud},workspace_projects={'demo':self.project},
                enable_workspace_jobs=True,min_free_mb=1)
            self.manager.model_lifecycle.measure=self.host.memory;self.manager.native_parent.measure=self.host.memory
            threading.Thread(target=self.manager.serve_forever,daemon=True).start()
            self.addCleanup(self.manager.server_close);self.addCleanup(self.manager.shutdown)
            native.NativeLifecycleTests.await_return(self,job)
            self.assertEqual(self.host.events.count('parent-run'),1)
            self.assertEqual(transport.call_count,1)
            self.assertFalse(self.host.requests)
