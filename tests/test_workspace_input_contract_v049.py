"""HTTP/MCP regression for the bounded executor's seal/start contract."""
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from minionone.mcp_server import LocalMCP

TOKEN = "review-boundary-" + "R" * 48


class BoundedExecutorContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        self.server = LocalMCP(
            ("127.0.0.1", 0), token=TOKEN, principal="review-owner",
            thread="review-thread", database_path=root / "state.sqlite",
            workspace_projects={"demo": project}, enable_workspace_jobs=True,
            worker_model="fake-model", lmstudio_url="http://127.0.0.1:2",
            min_free_mb=1,
        )
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, name, args):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": args}}
        try:
            conn.request("POST", "/mcp", json.dumps(msg),
                headers={"Host": f"127.0.0.1:{self.server.server_port}",
                         "Authorization": "Bearer " + TOKEN,
                         "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"})
            res = conn.getresponse()
            self.assertEqual(res.status, 200)
            result = json.loads(res.read())["result"]
            return result.get("isError", False), json.loads(result["content"][0]["text"])
        finally:
            conn.close()

    @staticmethod
    def stage(folder, task, sources):
        blobs = {"TASK.md": task.encode("utf-8"),
                 **{n: v.encode("utf-8") for n, v in sources.items()}}
        for name, blob in blobs.items():
            (folder / name).write_bytes(blob)
        (folder / "manifest.json").write_text(json.dumps({
            "files": {name: hashlib.sha256(blob).hexdigest()
                      for name, blob in blobs.items()}}), encoding="utf-8")

    def prepare(self, label):
        err, prep = self.call("minionone_prepare_workspace",
            {"project_id": "demo", "operation_id": "prep-v049-" + label})
        self.assertFalse(err, prep)
        return prep

    def seal(self, prep, label):
        return self.call("minionone_seal_workspace", {
            "job_id": prep["job_id"], "expected_version": 0,
            "operation_id": "seal-v049-" + label})

    def deny_before_accept(self, task, sources, label):
        prep = self.prepare(label)
        self.stage(Path(prep["input_dir"]), task, sources)
        err, reply = self.seal(prep, label)
        self.assertTrue(err, reply)
        err, status = self.call("minionone_workspace_status", {"job_id": prep["job_id"]})
        self.assertFalse(err, status)
        self.assertEqual(status["state"], "PREPARING")
        with self.server.workspace.store._connection() as con:
            count = con.execute("SELECT COUNT(*) FROM jobs WHERE job_id=?",
                                (prep["job_id"],)).fetchone()[0]
        self.assertEqual(count, 0)
        return prep

    def test_five_sources_denied(self):
        self.deny_before_accept("Summarize", {f"s{i}.txt": "x" for i in range(5)}, "five")

    def test_nine_kb_denied(self):
        self.deny_before_accept("Summarize",
                                {f"s{i}.txt": "X" * 3000 for i in range(3)}, "9k")

    def test_4000_character_task_denied(self):
        self.deny_before_accept("T" * 4000, {"s.txt": "source"}, "task4000")

    def test_casefold_collision_denied(self):
        self.deny_before_accept("Summarize", {"A.txt": "a", "a.txt": "b"}, "case")

    def test_unsupported_name_denied(self):
        self.deny_before_accept("Summarize", {"_hidden.txt": "a"}, "badname")

    def test_valid_seal_starts_same_job(self):
        prep = self.prepare("valid")
        self.stage(Path(prep["input_dir"]), "Summarize", {"a.txt": "ok", "b.txt": "fine"})
        err, accepted = self.seal(prep, "valid")
        self.assertFalse(err, accepted)
        self.assertEqual(accepted["state"], "ACCEPTED")
        err, started = self.call("minionone_start_workspace_job", {
            "job_id": prep["job_id"], "expected_version": accepted["version"],
            "operation_id": "start-v049-valid", "model_id": "fake-model"})
        self.assertFalse(err, started)
        self.assertEqual(started["job_id"], prep["job_id"])
        self.assertNotEqual(started["state"], "ACCEPTED")

    def test_rejected_input_can_be_fixed(self):
        prep = self.deny_before_accept("X" * 4000, {"a.txt": "source"}, "retry")
        self.stage(Path(prep["input_dir"]), "Within limit", {"a.txt": "source"})
        err, accepted = self.seal(prep, "retry")
        self.assertFalse(err, accepted)
        self.assertEqual(accepted["state"], "ACCEPTED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
