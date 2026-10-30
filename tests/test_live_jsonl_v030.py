"""Live JSONL durability, direct readability and pre-SQLite crash evidence."""
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
import unittest

from minionone.mcp_server import LocalMCP
from minionone.process_logs import ProcessLogs
from minionone.agent_loop import ModelStep
from test_flash_delegation_v027 import FlashDelegationHTTP,TOKEN,MODEL


class BlockingFlint:
    started=threading.Event()
    finish=threading.Event()

    def __init__(self,base_url,*,model_id,approved_prompt,system_prompt,
                 io_sink,**kwargs):
        self.sink=io_sink
        self.index=0
        self._provider=type("Provider",(),{"_opener":None})()

    def next_step(self,*,timeout_seconds):
        self.index+=1
        self.sink("MODEL_REQUEST",json.dumps({
            "messages":[{"role":"user","content":"SENSITIVE LIVE INPUT"}],
            "turn":self.index},sort_keys=True))
        if self.index==1:
            self.started.set()
            if not self.finish.wait(8):
                raise RuntimeError("test worker release not signaled")
            step=ModelStep("tool",name="create_work",
                 arguments={"name":"result.txt","content":"Finished"},total_tokens=2)
        elif self.index==2:
            step=ModelStep("tool",name="publish_artifact",
                 arguments={"name":"result.txt"},total_tokens=2)
        else:
            step=ModelStep("finish",text="done",total_tokens=2)
        self.sink("MODEL_RESPONSE",json.dumps({
            "action":step.action,"tool":step.name if step.action=="tool" else "none",
            "reasoning_content":"SYNTHETIC THINKING SENT BY PROVIDER"},sort_keys=True))
        return step

    def observe_tool(self,name,reply):
        self.sink("TOOL_RESPONSE",json.dumps({
            "name":name,"status":reply.status,"data":reply.data},sort_keys=True))


class LiveJobLogIntegration(FlashDelegationHTTP):
    def test_before_worker_finishes_there_is_a_direct_fsynced_jsonl_file(self):
        BlockingFlint.started.clear()
        BlockingFlint.finish.clear()
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",BlockingFlint):
            try:
                receipt=self.call("minionone_delegate",self.arguments())
                job=receipt["job_id"]
                self.assertTrue(BlockingFlint.started.wait(8))
                folder=self.project/".minionone"/"jobs"/job/"output"
                path=folder/"worker-io.jsonl"
                self.assertTrue(path.exists())
                contents=path.read_text(encoding="utf-8")
                lines=[json.loads(x) for x in contents.splitlines()]
                self.assertTrue(lines)
                self.assertEqual(lines[0]["kind"],"MODEL_REQUEST")
                self.assertIn("SENSITIVE LIVE INPUT",lines[0]["body_utf8"])
                self.assertTrue((folder/"job.log.jsonl").exists())
                status=self.call("minionone_job_status",{"job_id":job})
                self.assertEqual(status["state"],"RUNNING")
                live=self.call("minionone_worker_io",{"job_id":job})
                self.assertEqual(live["events"],lines)
                # The process log can be read directly, independently of
                # SQLite or MCP, and does not contain confidential payload.
                process_log=self.root/"logs"/"minionone-process.jsonl"
                self.assertTrue(process_log.is_file())
                self.assertNotIn("SENSITIVE LIVE INPUT",process_log.read_text())
            finally:
                BlockingFlint.finish.set()
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
            self.assertEqual(status["state"],"RESULT_READY",status)
            final=[json.loads(x) for x in path.read_text().splitlines()]
            self.assertTrue(any("SYNTHETIC THINKING SENT BY PROVIDER" in x["body_utf8"]
                                for x in final))
            self.assertTrue(any(x["kind"]=="TOOL_RESPONSE" for x in final))
            self.assertGreater(len(final),len(lines))
            manager_log=(folder/"job.log.jsonl").read_text()
            self.assertIn("WORKER_TOOL_COMPLETED",manager_log)
            # The log contains the exact provider payload but not an HTTP
            # Authorization header or the parent MCP bearer token.
            self.assertNotIn(TOKEN,path.read_text())


class ProcessLoggerIndependence(unittest.TestCase):
    def test_database_failure_still_creates_direct_startup_and_error_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"jobs.sqlite").mkdir()
            with self.assertRaises(Exception):
                LocalMCP(("127.0.0.1",0),token=TOKEN,
                         database_path=root/"jobs.sqlite")
            events=[json.loads(x) for x in
                    (root/"logs"/"minionone-process.jsonl").read_text().splitlines()]
            failures=[json.loads(x) for x in
                      (root/"logs"/"minionone-errors.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["event"],"PROCESS_START")
            self.assertTrue(any(x["event"]=="STARTUP_FAILED" for x in failures))
            self.assertTrue(any(x["event"]=="STARTUP_FAILED_STACK" for x in failures))
            self.assertNotIn(TOKEN,json.dumps(events+failures))

    def test_rotation_keeps_old_segments_and_append_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/"jobs.sqlite"
            with patch("minionone.process_logs._LIMIT",300), \
                 patch("minionone.process_logs._MAX_ARCHIVES",2):
                logs=ProcessLogs(state)
                for n in range(20):
                    logs.event("SAFE_TEST_EVENT",counter=n)
                logs.close()
            archived=list((state.parent/"logs").glob("minionone-process.*.jsonl"))
            self.assertEqual(len(archived),2)
            logs=ProcessLogs(state)
            logs.event("AFTER_RESTART")
            logs.close()
            self.assertIn("AFTER_RESTART",
                 (state.parent/"logs"/"minionone-process.jsonl").read_text())


if __name__=="__main__":
    unittest.main()
