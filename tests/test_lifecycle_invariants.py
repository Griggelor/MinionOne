"""Independent lifecycle policy challenges, no reliance on reference model."""
import unittest
from dataclasses import replace
from minionone.lifecycle import (
    Evidence, LifecycleRejected, LEGAL_TRANSITIONS, check_transition, check_evidence
)

DIGEST='a'*64

class TestLifecyclePolicy(unittest.TestCase):
    def test_no_unknown_or_self_or_terminal_rewrite(self):
        for a,b in (('FAKE','RUNNING'),('RUNNING','RUNNING'),('COMPLETED','RUNNING'),
                    ('CANCELLED','COMPLETED'),('ACCEPTED','COMPLETED')):
            with self.subTest(a=a,b=b),self.assertRaises(LifecycleRejected):
                check_transition(a,b,Evidence())

    def test_legal_admission_progression(self):
        e=Evidence()
        self.assertTrue(check_transition('ACCEPTED','VALIDATING',e))
        self.assertTrue(check_transition('VALIDATING','QUEUED',e))
        self.assertTrue(check_transition('QUEUED','RESOURCE_CHECK',e))

    def test_running_requires_real_fenced_attempt(self):
        good=Evidence(attempt_no=1,active_lease=True,worker_outcome='RUNNING')
        self.assertTrue(check_transition('RESOURCE_CHECK','RUNNING',good))
        for bad in (replace(good,attempt_no=0),replace(good,active_lease=False),
                    replace(good,worker_outcome='DONE')):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('RESOURCE_CHECK','RUNNING',bad)

    def test_result_ready_needs_digest_and_no_active_lease(self):
        good=Evidence(attempt_no=1,worker_outcome='DONE',result_digest=DIGEST,
                      integration_ack='NONE')
        self.assertTrue(check_transition('RUNNING','RESULT_READY',good))
        for bad in (replace(good,result_digest=None),replace(good,active_lease=True),
                    replace(good,worker_outcome='RUNNING'),replace(good,integration_ack='ACKED')):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('RUNNING','RESULT_READY',bad)

    def test_no_false_completed_from_worker_prose(self):
        good=Evidence(attempt_no=1,worker_outcome='DONE',result_digest=DIGEST,
                      integration_ack='ACKED',notification_delivery='DELIVERED',
                      parent_restore='NOT_NEEDED')
        self.assertTrue(check_transition('RESULT_READY','COMPLETED',good))
        for bad in (
            replace(good,worker_outcome='RUNNING'),replace(good,result_digest=None),
            replace(good,integration_ack='NONE'),replace(good,notification_delivery='FAILED'),
            replace(good,parent_restore='FAILED'),replace(good,active_lease=True),
            replace(good,error_code='FAILED_MODEL')):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('RESULT_READY','COMPLETED',bad)

    def test_cancelled_requires_independent_quiescence(self):
        good=Evidence(worker_outcome='CANCELLED',worker_quiescent=True)
        self.assertTrue(check_transition('CANCEL_REQUESTED','CANCELLED',good))
        for bad in (replace(good,worker_quiescent=False),
                    replace(good,worker_outcome='DONE'),
                    replace(good,active_lease=True,worker_quiescent=False)):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('CANCEL_REQUESTED','CANCELLED',bad)

    def test_reconciliation_is_quarantine(self):
        e=Evidence(reconciliation_required=True)
        self.assertTrue(check_transition('RUNNING','NEEDS_RECONCILIATION',e))
        with self.assertRaises(LifecycleRejected):
            check_transition('NEEDS_RECONCILIATION','RUNNING',e)
        with self.assertRaises(LifecycleRejected):
            check_transition('RUNNING','RESULT_READY',e)
        with self.assertRaises(LifecycleRejected):
            check_transition('RUNNING','NEEDS_RECONCILIATION',replace(e,active_lease=True))
        recovered=Evidence(reconciliation_verified=True,attempt_no=1,
                           active_lease=True,worker_outcome='RUNNING')
        self.assertTrue(check_transition('NEEDS_RECONCILIATION','RUNNING',recovered))

    def test_interrupted_requires_fence_diagnostic_and_attempt(self):
        good=Evidence(attempt_no=1,worker_outcome='RUNNING',error_code='CONNECTION_LOST')
        self.assertTrue(check_transition('RUNNING','INTERRUPTED',good))
        for bad in (replace(good,attempt_no=0),replace(good,error_code=None),
                    replace(good,active_lease=True),replace(good,worker_outcome='DONE')):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('RUNNING','INTERRUPTED',bad)

    def test_resume_requires_new_attempt_and_checkpoint(self):
        good=Evidence(attempt_no=2,new_attempt=True,checkpoint_verified=True)
        self.assertTrue(check_transition('INTERRUPTED','RESUMING',good))
        for bad in (replace(good,attempt_no=1),replace(good,new_attempt=False),
                    replace(good,checkpoint_verified=False),replace(good,active_lease=True)):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_transition('INTERRUPTED','RESUMING',bad)

    def test_reject_invalid_evidence_types(self):
        for bad in (replace(Evidence(),attempt_no=True),replace(Evidence(),active_lease=1),
                    replace(Evidence(),result_digest='XYZ'),replace(Evidence(),worker_outcome='YES'),
                    replace(Evidence(),error_code='raw password: ABC'),
                    replace(Evidence(),reconciliation_required=True,reconciliation_verified=True),
                    replace(Evidence(),worker_quiescent=True,active_lease=True)):
            with self.subTest(bad=bad),self.assertRaises(LifecycleRejected):
                check_evidence(bad)

    def test_adversarial_unhashable_fields_fail_closed(self):
        for bad in (replace(Evidence(), worker_outcome=[]),
                    replace(Evidence(), notification_delivery=[]),
                    replace(Evidence(), parent_restore={}),
                    replace(Evidence(), integration_ack=[])):
            with self.subTest(bad=bad), self.assertRaises(LifecycleRejected):
                check_evidence(bad)
        with self.assertRaises(LifecycleRejected):
            check_transition('ACCEPTED', [], Evidence())

    def test_restore_requires_confirmed_failure(self):
        good=Evidence(worker_outcome='DONE',parent_restore='FAILED')
        self.assertTrue(check_transition('RESULT_READY','RESTORATION_REQUIRED',good))
        with self.assertRaises(LifecycleRejected):
            check_transition('RESULT_READY','RESTORATION_REQUIRED',
                             replace(good,parent_restore='READY'))

    def test_failed_requires_redacted_diagnostic(self):
        good=Evidence(worker_outcome='FAILED',error_code='MODEL_TIMEOUT')
        self.assertTrue(check_transition('RUNNING','FAILED',good))
        with self.assertRaises(LifecycleRejected):
            check_transition('RUNNING','FAILED',replace(good,error_code=None))

    def test_question_requires_worker_question_outcome(self):
        self.assertTrue(check_transition('RUNNING','WAITING_FOR_PARENT',
                                         Evidence(worker_outcome='QUESTION')))
        with self.assertRaises(LifecycleRejected):
            check_transition('RUNNING','WAITING_FOR_PARENT',Evidence(worker_outcome='DONE'))

    def test_grammar_covers_all_referenced_states(self):
        self.assertEqual(LEGAL_TRANSITIONS['COMPLETED'],frozenset())
        self.assertEqual(LEGAL_TRANSITIONS['CANCELLED'],frozenset())
        self.assertEqual(len(LEGAL_TRANSITIONS),17)

if __name__=='__main__': unittest.main()
