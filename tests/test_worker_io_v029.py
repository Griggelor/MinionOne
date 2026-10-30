"""Exact external worker I/O, native output publication, and Flash readability.

The external LM Studio call below is a local fake socket opener; no claim of
live Flint or real Unsloth is made. Windows CI uses the production NTFS
output publisher, not an invented direct filesystem path.
"""
import contextlib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from minionone.agent_loop import BrokerReply, ModelStep
from minionone.lmstudio_agent import LMStudioAgentProvider
from test_flash_delegation_v027 import FlashHTTPFixture


class FakeProviderResponse:
    def __init__(self,body):
        self.status=200
        self.raw=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.raw[:n]


class FakeProviderTransport:
    def __init__(self,response):
        self.body=response
        self.sent=[]
    def open(self,request,*,timeout):
        self.sent.append(request)
        return FakeProviderResponse(self.body)


class ActualLMStudioPayload(unittest.TestCase):
    def test_exact_request_response_body_and_real_broker_tool_reply(self):
        raw=(b'{"choices":[{"message":{"role":"assistant","content":null,'
             b'"tool_calls":[{"id":"call-1","type":"function",'
             b'"function":{"name":"read_file",'
             b'"arguments":"{\\"path\\":\\"input/sample.txt\\"}"}}]},'
             b'"finish_reason":"tool_calls"}],'
             b'"usage":{"total_tokens":20}}')
        events=[]
        provider=LMStudioAgentProvider("http://127.0.0.1:1234",
            model_id="test-flint",approved_prompt="Test synthetic task",
            system_prompt="Authorized six scoped tools only",
            api_key="private-not-in-logs",
            io_sink=lambda kind,data:events.append((kind,data)))
        fake=FakeProviderTransport(raw)
        provider._provider._opener=fake
        step=provider.next_step(timeout_seconds=3)
        self.assertEqual(step.name,"read_file")
        self.assertEqual(step.arguments,{"path":"input/sample.txt"})
        self.assertEqual(events[0][0],"MODEL_REQUEST")
        self.assertEqual(events[0][1].encode("ascii"),fake.sent[0].data)
        self.assertEqual(events[1],("MODEL_RESPONSE",raw.decode("utf-8")))
        reply=BrokerReply("ok","OK",{"path":"input/sample.txt","content":"EXACT CONTENT"})
        provider.observe_tool("read_file",reply)
        self.assertEqual(events[-1][0],"TOOL_RESPONSE")
        self.assertEqual(json.loads(events[-1][1]),provider._messages[-1])
        self.assertIn("EXACT CONTENT",events[-1][1])
        self.assertNotIn("private-not-in-logs",json.dumps(events))

    def test_capture_failure_before_transport_prevents_model_side_effect(self):
        provider=LMStudioAgentProvider("http://127.0.0.1:1234",
            model_id="test-flint",approved_prompt="Fake task",
            io_sink=lambda *_:(_ for _ in ()).throw(RuntimeError("journal down")))
        fake=FakeProviderTransport(b'{"choices":[]}')
        provider._provider._opener=fake
        with self.assertRaises(RuntimeError):
            provider.next_step(timeout_seconds=3)
        self.assertEqual(fake.sent,[])


class WireIOFixture:
    """Minimal transport fixture; manager/broker/publisher remain real."""
    def __init__(self,base_url,*,model_id,approved_prompt,system_prompt,io_sink,**kwargs):
        self._provider=SimpleNamespace(_opener=None)
        self.sink=io_sink
        self.index=0
        self.user=approved_prompt
        self.system=system_prompt
        self.steps=[
            ModelStep("tool",name="read_file",arguments={"path":"input/source.txt"},total_tokens=2),
            ModelStep("tool",name="create_work",arguments={"name":"result.txt",
                    "content":"FLASH FLINT BOUNDED TASK COMPLETE"},total_tokens=2),
            ModelStep("tool",name="publish_artifact",arguments={"name":"result.txt"},total_tokens=2),
            ModelStep("finish",text="worker says published",total_tokens=2)
        ]
    def next_step(self,*,timeout_seconds):
        self.index+=1
        step=self.steps[self.index-1]
        req=json.dumps({"messages":[{"role":"system","content":self.system},
                                   {"role":"user","content":self.user}],
                        "turn":self.index},sort_keys=True)
        self.sink("MODEL_REQUEST",req)
        payload={"finish_reason":"stop","content":step.text} if step.action=="finish" else {
            "finish_reason":"tool_calls","function":{"name":step.name,
                "arguments":step.arguments}}
        self.sink("MODEL_RESPONSE",json.dumps(payload,sort_keys=True))
        return step
    def observe_tool(self,name,reply):
        if reply.status!="ok":raise RuntimeError("fixture broker denied")
        self.sink("TOOL_RESPONSE",json.dumps({"tool":name,"status":reply.status,
                                             "data":reply.data},sort_keys=True))


class FlashRawWorkerIO(FlashHTTPFixture,unittest.TestCase):
    def test_worker_transcript_is_real_manager_output_file_and_flash_readable(self):
        import time
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",WireIOFixture):
            args=self.arguments()
            started=self.call("minionone_delegate",args)
            job=started["job_id"]
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
        self.assertEqual(status["state"],"RESULT_READY",status)
        transcript=self.call("minionone_worker_io",{"job_id":job,"after_seq":0,"limit":10})
        self.assertTrue(transcript["sensitive"])
        self.assertEqual(transcript["integrity"],"VERIFIED_HASH_CHAIN")
        events=list(transcript["events"])
        while transcript["has_more"]:
            transcript=self.call("minionone_worker_io",{
                "job_id":job,"after_seq":transcript["next_after_seq"],"limit":10})
            events.extend(transcript["events"])
        self.assertEqual(len(events),transcript["total_events"])
        self.assertTrue(events)
        kinds=[x["kind"] for x in events]
        self.assertIn("MODEL_REQUEST",kinds)
        self.assertIn("MODEL_RESPONSE",kinds)
        self.assertIn("TOOL_RESPONSE",kinds)
        self.assertIn("FLASH FLINT BOUNDED TASK COMPLETE",json.dumps(events))
        self.assertIn(args["goal"],json.dumps(events))
        exactfile=self.project/".minionone"/"jobs"/job/"output"/"worker-io.jsonl"
        self.assertTrue(exactfile.is_file())
        written=[json.loads(line) for line in exactfile.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(written,events)
        self.assertFalse((self.project/".minionone"/"jobs"/job/"work"/"worker-io.jsonl").exists())
        result=self.call("minionone_workspace_result",{"job_id":job})
        self.assertTrue(all(x['name']!='worker-io.jsonl' for x in result['files']))
        ack=self.call("minionone_accept_workspace_result",{
            "job_id":job,"expected_version":result["version"],
            "result_digest":result["result_digest"],"operation_id":"raw-io-ack-001"})
        self.assertEqual(ack["state"],"COMPLETED")
        self.assertEqual(self.call("minionone_worker_io",{"job_id":job})["events"],events[:10])

    def test_worker_cannot_publish_manager_reserved_log_name(self):
        from minionone.agent_loop import ModelStep
        class MaliciousLogWriter(WireIOFixture):
            def __init__(self,*a,**k):
                super().__init__(*a,**k)
                self.steps=[ModelStep("tool",name="create_work",arguments={
                    "name":"worker-io.jsonl","content":"FALSIFIED WORKER LOG"},total_tokens=2)]
        import time
        with patch("minionone.pilot_jobs.PilotJobs._readiness",return_value=None), \
             patch("minionone.pilot_jobs.LMStudioAgentProvider",MaliciousLogWriter):
            started=self.call("minionone_delegate",self.arguments())
            job=started["job_id"]
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=self.call("minionone_job_status",{"job_id":job})
                if status["state"] not in ("QUEUED","RESOURCE_CHECK","RUNNING"):
                    break
                time.sleep(.1)
        self.assertNotEqual(status["state"],"RESULT_READY")
        exactfile=self.project/".minionone"/"jobs"/job/"output"/"worker-io.jsonl"
        self.assertTrue(exactfile.exists())
        # The forged content MUST be visible as a rejected model proposal:
        # content fidelity is the whole point of the forensic transcript.
        self.assertIn("FALSIFIED WORKER LOG",exactfile.read_text())
        self.assertFalse((self.project/".minionone"/"jobs"/job/"output"/"result.txt").exists())


if __name__=="__main__":
    unittest.main()
