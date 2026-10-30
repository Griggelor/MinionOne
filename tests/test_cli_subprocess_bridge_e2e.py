"""Separate-process CLI E2E with real TCP MCP and fake HTTP model provider.

Unlike unit fixtures this starts python -m minionone.pilot_cli as an OS child.
The child uses the production LocalMCP, actual LMStudioAgentProvider and
native filesystem publisher. No Unsloth or real model is claimed.
"""
from contextlib import closing
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_full_bridge_http_e2e_ci import ModelOverHTTP,MODEL,TOKEN


def free_port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


class SubprocessBridge(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.project=self.root/"approved-project"
        self.project.mkdir()
        self.state=self.root/"state"
        self.state.mkdir()
        self.model=ThreadingHTTPServer(("127.0.0.1",0),ModelOverHTTP)
        self.model.daemon_threads=True
        self.model.synchronizer=threading.Lock()
        self.model.calls=[]
        self.model.loaded=True
        self.model.mode="success"
        threading.Thread(target=self.model.serve_forever,daemon=True).start()
        self.addCleanup(self.model.server_close)
        self.addCleanup(self.model.shutdown)
        self.proc=None
        self.addCleanup(self.stop_process)
        self.port=free_port()

    def stop_process(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc=None

    def start(self):
        args=[sys.executable,"-m","minionone.pilot_cli",
              "--state",str(self.state),"serve",
              "--model",MODEL,
              "--provider-url",f"http://127.0.0.1:{self.model.server_port}",
              "--port",str(self.port),"--min-free-mb","1",
              "--model-call-seconds","2",
              "--experimental-workspace-jobs",
              "--workspace-project","demo="+str(self.project)]
        self.proc=subprocess.Popen(args,cwd=str(self.root),
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            self.assertIsNone(self.proc.poll(),"MinionOne child terminated during startup")
            try:
                received=subprocess.check_output([sys.executable,
                    "-m","minionone.pilot_cli","--state",str(self.state),
                    "connection","--show-token"],stderr=subprocess.DEVNULL,
                    timeout=2)
                cfg=json.loads(received)
                self.parent_token=cfg["header_value"].removeprefix("Bearer ")
                self.assertEqual(cfg["worker_model"],MODEL)
                self.rpc("minionone_status",{})
                return
            except (OSError,ValueError,KeyError,subprocess.SubprocessError,
                    ConnectionError,TimeoutError):
                time.sleep(.1)
        self.fail("packaged-style MinionOne CLI child never initialized")

    def rpc(self,name,args,*,error=False):
        with closing(http.client.HTTPConnection("127.0.0.1",self.port,timeout=10)) as c:
            c.request("POST","/mcp",json.dumps({
                "jsonrpc":"2.0","id":5,"method":"tools/call",
                "params":{"name":name,"arguments":args}}),
                headers={"Authorization":"Bearer "+self.parent_token,
                         "Content-Type":"application/json",
                         "Accept":"application/json, text/event-stream"})
            response=c.getresponse()
            self.assertEqual(response.status,200)
            result=json.loads(response.read())["result"]
            self.assertEqual(bool(result.get("isError")),error,result)
            return json.loads(result["content"][0]["text"])

    def test_os_child_full_delegation_and_persistent_logs_then_parent_ack(self):
        self.start()
        self.assertTrue(self.rpc("minionone_status",{})["workspace_execution_enabled"])
        job=self.rpc("minionone_delegate",{
            "project_id":"demo",
            "goal":"Publish uppercase of input as result.txt",
            "context":"Subprocess CI synthetic only",
            "acceptance_criteria":["Use the approved files only"],
            "files":{"sample.txt":"subprocess test data"},
            "operation_id":"ci-subprocess-delegate-0001"})
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status=self.rpc("minionone_job_status",{"job_id":job["job_id"]})
            if status["state"] in ("RESULT_READY","FAILED","NEEDS_RECONCILIATION"):
                break
            time.sleep(.1)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertEqual((root/"result.txt").read_text(),"SUBPROCESS TEST DATA")
        records=[json.loads(line) for line in
                 (root/"worker-io.jsonl").read_text().splitlines()]
        self.assertIn("SYNTHETIC-THINKING-TOOL",json.dumps(records))
        self.assertEqual(self.rpc("minionone_worker_io",{
            "job_id":job["job_id"],"limit":10})["events"],records[:10])
        self.assertIn("MODEL_REQUEST_FINISHED",(root/"job.log.jsonl").read_text())
        result=self.rpc("minionone_workspace_result",{"job_id":job["job_id"]})
        receipt=self.rpc("minionone_accept_workspace_result",{
            "job_id":job["job_id"],"expected_version":result["version"],
            "result_digest":result["result_digest"],
            "operation_id":"ci-subprocess-ack-00001"})
        self.assertEqual(receipt["state"],"COMPLETED")
        self.stop_process()
        # Files remain accessible without MinionOne or SQLite and the
        # process log contains no parent credential or model content.
        self.assertTrue((root/"worker-io.jsonl").exists())
        proc=(self.state/"logs"/"minionone-process.jsonl").read_text()
        self.assertIn("PROCESS_START",proc)
        self.assertNotIn(self.parent_token,proc)
        self.assertNotIn("subprocess test data",proc)


if __name__=="__main__":unittest.main()
