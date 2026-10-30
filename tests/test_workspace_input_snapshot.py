"""v0.26 sealed snapshot read contract, including real MCP and corruption."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from minionone.admission import AdmissionStore, InputRejected, PermissionDenied
from minionone.mcp_server import LocalMCP
from minionone.workspace_input_snapshot import SealedWorkspaceInput
from minionone.workspace_preparation import WorkspacePreparation

TOKEN = "workspace-v026-" + "W" * 48
THREAD = "v026-thread"


def prepare(root, store=None):
    project = root / "project"
    project.mkdir()
    manager = store or AdmissionStore(root / "manager.sqlite",
        credentials={TOKEN: ("v026-principal", {THREAD})})
    workspace = WorkspacePreparation(manager, {"demo": project})
    receipt = workspace.reserve(TOKEN, THREAD, "demo", "prepare-v026-001")
    data = {"TASK.md": b"Summarize input.txt", "input.txt": b"Hello, worker.\n"}
    folder = Path(receipt["input_dir"])
    for name, content in data.items():
        (folder / name).write_bytes(content)
    (folder / "manifest.json").write_text(json.dumps({
        "files": {name: hashlib.sha256(content).hexdigest()
                  for name, content in data.items()}}), encoding="utf-8")
    seal = workspace.seal(TOKEN, THREAD, receipt["job_id"], 0,
                          "seal-v026-00001")
    return workspace, receipt, seal


class SealedInputSnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspace, self.prep, self.seal = prepare(self.root)
        self.job = self.prep["job_id"]
        self.reader = SealedWorkspaceInput(self.workspace)

    def test_authentic_snapshot_and_repeatable_bytes(self):
        meta = self.reader.metadata(TOKEN, THREAD, self.job)
        self.assertEqual(meta["input_digest"], self.seal["input_digest"])
        self.assertEqual(meta["source"], "sealed_manager_sqlite_snapshot")
        self.assertEqual(meta["on_disk_integrity"], "NOT_RECHECKED")
        self.assertEqual([x["name"] for x in meta["files"]],
                         ["TASK.md", "input.txt"])
        self.assertEqual(self.reader.read_bytes(TOKEN, THREAD, self.job, "input.txt"),
                         b"Hello, worker.\n")
        (Path(self.prep["input_dir"]) / "input.txt").write_text("TAMPERED")
        self.assertEqual(self.reader.read_bytes(TOKEN, THREAD, self.job, "input.txt"),
                         b"Hello, worker.\n")
        with self.assertRaises(InputRejected):
            self.workspace.verify_input(TOKEN, THREAD, self.job)

    def test_credentials_and_scopes_are_checked_each_time(self):
        with self.assertRaises(PermissionDenied):
            self.reader.metadata("Z" * 64, THREAD, self.job)
        with self.assertRaises(PermissionDenied):
            self.reader.read_bytes(TOKEN, "other-thread", self.job, "TASK.md")
        with self.assertRaises((InputRejected, PermissionDenied)):
            self.reader.metadata(TOKEN, THREAD, "JOB-" + "a" * 32)

    def test_no_arbitrary_path_or_manifest_document(self):
        for name in ("../secret", "input.txt/../x", "output.txt",
                     "manifest.json", "C:secret", "\\\\server\\share"):
            with self.subTest(name=name), self.assertRaises(InputRejected):
                self.reader.read_bytes(TOKEN, THREAD, self.job, name)

    def test_tampered_sqlite_blob_or_digest_fails_closed(self):
        with self.workspace.store._connection() as con:
            con.execute("UPDATE workspace_input_blobs SET content=? "
                        "WHERE job_id=? AND name='input.txt'",
                        (b"TAMPER", self.job))
        with self.assertRaises(InputRejected):
            self.reader.metadata(TOKEN, THREAD, self.job)

    def test_tampered_manifest_fails_even_with_updated_row_hash(self):
        forged = json.dumps({"files": {
            "TASK.md": hashlib.sha256(b"Summarize input.txt").hexdigest(),
            "input.txt": "0" * 64}}).encode()
        with self.workspace.store._connection() as con:
            con.execute("UPDATE workspace_input_blobs SET content=?,sha256=? "
                        "WHERE job_id=? AND name='manifest.json'",
                        (forged, hashlib.sha256(forged).hexdigest(), self.job))
        with self.assertRaises(InputRejected):
            self.reader.read_bytes(TOKEN, THREAD, self.job, "TASK.md")


class SnapshotMCPTests(unittest.TestCase):
    def test_http_snapshot_metadata_is_scoped_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            server = LocalMCP(("127.0.0.1", 0), token=TOKEN,
                principal="v026-principal", thread=THREAD,
                database_path=root / "manager.sqlite",
                workspace_projects={"demo": project},
                lmstudio_url="http://127.0.0.1:2")
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                receipt = server.workspace.reserve(TOKEN, THREAD, "demo",
                                                  "prepare-v026-002")
                data = b"Example task"
                input_dir = Path(receipt["input_dir"])
                (input_dir / "TASK.md").write_bytes(data)
                (input_dir / "manifest.json").write_text(json.dumps({"files": {
                    "TASK.md": hashlib.sha256(data).hexdigest()}}), encoding="utf-8")
                server.workspace.seal(TOKEN, THREAD, receipt["job_id"], 0,
                                      "seal-v026-00002")
                msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "minionone_workspace_snapshot",
                                  "arguments": {"job_id": receipt["job_id"]}}}
                body = json.dumps(msg).encode()
                conn = http.client.HTTPConnection("127.0.0.1",
                    server.server_port, timeout=5)
                try:
                    conn.request("POST", "/mcp", body, headers={
                        "Host": "127.0.0.1:" + str(server.server_port),
                        "Authorization": "Bearer " + TOKEN,
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    reply = json.loads(response.read())["result"]
                    self.assertFalse(reply.get("isError"), reply)
                    metadata = json.loads(reply["content"][0]["text"])
                    self.assertEqual(metadata["files"][0]["name"], "TASK.md")
                    self.assertNotIn("Example task", json.dumps(metadata))
                    self.assertFalse(metadata["worker_execution_enabled"])
                finally:
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
