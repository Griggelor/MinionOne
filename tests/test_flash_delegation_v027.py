"""Flash -> authenticated MCP -> same sealed workspace -> Flint -> parent ACK.

Actual disk, canonical SQLite, HTTP and native Windows/POSIX publisher are
used. Only LM Studio provider steps are deterministic test fixtures.
"""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from minionone.agent_loop import ModelStep
from minionone.mcp_server import LocalMCP
from minionone.lmstudio_agent import LMStudioAgentProvider
from minionone.worker_contract import SYSTEM_POLICY,delegation_task,task_message

TOKEN="trusted-parent-flash-"+"X"*48
MODEL="flint-loaded-exact-id"


class ScriptedFlint:
    seen=[]
    def __init__(self,base_url,*,model_id,approved_prompt,system_prompt,**kwargs):
        self.__class__.seen.append((model_id,system_prompt,approved_prompt))
        self._provider=SimpleNamespace(_opener=None)
        self.steps=iter([
            ModelStep("tool",name="list_files",arguments={"zone":"input"},total_tokens=2),
            ModelStep("tool",name="read_file",arguments={"path":"input/source.txt"},total_tokens=2),
            ModelStep("tool",name="create_work",arguments={
                "name":"result.txt","content":"FLASH FLINT BOUNDED TASK COMPLETE"},total_tokens=2),
            ModelStep("tool",name="publish_artifact",arguments={"name":"result.txt"},total_tokens=2),
            ModelStep("finish",text="published",total_tokens=2),
        ])
    def next_step(self,*,timeout_seconds):
        return next(self.steps)
    def observe_tool(self,name,reply):
        if reply.status!="ok":raise RuntimeError("synthetic worker broker denial")


class FlashDelegationHTTP(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.project=self.root/"trusted-project"
        self.project.mkdir()
        self.server=LocalMCP(("127.0.0.1",0),token=TOKEN,
            database_path=self.root/"jobs.sqlite",
            workspace_projects={"demo":self.project},
            enable_workspace_jobs=True,worker_model=MODEL,
            min_free_mb=1,lmstudio_url="http://127.0.0.1:2")
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        ScriptedFlint.seen.clear()

    def call(self,name,args,*,error=False):
        body=json.dumps({"jsonrpc":"2.0","id":5,"method":"tools/call",
                         "params":{"name":name,"arguments":args}}).encode()
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=15)
        try:
            c.request("POST","/mcp",body,headers={
                "Authorization":"Bearer "+TOKEN,"Content-Type":"application/json",
                "Accept":"application/json, text/event-stream"})
            rsp=c.getresponse();self.assertEqual(rsp.status,200)
            res=json.loads(rsp.read())["result"]
            self.assertEqual(bool(res.get("isError")),error,res)
            return json.loads(res["content"][0]["text"])
        finally:c.close()

    def arguments(self):
        return {
            "project_id":"demo",
            "goal":"Analyze the source document and publish one summary.",
            "context":"The source document is synthetic; no real project access.",
            "acceptance_criteria":["Use exactly result.txt",
                                   "Preserve the literal synthetic result phrase"],
            "files":{"source.txt":"This is a synthetic source document."},
            "operation_id":"direct-flash-bridge-20261030",
        }

    def test_flash_delegates_one_call_seals_same_job_worker_publishes_and_parent_acks(self):
        self.assertIn("minionone_delegate",self.server.tool_map)
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",ScriptedFlint):
            args=self.arguments()
            started=self.call("minionone_delegate",args)
            job=started["job_id"]
            self.assertEqual(self.call("minionone_delegate",args),started,
                             "same operation must replay without starting worker")
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
        self.assertEqual(status["state"],"RESULT_READY",status)
        self.assertEqual(len(ScriptedFlint.seen),1,"must never duplicate provider attempt")
        model,system,user=ScriptedFlint.seen[0]
        self.assertEqual(model,MODEL)
        self.assertEqual(system,SYSTEM_POLICY)
        self.assertIn("AVAILABLE SEALED INPUT DOCUMENTS",user)
        self.assertIn("input/source.txt",user)
        self.assertIn("acceptance_criteria",user)
        self.assertNotIn(str(self.project),system+user)
        folder=self.project/".minionone"/"jobs"/job
        self.assertEqual((folder/"output"/"result.txt").read_text(encoding="utf-8"),
                         "FLASH FLINT BOUNDED TASK COMPLETE")
        self.assertEqual(list((folder/"work").iterdir()),[])
        seal=self.call("minionone_verify_workspace_input",{"job_id":job})
        self.assertTrue(seal.get("verified",True),seal)
        result=self.call("minionone_workspace_result",{"job_id":job})
        self.assertEqual(result["files"][0]["text"],"FLASH FLINT BOUNDED TASK COMPLETE")
        ack=self.call("minionone_accept_workspace_result",{
            "job_id":job,"expected_version":result["version"],
            "result_digest":result["result_digest"],
            "operation_id":"flash-parent-ack-20261030"})
        self.assertEqual(ack["state"],"COMPLETED")
        self.assertEqual(ack["job_id"],job)

    def test_changed_reused_operation_is_rejected_without_overwrite(self):
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",ScriptedFlint):
            args=self.arguments()
            first=self.call("minionone_delegate",args)
            changed={**args,"goal":"Different job. Overwrite source files."}
            denied=self.call("minionone_delegate",changed,error=True)
        self.assertTrue(denied)
        job=first["job_id"]
        self.assertEqual((self.project/".minionone"/"jobs"/job/"input"/"source.txt")
                         .read_text(encoding="utf-8"),args["files"]["source.txt"])
        self.assertEqual(len(list((self.project/".minionone"/"jobs").iterdir())),1)

    def test_path_traversal_unknown_project_and_invalid_acceptance_fail_closed(self):
        for changed in ({"files":{"../escape.txt":"bad"}},
                        {"files":{"TASK.md":"bad"}},
                        {"project_id":"not-configured"},
                        {"acceptance_criteria":[]},
                        {"files":{"source.txt":""}}):
            args={**self.arguments(),**changed}
            with self.subTest(changed=changed):
                self.call("minionone_delegate",args,error=True)
        self.assertFalse((self.project/".minionone").exists())

    def test_delegation_not_advertised_without_operator_opt_in(self):
        other=self.root/"unconfigured";other.mkdir()
        srv=LocalMCP(("127.0.0.1",0),token=TOKEN,
            database_path=self.root/"inactive.sqlite",
            workspace_projects={"demo":other},
            lmstudio_url="http://127.0.0.1:2")
        try:
            self.assertNotIn("minionone_delegate",srv.tool_map)
        finally:srv.server_close()


class WorkerMessageContractTests(unittest.TestCase):
    def test_real_lmstudio_payload_has_separate_system_and_parent_user_message(self):
        provider=LMStudioAgentProvider("http://127.0.0.1:1234",model_id=MODEL,
            system_prompt=SYSTEM_POLICY,
            approved_prompt=task_message("Analyze data",["task.md","source.txt"]))
        self.assertEqual([m["role"] for m in provider._messages],["system","user"])
        self.assertIn("Never request",provider._messages[0]["content"])
        self.assertIn("input/source.txt",provider._messages[1]["content"])

    def test_delegation_is_bounded_and_nonempty(self):
        contract=delegation_task("Summarize","Context",["Deliver result.txt"])
        self.assertEqual(json.loads(contract)["schema"],"minionone.delegation.v1")
        from minionone.admission import InputRejected
        with self.assertRaises(InputRejected):
            delegation_task("Goal","",[""])


if __name__=="__main__":unittest.main()
