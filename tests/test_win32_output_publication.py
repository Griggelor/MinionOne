"""Native Windows manager-only output publication adversarial proof.

Synthetic ADMITTED rows are seeded explicitly because Windows input sealing and
worker dispatch MUST remain unavailable. Not a real product end-to-end test.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from minionone.admission import AdmissionStore, InputRejected, IdempotencyConflict, PermissionDenied
from minionone.workspace_preparation import WorkspacePreparation
from minionone.win32_output_publication import WindowsOutputPublisher, _output_directory

TOKEN="native-output-publisher-"+"z"*32

@unittest.skipUnless(sys.platform=="win32", "native NTFS test only")
class WindowsOutputPublisherTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="minionone-output-manager-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.project=self.root/"project"
        self.project.mkdir()
        self.db=self.root/"manager.sqlite"
        self.creds={TOKEN:("owner",{"thread"})}
        self.store=AdmissionStore(self.db,credentials=self.creds)
        self.workspace=WorkspacePreparation(self.store,{"demo":self.project})
        self.row=self.workspace.reserve(TOKEN,"thread","demo","prepare-output-00001")
        self.job=self.row["job_id"]
        self.out=Path(self.row["output_dir"])
        # TEST-ONLY. This is NOT a valid Windows seal. There is deliberately
        # no public feature for caller-supplied preadmission bypass.
        now=datetime.now(timezone.utc).isoformat()
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO jobs
                (job_id,principal,thread_id,input_digest,state,version,created_utc,updated_utc)
                VALUES (?,?,?,?,?,?,?,?)""",
                (self.job,"owner","thread","a"*64,"ACCEPTED",1,now,now))
            db.execute("UPDATE workspace_reservations SET state='ADMITTED' WHERE job_id=?",
                       (self.job,))
        self.publisher=WindowsOutputPublisher(self.workspace)

    def stage(self,data=b"synthetic result",name="result.txt",op="output-operation-0001"):
        return self.publisher.stage(TOKEN,"thread",self.job,name,data,op)

    def publish(self,name="result.txt",op="output-operation-0001"):
        return self.publisher.publish(TOKEN,"thread",self.job,name,op)

    def test_durable_stage_does_not_publish(self):
        result=self.stage()
        self.assertEqual(result["state"],"PENDING")
        self.assertFalse((self.out/"result.txt").exists())
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT state FROM workspace_publications").fetchone()[0],
                             "PENDING")

    def test_approved_output_directory_stays_pinned_against_rename(self):
        self.stage()
        approved=self.publisher._row(TOKEN,"thread",self.job)
        with _output_directory(self.workspace,approved):
            with self.assertRaises(OSError):
                self.out.rename(self.out.with_name("output-escaped"))
        self.assertTrue(self.out.is_dir())
        self.assertFalse(self.out.with_name("output-escaped").exists())

    def test_atomic_final_and_repeat_after_restart(self):
        self.stage(b"\x00result\xff")
        receipt=self.publish()
        self.assertEqual(receipt["state"],"PUBLISHED")
        self.assertEqual((self.out/"result.txt").read_bytes(),b"\x00result\xff")
        self.assertEqual(self.publish(),receipt)
        restarted=WindowsOutputPublisher(WorkspacePreparation(
            AdmissionStore(self.db,credentials=self.creds),{"demo":self.project}))
        self.assertEqual(restarted.publish(TOKEN,"thread",self.job,"result.txt",
                                           "output-operation-0001"),receipt)
        self.assertEqual(self.stage(b"\x00result\xff")["state"],"PUBLISHED")
        self.assertFalse(any(x.name.startswith("pending-") for x in self.out.iterdir()))

    def test_crash_after_pending_fsync_is_replayable(self):
        self.stage()
        def crash(point):
            if point=="AFTER_PENDING_FSYNC":
                raise RuntimeError("injected power-loss point")
        self.publisher._fault_hook=crash
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertFalse((self.out/"result.txt").exists())
        self.assertEqual(len(list(self.out.glob("pending-*.part"))),1)
        self.publisher._fault_hook=None
        self.assertEqual(self.publish()["state"],"PUBLISHED")

    def test_crash_after_rename_before_database_commit(self):
        self.stage()
        self.publisher._fault_hook=lambda pt: (_ for _ in ()).throw(
            RuntimeError("injected crash")) if pt=="AFTER_ATOMIC_RENAME" else None
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertEqual((self.out/"result.txt").read_bytes(),b"synthetic result")
        self.publisher._fault_hook=None
        self.assertEqual(self.publish()["state"],"PUBLISHED")

    def test_crash_after_visibility_before_commit(self):
        self.stage()
        self.publisher._fault_hook=lambda pt: (_ for _ in ()).throw(
            RuntimeError("injected crash")) if pt=="AFTER_VISIBLE_BEFORE_DB" else None
        with self.assertRaises(RuntimeError):
            self.publish()
        self.publisher._fault_hook=None
        self.assertEqual(self.publish()["state"],"PUBLISHED")

    def test_incomplete_pending_blocks_visibility(self):
        self.stage()
        self.publisher._fault_hook=lambda pt: (_ for _ in ()).throw(
            RuntimeError("injected crash")) if pt=="AFTER_PENDING_FSYNC" else None
        with self.assertRaises(RuntimeError):
            self.publish()
        pending=next(self.out.glob("pending-*.part"))
        pending.write_bytes(b"partial")
        self.publisher._fault_hook=None
        with self.assertRaises(InputRejected):
            self.publish()
        self.assertFalse((self.out/"result.txt").exists())

    def test_existing_final_is_not_overwritten(self):
        self.stage()
        (self.out/"result.txt").write_bytes(b"existing")
        with self.assertRaises(InputRejected):
            self.publish()
        self.assertEqual((self.out/"result.txt").read_bytes(),b"existing")

    def test_final_symlink_and_hardlink_denied(self):
        external=self.root/"external.txt"
        external.write_bytes(b"external")
        self.stage()
        os.link(external,self.out/"result.txt")
        with self.assertRaises(InputRejected):
            self.publish()
        self.assertEqual(external.read_bytes(),b"external")

    def test_directory_junction_or_symlink_denied(self):
        outside=self.root/"outside"
        outside.mkdir()
        self.out.rename(self.out.with_name("output-old"))
        try:
            os.symlink(outside,self.out,target_is_directory=True)
        except (OSError,NotImplementedError) as exc:
            self.skipTest("symlinks unavailable: "+type(exc).__name__)
        self.stage()
        with self.assertRaises(InputRejected):
            self.publish()
        self.assertEqual(list(outside.iterdir()),[])

    def test_output_scope_invalid_names_and_foreign_principal(self):
        for name in ("../input/TASK.md",r"\\server\share","file:ads",
                     "job.log.jsonl","NUL","pending-sneaky.part","report.txt."):
            with self.subTest(name=name),self.assertRaises(InputRejected):
                self.stage(name=name)
        self.stage()
        with self.assertRaises(PermissionDenied):
            self.publisher.publish("wrong-token","thread",self.job,
                                   "result.txt","output-operation-0001")

    def test_durable_idempotency_quotas(self):
        self.stage()
        with self.assertRaises(IdempotencyConflict):
            self.stage(b"different")
        for i in range(1,16):
            self.stage(b"x",f"part-{i}.txt",f"output-new-{i:04d}")
        with self.assertRaises(InputRejected):
            self.stage(b"x","extra.txt","output-extra-0001")
        with self.assertRaises(InputRejected):
            self.stage(b"x"*1048577,"too-big.txt","output-big-00001")

    def test_corrupt_sqlite_blob_and_deleted_final_denied(self):
        self.stage()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE workspace_publications SET content=?",(b"tampered",))
        with self.assertRaises(InputRejected):
            self.publish()

    def test_deleted_published_file_denied(self):
        self.stage()
        self.publish()
        (self.out/"result.txt").unlink()
        with self.assertRaises(InputRejected):
            self.publish()

    def test_multiple_publishers_serialize_with_sqlite(self):
        self.stage()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.publish(),range(3)))
        self.assertEqual([r["state"] for r in results],["PUBLISHED"]*3)

    def test_zero_length_and_multichunk(self):
        self.stage(b"","zero.bin","output-operation-zero")
        self.assertEqual(self.publish("zero.bin","output-operation-zero")["size"],0)
        self.stage(b"Z"*130001,"large.bin","output-operation-large")
        self.assertEqual(self.publish("large.bin","output-operation-large")["size"],130001)
        self.assertEqual((self.out/"large.bin").stat().st_size,130001)

class OtherPlatformDenied(unittest.TestCase):
    @unittest.skipIf(sys.platform=="win32","non-Windows check only")
    def test_native_windows_output_unavailable_on_posix(self):
        with self.assertRaises(InputRejected):
            with _output_directory(None,{}):
                pass

if __name__=="__main__":
    unittest.main(verbosity=2)
