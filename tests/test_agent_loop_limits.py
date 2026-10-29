"""G2 manager-only agent loop: bounded synthetic provider and synthetic broker.

These are policy/runtime-contract tests, NOT proof of LM Studio live agentic
worker execution, Win32 sandbox or Unsloth original-chat delivery.
"""
import unittest
from dataclasses import FrozenInstanceError
from minionone.agent_loop import (
    AgentLimits, AgentLoop, ModelStep, BrokerReply, BrokerScopeDenied,
    AgentConfigurationError, ALLOWED_WORKER_TOOLS,
)


class FakeProvider:
    def __init__(self, steps):
        self.steps=iter(steps)
        self.timeouts=[]
        self.observations=[]
    def next_step(self, *, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        return next(self.steps)
    def observe_tool(self, tool, reply):
        self.observations.append((tool,reply))


class FakeBroker:
    def __init__(self, decisions=None):
        self.decisions=decisions or {}
        self.calls=[]
    def execute(self, name, arguments):
        self.calls.append(name)
        outcome=self.decisions.get(name,"ok")
        if outcome=="deny":
            raise BrokerScopeDenied("outside JOB scope / forbidden path")
        if outcome=="crash":
            raise RuntimeError("BROKER_SECRET_SHOULD_NOT_LEAK")
        return BrokerReply("ok", result_code="ACK")


class Clock:
    def __init__(self): self.now=0.
    def __call__(self): return self.now


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.limits=AgentLimits(max_loops=200,max_tool_calls=400,
                                max_tokens=10000,wall_seconds=300,
                                model_call_seconds=30,max_denials=3,max_idle_loops=8)
    def runner(self, **limits):
        return AgentLoop(AgentLimits(**limits) if limits else self.limits,clock=self.clock)
    def tool(self, name="read_file", count=2, args=None):
        return ModelStep("tool", name=name, arguments=args or {"path":"input/test.txt"},
                         total_tokens=count, reasoning_tokens=1)

    def test_valid_bounded_tools_then_final_is_pending_review_not_delivered(self):
        provider=FakeProvider([self.tool("list_files"),self.tool("read_file"),
                               ModelStep("finish",text="candidate UNTRUSTED",total_tokens=3)])
        broker=FakeBroker()
        result=self.runner().run(provider,broker)
        self.assertEqual(result.status,"AWAITING_REVIEW")
        self.assertFalse(result.delivered)
        self.assertEqual(result.loops,3)
        self.assertEqual(result.tool_calls,2)
        self.assertEqual(result.tokens_used,7)
        self.assertEqual(len(result.candidate_sha256),64)
        self.assertNotIn("candidate UNTRUSTED",str(result))
        self.assertEqual(broker.calls,["list_files","read_file"])
        self.assertEqual(len(provider.observations),2)
        self.assertTrue(all(x>0 and x<=30 for x in provider.timeouts))

    def test_can_finish_exactly_on_loop_200(self):
        provider=FakeProvider([ModelStep("tool",name="report_progress",
                                         arguments={},total_tokens=1)]*199+
                              [ModelStep("finish",text="finished",total_tokens=1)])
        r=self.runner().run(provider,FakeBroker())
        self.assertEqual((r.status,r.loops),("AWAITING_REVIEW",200))

    def test_does_not_execute_iteration_201(self):
        provider=FakeProvider([ModelStep("tool",name="report_progress",
                                         arguments={},total_tokens=1)]*201)
        r=self.runner().run(provider,FakeBroker())
        self.assertEqual((r.status,r.loops),("LOOP_LIMIT",200))
        self.assertEqual(len(provider.timeouts),200)
        self.assertEqual(r.tool_calls,200)
        self.assertFalse(r.delivered)

    def test_default_is_50_and_hard_max_200(self):
        self.assertEqual(AgentLimits().max_loops,50)
        self.assertEqual(AgentLimits(max_loops=200).max_loops,200)
        for value in (201,0,True,False,2.3,"200"):
            with self.subTest(value=value),self.assertRaises(AgentConfigurationError):
                AgentLimits(max_loops=value)
        for params in ({"max_tool_calls":0},{"max_tokens":False},
                       {"wall_seconds":0},{"model_call_seconds":True},
                       {"max_denials":0},{"max_idle_loops":0}):
            with self.subTest(params=params),self.assertRaises(AgentConfigurationError):
                AgentLimits(**params)

    def test_forbidden_tools_denied_without_invoking_broker_then_third_quarantines(self):
        bad=["shell","execute_python","browser"]
        steps=[self.tool(name) for name in bad]+[ModelStep("finish",text="must not deliver")]
        provider=FakeProvider(steps)
        broker=FakeBroker()
        r=self.runner().run(provider,broker)
        self.assertEqual((r.status,r.denials,r.loops),("SECURITY_ABORT",3,3))
        self.assertEqual(broker.calls,[])
        self.assertEqual(len(provider.timeouts),3)
        self.assertFalse(r.delivered)
        self.assertNotIn("shell",str(r))

    def test_out_of_scope_file_access_three_attempts_quarantines(self):
        provider=FakeProvider([self.tool("read_file",args={"path":"../../secrets"}),
                               self.tool("read_file",args={"path":"../../secrets"}),
                               self.tool("create_work",args={"path":"../escape"})])
        broker=FakeBroker({"read_file":"deny","create_work":"deny"})
        r=self.runner().run(provider,broker)
        self.assertEqual(r.status,"SECURITY_ABORT")
        self.assertEqual(r.denials,3)
        self.assertEqual(broker.calls,["read_file","read_file","create_work"])
        self.assertTrue(all(reply.status=="DENIED" for _,reply in provider.observations))
        self.assertNotIn("../../secrets",str(r))

    def test_limit_tool_calls_independent_of_loops(self):
        provider=FakeProvider([self.tool("list_files")]*4)
        r=self.runner(max_loops=10,max_tool_calls=3).run(provider,FakeBroker())
        self.assertEqual((r.status,r.tool_calls,r.loops),("TOOL_LIMIT",3,3))
        self.assertEqual(len(provider.timeouts),3)

    def test_global_token_budget_including_reported_thinking(self):
        provider=FakeProvider([ModelStep("continue",total_tokens=4,reasoning_tokens=3),
                               ModelStep("finish",text="do not publish",total_tokens=4,
                                         reasoning_tokens=2)])
        r=self.runner(max_tokens=7).run(provider,FakeBroker())
        self.assertEqual(r.status,"TOKEN_LIMIT")
        self.assertEqual(r.tokens_used,8)
        self.assertIsNone(r.candidate_sha256)
        self.assertFalse(r.delivered)

    def test_thinking_usage_must_be_reported_consistently(self):
        provider=FakeProvider([ModelStep("finish",text="unsafe",
                                         total_tokens=1,reasoning_tokens=2)])
        r=self.runner().run(provider,FakeBroker())
        self.assertEqual(r.status,"PROVIDER_INVALID")
        self.assertFalse(r.delivered)

    def test_wall_deadline_checked_before_and_after_provider_call(self):
        class SlowProvider(FakeProvider):
            def next_step(self, *, timeout_seconds):
                result=super().next_step(timeout_seconds=timeout_seconds)
                self.outer.now=400
                return result
        p=SlowProvider([ModelStep("finish",text="too late")]);p.outer=self.clock
        r=self.runner().run(p,FakeBroker())
        self.assertEqual(r.status,"TIMEOUT")
        self.assertIsNone(r.candidate_sha256)
        self.assertLessEqual(p.timeouts[0],30)

    def test_immediate_timeout_does_not_invoke_provider(self):
        clock=Clock()
        p=FakeProvider([ModelStep("finish",text="ignore")])
        r=AgentLoop(AgentLimits(wall_seconds=1),clock=clock).run(
            p,FakeBroker(),cancelled=lambda:True)
        self.assertEqual(r.status,"CANCELLED")
        self.assertEqual(p.timeouts,[])

    def test_question_interrupts_without_claiming_final_result(self):
        provider=FakeProvider([ModelStep("question",text="Need a decision",total_tokens=2)])
        result=self.runner().run(provider,FakeBroker())
        self.assertEqual(result.status,"WAITING_FOR_PARENT")
        self.assertFalse(result.delivered)
        self.assertIsNone(result.candidate_sha256)

    def test_repeated_continue_stops_no_progress(self):
        p=FakeProvider([ModelStep("continue",total_tokens=1)]*10)
        r=self.runner(max_idle_loops=3).run(p,FakeBroker())
        self.assertEqual((r.status,r.loops),("NO_PROGRESS",3))

    def test_broker_exception_fail_closed_never_exposes_message(self):
        p=FakeProvider([self.tool("list_files")])
        r=self.runner().run(p,FakeBroker({"list_files":"crash"}))
        self.assertEqual(r.status,"BROKER_FAILURE")
        self.assertNotIn("BROKER_SECRET_SHOULD_NOT_LEAK",str(r))
        self.assertFalse(r.delivered)

    def test_provider_exception_fail_closed(self):
        class BrokenProvider:
            def next_step(self,*,timeout_seconds):
                raise RuntimeError("LEAK_MY_PROMPT")
        r=self.runner().run(BrokenProvider(),FakeBroker())
        self.assertEqual(r.status,"PROVIDER_FAILURE")
        self.assertNotIn("LEAK_MY_PROMPT",str(r))

    def test_unknown_or_malformed_step_fails_without_tools(self):
        for step in (ModelStep("tool",name="read_file",arguments="bad"),
                     ModelStep("tool",name="read_file",arguments={"x":"y"*10000}),
                     ModelStep("finish",text=""),
                     ModelStep("tool",name="list_files",arguments={},total_tokens=True)):
            with self.subTest(step=step):
                broker=FakeBroker()
                r=self.runner().run(FakeProvider([step]),broker)
                self.assertEqual(r.status,"PROVIDER_INVALID")
                self.assertEqual(broker.calls,[])

    def test_tool_broker_contract_cannot_fabricate_status(self):
        class BadBroker:
            def execute(self,name,arguments): return {"status":"ok"}
        r=self.runner().run(FakeProvider([self.tool()]),BadBroker())
        self.assertEqual(r.status,"BROKER_FAILURE")

    def test_worker_tools_are_exactly_six_and_no_general_os_io(self):
        self.assertEqual(ALLOWED_WORKER_TOOLS,frozenset({
            "list_files","read_file","create_work","publish_artifact",
            "report_progress","ask_question"}))
        self.assertNotIn("shell",ALLOWED_WORKER_TOOLS)
        self.assertNotIn("python",ALLOWED_WORKER_TOOLS)
        self.assertNotIn("http",ALLOWED_WORKER_TOOLS)
        limits=AgentLimits()
        with self.assertRaises(FrozenInstanceError): limits.max_loops=999

if __name__=="__main__":
    unittest.main()
