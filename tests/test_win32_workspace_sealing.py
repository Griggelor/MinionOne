"""Windows-native canonical admission tests; NOT target-host G1b certification."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from minionone.admission import AdmissionStore, InputRejected, IdempotencyConflict
from minionone.workspace_preparation import WorkspacePreparation
from minionone.win32_workspace_sealing import pinned_capture

TOKEN="native-windows-sealing-"+"w"*32


@unittest.skipUnless(sys.platform == "win32", "native NTFS admission evidence only")
class NativeWindowsSealTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="minionone-winseal-")
        self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name)
        self.project=self.home/"project"
        self.project.mkdir()
        self.db=self.home/"manager.sqlite"
        self.creds={TOKEN:("owner",{"thread"})}
        self.store=AdmissionStore(self.db,credentials=self.creds)
        self.workspace=WorkspacePreparation(self.store,{"demo":self.project})
        self.rec=self.workspace.reserve(TOKEN,"thread","demo","prepare-winseal-0001")
        self.job=self.rec["job_id"]
        self.input=Path(self.rec["input_dir"])
        self.output=Path(self.rec["output_dir"])
        self.task=b"Write a brief, entirely synthetic test report."
        self.sample=b"synthetic binary input"
        self.make_input()

    def make_input(self):
        (self.input/"TASK.md").write_bytes(self.task)
        (self.input/"sample.txt").write_bytes(self.sample)
        files={"TASK.md":hashlib.sha256(self.task).hexdigest(),
               "sample.txt":hashlib.sha256(self.sample).hexdigest()}
        (self.input/"manifest.json").write_text(json.dumps({"files":files}),
                                                encoding="utf-8")

    def seal(self,op="seal-win32-0001"):
        return self.workspace.seal(TOKEN,"thread",self.job,0,op)

    def canonical_rows(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE job_id=?",
                              (self.job,)).fetchone()[0]

    def test_admits_same_id_and_pins_exact_bytes(self):
        receipt=self.seal()
        self.assertEqual(receipt["job_id"],self.job)
        self.assertEqual((receipt["state"],receipt["version"]),("ACCEPTED",1))
        self.assertTrue(receipt["input_sealed"])
        self.assertFalse(receipt["worker_execution_enabled"])
        self.assertFalse(receipt["file_broker_verified"])
        self.assertEqual(self.canonical_rows(),1)
        with closing(sqlite3.connect(self.db)) as db:
            content=dict(db.execute("SELECT name,content FROM workspace_input_blobs "
                                    "WHERE job_id=?",(self.job,)).fetchall())
        self.assertEqual(content["TASK.md"],self.task)
        self.assertEqual(content["sample.txt"],self.sample)
        self.assertEqual(self.workspace.verify_input(TOKEN,"thread",self.job)
                         ["input_integrity"],"VERIFIED")
        self.assertEqual(self.store.status(TOKEN,"thread",self.job)["version"],1)
        log=(self.output/"job.log.jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(log)["status"],"ACCEPTED")
        self.assertNotIn(self.task.decode(),log)
        print("WIN32_CANONICAL_SEAL_ACCEPTED_SAME_JOB_ID")

    def test_manifest_wrong_digest_does_not_admit(self):
        (self.input/"sample.txt").write_bytes(b"changed without manifest")
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)
        self.assertEqual(self.workspace.status(TOKEN,"thread",self.job)["state"],
                         "PREPARING")

    def test_undeclared_input_rejected(self):
        (self.input/"rogue.txt").write_bytes(b"not in manifest")
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)

    def test_duplicate_manifest_keys_rejected(self):
        (self.input/"manifest.json").write_bytes(b'{"files":{"TASK.md":"a","TASK.md":"b"}}')
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)

    def test_bad_task_utf8_empty_and_size_denied(self):
        for task in (b" \r\n ",b"\x00x",b"\xff",b"x"*65537):
            with self.subTest(size=len(task)):
                self.task=task
                self.make_input()
                with self.assertRaises(InputRejected):self.seal()
                self.assertEqual(self.canonical_rows(),0)

    def test_manifest_quota_rejected(self):
        (self.input/"manifest.json").write_bytes(b"x"*32769)
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)

    def test_hardlink_rejected(self):
        external=self.home/"outside.txt"
        external.write_bytes(self.sample)
        (self.input/"sample.txt").unlink()
        os.link(external,self.input/"sample.txt")
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(external.read_bytes(),self.sample)
        self.assertEqual(self.canonical_rows(),0)

    def test_file_symlink_rejected(self):
        external=self.home/"outside.txt"
        external.write_bytes(self.sample)
        (self.input/"sample.txt").unlink()
        try:os.symlink(external,self.input/"sample.txt")
        except OSError as exc:self.skipTest("symlink unavailable: "+str(exc))
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(external.read_bytes(),self.sample)

    def test_input_directory_reparse_rejected(self):
        external=self.home/"outside-input"
        external.mkdir()
        old=self.input.with_name("input-old")
        self.input.rename(old)
        try:os.symlink(external,self.input,target_is_directory=True)
        except OSError as exc:self.skipTest("directory symlink unavailable: "+str(exc))
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)

    def test_pinned_input_prevents_file_rename_through_sqlite_commit(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM workspace_reservations WHERE job_id=?",
                           (self.job,)).fetchone()
        with pinned_capture(self.workspace,row) as (_data,_digests,_digest):
            with self.assertRaises(OSError):
                (self.input/"TASK.md").rename(self.input/"renamed.md")
            with self.assertRaises(OSError):
                self.input.rename(self.input.with_name("input-moved"))
        self.assertFalse((self.input/"renamed.md").exists())
        self.assertEqual(self.seal()["state"],"ACCEPTED")

    def test_after_seal_mutation_detected(self):
        self.seal()
        (self.input/"TASK.md").write_bytes(b"mutated after seal")
        with self.assertRaises(InputRejected):
            self.workspace.verify_input(TOKEN,"thread",self.job)
        self.assertEqual(self.canonical_rows(),1)

    def test_restart_same_operation_idempotent(self):
        first=self.seal()
        restored=WorkspacePreparation(
            AdmissionStore(self.db,credentials=self.creds),{"demo":self.project})
        second=restored.seal(TOKEN,"thread",self.job,0,"seal-win32-0001")
        self.assertEqual(first,second)
        with self.assertRaises(IdempotencyConflict):
            restored.seal(TOKEN,"thread",self.job,0,"seal-win32-0002")
        self.assertEqual(self.canonical_rows(),1)

    def test_repeated_seal_does_not_replace_tampered_log(self):
        self.seal()
        (self.output/"job.log.jsonl").write_bytes(b"adversarial log")
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual((self.output/"job.log.jsonl").read_bytes(),b"adversarial log")
        self.assertEqual(self.canonical_rows(),1)

    def test_same_operation_parallel_single_job(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.seal(),range(3)))
        self.assertEqual(len({r["input_digest"] for r in results}),1)
        self.assertEqual(self.canonical_rows(),1)

    def test_project_root_substitution_denied(self):
        moved=self.home/"old-project"
        self.project.rename(moved)
        self.project.mkdir()
        with self.assertRaises(InputRejected):self.seal()
        self.assertEqual(self.canonical_rows(),0)

    def test_task_only_supported(self):
        (self.input/"sample.txt").unlink()
        (self.input/"manifest.json").write_text(json.dumps({"files":{
            "TASK.md":hashlib.sha256(self.task).hexdigest()}}),encoding="utf-8")
        self.assertEqual(self.seal()["state"],"ACCEPTED")

    def test_no_worker_start_or_general_file_tool(self):
        self.seal()
        from minionone.mcp_server import LocalMCP
        server=LocalMCP(("127.0.0.1",0),token=TOKEN,
                        database_path=self.db,workspace_projects={"demo":self.project},
                        lmstudio_url="http://127.0.0.1:2")
        try:
            self.assertIn("minionone_seal_workspace",server.tool_map)
            self.assertNotIn("minionone_start_workspace_job",server.tool_map)
            self.assertFalse(server.call_tool("minionone_status",{})
                             ["workspace_execution_enabled"])
            fresh=server.call_tool("minionone_prepare_workspace",{
                "project_id":"demo","operation_id":"prepare-mcp-winseal-0001"})
            native_input=Path(fresh["input_dir"])
            (native_input/"TASK.md").write_bytes(self.task)
            (native_input/"manifest.json").write_text(json.dumps({"files":{
                "TASK.md":hashlib.sha256(self.task).hexdigest()}}),encoding="utf-8")
            accepted=server.call_tool("minionone_seal_workspace",{
                "job_id":fresh["job_id"],"expected_version":0,
                "operation_id":"seal-mcp-winseal-0001"})
            self.assertEqual(accepted["job_id"],fresh["job_id"])
            self.assertEqual(accepted["state"],"ACCEPTED")
            receipt=server.call_tool("minionone_verify_workspace_input",{
                "job_id":fresh["job_id"]})
            self.assertEqual(receipt["input_integrity"],"VERIFIED")
        finally:
            server.server_close()


class NonWindowsStillFailClosed(unittest.TestCase):
    @unittest.skipIf(sys.platform=="win32","POSIX-only check")
    def test_native_windows_capture_denied(self):
        with self.assertRaises(InputRejected):
            with pinned_capture(None,{}):
                pass


if __name__=="__main__":
    unittest.main(verbosity=2)
