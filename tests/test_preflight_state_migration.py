"""G2 canonical pre-dispatch: real SQLite owner, legacy migration and MCP denial.

No worker can run. Tests challenge versioned state, rollback, foreign keys,
restarts, concurrency, and the existing v0.10 pilot's trusted boundaries.
"""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from minionone.admission import (
    AdmissionStore, InputRejected, IdempotencyConflict, VersionConflict,
)
from minionone.lifecycle import LifecycleRejected
from minionone.mcp_server import TOOLS

TOKEN="internal-preflight-"+"z"*48
DIGEST="b"*64


class PreflightCanonicalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"manager.db"
        self.store=AdmissionStore(self.path,credentials={TOKEN:("operator",{"thread"})})
        self.admitted=self.store.admit(TOKEN,"thread",DIGEST,"admit-0001")
        self.job=self.admitted["job_id"]

    def advance(self,to,ver,op):
        return self.store.advance_pre_dispatch(self.job,ver,op,to)

    def test_chain_uses_same_canonical_job_with_monotonic_log(self):
        a=self.advance("VALIDATING",0,"validate-0001")
        q=self.advance("QUEUED",1,"queue-0001")
        c=self.advance("RESOURCE_CHECK",2,"resource-0001")
        self.assertEqual([(a["state"],a["version"]),(q["state"],q["version"]),(c["state"],c["version"])],
                         [("VALIDATING",1),("QUEUED",2),("RESOURCE_CHECK",3)])
        self.assertEqual(self.store.status(TOKEN,"thread",self.job),c)
        rows=self.store.events(TOKEN,"thread",self.job)
        self.assertEqual([r["seq"] for r in rows],[1,2,3,4])
        self.assertEqual([r["status"] for r in rows],
                         ["ACCEPTED","VALIDATING","QUEUED","RESOURCE_CHECK"])
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM jobs").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT worker_started FROM jobs WHERE job_id=?",(self.job,)).fetchone()[0],0)

    def test_direct_queue_then_cas_abort_and_quiescent_cancel(self):
        queued=self.advance("QUEUED",0,"queue-0001")
        self.assertEqual(queued["version"],1)
        request=self.store.request_abort(TOKEN,"thread",self.job,1,"abort-0001")
        self.assertEqual((request["state"],request["version"]),("CANCEL_REQUESTED",2))
        done=self.store.confirm_quiescent_cancel(self.job,2,"cancel-0001")
        self.assertEqual((done["state"],done["version"]),("CANCELLED",3))
        self.assertEqual([x["seq"] for x in self.store.events(TOKEN,"thread",self.job)],
                         [1,2,3,4])
        with self.assertRaises(VersionConflict):
            self.advance("QUEUED",3,"queue-0002")

    def test_abort_from_validating_and_resource_check(self):
        for states in (["VALIDATING"], ["QUEUED","RESOURCE_CHECK"]):
            with self.subTest(states=states):
                job=self.store.admit(TOKEN,"thread",DIGEST,"admit-"+("-".join(states)))
                for i,state in enumerate(states):
                    self.store.advance_pre_dispatch(job["job_id"],i,
                                                    "stage-%d-%s"%(i,state),state)
                request=self.store.request_abort(TOKEN,"thread",job["job_id"],
                                                 len(states),"abort-"+states[-1])
                self.assertEqual(request["state"],"CANCEL_REQUESTED")
                cancelled=self.store.confirm_quiescent_cancel(
                    job["job_id"],len(states)+1,"complete-"+states[-1])
                self.assertEqual(cancelled["state"],"CANCELLED")
                events=self.store.events(TOKEN,"thread",job["job_id"])
                self.assertEqual([r["seq"] for r in events],list(range(1,len(events)+1)))

    def test_idempotent_replay_survives_restart_and_next_stage(self):
        first=self.advance("VALIDATING",0,"validate-0001")
        self.assertEqual(self.advance("VALIDATING",0,"validate-0001"),first)
        self.advance("QUEUED",1,"queue-0001")
        again=AdmissionStore(self.path,credentials={TOKEN:("operator",{"thread"})})
        self.assertEqual(again.advance_pre_dispatch(self.job,0,"validate-0001","VALIDATING"),first)
        self.assertEqual(len(again.events(TOKEN,"thread",self.job)),3)
        with self.assertRaises(IdempotencyConflict):
            again.advance_pre_dispatch(self.job,0,"validate-0001","QUEUED")

    def test_stale_cas_illegal_or_faked_target_fail_closed(self):
        for target in ("RUNNING","COMPLETED","FAILED","CANCELLED","INTEGRATING",
                       "NEEDS_RECONCILIATION","ACCEPTED","../QUEUED","resource_check"):
            with self.subTest(target=target),self.assertRaises(InputRejected):
                self.advance(target,0,"invalid-0001")
        self.advance("QUEUED",0,"queue-0001")
        for target in ("VALIDATING","QUEUED","RESOURCE_CHECK"):
            with self.subTest(target=target),self.assertRaises(VersionConflict):
                self.advance(target,0,"late-0001")
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM manager_transitions").fetchone()[0],1)

    def test_forged_types_are_denied(self):
        for val in (None,True,False,[],{},-1,0.0):
            with self.subTest(value=val):
                with self.assertRaises(InputRejected):
                    self.advance("QUEUED",val,"badver-0001")
        for val in (None,True,False,[],{},0,0.0):
            with self.subTest(state=val):
                with self.assertRaises(InputRejected):
                    self.advance(val,0,"badstage-0001")
        with self.assertRaises(InputRejected):
            self.advance("QUEUED",0,"../escape")
        with self.assertRaises(InputRejected):
            self.store.advance_pre_dispatch("../bad",0,"stage-0001","QUEUED")
        self.assertEqual(self.store.status(TOKEN,"thread",self.job)["state"],"ACCEPTED")

    def test_fault_injection_rolls_back_state_events_and_idempotency(self):
        with patch("minionone.admission.check_transition",
                   side_effect=LifecycleRejected("denied")):
            with self.assertRaises(LifecycleRejected):
                self.advance("QUEUED",0,"queue-0001")
        self.assertEqual(self.store.status(TOKEN,"thread",self.job)["version"],0)
        self.assertEqual(len(self.store.events(TOKEN,"thread",self.job)),1)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM manager_transitions").fetchone()[0],0)
        self.assertEqual(self.advance("QUEUED",0,"queue-0001")["state"],"QUEUED")

    def test_concurrent_queue_cas_one_winner(self):
        def task(i):
            try:
                self.advance("QUEUED",0,"queue-%04d"%i)
                return "won"
            except VersionConflict:
                return "lost"
        with ThreadPoolExecutor(max_workers=8) as pool:
            got=list(pool.map(task,range(16)))
        self.assertEqual(got.count("won"),1)
        self.assertEqual(got.count("lost"),15)
        self.assertEqual(self.store.status(TOKEN,"thread",self.job)["version"],1)
        self.assertEqual(len(self.store.events(TOKEN,"thread",self.job)),2)

    def test_actual_local_mcp_status_and_abort_use_canonical_manager_state(self):
        from minionone.mcp_server import LocalMCP
        server=LocalMCP(("127.0.0.1",0),token=TOKEN,database_path=self.path,
                        principal="operator",thread="thread")
        self.addCleanup(server.server_close)
        stage=server.store.advance_pre_dispatch(self.job,0,"queue-0001","QUEUED")
        self.assertEqual(stage["state"],"QUEUED")
        status=server.call_tool("minionone_job_status",{"job_id":self.job})
        self.assertEqual((status["state"],status["version"]),("QUEUED",1))
        self.assertFalse(server.call_tool("minionone_status",{})["worker_execution_enabled"])
        abort=server.call_tool("minionone_abort",{
            "job_id":self.job,"expected_version":1,"operation_id":"abort-0001"})
        self.assertEqual(abort["state"],"CANCEL_REQUESTED")
        rows=server.call_tool("minionone_job_events",{"job_id":self.job})["events"]
        self.assertEqual([r["seq"] for r in rows],[1,2,3])
        self.assertEqual([r["status"] for r in rows],
                         ["ACCEPTED","QUEUED","CANCEL_REQUESTED"])

    def test_no_mcp_or_worker_privilege_added(self):
        names={t["name"] for t in TOOLS}
        self.assertNotIn("minionone_advance_pre_dispatch",names)
        self.assertNotIn("minionone_run",names)
        self.assertFalse(hasattr(self.store,"run_worker"))
        self.assertFalse(hasattr(self.store,"mark_completed"))
        with closing(sqlite3.connect(self.path)) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE jobs SET worker_started=1 WHERE job_id=?",(self.job,))

    def test_foreign_keys_and_outbox_still_valid_after_migration(self):
        from minionone.attempt_fence import AttemptFenceStore
        from minionone.outbox import OutboxStore
        OutboxStore(self.path)
        AttemptFenceStore(self.path)
        self.advance("QUEUED",0,"queue-0001")
        fence=AttemptFenceStore(self.path).reserve(
            self.job,operation_id="reserve-0001",expected_job_version=1)
        self.assertEqual(fence["attempt_epoch"],1)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(),[])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE jobs SET worker_started=1 WHERE job_id=?",(self.job,))


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"legacy.sqlite"

    def make_legacy(self):
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs(
              job_id TEXT PRIMARY KEY, principal TEXT NOT NULL, thread_id TEXT NOT NULL,
              input_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                ('ACCEPTED','CANCEL_REQUESTED','CANCELLED')),
              version INTEGER NOT NULL CHECK(version>=0),
              worker_started INTEGER NOT NULL DEFAULT 0 CHECK(worker_started=0),
              created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
            CREATE TABLE admissions(
              principal TEXT NOT NULL, operation_id TEXT NOT NULL,
              request_hash TEXT NOT NULL, job_id TEXT NOT NULL, receipt TEXT NOT NULL,
              PRIMARY KEY(principal,operation_id), FOREIGN KEY(job_id) REFERENCES jobs(job_id));
            CREATE TABLE admission_events(
              job_id TEXT NOT NULL,seq INTEGER NOT NULL,status TEXT NOT NULL,
              operation_id TEXT NOT NULL,actor TEXT NOT NULL,occurred_utc TEXT NOT NULL,
              PRIMARY KEY(job_id,seq),FOREIGN KEY(job_id) REFERENCES jobs(job_id));
            INSERT INTO jobs VALUES
              ('JOB-legacy001','operator','thread','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
              'ACCEPTED',0,0,'t','t');
            INSERT INTO admissions VALUES
              ('operator','old-unique','hash','JOB-legacy001','receipt');
            INSERT INTO admission_events VALUES
              ('JOB-legacy001',1,'ACCEPTED','old-unique','MANAGER','t');
            """)
            con.commit()

    def test_migrate_prealpha_with_child_references_without_data_loss(self):
        self.make_legacy()
        from minionone.outbox import OutboxStore
        from minionone.attempt_fence import AttemptFenceStore
        # Existing v0.10 optional tables also reference the jobs parent.
        OutboxStore(self.path)
        AttemptFenceStore(self.path)
        with closing(sqlite3.connect(self.path)) as con:
            # Simulated historical unresolved child effects: migration must
            # preserve their FK references and quarantine metadata untouched.
            con.execute("""INSERT INTO outbox_intents
              (job_id,operation_id,payload_sha256,job_state_version,
               delivery_state,outbox_version,attempts,dispatch_token_hash,
               receipt_json,created_utc,updated_utc)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              ("JOB-legacy001","notify-legacy1",DIGEST,0,
               "NEEDS_RECONCILIATION",2,1,"c"*64,"{}",'t','t'))
            con.execute("""INSERT INTO attempt_fences
              (job_id,operation_id,job_version,attempt_epoch,status,
               capability_sha256,created_utc,updated_utc)
              VALUES (?,?,?,?,?,?,?,?)""",
              ("JOB-legacy001","reserve-legacy1",0,1,"FENCED","d"*64,"t","t"))
            con.commit()
        store=AdmissionStore(self.path,credentials={TOKEN:("operator",{"thread"})})
        self.assertEqual(store.status(TOKEN,"thread","JOB-legacy001")["state"],"ACCEPTED")
        updated=store.advance_pre_dispatch("JOB-legacy001",0,"validate-0001","VALIDATING")
        self.assertEqual(updated["state"],"VALIDATING")
        store2=AdmissionStore(self.path,credentials={TOKEN:("operator",{"thread"})})
        self.assertEqual(store2.status(TOKEN,"thread","JOB-legacy001")["version"],1)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM admissions").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM admission_events").fetchone()[0],2)
            self.assertEqual(con.execute("SELECT count(*) FROM outbox_intents").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM attempt_fences").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT delivery_state FROM outbox_intents").fetchone()[0],
                             "NEEDS_RECONCILIATION")
            self.assertEqual(con.execute("SELECT status FROM attempt_fences").fetchone()[0],
                             "FENCED")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(con.execute("SELECT worker_started FROM jobs").fetchone()[0],0)

    def test_broken_foreign_keys_abort_migration_without_dropping_source(self):
        self.make_legacy()
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("INSERT INTO admission_events VALUES ('JOB-absent',1,'X','op','ACTOR','t')")
            con.commit()
        with self.assertRaises(InputRejected):
            AdmissionStore(self.path,credentials={TOKEN:("operator",{"thread"})})
        with closing(sqlite3.connect(self.path)) as con:
            self.assertIn("CANCEL_REQUESTED",con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()[0])
            self.assertNotIn("'VALIDATING'",con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()[0])
            self.assertEqual(con.execute("SELECT count(*) FROM jobs").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
