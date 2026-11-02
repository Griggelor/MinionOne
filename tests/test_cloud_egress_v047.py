"""Cloud gates use real manager state and a controlled HTTPS transport substitute."""
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch

from minionone.admission import InputRejected
from minionone.worker_profiles import WorkerProfile,WorkerProviderError
from test_text_workers_v044 import WorkerMCPFixture,MODEL,completion
from test_question_resume_v047 import tool,question_script


def policy(**changes):
    return {'contract':'text_tokens_v1','data_class':'synthetic',
        'max_job_cost_microusd':1000000,'max_total_cost_microusd':10000000,
        'input_token_ceiling':262144,'input_price_microusd_per_million':1000,
        'output_price_microusd_per_million':1000,**changes}


class CloudGateTests(WorkerMCPFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cloud=WorkerProfile('cloud','cloud',MODEL,'https://synthetic.invalid/v1',
            secret_env='MINIONONE_CLOUD_GATE_KEY',cloud_approved=True,cloud_policy=policy())
        self.server.pilot.worker_profiles['cloud']=self.cloud
        self.seen=[];self.reply=completion
        def transport(url,body,key,timeout):
            self.assertEqual(url,'https://synthetic.invalid/v1/chat/completions')
            data=json.loads(body);self.seen.append(data)
            return json.dumps(self.reply(data)).encode()
        self.env=patch.dict(os.environ,{'MINIONONE_CLOUD_GATE_KEY':'synthetic-cloud-gate-key-value'})
        self.env.start();self.addCleanup(self.env.stop)
        self.transport=patch('minionone.worker_profiles.bounded_request',transport)
        self.transport.start();self.addCleanup(self.transport.stop)

    def configure(self,**changes):
        self.server.pilot.worker_profiles['cloud']=replace(self.cloud,cloud_policy=policy(**changes))

    def cloud_job(self,op='cloud-gate-operation-001',task='Review the supplied material.',files=None):
        return self.client.tool('minionone_delegate',{'project_id':'demo','profile_id':'cloud',
            'allow_cloud':True,'task':task,'files':files or {},'operation_id':op})

    def test_valid_policy_transports_three_turns_with_durable_conservative_charges(self):
        job=self.cloud_job();status=self.terminal(job)
        self.assertEqual(status['state'],'RESULT_READY',status)
        self.assertEqual(len(self.seen),3)
        self.assertEqual(status['workspace_job']['cloud_budget']['requests_reserved'],3)
        self.assertGreater(status['workspace_job']['cloud_budget']['reserved_upper_bound_microusd'],0)
        with self.server.store._connection() as con:
            self.assertEqual({r[0] for r in con.execute('SELECT state FROM cloud_request_reservations')},{'RESPONSE_RECEIVED'})
            with self.assertRaises(Exception):con.execute('DELETE FROM cloud_request_reservations')

    def test_missing_policy_blocks_before_any_network_and_does_not_quarantine(self):
        self.server.pilot.worker_profiles['cloud']=replace(self.cloud,cloud_policy=None)
        status=self.terminal(self.cloud_job())
        self.assertEqual(status['state'],'FAILED',status)
        self.assertEqual(status['workspace_job']['error']['code'],'CLOUD_POLICY_REQUIRED')
        self.assertFalse(self.seen)
        self.assertEqual(status['workspace_job']['cloud_budget']['requests_reserved'],0)

    def test_names_addresses_keys_emails_metadata_and_operator_terms_are_blocked(self):
        self.configure(sensitive_terms=['ACME-CONFIDENTIAL'])
        samples=['Review Max Mustermann.', 'Contact max@example.invalid.',
            'Address: Hauptstraße 42.', 'Phone: +49 170 1234567.',
            'Key: sk-0123456789abcdefghijklmnop.',
            'File C:\\Users\\PrivateUser\\notes.txt.', 'ACME-CONFIDENTIAL',
            'Review input/Max_Mustermann.txt.', 'Review Ｍax Mustermann.']
        for i,text in enumerate(samples):
            with self.subTest(case=i):
                status=self.terminal(self.cloud_job('pii-sample-'+str(i),task=text))
                self.assertEqual(status['state'],'FAILED',status)
                self.assertEqual(status['workspace_job']['error']['code'],'CLOUD_EGRESS_BLOCKED')
                self.assertFalse(self.seen)
                self.assertEqual(status['workspace_job']['cloud_budget']['requests_reserved'],0)
                trace=self.client.tool('minionone_job_trace',{'job_id':status['job_id']})
                self.assertNotIn(text,json.dumps(trace))

    def test_later_tool_read_is_checked_before_the_next_cloud_call(self):
        self.reply=lambda data:tool(data,'read_file',{'path':'input/source.txt'})
        job=self.cloud_job(files={'source.txt':'Contact Max Mustermann at private@example.invalid.'})
        status=self.terminal(job)
        self.assertEqual(status['state'],'FAILED',status)
        self.assertEqual(status['workspace_job']['error']['code'],'CLOUD_EGRESS_BLOCKED')
        self.assertEqual(len(self.seen),1)
        self.assertNotIn('Max Mustermann',json.dumps(self.seen))
        self.assertEqual(status['workspace_job']['cloud_budget']['requests_reserved'],1)

    def test_job_and_global_budget_are_reserved_before_send_and_survive_new_jobs(self):
        reserve=(262144*1000+2048*1000+999999)//1000000
        self.configure(max_job_cost_microusd=reserve,max_total_cost_microusd=reserve)
        first=self.terminal(self.cloud_job())
        self.assertEqual(first['state'],'FAILED',first)
        self.assertEqual(first['workspace_job']['error']['code'],'CLOUD_BUDGET_EXHAUSTED')
        self.assertEqual(len(self.seen),1)
        self.assertEqual(first['workspace_job']['cloud_budget']['reserved_upper_bound_microusd'],reserve)
        second=self.terminal(self.cloud_job('cloud-second-job'))
        self.assertEqual(second['workspace_job']['error']['code'],'CLOUD_BUDGET_EXHAUSTED')
        self.assertEqual(len(self.seen),1)
        self.assertEqual(second['workspace_job']['cloud_budget']['requests_reserved'],0)

    def test_transport_timeout_never_refunds_reserved_money(self):
        def lost(*args):raise WorkerProviderError('PROVIDER_TIMEOUT')
        with patch('minionone.worker_profiles.bounded_request',lost):
            status=self.terminal(self.cloud_job())
        self.assertEqual(status['state'],'NEEDS_RECONCILIATION',status)
        self.assertGreater(status['workspace_job']['cloud_budget']['reserved_upper_bound_microusd'],0)
        with self.server.store._connection() as con:
            self.assertEqual(con.execute('SELECT state FROM cloud_request_reservations').fetchone()[0],'UNKNOWN')

    def test_explicit_answer_is_checked_before_resumed_cloud_generation(self):
        self.reply=question_script
        job=self.cloud_job();paused=self.terminal(job)
        self.assertEqual(paused['state'],'WAITING_FOR_PARENT',paused)
        self.client.tool('minionone_answer_question',{'job_id':job['job_id'],
            'expected_version':paused['version'],'question_digest':paused['workspace_job']['question_digest'],
            'answer':'Send the result to Max Mustermann.','operation_id':'cloud-sensitive-answer'})
        status=self.terminal(job)
        self.assertEqual(status['state'],'FAILED',status)
        self.assertEqual(status['workspace_job']['error']['code'],'CLOUD_EGRESS_BLOCKED')
        self.assertEqual(len(self.seen),2)

    def test_large_non_pii_payload_does_not_make_regex_scanning_quadratic(self):
        import time
        from minionone.cloud_egress import CloudEgressGuard
        guard=CloudEgressGuard(self.server.pilot,'validation-only','unused',self.cloud)
        body=json.dumps({'model':MODEL,'stream':False,'max_tokens':16,'tools':[],
            'messages':[{'role':'user','content':('a.'*60000)+'@invalid '+('a-'*30000)}]}).encode('ascii')
        started=time.perf_counter()
        self.assertGreater(guard.validate(body),0)
        self.assertLess(time.perf_counter()-started,5)
        self.assertFalse(self.seen)

    def test_billing_policy_rejects_unknown_contract_float_prices_and_invalid_classes(self):
        for changes in ({'contract':'arbitrary_fees'}, {'data_class':'unclassified'},
                        {'input_price_microusd_per_million':0.5},
                        {'max_total_cost_microusd':1}, {'sensitive_terms':['']}):
            with self.subTest(changes=changes),self.assertRaises(InputRejected):
                replace(self.cloud,cloud_policy=policy(**changes))
