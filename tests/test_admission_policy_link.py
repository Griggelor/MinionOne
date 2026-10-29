"""Prove AdmissionStore uses canonical lifecycle policy in real SQLite transaction."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minionone.admission import AdmissionStore
from minionone.lifecycle import LifecycleRejected

SECRET="long-parent-secret-"+("z"*48)
DIGEST="c"*64

class TestCanonicalAdmissionBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=AdmissionStore(Path(self.tmp.name)/"state.sqlite",
                                  credentials={SECRET:("operator",{"thread"})})
        self.job=self.store.admit(SECRET,"thread",DIGEST,"creation-0001")

    def test_abort_must_pass_manager_policy_and_rollback_on_rejection(self):
        with patch("minionone.admission.check_transition",
                   side_effect=LifecycleRejected("injected-denial")) as guard:
            with self.assertRaises(LifecycleRejected):
                self.store.request_abort(SECRET,"thread",self.job["job_id"],0,
                                         "abort-0001")
            guard.assert_called_once()
        state=self.store.status(SECRET,"thread",self.job["job_id"])
        self.assertEqual(state["state"],"ACCEPTED")
        self.assertEqual(state["version"],0)
        self.assertEqual(len(self.store.events(SECRET,"thread",self.job["job_id"])),1)

    def test_verified_cancel_has_same_policy_gate(self):
        a=self.store.request_abort(SECRET,"thread",self.job["job_id"],0,"abort-0001")
        self.assertEqual(a["state"],"CANCEL_REQUESTED")
        with patch("minionone.admission.check_transition",
                   side_effect=LifecycleRejected("injected-denial")) as guard:
            with self.assertRaises(LifecycleRejected):
                self.store.confirm_quiescent_cancel(self.job["job_id"],1,"quiesce-0001")
            guard.assert_called_once()
        state=self.store.status(SECRET,"thread",self.job["job_id"])
        self.assertEqual(state["state"],"CANCEL_REQUESTED")
        self.assertEqual(len(self.store.events(SECRET,"thread",self.job["job_id"])),2)

if __name__=="__main__":
    unittest.main()
