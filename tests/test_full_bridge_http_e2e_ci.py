"""Cross-boundary synthetic E2E: REAL MCP HTTP -> MinionOne manager ->
REAL OpenAI-compatible LM Studio HTTP -> scoped broker -> native output -> ACK.

Only the external inference service is deterministic; it is a loopback HTTP
server, NOT a mocked LMStudioAgentProvider class. This is a CI integration
proof, not a real Unsloth/Flint or G1b security attestation.
"""
from contextlib import closing
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from minionone.mcp_server import LocalMCP

TOKEN="ci-bridge-parent-"+"P"*48
MODEL="synthetic-loaded-flint"


class ModelOverHTTP(BaseHTTPRequestHandler):
    def log_message(self,*args):
        return

    def respond(self,payload,code=200):
        body=json.dumps(payload,ensure_ascii=True,separators=(",",":")).encode("ascii")
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass  # Deliberately timed-out inference request.

    def do_GET(self):
        if self.path!="/api/v0/models":
            self.send_error(404)
            return
        self.respond({"data":[{"id":MODEL,"state":
             "loaded" if self.server.loaded else "not-loaded"}]})

    def do_POST(self):
        if self.path!="/v1/chat/completions":
            self.send_error(404)
            return
        data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.synchronizer:
            self.server.calls.append(data)
        if self.server.mode=="malformed":
            self.respond({"choices":[],"usage":{"total_tokens":4}})
            return
        if self.server.mode=="premature_finish":
            self.respond({"choices":[{"finish_reason":"stop",
                "message":{"role":"assistant","content":"I am done without files."}}],
                "usage":{"total_tokens":10}})
            return
        if self.server.mode=="timeout":
            time.sleep(3)
        messages=data["messages"]
        completed=[message for message in messages if message.get("role")=="tool"]
        n=len(completed)
        if self.server.mode=="denied":
            name,args="execute_shell",{"command":"this must never execute"}
        elif self.server.mode=="question":
            name,args="ask_question",{"question":"Synthetic clarification?"}
        elif n==0:
            name,args="read_file",{"path":"input/sample.txt"}
        elif n==1:
            content=json.loads(completed[-1]["content"])["data"]["content"]
            name,args="create_work",{"name":"result.txt","content":content.upper()}
        elif n==2:
            name,args="publish_artifact",{"name":"result.txt"}
        else:
            self.respond({"choices":[{"finish_reason":"stop",
                         "message":{"role":"assistant","content":"Published synthetic artifact.",
                         "reasoning_content":"SYNTHETIC-THINKING-END"}}],
                         "usage":{"total_tokens":20,
                         "completion_tokens_details":{"reasoning_tokens":5}}})
            return
        self.respond({"choices":[{"finish_reason":"tool_calls",
             "message":{"role":"assistant","content":None,
               "reasoning_content":"SYNTHETIC-THINKING-TOOL",
               "tool_calls":[{"id":"call-"+str(n),"type":"function",
               "function":{"name":name,"arguments":json.dumps(args)}}]}}],
               "usage":{"total_tokens":20,
                        "completion_tokens_details":{"reasoning_tokens":4}}})


class CompleteBridgeE2E(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.project=self.root/"synthetic-project"
        self.project.mkdir()
        self.model=ThreadingHTTPServer(("127.0.0.1",0),ModelOverHTTP)
        self.model.daemon_threads=True
        self.model.synchronizer=threading.Lock()
        self.model.calls=[]
        self.model.loaded=True
        self.model.mode="success"
        threading.Thread(target=self.model.serve_forever,daemon=True).start()
        self.addCleanup(self.model.server_close)
        self.addCleanup(self.model.shutdown)
        self.manager=LocalMCP(("127.0.0.1",0),token=TOKEN,
            database_path=self.root/"jobs.sqlite",
            workspace_projects={"demo":self.project},
            enable_workspace_jobs=True,worker_model=MODEL,min_free_mb=1,
            lmstudio_url=f"http://127.0.0.1:{self.model.server_port}",
            model_call_seconds=2)
        threading.Thread(target=self.manager.serve_forever,daemon=True).start()
        self.addCleanup(self.manager.server_close)
        self.addCleanup(self.manager.shutdown)

    def rpc(self,name,args,*,error=False):
        msg={"jsonrpc":"2.0","id":1,"method":"tools/call",
             "params":{"name":name,"arguments":args}}
        with closing(http.client.HTTPConnection("127.0.0.1",
                                                 self.manager.server_port,timeout=15)) as conn:
            conn.request("POST","/mcp",json.dumps(msg),headers={
                "Authorization":"Bearer "+TOKEN,
                "Accept":"application/json, text/event-stream",
                "Content-Type":"application/json"})
            response=conn.getresponse()
            self.assertEqual(response.status,200)
            result=json.loads(response.read())["result"]
            self.assertEqual(bool(result.get("isError")),error,result)
            return json.loads(result["content"][0]["text"])

    def direct(self,op="e2e-delegate-00001"):
        return self.rpc("minionone_delegate",{
            "project_id":"demo",
            "goal":"Publish the uppercase synthetic document as result.txt.",
            "context":"CI real MCP and real HTTP fake provider. No live user content.",
            "acceptance_criteria":["Read sample.txt","Create result.txt",
                                   "Publish result.txt"],
            "files":{"sample.txt":"synthetic bridge content"},
            "operation_id":op})

    def staged(self):
        job=self.rpc("minionone_prepare_workspace",
            {"project_id":"demo","operation_id":"e2e-prepare-00001"})
        folder=Path(job["input_dir"])
        task=folder/"TASK.md"
        source=folder/"sample.txt"
        task.write_text("Publish uppercase of sample.txt",encoding="utf-8")
        source.write_text("synthetic staged content",encoding="utf-8")
        payload={"files":{"TASK.md":sha256(task.read_bytes()).hexdigest(),
                          "sample.txt":sha256(source.read_bytes()).hexdigest()}}
        (folder/"manifest.json").write_text(json.dumps(payload),encoding="utf-8")
        sealed=self.rpc("minionone_seal_workspace",{
            "job_id":job["job_id"],"expected_version":0,
            "operation_id":"e2e-seal-00001"})
        self.rpc("minionone_start_workspace_job",{
            "job_id":job["job_id"],"expected_version":sealed["version"],
            "operation_id":"e2e-start-00001","model_id":MODEL})
        return sealed

    def outcome(self,job,seconds=15):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            status=self.rpc("minionone_job_status",{"job_id":job["job_id"]})
            if status["state"] in ("RESULT_READY","FAILED","WAITING_FOR_PARENT",
                                   "NEEDS_RECONCILIATION","CANCELLED","COMPLETED"):
                return status
            time.sleep(.05)
        self.fail("Real HTTP-to-HTTP CI job did not finish in its bounded deadline")

    def transcript(self,job):
        records=[];after=0
        while True:
            page=self.rpc("minionone_worker_io",{
                "job_id":job["job_id"],"after_seq":after,"limit":10})
            self.assertEqual(page["integrity"],"VERIFIED_HASH_CHAIN")
            self.assertTrue(page["sensitive"])
            records+=page["events"]
            after=page["next_after_seq"]
            if not page["has_more"]:
                self.assertEqual(len(records),page["total_events"])
                break
        return records

    def assert_output(self,job,expected):
        path=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertEqual((path/"result.txt").read_text(encoding="utf-8"),expected)
        records=self.transcript(job)
        disk=[json.loads(line) for line in
              (path/"worker-io.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(disk,records,"live JSONL must match authorized MCP journal")
        self.assertIn("MODEL_REQUEST",[x["kind"] for x in disk])
        self.assertIn("MODEL_RESPONSE",[x["kind"] for x in disk])
        self.assertIn("TOOL_RESPONSE",[x["kind"] for x in disk])
        self.assertIn("SYNTHETIC-THINKING-TOOL",json.dumps(disk))
        self.assertNotIn(TOKEN,json.dumps(disk))
        self.assertTrue((path/"job.log.jsonl").is_file())
        self.assertIn("MODEL_REQUEST_FINISHED",(path/"job.log.jsonl").read_text())
        self.assertTrue((self.root/"logs"/"minionone-process.jsonl").is_file())
        self.assertNotIn("synthetic bridge content",
                         (self.root/"logs"/"minionone-process.jsonl").read_text())
        return records

    def test_direct_route_full_real_http_thinking_and_exact_ack(self):
        job=self.direct()
        self.assertEqual(self.direct(),job,"operation replay cannot create second worker")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        records=self.assert_output(job,"SYNTHETIC BRIDGE CONTENT")
        self.assertEqual(len(self.model.calls),4)
        self.assertEqual(self.model.calls[0]["model"],MODEL)
        self.assertEqual([x["role"] for x in self.model.calls[0]["messages"]],
                         ["system","user"])
        self.assertIn("ONLY",self.model.calls[0]["messages"][0]["content"])
        self.assertEqual(self.model.calls[0]["stream"],False)
        self.assertTrue(any(x["kind"]=="MODEL_RESPONSE" and
            "SYNTHETIC-THINKING-END" in x["body_utf8"] for x in records))
        with self.subTest("ack_requires_prior_result_delivery"):
            denied=self.rpc("minionone_accept_workspace_result",{
                "job_id":job["job_id"],"expected_version":status["version"],
                "result_digest":status["workspace_job"]["result_digest"],
                "operation_id":"e2e-premature-ack-0001"},error=True)
            self.assertEqual(denied["error"]["code"],"PILOT_RESULT_NOT_DELIVERED")
        result=self.rpc("minionone_workspace_result",{"job_id":job["job_id"]})
        self.assertEqual(result["files"][0]["text"],"SYNTHETIC BRIDGE CONTENT")
        ackargs={"job_id":job["job_id"],"expected_version":result["version"],
                 "result_digest":result["result_digest"],
                 "operation_id":"e2e-verified-ack-00001"}
        ack=self.rpc("minionone_accept_workspace_result",ackargs)
        self.assertEqual(ack["state"],"COMPLETED")
        self.assertEqual(self.rpc("minionone_accept_workspace_result",ackargs),ack)
        self.assertEqual(len(self.model.calls),4)
        self.assertEqual(self.transcript(job),records,"logs remain after ACK")

    def test_staged_route_real_http_seal_start_publish_ack(self):
        job=self.staged()
        self.assertEqual(self.outcome(job)["state"],"RESULT_READY")
        self.assert_output(job,"SYNTHETIC STAGED CONTENT")
        result=self.rpc("minionone_workspace_result",{"job_id":job["job_id"]})
        self.assertEqual(self.rpc("minionone_accept_workspace_result",{
            "job_id":job["job_id"],"expected_version":result["version"],
            "result_digest":result["result_digest"],
            "operation_id":"e2e-staged-ack-00001"})["state"],"COMPLETED")

    def test_model_denied_shell_three_times_and_logs_show_attempt(self):
        self.model.mode="denied"
        job=self.direct("e2e-denied-00001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"FAILED",status)
        self.assertEqual(status["workspace_job"]["denials"],3)
        records=self.transcript(job)
        self.assertIn("execute_shell",json.dumps(records))
        path=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertFalse((path/"result.txt").exists())
        self.assertTrue((path/"worker-io.jsonl").is_file())

    def test_worker_question_waits_for_parent_not_auto_resumed(self):
        self.model.mode="question"
        job=self.direct("e2e-question-00001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"WAITING_FOR_PARENT",status)
        self.assertEqual(len(self.model.calls),1)
        self.assertIn("ask_question",json.dumps(self.transcript(job)))
        self.assertFalse((self.project/".minionone"/"jobs"/job["job_id"]/
                          "output"/"result.txt").exists())

    def test_malformed_provider_output_fails_and_original_body_is_logged(self):
        self.model.mode="malformed"
        job=self.direct("e2e-malformed-00001")
        status=self.outcome(job)
        # A syntactically invalid response was received, but the model
        # boundary is deliberately conservative about unaccounted effects.
        self.assertEqual(status["state"],"NEEDS_RECONCILIATION",status)
        self.assertIn('"choices":[]',json.dumps(self.transcript(job)))
        self.assertFalse((self.project/".minionone"/"jobs"/job["job_id"]/
                          "output"/"result.txt").exists())

    def test_unloaded_model_never_gets_generation(self):
        self.model.loaded=False
        job=self.direct("e2e-unloaded-00001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"FAILED",status)
        self.assertEqual(len(self.model.calls),0)
        self.assertTrue((self.root/"logs"/"minionone-errors.jsonl").is_file())

    def test_unpublished_model_finish_is_not_a_verified_result(self):
        self.model.mode="premature_finish"
        job=self.direct("e2e-finish-without-artifact-00001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"FAILED",status)
        self.assertIn("I am done without files.",json.dumps(self.transcript(job)))
        output=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertFalse((output/"result.txt").exists())
        self.rpc("minionone_workspace_result",{"job_id":job["job_id"]},error=True)

    def test_wrong_ack_digest_is_rejected_without_state_change(self):
        job=self.direct("e2e-bad-digest-00001")
        ready=self.outcome(job)
        self.assertEqual(ready["state"],"RESULT_READY",ready)
        result=self.rpc("minionone_workspace_result",{"job_id":job["job_id"]})
        bad=self.rpc("minionone_accept_workspace_result",{
            "job_id":job["job_id"],"expected_version":result["version"],
            "result_digest":"0"*64,"operation_id":"e2e-bad-ack-00001"},error=True)
        self.assertEqual(bad["error"]["code"],"PILOT_RESULT_NOT_DELIVERED")
        self.assertEqual(self.rpc("minionone_job_status",{"job_id":job["job_id"]})["state"],
                         "RESULT_READY")

    def test_mcp_rejects_foreign_bearer_without_touching_worker(self):
        body=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call",
                         "params":{"name":"minionone_delegate",
                         "arguments":{"project_id":"demo"}}})
        with closing(http.client.HTTPConnection("127.0.0.1",
                    self.manager.server_port,timeout=10)) as conn:
            conn.request("POST","/mcp",body,headers={
                "Authorization":"Bearer NOT-THE-APPROVED-PARENT",
                "Accept":"application/json, text/event-stream",
                "Content-Type":"application/json"})
            response=conn.getresponse()
            self.assertEqual(response.status,401)
            response.read()
        self.assertEqual(self.model.calls,[])
        self.assertIn("MCP_AUTH_DENIED",
                      (self.root/"logs"/"minionone-process.jsonl").read_text())

    def test_failure_after_file_fsync_before_sqlite_insert_quarantines(self):
        # Injection EXACTLY between persisted native file bytes and the
        # SQLite mirror INSERT. The original request remains readable, but
        # no provider invocation or completed work may be inferred.
        from unittest.mock import patch
        from minionone.live_job_files import LiveJobFiles
        original=LiveJobFiles.worker_event
        triggered={"once":False}
        def break_after_fsync(writer,record):
            original(writer,record)  # fsync/FlushFileBuffers succeeded.
            if record["kind"]=="MODEL_REQUEST" and not triggered["once"]:
                triggered["once"]=True
                raise OSError("synthetic after-fsync before-SQLite injection")
        with patch.object(LiveJobFiles,"worker_event",break_after_fsync):
            job=self.direct("e2e-after-fsync-before-sqlite-001")
            status=self.outcome(job)
        self.assertTrue(triggered["once"])
        self.assertEqual(status["state"],"NEEDS_RECONCILIATION",status)
        self.assertEqual(self.model.calls,[],"unsafely unlogged network call prohibited")
        path=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertIn("MODEL_REQUEST",(path/"worker-io.jsonl").read_text())
        self.assertFalse((path/"result.txt").exists())

    def test_same_length_on_disk_corruption_blocks_ready_and_quarantines(self):
        # Real HTTP provider + real file/DB boundary; inject only a single
        # native output byte-content deviation (same byte length). A size
        # comparison would miss this, full pinned content verification must not.
        from unittest.mock import patch
        from minionone.live_job_files import LiveJobFiles
        original=LiveJobFiles.worker_event
        def corrupt_once(writer,record):
            if record["kind"]=="MODEL_RESPONSE" and "SYNTHETIC-THINKING-TOOL" in record["body_utf8"]:
                record={**record,"body_utf8":
                    record["body_utf8"].replace("SYNTHETIC-THINKING-TOOL",
                                                "SYNTHETIC-THINKING-FOOL")}
            return original(writer,record)
        with patch.object(LiveJobFiles,"worker_event",corrupt_once):
            job=self.direct("e2e-mutated-live-log-00001")
            status=self.outcome(job)
        self.assertEqual(status["state"],"NEEDS_RECONCILIATION",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]/"output"
        self.assertTrue((root/"worker-io.jsonl").is_file())
        self.assertIn("SYNTHETIC-THINKING-FOOL",
                      (root/"worker-io.jsonl").read_text())
        self.assertNotIn("SYNTHETIC-THINKING-FOOL",json.dumps(self.transcript(job)))
        self.assertFalse((root/"result.txt").exists(),
                         "corrupt content must never be published as accepted result")

    def test_deadline_unknown_outcome_quarantines_next_generation(self):
        self.model.mode="timeout"
        job=self.direct("e2e-timeout-00001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"NEEDS_RECONCILIATION",status)
        second=self.rpc("minionone_delegate",{
            "project_id":"demo","goal":"New synthetic job",
            "context":"Should be blocked until reconciliation",
            "acceptance_criteria":["Do not start a second inference"],
            "files":{"sample.txt":"second synthetic document"},
            "operation_id":"e2e-second-after-timeout-00001"},error=True)
        self.assertEqual(second["error"]["code"],"PILOT_JOB_BUSY")
        self.assertEqual(len(self.model.calls),1)


if __name__=="__main__":unittest.main()
