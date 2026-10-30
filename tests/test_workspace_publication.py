"""Manager-only POSIX publication tests: no worker runtime, no Windows gate."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from minionone.admission import AdmissionStore, InputRejected, IdempotencyConflict, PermissionDenied
from minionone.workspace_preparation import WorkspacePreparation
from minionone.workspace_publication import WorkspacePublisher

TOKEN = "publish-test-" + "k" * 40

@unittest.skipIf(os.name == "nt", "native Windows output publisher intentionally unavailable")
class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.db = root / "state.sqlite"
        self.store = AdmissionStore(self.db, credentials={TOKEN:("owner", {"thread"})})
        self.workspace = WorkspacePreparation(self.store, {"demo":self.project})
        self.rec = self.workspace.reserve(TOKEN,"thread","demo","prepare-publish-0001")
        self.job = self.rec["job_id"]
        payload = b"synthetic task"
        inp = Path(self.rec["input_dir"])
        (inp / "TASK.md").write_bytes(payload)
        (inp / "manifest.json").write_text(json.dumps({"files":{
            "TASK.md":hashlib.sha256(payload).hexdigest()}}))
        self.workspace.seal(TOKEN,"thread",self.job,0,"seal-publish-0001")
        self.publisher = WorkspacePublisher(self.workspace)
        self.out = Path(self.rec["output_dir"])

    def stage(self, content=b"answer", name="answer.txt", op="publish-op-0001"):
        return self.publisher.stage(TOKEN,"thread",self.job,name,content,op)

    def publish(self, name="answer.txt", op="publish-op-0001"):
        return self.publisher.publish(TOKEN,"thread",self.job,name,op)

    def test_stage_pin_before_visibility(self):
        self.assertEqual(self.stage()["state"], "PENDING")
        self.assertFalse((self.out/"answer.txt").exists())
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT state FROM workspace_publications").fetchone()[0],"PENDING")

    def test_atomic_publish_repeat_restart(self):
        self.stage(b"hello")
        expected = self.publish()
        self.assertEqual(expected["state"],"PUBLISHED")
        self.assertEqual(self.publish(),expected)
        self.assertEqual((self.out/"answer.txt").read_bytes(),b"hello")
        restored = WorkspacePreparation(AdmissionStore(self.db,credentials={
            TOKEN:("owner",{"thread"})}),{"demo":self.project})
        restarted = WorkspacePublisher(restored)
        self.assertEqual(restarted.publish(TOKEN,"thread",self.job,"answer.txt",
                                           "publish-op-0001"),expected)
        self.assertEqual(self.stage(b"hello")["state"],"PUBLISHED")

    def test_retry_after_crash_before_final_visibility(self):
        self.stage(b"verified")
        def fault(point):
            if point == "AFTER_PENDING_FSYNC":
                raise RuntimeError("synthetic crash")
        self.publisher._fault_hook = fault
        with self.assertRaises(RuntimeError):self.publish()
        self.assertFalse((self.out/"answer.txt").exists())
        self.publisher._fault_hook = None
        self.assertEqual(self.publish()["state"],"PUBLISHED")
        self.assertEqual((self.out/"answer.txt").read_bytes(),b"verified")

    def test_retry_after_crash_after_atomic_link(self):
        self.stage()
        def fault(point):
            if point == "AFTER_ATOMIC_LINK":
                raise RuntimeError("synthetic crash")
        self.publisher._fault_hook = fault
        with self.assertRaises(RuntimeError):self.publish()
        self.assertEqual((self.out/"answer.txt").read_bytes(),b"answer")
        self.publisher._fault_hook = None
        self.assertEqual(self.publish()["state"],"PUBLISHED")
        self.assertEqual(set(x.name for x in self.out.iterdir()),
                         {"answer.txt","job.log.jsonl"})

    def test_retry_after_link_before_database_commit(self):
        self.stage()
        def fault(point):
            if point == "AFTER_VISIBLE_BEFORE_DB":
                raise RuntimeError("synthetic crash")
        self.publisher._fault_hook = fault
        with self.assertRaises(RuntimeError):self.publish()
        self.publisher._fault_hook = None
        self.assertEqual(self.publish()["state"],"PUBLISHED")

    def test_incomplete_pending_rejected(self):
        self.stage()
        def fault(point):
            if point == "AFTER_PENDING_FSYNC":
                raise RuntimeError("synthetic interruption")
        self.publisher._fault_hook = fault
        with self.assertRaises(RuntimeError):self.publish()
        pending = next(x for x in self.out.iterdir() if x.name.startswith(".pending-"))
        pending.write_bytes(b"truncated")
        self.publisher._fault_hook = None
        with self.assertRaises(InputRejected):self.publish()
        self.assertFalse((self.out/"answer.txt").exists())

    def test_existing_collision_not_overwritten(self):
        self.stage()
        target = self.out / "answer.txt"
        target.write_bytes(b"existing")
        with self.assertRaises(InputRejected):self.publish()
        self.assertEqual(target.read_bytes(),b"existing")

    def test_symlink_rejected(self):
        self.stage()
        outside = self.project / "private"
        outside.write_bytes(b"private")
        (self.out/"answer.txt").symlink_to(outside)
        with self.assertRaises(InputRejected):self.publish()
        self.assertEqual(outside.read_bytes(),b"private")

    def test_quota_and_idempotency_persisted(self):
        self.stage()
        with self.assertRaises(IdempotencyConflict):self.stage(b"changed")
        with self.assertRaises(IdempotencyConflict):
            self.stage(b"answer",name="other.txt")
        for i in range(1,16):
            self.stage(b"x",f"file-{i}.txt",f"publish-new-{i:04d}")
        with self.assertRaises(InputRejected):
            self.stage(b"x","extra.txt","publish-extra-001")
        with self.assertRaises(InputRejected):
            self.stage(b"x" * 1048577,"oversized.txt","publish-big-001")

    def test_invalid_names_and_nonstaged_rejected(self):
        for name in ("../x","folder/x","job.log.jsonl","NUL.txt","file:stream","bad."):
            with self.subTest(name=name),self.assertRaises(InputRejected):
                self.stage(name=name)
        with self.assertRaises(PermissionDenied):self.publish()

    def test_database_corruption_fails_closed(self):
        self.stage()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workspace_publications SET content=?",(b"tampered",))
        with self.assertRaises(InputRejected):self.publish()

    def test_missing_already_published_file_fails_closed(self):
        self.stage()
        self.publish()
        (self.out/"answer.txt").unlink()
        with self.assertRaises(InputRejected):self.publish()

    def test_parallel_publication_is_serialized(self):
        self.stage()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.publish(),range(3)))
        self.assertEqual([x["state"] for x in results],["PUBLISHED"]*3)

@unittest.skipUnless(os.name == "nt", "native Windows only")
class WindowsFailClosed(unittest.TestCase):
    def test_publish_disabled_on_windows(self):
        # The generic manager output publication MUST NOT become an NTFS writer.
        import minionone.workspace_sealing as seal
        self.assertNotEqual(seal._job_directory.__doc__,"native win32 publication")
        with self.assertRaises(InputRejected):
            with seal._job_directory(None,{},"output"):
                pass

if __name__ == "__main__":
    unittest.main()
