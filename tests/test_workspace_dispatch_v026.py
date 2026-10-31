"""Opt-in v0.26 workspace dispatch: actual MCP/SQL/broker/publisher integration.

Provider responses are deterministic fakes to test manager boundaries, NOT
real Flint/Unsloth target-host attestation. Windows runner exercises native
publication; Linux runner exercises POSIX publication.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from minionone.agent_loop import ModelStep
from minionone.admission import InputRejected, VersionConflict
from minionone.mcp_server import LocalMCP

TOKEN = "workspace-v026-dispatch-" + "M" * 48
MODEL = "configured-model"


class DeterministicProvider:
    """Only the model step generator is synthetic; broker and file I/O are real."""
    def __init__(self, *args, **kwargs):
        self._provider = SimpleNamespace(_opener=None)
        self.index = 0
        self.sink = kwargs.get("io_sink")

    def next_step(self, *, timeout_seconds):
        choices = [
            ModelStep("tool",name="read_file",
                      arguments={"path":"input/sample.txt"},total_tokens=2),
            ModelStep("tool",name="create_work",
                      arguments={"name":"summary.txt","content":"Sandbox result"},
                      total_tokens=2),
            ModelStep("tool",name="publish_artifact",
                      arguments={"name":"summary.txt"},total_tokens=2),
            ModelStep("finish",text="published",total_tokens=2),
        ]
        if self.index >= len(choices):
            raise RuntimeError("unexpected extra provider step")
        reply=choices[self.index]
        self.index+=1
        if self.sink is not None:
            self.sink("MODEL_REQUEST",json.dumps({"synthetic":True,"turn":self.index}))
            self.sink("MODEL_RESPONSE",json.dumps({"synthetic":True,"action":reply.action,
                 "tool":reply.name if reply.action=="tool" else "none"}))
        return reply

    def observe_tool(self,name,reply):
        if self.sink is not None:
            self.sink("TOOL_RESPONSE",json.dumps({"tool":name,"data":reply.data}))
        if reply.status != "ok":
            raise RuntimeError("broker rejected normal synthetic tool")


class WorkspaceDispatchMCP(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        project = self.root / "project"
        project.mkdir()
        self.srv = LocalMCP(("127.0.0.1",0),token=TOKEN,
                            principal="dev-owner",thread="dev-thread",
                            database_path=self.root / "control.sqlite",
                            workspace_projects={"demo":project},
                            enable_workspace_jobs=True,
                            worker_model=MODEL,min_free_mb=1,
                            lmstudio_url="http://127.0.0.1:2")
        thread=threading.Thread(target=self.srv.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.prep=self.call("minionone_prepare_workspace",
                            {"project_id":"demo","operation_id":"prepare-test-v026"})
        self.job=self.prep["job_id"]
        self.task=Path(self.prep["input_dir"]) / "TASK.md"
        self.sample=Path(self.prep["input_dir"]) / "sample.txt"
        self.task.write_text("Summarize sample.txt", encoding="utf-8")
        self.sample.write_text("Synthetic example only", encoding="utf-8")
        self.write_manifest()
        self.seal=self.call("minionone_seal_workspace",{
            "job_id":self.job,"expected_version":0,
            "operation_id":"seal-test-v026-001"})

    def write_manifest(self):
        payload={"files":{
            "TASK.md":hashlib.sha256(self.task.read_bytes()).hexdigest(),
            "sample.txt":hashlib.sha256(self.sample.read_bytes()).hexdigest()}}
        (self.task.parent / "manifest.json").write_text(json.dumps(payload),
                                                         encoding="utf-8")

    def call(self,name,args,*,error=False):
        msg={"jsonrpc":"2.0","id":14,"method":"tools/call",
             "params":{"name":name,"arguments":args}}
        conn=http.client.HTTPConnection("127.0.0.1",self.srv.server_port,timeout=8)
        try:
            conn.request("POST","/mcp",json.dumps(msg),headers={
                "Host":"127.0.0.1:"+str(self.srv.server_port),
                "Authorization":"Bearer "+TOKEN,
                "Content-Type":"application/json",
                "Accept":"application/json, text/event-stream"})
            resp=conn.getresponse()
            self.assertEqual(resp.status,200)
            payload=json.loads(resp.read())["result"]
            if error:
                self.assertTrue(payload.get("isError"),payload)
            else:
                self.assertFalse(payload.get("isError"),payload)
            return json.loads(payload["content"][0]["text"])
        finally:
            conn.close()

    def start(self,operation="start-test-v026-001", version=None):
        return self.call("minionone_start_workspace_job",{
            "job_id":self.job,"expected_version":self.seal["version"] if version is None else version,
            "operation_id":operation,"model_id":MODEL})

    def test_t163_positive_real_mcp_start_published_result_and_ack(self):
        self.assertIn("minionone_start_workspace_job",self.srv.tool_map)
        self.assertFalse(self.call("minionone_status",{})["host_filesystem_tools_enabled"])
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",DeterministicProvider):
            start=self.start()
            self.assertEqual(start["job_id"],self.job)
            self.assertIn(start["state"],("RESOURCE_CHECK","RUNNING"))
            self.assertEqual(self.start(),start,"replay must preserve original receipt")
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                result=self.call("minionone_job_status",{"job_id":self.job})
                if result["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.05)
        self.assertEqual(result["state"],"RESULT_READY",result)
        self.assertIn("workspace_job",result)
        artifact=Path(self.prep["output_dir"]) / "summary.txt"
        self.assertEqual(artifact.read_text(encoding="utf-8"),"Sandbox result")
        if os.name=='nt':
            self.assertEqual(list(Path(self.prep["work_dir"]).iterdir()),[],
                             "Windows worker still has no native Work capability")
        else:
            self.assertEqual((Path(self.prep["work_dir"])/"summary.txt").read_text(encoding="utf-8"),
                             "Sandbox result")
        ready=self.call("minionone_workspace_result",{"job_id":self.job})
        self.assertEqual(ready["files"][0]["text"],"Sandbox result")
        self.assertEqual(ready["job_id"],self.job)
        self.assertNotIn("minionone_test_result",self.srv.tool_map)
        acknowledged=self.call("minionone_accept_workspace_result",{
            "job_id":self.job,"expected_version":ready["version"],
            "result_digest":ready["result_digest"],"operation_id":"ack-test-v026-001"})
        self.assertEqual(acknowledged["state"],"COMPLETED")
        self.assertEqual(acknowledged["job_id"],self.job)

    def test_t163_tampered_after_seal_denies_start_even_with_changed_manifest(self):
        self.task.write_text("Changed after seal", encoding="utf-8")
        self.write_manifest()
        self.start_error("tamper-start-v026-001")
        self.assertEqual(self.call("minionone_job_status",{"job_id":self.job})["state"],
                         "ACCEPTED")
        with self.srv.store._connection() as con:
            self.assertIsNone(con.execute("SELECT * FROM pilot_runs WHERE job_id=?",
                                          (self.job,)).fetchone())

    def start_error(self,operation):
        return self.call("minionone_start_workspace_job",{
            "job_id":self.job,"expected_version":self.seal["version"],
            "operation_id":operation,"model_id":MODEL},error=True)

    def test_bad_cas_model_and_competing_operation_denied(self):
        self.call("minionone_start_workspace_job",{
            "job_id":self.job,"expected_version":999,
            "operation_id":"stale-start-v026-001","model_id":MODEL},error=True)
        self.call("minionone_start_workspace_job",{
            "job_id":self.job,"expected_version":self.seal["version"],
            "operation_id":"bad-model-v026-001","model_id":"other-model"},error=True)
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",DeterministicProvider):
            receipt=self.start()
            self.assertEqual(self.start(),receipt)
            self.start_error("competing-v026-001")

    def test_crash_between_durable_queue_and_dispatch_retries_only_same_operation(self):
        with patch("minionone.pilot_jobs.PilotJobs.start",
                   side_effect=RuntimeError("synthetic process interruption")):
            self.start_error("crash-gap-v026-001")
        queued=self.call("minionone_job_status",{"job_id":self.job})
        self.assertEqual(queued["state"],"QUEUED")
        self.start_error("different-operation-v026-001")
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",DeterministicProvider):
            first=self.start("crash-gap-v026-001")
            replay=self.start("crash-gap-v026-001")
            self.assertEqual(first,replay)
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                state=self.call("minionone_job_status",{"job_id":self.job})
                if state["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.05)
            self.assertEqual(state["state"],"RESULT_READY",state)

    def test_raw_workspace_input_not_exposed_by_mcp(self):
        meta=self.call("minionone_workspace_snapshot",{"job_id":self.job})
        self.assertNotIn("Synthetic example only",json.dumps(meta))
        self.assertNotIn("read_workspace_file",self.srv.tool_map)
        self.assertNotIn("create_workspace_work_file",self.srv.tool_map)


if __name__=="__main__":
    unittest.main()
