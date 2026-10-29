"""G2 test-first worker-attempt fencing contract (never invokes a model).

Fixtures create synthetic QUEUED jobs; the real pilot is ACCEPTED-only
and must reject reservations. SQLite handles are explicitly closed on Windows.
"""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from minionone.attempt_fence import AttemptFenceStore, AttemptDenied, AttemptConflict
from minionone.admission import AdmissionStore

JOB = "JOB-" + "1" * 32
OTHER = "JOB-" + "2" * 32
TOKEN = "local-admin-only-" + "x" * 48
DIGEST = "f" * 64


class AttemptFenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "manager.sqlite"
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL)")
            con.execute("INSERT INTO jobs VALUES (?,?,?)", (JOB, "QUEUED", 3))
            con.execute("INSERT INTO jobs VALUES (?,?,?)", (OTHER, "QUEUED", 3))
            con.commit()
        self.store = AttemptFenceStore(self.db)

    def reserve(self, op="reserve-0001", job=JOB, version=3):
        return self.store.reserve(job, operation_id=op, expected_job_version=version)

    def test_first_reservation_issues_unrepeatable_secret(self):
        issued = self.reserve()
        self.assertEqual(issued["attempt_epoch"], 1)
        self.assertEqual(issued["state"], "RESERVED")
        self.assertEqual(len(issued["capability"]), 64)
        self.assertTrue(self.store.is_current(JOB, issued["attempt_epoch"], issued["capability"]))
        view = self.store.status(JOB)
        self.assertNotIn("capability", str(view))
        self.assertNotIn(issued["capability"], str(view))
        second = self.reserve()
        self.assertTrue(second["replayed"])
        self.assertNotIn("capability", second)
        self.assertFalse(issued["replayed"])
        self.assertEqual(view["attempt_epoch"], 1)
        with closing(sqlite3.connect(self.db)) as con:
            columns = [r[1] for r in con.execute("PRAGMA table_info(attempt_fences)")]
            self.assertNotIn("capability", columns)
            self.assertNotIn("secret", columns)
            raw = con.execute("SELECT * FROM attempt_fences WHERE job_id=?", (JOB,)).fetchone()
            self.assertNotIn(issued["capability"], str(raw))

    def test_wrong_token_epoch_and_job_cannot_pass_fence(self):
        first = self.reserve()
        for job, epoch, secret in [
            (JOB, 0, first["capability"]),
            (JOB, 2, first["capability"]),
            (JOB, 1, "a" * 64),
            (OTHER, 1, first["capability"]),
            (JOB, 1, "not-a-capability"),
        ]:
            with self.subTest(job=job,epoch=epoch):
                self.assertFalse(self.store.is_current(job, epoch, secret))

    def test_no_second_reservation_or_key_repurpose(self):
        first = self.reserve()
        with self.assertRaises(AttemptConflict):
            self.reserve(op="reserve-0002")
        with self.assertRaises(AttemptConflict):
            self.reserve(version=4)
        self.assertTrue(self.store.is_current(JOB,1,first["capability"]))

    def test_concurrent_reservations_one_winner_and_no_duplicate_epoch(self):
        def task(i):
            try:
                return self.reserve(op="reserve-%04d" % (i+1000))
            except AttemptConflict:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(task,range(30)))
        winners=[x for x in results if x is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(winners[0]["attempt_epoch"],1)
        self.assertEqual(self.store.status(JOB)["attempt_epoch"],1)

    def test_queued_job_cas_must_hold(self):
        with self.assertRaises(AttemptDenied):
            self.reserve(version=2)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE jobs SET state='CANCEL_REQUESTED' WHERE job_id=?", (JOB,))
            con.commit()
        with self.assertRaises(AttemptDenied):
            self.reserve()
        self.assertIsNone(self.store.status(JOB))

    def test_canonical_job_change_invalidates_existing_fence(self):
        first = self.reserve()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE jobs SET state='CANCEL_REQUESTED', version=4 WHERE job_id=?", (JOB,))
            con.commit()
        self.assertFalse(self.store.is_current(JOB,1,first["capability"]))
        with self.assertRaises(AttemptConflict):
            self.reserve()

    def test_manager_revoke_invalidates_lease_without_reissue(self):
        first = self.reserve()
        revoked = self.store.revoke(JOB,expected_epoch=1)
        self.assertEqual(revoked["state"],"FENCED")
        self.assertFalse(self.store.is_current(JOB,1,first["capability"]))
        with self.assertRaises(AttemptConflict):
            self.reserve(op="reserve-0002")
        # Replaying the old reservation never resurrects its authority.
        replay = self.reserve()
        self.assertEqual(replay["state"],"FENCED")
        self.assertTrue(replay["replayed"])
        self.assertNotIn("capability",replay)
        self.assertEqual(self.store.revoke(JOB,expected_epoch=1),revoked)
        with self.assertRaises(AttemptConflict):
            self.store.revoke(JOB,expected_epoch=2)

    def test_crash_recovery_fences_inflight_and_never_redispatches(self):
        first=self.reserve()
        reboot=AttemptFenceStore(self.db)
        self.assertEqual(reboot.fence_unreconciled(),1)
        self.assertEqual(reboot.fence_unreconciled(),0)
        self.assertFalse(reboot.is_current(JOB,1,first["capability"]))
        self.assertEqual(reboot.status(JOB)["state"],"FENCED")
        with self.assertRaises(AttemptConflict):
            reboot.reserve(JOB,operation_id="reserve-0002",expected_job_version=3)
        self.assertFalse(hasattr(reboot,"launch"))
        self.assertFalse(hasattr(reboot,"complete"))
        self.assertFalse(hasattr(reboot,"resume"))
        self.assertFalse(hasattr(reboot,"publish"))

    def test_keyed_reservation_does_not_leak_capability_after_restart(self):
        issued = self.reserve()
        reopened = AttemptFenceStore(self.db)
        retry = reopened.reserve(JOB,operation_id="reserve-0001",expected_job_version=3)
        self.assertNotIn("capability", retry)
        self.assertEqual(retry["attempt_epoch"],1)
        self.assertTrue(retry["replayed"])
        self.assertTrue(self.store.is_current(JOB,1,issued["capability"]))

    def test_lifetime_quota_and_isolation(self):
        small=AttemptFenceStore(self.db,max_entries=1)
        small.reserve(JOB,operation_id="reserve-0001",expected_job_version=3)
        with self.assertRaises(AttemptDenied):
            small.reserve(OTHER,operation_id="reserve-0002",expected_job_version=3)
        self.assertIsNone(small.status(OTHER))
        # Key replay remains possible at quota without regranting secrets.
        self.assertTrue(small.reserve(JOB,operation_id="reserve-0001",expected_job_version=3)["replayed"])

    def test_invalid_data_fails_closed(self):
        bad=[(JOB,"abc",3),(JOB,"../bad-id",3),(JOB,"reserve-0001",True),
             ("../bad","reserve-0001",3),(OTHER,"reserve-0001",-1)]
        for job,op,version in bad:
            with self.subTest(job=job,op=op,version=version):
                with self.assertRaises(AttemptDenied):
                    self.store.reserve(job,operation_id=op,expected_job_version=version)
        for epoch,cap in [(True,"a"*64),(1,"B"*64),(1,None),(1,[])]:
            self.assertFalse(self.store.is_current(JOB,epoch,cap))
        self.assertIsNone(self.store.status(JOB))

    def test_current_pilot_admission_jobs_are_not_eligible(self):
        db=Path(self.tmp.name)/"pilot.sqlite"
        adm=AdmissionStore(db,credentials={TOKEN:("parent",{"chat"})})
        j=adm.admit(TOKEN,"chat",DIGEST,"admit-0001")
        ledger=AttemptFenceStore(db)
        with self.assertRaises(AttemptDenied):
            ledger.reserve(j["job_id"],operation_id="reserve-0001",expected_job_version=0)
        self.assertIsNone(ledger.status(j["job_id"]))

    def test_cannot_implicitly_create_database(self):
        with self.assertRaises(AttemptDenied):
            AttemptFenceStore(Path(self.tmp.name)/"not-present.sqlite")


if __name__ == "__main__":
    unittest.main()
