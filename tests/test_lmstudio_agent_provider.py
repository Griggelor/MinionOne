"""Synthetic HTTP responses for LM Studio tool-calling provider integration.

No real worker file broker, model instance or original Unsloth chat exists.
"""
import json
import unittest
from minionone.lmstudio_agent import LMStudioAgentProvider, PlannerRejected
from minionone.agent_loop import (
    AgentLoop, AgentLimits, BrokerReply, BrokerScopeDenied, ALLOWED_WORKER_TOOLS,
)


class FakeHTTP:
    status=200
    def __init__(self, payload):
        self.buf=json.dumps(payload).encode("utf-8") if isinstance(payload,dict) else payload
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,limit): return self.buf[:limit]


class FakeOpener:
    def __init__(self, payloads):
        self.payloads=iter(payloads)
        self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        return FakeHTTP(next(self.payloads))


def response(kind="stop", *, text="Candidate only", name="list_files",
             args="{}", total=17, reasoning=4, multiple=False):
    choice={"finish_reason":kind,
            "message":{"role":"assistant","content":text}}
    if kind=="tool_calls":
        choice["message"]["content"]=None
        choice["message"]["tool_calls"]=[
            {"id":"call-abc1","type":"function",
             "function":{"name":name,"arguments":args}}]
        if multiple:
            choice["message"]["tool_calls"].append({
                "id":"call-abc2","type":"function",
                "function":{"name":"shell","arguments":"{}"}})
    return {"choices":[choice],
            "usage":{"total_tokens":total,
                     "completion_tokens_details":{"reasoning_tokens":reasoning}}}


class FakeBroker:
    def __init__(self,deny=False):
        self.calls=[]
        self.deny=deny
    def execute(self,name,args,*,timeout_seconds):
        self.calls.append((name,args))
        if self.deny:
            raise BrokerScopeDenied("outside root")
        return BrokerReply("ok","ACK")


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider=LMStudioAgentProvider("http://127.0.0.1:1234",
                   model_id="worker-model",approved_prompt="synthetic work only",
                   max_output_tokens=512)
    def opener(self, *values):
        f=FakeOpener(values)
        self.provider._provider._opener=f
        return f

    def test_finish_is_untrusted_candidate_only(self):
        fake=self.opener(response())
        step=self.provider.next_step(timeout_seconds=10)
        self.assertEqual((step.action,step.text,step.total_tokens,step.reasoning_tokens),
                         ("finish","Candidate only",17,4))
        req,timeout=fake.requests[0]
        self.assertEqual(timeout,10)
        self.assertEqual(req.full_url,"http://127.0.0.1:1234/v1/chat/completions")
        data=json.loads(req.data)
        self.assertEqual(data["model"],"worker-model")
        self.assertEqual(data["tool_choice"],"auto")
        self.assertEqual(data["stream"],False)
        self.assertEqual(data["max_tokens"],512)
        self.assertEqual({t["function"]["name"] for t in data["tools"]},
                         ALLOWED_WORKER_TOOLS)
        self.assertFalse(any(t["function"]["name"]=="shell" for t in data["tools"]))
        self.assertEqual(data["messages"][0]["content"],"synthetic work only")

    def test_one_tool_round_trip_then_review_candidate(self):
        fake=self.opener(response("tool_calls",name="list_files",
                                  args='{"path":"input"}',total=7,reasoning=2),
                         response("stop",text="Summary for review",total=8,reasoning=1))
        broker=FakeBroker()
        r=AgentLoop(AgentLimits(max_loops=5)).run(self.provider,broker)
        self.assertEqual((r.status,r.loops,r.tool_calls,r.tokens_used),
                         ("AWAITING_REVIEW",2,1,15))
        self.assertFalse(r.delivered)
        self.assertEqual(broker.calls,[("list_files",{"path":"input"})])
        second=json.loads(fake.requests[1][0].data)
        self.assertEqual(second["messages"][-1]["role"],"tool")
        self.assertEqual(second["messages"][-1]["tool_call_id"],"call-abc1")
        self.assertEqual(json.loads(second["messages"][-1]["content"]),{"result_code":"ACK","status":"ok"})
        self.assertNotIn("reasoning_content",str(second))

    def test_out_of_scope_calls_are_counted_and_stopped_at_three(self):
        self.opener(*[response("tool_calls",name="read_file",args='{"path":"../outside"}')
                      for _ in range(4)])
        broker=FakeBroker(deny=True)
        r=AgentLoop(AgentLimits(max_loops=200,max_denials=3)).run(self.provider,broker)
        self.assertEqual((r.status,r.denials,r.loops),
                         ("SECURITY_ABORT",3,3))
        self.assertEqual(len(broker.calls),3)

    def test_unknown_tool_name_never_calls_broker(self):
        self.opener(*[response("tool_calls",name="shell") for _ in range(4)])
        broker=FakeBroker()
        r=AgentLoop(AgentLimits(max_loops=10)).run(self.provider,broker)
        self.assertEqual((r.status,r.denials),("SECURITY_ABORT",3))
        self.assertEqual(broker.calls,[])

    def test_tool_response_must_be_observed_by_matching_tool(self):
        self.opener(response("tool_calls",name="read_file"))
        self.provider.next_step(timeout_seconds=10)
        with self.assertRaises(PlannerRejected):
            self.provider.observe_tool("shell",BrokerReply("ok","ACK"))
        self.provider.observe_tool("read_file",BrokerReply("DENIED","SCOPE_DENIED"))
        with self.assertRaises(PlannerRejected):
            self.provider.observe_tool("read_file",BrokerReply("ok","ACK"))

    def test_parallel_tool_calls_must_fail_closed(self):
        self.opener(response("tool_calls",multiple=True))
        with self.assertRaises(PlannerRejected):
            self.provider.next_step(timeout_seconds=10)

    def test_malformed_tool_arguments_fail_closed(self):
        for args in ("not-json","[]",'{"x":9999999999999999999999999}',"{bad}"):
            with self.subTest(args=args):
                self.provider=LMStudioAgentProvider("http://127.0.0.1:1234",
                    model_id="worker",approved_prompt="test")
                self.opener(response("tool_calls",args=args))
                with self.assertRaises(PlannerRejected):
                    self.provider.next_step(timeout_seconds=10)

    def test_missing_or_zero_usage_rejected(self):
        for number in (0,True,-1,None):
            self.provider=LMStudioAgentProvider("http://127.0.0.1:1234",
                    model_id="worker",approved_prompt="test")
            self.opener(response(total=number))
            with self.subTest(number=number),self.assertRaises(PlannerRejected):
                self.provider.next_step(timeout_seconds=10)

    def test_no_reasoning_text_persisted(self):
        data=response()
        data["choices"][0]["message"]["reasoning_content"]="private hidden thinking"
        self.opener(data)
        self.provider.next_step(timeout_seconds=10)
        self.assertNotIn("private hidden thinking",str(self.provider._messages))

    def test_invalid_provider_endpoint_and_prompt(self):
        for url in ("http://evil.example:1234","https://127.0.0.1:1234",
                    "http://127.0.0.1:1234/malicious"):
            with self.subTest(url=url),self.assertRaises(Exception):
                LMStudioAgentProvider(url,model_id="worker",approved_prompt="approved")
        with self.assertRaises(PlannerRejected):
            LMStudioAgentProvider("http://127.0.0.1:1234",
                                  model_id="worker",approved_prompt="")
        with self.assertRaises(PlannerRejected):
            LMStudioAgentProvider("http://127.0.0.1:1234",
                                  model_id="worker\nINJECT",approved_prompt="hello")

    def test_invalid_response_size_and_finish_reason(self):
        self.opener(b"x"*(200000))
        with self.assertRaises(PlannerRejected):
            self.provider.next_step(timeout_seconds=10)
        self.provider=LMStudioAgentProvider("http://127.0.0.1:1234",
                                          model_id="worker",approved_prompt="test")
        self.opener(response("length"))
        with self.assertRaises(PlannerRejected):
            self.provider.next_step(timeout_seconds=10)

    def test_no_worker_broker_or_os_methods_exposed(self):
        for attribute in ("read_file","write_file","shell",
                          "execute_tool","run_worker","publish_artifact"):
            self.assertFalse(hasattr(self.provider,attribute))

if __name__=="__main__":
    unittest.main()
