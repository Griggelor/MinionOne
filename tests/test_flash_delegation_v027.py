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

    def test_redacted_job_debug_audit_trace_and_support_export(self):
        from minionone.pilot_cli import PilotClient, export_diagnostics
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",ScriptedFlint):
            started=self.call("minionone_delegate",self.arguments())
            job=started["job_id"]
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
        self.assertEqual(status["state"],"RESULT_READY",status)
        first=self.call("minionone_job_trace",{"job_id":job,"after_seq":0,"limit":2})
        self.assertEqual(first["integrity"],"VERIFIED_HASH_CHAIN")
        self.assertTrue(first["has_more"])
        all_events=[]
        after=0
        for _ in range(40):
            page=self.call("minionone_job_trace",
                           {"job_id":job,"after_seq":after,"limit":3})
            self.assertEqual(page["trace_id"],first["trace_id"])
            all_events+=page["events"]
            after=page["next_after_seq"]
            if not page["has_more"]:break
        self.assertEqual(len(all_events),page["total_events"])
        self.assertEqual({e["seq"] for e in all_events},set(range(1,len(all_events)+1)))
        levels={e["level"] for e in all_events}
        self.assertEqual(levels,{"JOB","DEBUG","AUDIT"})
        events={e["event"] for e in all_events}
        self.assertTrue({"JOB_ACCEPTED","JOB_TRANSITION","MODEL_REQUEST_STARTED",
                         "MODEL_REQUEST_FINISHED","WORKER_TOOL_COMPLETED"}<=events)
        text=json.dumps(all_events)
        for secret in (TOKEN,self.arguments()["goal"],self.arguments()["files"]["source.txt"],
                       "FLASH FLINT BOUNDED TASK COMPLETE",str(self.project)):
            self.assertNotIn(secret,text)
        self.assertIn("create_work",text)
        self.assertNotIn("source.txt",text)
        result=self.call("minionone_workspace_result",{"job_id":job})
        ack=self.call("minionone_accept_workspace_result",{
            "job_id":job,"expected_version":result["version"],
            "result_digest":result["result_digest"],
            "operation_id":"parent-ack-for-trace-20261030"})
        self.assertEqual(ack["state"],"COMPLETED")
        saved=self.root/"verified-diagnostic.json"
        exported=export_diagnostics(PilotClient(self.server.server_port,TOKEN),job,saved)
        self.assertEqual(exported["event_count"],len(json.loads(saved.read_text())["trace_events"]))
        self.assertEqual(json.loads(saved.read_text())["job"]["state"],"COMPLETED")
        with self.assertRaises(FileExistsError):
            export_diagnostics(PilotClient(self.server.server_port,TOKEN),job,saved)
        self.assertNotIn(TOKEN,saved.read_text())
        self.assertNotIn(self.arguments()["files"]["source.txt"],saved.read_text())

    def test_trace_integrity_tampering_fails_closed(self):
        import sqlite3
        from contextlib import closing
        from minionone.pilot_cli import PilotClient
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",ScriptedFlint):
            started=self.call("minionone_delegate",self.arguments())
            job=started["job_id"]
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
        self.assertEqual(status["state"],"RESULT_READY",status)
        # sqlite3.Connection's context manager commits, but does NOT close.
        # Windows refuses TemporaryDirectory cleanup with an open DB handle.
        with closing(sqlite3.connect(self.root/"jobs.sqlite")) as con:
            with con:
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute("UPDATE job_telemetry SET event='BOGUS' WHERE job_id=?",(job,))
                con.execute("DROP TRIGGER telemetry_no_update")
                con.execute("UPDATE job_telemetry SET event='MODEL_REQUEST_FAILED' WHERE job_id=? AND seq=1",(job,))
        error=self.call("minionone_job_trace",{"job_id":job},error=True)
        self.assertEqual(error["error"]["code"],"DB_INTEGRITY_ERROR")
        with self.assertRaises(Exception):
            export_diagnostics(PilotClient(self.server.server_port,TOKEN),job,self.root/"bad.json")
        self.assertFalse((self.root/"bad.json").exists())

    def test_workspace_mode_guides_flash_and_hides_legacy_test_tools(self):
        from minionone.pilot_cli import PilotClient
        client=PilotClient(self.server.server_port,TOKEN)
        rsp=client.request("initialize",{"protocolVersion":"2025-06-18",
            "capabilities":{},"clientInfo":{"name":"flash-fixture","version":"1"}})
        instructions=rsp["result"]["instructions"]
        self.assertIn("minionone_delegate",instructions)
        self.assertIn("minionone_prepare_workspace",instructions)
        self.assertIn("minionone_job_trace",instructions)
        self.assertNotIn("minionone_submit_test_job",self.server.tool_map)
        self.assertIn("minionone_delegate",self.server.tool_map)
        self.assertIn("minionone_prepare_workspace",self.server.tool_map)
        self.assertIn("minionone_job_trace",self.server.tool_map)
        unavailable=client.request("tools/call",{"name":"minionone_submit_test_job","arguments":{"task":"x"}})
        self.assertEqual(unavailable["error"]["code"],-32602)

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
