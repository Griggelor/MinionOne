"""Timeout/resume control plane: real SQLite, synthetic prior worker effect only.

No actual provider, file broker, or model is started by these tests.
"""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from minionone.admission import (
    AdmissionStore, InputRejected, PermissionDenied, VersionConflict, IdempotencyConflict
)
from minionone.attempt_fence import AttemptFenceStore
from minionone.timeout_control import TimeoutControlStore, TimeoutControlDenied
from minionone.mcp_server import LocalMCP, TOOLS

TOKEN="synthetic-parent-"+"x"*48
WRONG="synthetic-wrong-"+"y"*48
DIGEST="b"*64
CHECKPOINT="c"*64


class DurableTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"jobs.sqlite"
        self.auth={TOKEN:("parent",{"thread"}),WRONG:("outsider",{"other"})}
        self.owner=AdmissionStore(self.db,credentials=self.auth)
        self.job=self.owner.admit(TOKEN,"thread",DIGEST,"admit-0001")["job_id"]
        self.owner.advance_pre_dispatch(self.job,0,"queue-0001","QUEUED")
        self.fence=AttemptFenceStore(self.db)
        self.fence.reserve(self.job,operation_id="attempt-0001",expected_job_version=1)
        self.fence.revoke(self.job,expected_epoch=1)
        # TEST ONLY: fake a former RUNNING attempt. Never a real worker.
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE jobs SET state='RUNNING',version=2 WHERE job_id=?",(self.job,))
            con.commit()
        self.control=TimeoutControlStore(self.owner)

    def timeout(self,quiescent=True,checkpoint=CHECKPOINT,op="timeout-0001"):
        return self.control.record_timeout(self.job,expected_version=2,
             attempt_epoch=1,worker_quiescent=quiescent,
             checkpoint_sha256=checkpoint,operation_id=op)

    def test_valid_canonical_interruption_and_durable_status(self):
        first=self.timeout()
        self.assertEqual((first["state"],first["error_code"],first["version"]),
                         ("INTERRUPTED","TIMEOUT",3))
        self.assertTrue(first["resume_available"])
        self.assertFalse(first["resume_requested"])
        self.assertFalse(first["worker_running"])
        reopened=TimeoutControlStore(AdmissionStore(self.db,credentials=self.auth))
        v=reopened.status(TOKEN,"thread",self.job)
        self.assertEqual(v["state"],"INTERRUPTED")
        self.assertEqual(v["error_code"],"TIMEOUT")
        self.assertTrue(v["resume_available"])
        self.assertNotIn(CHECKPOINT,str(v))
        self.assertEqual(self.owner.status(TOKEN,"thread",self.job)["state"],"INTERRUPTED")

    def test_resume_is_durable_request_not_unverified_worker_restart(self):
        self.timeout()
        receipt=self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001")
        self.assertEqual(receipt["state"],"INTERRUPTED")
        self.assertEqual(receipt["version"],4)
        self.assertTrue(receipt["resume_requested"])
        self.assertFalse(receipt["worker_running"])
        self.assertFalse(receipt["resume_started"])
        self.assertEqual(self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001"),receipt)
        after=TimeoutControlStore(AdmissionStore(self.db,credentials=self.auth))
        self.assertEqual(after.status(TOKEN,"thread",self.job),receipt)
        with closing(sqlite3.connect(self.db)) as con:
            rows=con.execute("SELECT status,actor FROM admission_events WHERE job_id=? ORDER BY seq",
                             (self.job,)).fetchall()
            self.assertEqual(rows[-2:],[
                ("INTERRUPTED","MANAGER"),
                ("INTERRUPTED","AUTHENTICATED_PARENT")
            ])
            self.assertEqual(con.execute("SELECT worker_started FROM jobs WHERE job_id=?",
                          (self.job,)).fetchone()[0],0)

    def test_uncertain_worker_is_quarantined_never_resume_eligible(self):
        receipt=self.timeout(quiescent=False)
        self.assertEqual(receipt["state"],"NEEDS_RECONCILIATION")
        self.assertEqual(receipt["error_code"],"TIMEOUT")
        self.assertFalse(receipt["resume_available"])
        self.assertFalse(receipt["resume_requested"])
        self.assertIsNone(receipt["worker_running"])
        with self.assertRaises(TimeoutControlDenied):
            self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001")
        quarantined=self.control.status(TOKEN,"thread",self.job)
        self.assertEqual(quarantined["state"],"NEEDS_RECONCILIATION")
        self.assertIsNone(quarantined["worker_running"])

    def test_unverified_checkpoint_is_not_recoverable(self):
        r=self.timeout(checkpoint=None)
        self.assertEqual(r["state"],"INTERRUPTED")
        self.assertFalse(r["resume_available"])
        with self.assertRaises(TimeoutControlDenied):
            self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001")

    def test_raw_worker_lease_must_be_fenced_first(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE attempt_fences SET status='RESERVED' WHERE job_id=?",
                        (self.job,))
            con.commit()
        with self.assertRaises(TimeoutControlDenied):
            self.timeout()
        self.assertEqual(self.owner.status(TOKEN,"thread",self.job)["state"],"RUNNING")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM timeout_records").fetchone()[0],0)

    def test_status_never_offers_resume_if_fence_becomes_active(self):
        self.timeout()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE attempt_fences SET status='RESERVED' WHERE job_id=?",
                        (self.job,))
            con.commit()
        status=self.control.status(TOKEN,"thread",self.job)
        self.assertFalse(status["resume_available"])
        with self.assertRaises(TimeoutControlDenied):
            self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001")

    def test_idempotent_timeout_replay_no_duplicate_events(self):
        a=self.timeout()
        self.assertEqual(self.timeout(),a)
        with self.assertRaises(IdempotencyConflict):
            self.timeout(checkpoint=None)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM timeout_records").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM admission_events WHERE job_id=?",
                                        (self.job,)).fetchone()[0],3)

    def test_owner_auth_and_stale_cas(self):
        self.timeout()
        with self.assertRaises(PermissionDenied):
            self.control.status(WRONG,"other",self.job)
        with self.assertRaises(PermissionDenied):
            self.control.request_resume(WRONG,"other",self.job,3,"resume-0001")
        with self.assertRaises(VersionConflict):
            self.control.request_resume(TOKEN,"thread",self.job,2,"resume-0001")
        with self.assertRaises(IdempotencyConflict):
            self.control.request_resume(TOKEN,"thread",self.job,3,"resume-0001")
            self.control.request_resume(TOKEN,"thread",self.job,4,"resume-0001")

    def test_concurrent_resume_single_winner(self):
        self.timeout()
        def task(i):
            try:
                return self.control.request_resume(TOKEN,"thread",self.job,3,
                                                  "resume-%04d"%i)
            except VersionConflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            got=list(pool.map(task,range(16)))
        winners=[x for x in got if x]
        self.assertEqual(len(winners),1)
        self.assertTrue(winners[0]["resume_requested"])
        self.assertEqual(self.control.status(TOKEN,"thread",self.job)["version"],4)

    def test_bad_or_forged_timeout_data_fails_closed(self):
        for version in (False,-1,"2",None):
            with self.subTest(version=version),self.assertRaises(InputRejected):
                self.control.record_timeout(self.job,expected_version=version,
                     attempt_epoch=1,worker_quiescent=True,
                     checkpoint_sha256=CHECKPOINT,operation_id="timeout-0001")
        for digest in ("../passwd","d"*63,True,[],{}):
            with self.subTest(digest=digest),self.assertRaises(InputRejected):
                self.timeout(checkpoint=digest)
        with self.assertRaises(InputRejected):
            self.control.record_timeout(self.job,expected_version=2,
                 attempt_epoch=True,worker_quiescent=True,
                 checkpoint_sha256=CHECKPOINT,operation_id="timeout-0001")
        with self.assertRaises(InputRejected):
            self.control.record_timeout(self.job,expected_version=2,
                 attempt_epoch=1,worker_quiescent="true",
                 checkpoint_sha256=CHECKPOINT,operation_id="timeout-0001")
        self.assertEqual(self.owner.status(TOKEN,"thread",self.job)["state"],"RUNNING")

    def test_parent_may_cancel_interrupted_job_instead_of_resuming(self):
        self.timeout()
        cancelled_request=self.owner.request_abort(
            TOKEN,"thread",self.job,3,"abort-0001")
        self.assertEqual(cancelled_request["state"],"CANCEL_REQUESTED")
        cancelled=self.owner.confirm_quiescent_cancel(self.job,4,"cancel-0001")
        self.assertEqual(cancelled["state"],"CANCELLED")
        control=self.control.status(TOKEN,"thread",self.job)
        self.assertFalse(control["resume_available"])
        with self.assertRaises(TimeoutControlDenied):
            self.control.request_resume(TOKEN,"thread",self.job,5,"resume-0001")

    def test_no_new_mcp_execute_or_resume_dispatch_and_synthetic_ui(self):
        names={tool["name"] for tool in TOOLS}
        self.assertIn("minionone_resume_request",names)
        self.assertNotIn("minionone_resume_worker",names)
        self.assertFalse(hasattr(self.control,"start_worker"))
        self.timeout()
        server=LocalMCP(("127.0.0.1",0),token=TOKEN,database_path=self.db,
                        principal="parent",thread="thread")
        self.addCleanup(server.server_close)
        status=server.call_tool("minionone_job_status",{"job_id":self.job})
        self.assertEqual(status["state"],"INTERRUPTED")
        self.assertEqual(status["timeout"]["error_code"],"TIMEOUT")
        self.assertTrue(status["timeout"]["resume_available"])
        res=server.call_tool("minionone_resume_request",{
            "job_id":self.job,"expected_version":3,"operation_id":"resume-0001"})
        self.assertTrue(res["resume_requested"])
        self.assertFalse(res["resume_started"])
        self.assertEqual(server.call_tool("minionone_job_status",{"job_id":self.job})["state"],
                         "INTERRUPTED")
        self.assertFalse(server.call_tool("minionone_status",{})["worker_execution_enabled"])


class HistoricalSixStateMigrationTests(unittest.TestCase):
    def test_v013_jobs_schema_migrates_with_existing_children(self):
        with tempfile.TemporaryDirectory() as root:
            db=Path(root)/"existing.sqlite"
            with closing(sqlite3.connect(db)) as con:
                con.executescript("""PRAGMA foreign_keys=ON;
                CREATE TABLE jobs (
                  job_id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                  thread_id TEXT NOT NULL, input_digest TEXT NOT NULL,
                  state TEXT NOT NULL CHECK(state IN
                    ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',
                     'CANCEL_REQUESTED','CANCELLED')),
                  version INTEGER NOT NULL CHECK(version>=0),
                  worker_started INTEGER NOT NULL DEFAULT 0 CHECK(worker_started=0),
                  created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
                CREATE TABLE admission_events(
                  job_id TEXT NOT NULL,seq INTEGER NOT NULL,status TEXT NOT NULL,
                  operation_id TEXT NOT NULL,actor TEXT NOT NULL,
                  occurred_utc TEXT NOT NULL,PRIMARY KEY(job_id,seq),
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id));
                INSERT INTO jobs VALUES
                 ('JOB-existing001','parent','thread',
                  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                  'QUEUED',2,0,'t','t');
                INSERT INTO admission_events VALUES
                 ('JOB-existing001',1,'ACCEPTED','admit-0001','MANAGER','t');
                """)
                con.commit()
            owner=AdmissionStore(db,credentials={TOKEN:("parent",{"thread"})})
            self.assertEqual(owner.status(TOKEN,"thread","JOB-existing001")["state"],"QUEUED")
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertEqual(con.execute("SELECT count(*) FROM admission_events").fetchone()[0],1)
                con.execute("UPDATE jobs SET state='INTERRUPTED' WHERE job_id='JOB-existing001'")
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute("UPDATE jobs SET worker_started=1 WHERE job_id='JOB-existing001'")

if __name__=="__main__":
    unittest.main()
