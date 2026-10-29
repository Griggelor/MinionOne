"""Independent G2 outbox safety tests: synthetic provider effects, NO real send."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from minionone.outbox import OutboxStore, OutboxRejected, OutboxConflict

JOB = "JOB-" + "1" * 32
DIGEST = "a" * 64


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "jobs.sqlite"
        # Synthetic *future-manager* state used only for outbox contract tests.
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL)")
            con.execute("INSERT INTO jobs VALUES (?, ?, ?)", (JOB, "RESULT_READY", 7))
        self.store = OutboxStore(self.db, max_entries_per_job=2)

    def prepare(self, key="notify-0001", digest=DIGEST):
        return self.store.prepare(JOB, key, digest, expected_state_version=7)

    def test_prepare_replay_is_durable_and_never_double_creates(self):
        first = self.prepare()
        self.assertEqual(first["delivery_state"], "PREPARED")
        self.assertEqual(first["attempt"], 0)
        self.assertEqual(first, self.prepare())
        reboot = OutboxStore(self.db, max_entries_per_job=2)
        self.assertEqual(reboot.prepare(JOB, "notify-0001", DIGEST, expected_state_version=7), first)
        self.assertEqual(len(reboot.list_entries(JOB)), 1)

    def test_payload_replay_conflict(self):
        self.prepare()
        with self.assertRaises(OutboxConflict):
            self.prepare(digest="b"*64)
        with self.assertRaises(OutboxConflict):
            self.store.prepare(JOB,"notify-0001",DIGEST,expected_state_version=8)

    def test_no_premature_side_effect_without_result_ready(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE jobs SET state='ACCEPTED' WHERE job_id=?", (JOB,))
        with self.assertRaises(OutboxRejected):
            self.prepare()
        self.assertEqual(self.store.list_entries(JOB), [])

    def test_state_version_guard_and_missing_job(self):
        with self.assertRaises(OutboxRejected):
            self.store.prepare(JOB,"notify-0001",DIGEST,expected_state_version=6)
        with self.assertRaises(OutboxRejected):
            self.store.prepare("JOB-"+"2"*32,"notify-0001",DIGEST,expected_state_version=7)
        self.assertEqual(self.store.list_entries(JOB), [])

    def test_durable_quota_with_replay_at_cap(self):
        x=self.prepare()
        y=self.prepare(key="notify-0002")
        self.assertNotEqual(x["operation_id"],y["operation_id"])
        with self.assertRaises(OutboxRejected):
            self.prepare(key="notify-0003")
        self.assertEqual(self.prepare(),x)
        self.assertEqual(len(self.store.list_entries(JOB)),2)

    def test_claim_exactly_once_under_concurrency(self):
        self.prepare()
        def claim(_):
            try:
                return self.store.claim(JOB, "notify-0001", expected_outbox_version=0)
            except OutboxConflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(claim,range(16)))
        winners=[r for r in results if r is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(len(winners[0]["dispatch_token"]),64)
        status=self.store.status(JOB,"notify-0001")
        self.assertEqual(status["delivery_state"],"IN_FLIGHT")
        self.assertEqual(status["attempt"],1)
        self.assertNotIn("dispatch_token", str(status))
        self.assertNotIn(winners[0]["dispatch_token"], str(self.store.list_entries(JOB)))

    def test_recovery_quarantines_ambiguous_send_not_resend(self):
        self.prepare()
        p=self.store.claim(JOB,"notify-0001",expected_outbox_version=0)
        self.assertEqual(p["delivery_state"],"IN_FLIGHT")
        reboot=OutboxStore(self.db,max_entries_per_job=2)
        affected=reboot.quarantine_in_flight()
        self.assertEqual(affected,1)
        self.assertEqual(reboot.quarantine_in_flight(),0)
        self.assertEqual(reboot.status(JOB,"notify-0001")["delivery_state"],"NEEDS_RECONCILIATION")
        with self.assertRaises(OutboxConflict):
            reboot.claim(JOB,"notify-0001",expected_outbox_version=1)
        self.assertFalse(hasattr(reboot,"mark_delivered"))
        self.assertFalse(hasattr(reboot,"resend"))
        self.assertFalse(hasattr(reboot,"dispatch_http"))

    def test_operator_is_not_allowed_to_fabricate_delivery(self):
        self.prepare()
        self.assertNotIn("DELIVERED", [x["delivery_state"] for x in self.store.list_entries(JOB)])
        for method in ("ack_delivery", "send", "retry", "mark_delivered", "trust_worker_receipt"):
            self.assertFalse(hasattr(self.store,method))

    def test_invalid_inputs_rejected_before_transaction(self):
        invalid_cases=[(JOB,"hi",DIGEST,7), (JOB,"../escapex",DIGEST,7),
                       (JOB,"notify-0001","D"*64,7), (JOB,"notify-0001",DIGEST,True),
                       ("../escape","notify-0001",DIGEST,7)]
        for job,key,digest,version in invalid_cases:
            with self.subTest(key=key,version=version), self.assertRaises(OutboxRejected):
                self.store.prepare(job,key,digest,expected_state_version=version)
        self.assertEqual(self.store.list_entries(JOB), [])

    def test_existing_admission_db_cannot_pretend_job_has_result(self):
        from minionone.admission import AdmissionStore
        db=Path(self.temp.name)/"real-admission.sqlite"
        token="trusted-"+("s"*48)
        adm=AdmissionStore(db,credentials={token:("p",{"t"})})
        job=adm.admit(token,"t",DIGEST,"registration-0001")
        store=OutboxStore(db)
        with self.assertRaises(OutboxRejected):
            store.prepare(job["job_id"],"notify-0001",DIGEST,expected_state_version=0)
        self.assertEqual(store.list_entries(job["job_id"]), [])

    def test_reject_existing_file_not_a_manager_db(self):
        with self.assertRaises(OutboxRejected):
            OutboxStore(Path(self.temp.name)/"missing.sqlite")

    def test_no_raw_payload_or_network_endpoint_stored(self):
        self.prepare()
        with sqlite3.connect(self.db) as con:
            cols=[r[1] for r in con.execute("PRAGMA table_info(outbox_intents)")]
            self.assertNotIn("prompt",cols)
            self.assertNotIn("body",cols)
            self.assertNotIn("recipient",cols)
            self.assertNotIn("url",cols)


if __name__ == "__main__":
    unittest.main()
