"""Explicit manager-selected extended time budgets, preserving v0.6 default.

This is a pure synthetic clock/broker contract test, not actual worker IO.
"""
import unittest
from minionone.agent_loop import (
    AgentConfigurationError, AgentLimits, AgentLoop, BrokerReply, ModelStep,
)

class Clock:
    def __init__(self): self.now=0.0
    def __call__(self): return self.now

class Provider:
    def __init__(self, clock, step=None, elapsed=0):
        self.clock=clock
        self.step=step or ModelStep("finish",text="UNTRUSTED",total_tokens=3)
        self.elapsed=elapsed
        self.timeouts=[]
    def next_step(self, *, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        self.clock.now+=self.elapsed
        return self.step

class Broker:
    def __init__(self):
        self.calls=[]
    def execute(self,name,args,*,timeout_seconds):
        self.calls.append((name,timeout_seconds))
        return BrokerReply("ok","ACK")

class LongRunPolicyTests(unittest.TestCase):
    def test_original_v06_standard_limits_unchanged(self):
        a=AgentLimits()
        self.assertEqual(a.runtime_profile,"standard")
        self.assertEqual(a.wall_seconds,900)
        self.assertEqual(a.max_tool_calls,60)
        self.assertEqual(a.max_loops,50)
        self.assertRaises(AgentConfigurationError,AgentLimits,wall_seconds=3600)
        self.assertRaises(AgentConfigurationError,AgentLimits,wall_seconds=1201)

    def test_long_profile_explicit_choice_defaults_to_one_hour(self):
        a=AgentLimits(runtime_profile="extended")
        self.assertEqual(a.wall_seconds,3600)
        self.assertEqual(a.runtime_profile,"extended")
        self.assertEqual(a.max_tool_calls,60)
        self.assertEqual(a.model_call_seconds,120)
        self.assertEqual(a.max_denials,3)

    def test_long_profile_may_run_four_hours_not_longer(self):
        a=AgentLimits(runtime_profile="extended",wall_seconds=14400,max_loops=200)
        self.assertEqual(a.wall_seconds,14400)
        for seconds in (0,1,899,1200,3599,14401,86400,True,"3600",2.5):
            with self.subTest(seconds=seconds),self.assertRaises(AgentConfigurationError):
                AgentLimits(runtime_profile="extended",wall_seconds=seconds)
        for profile in (None,True,1,"anything","Extended"):
            with self.subTest(profile=profile),self.assertRaises(AgentConfigurationError):
                AgentLimits(runtime_profile=profile)

    def test_long_profile_does_not_unlock_tools_loops_denials_or_tokens(self):
        for kwargs in ({"max_loops":201},{"max_tool_calls":61},
                       {"max_denials":4},{"max_tokens":2_000_001},
                       {"model_call_seconds":301},{"tool_call_seconds":301}):
            with self.subTest(kwargs=kwargs),self.assertRaises(AgentConfigurationError):
                AgentLimits(runtime_profile="extended",**kwargs)

    def test_one_hour_allows_late_valid_candidate_but_not_late_result(self):
        clock=Clock()
        p=Provider(clock,elapsed=3599)
        r=AgentLoop(AgentLimits(runtime_profile="extended",model_call_seconds=300),clock=clock).run(p,Broker())
        self.assertEqual((r.status,r.loops),("AWAITING_REVIEW",1))
        self.assertFalse(r.delivered)
        self.assertEqual(p.timeouts,[300])
        clock=Clock()
        p=Provider(clock,elapsed=3600)
        r=AgentLoop(AgentLimits(runtime_profile="extended"),clock=clock).run(p,Broker())
        self.assertEqual(r.status,"TIMEOUT")
        self.assertIsNone(r.candidate_sha256)

    def test_four_hour_deadline_still_fences_late_candidate(self):
        clock=Clock()
        p=Provider(clock,elapsed=14401)
        limits=AgentLimits(runtime_profile="extended",wall_seconds=14400)
        r=AgentLoop(limits,clock=clock).run(p,Broker())
        self.assertEqual(r.status,"TIMEOUT")

    def test_tool_specific_deadline_cannot_inherit_five_minute_global_cap(self):
        actual={"list_files":10,"read_file":30,"create_work":60,
                "publish_artifact":120,"report_progress":5,"ask_question":5}
        for name,cap in actual.items():
            with self.subTest(name=name):
                clock=Clock()
                provider=Provider(clock,ModelStep(
                    "tool",name=name,arguments={},total_tokens=1))
                broker=Broker()
                result=AgentLoop(AgentLimits(runtime_profile="extended",
                                   tool_call_seconds=300,max_loops=1),clock=clock).run(provider,broker)
                self.assertEqual(result.tool_calls,1)
                self.assertEqual(broker.calls,[(name,cap)])
                self.assertEqual(result.status,"TOOL_LIMIT" if False else "LOOP_LIMIT")

    def test_operator_may_set_lower_per_tool_cap(self):
        clock=Clock()
        p=Provider(clock,ModelStep("tool",name="publish_artifact",arguments={},total_tokens=1))
        b=Broker()
        r=AgentLoop(AgentLimits(runtime_profile="extended",tool_call_seconds=8,max_loops=1),
                    clock=clock).run(p,b)
        self.assertEqual(b.calls,[("publish_artifact",8)])
        self.assertEqual(r.status,"LOOP_LIMIT")

    def test_remaining_wall_time_has_priority_over_any_call_deadline(self):
        clock=Clock(); clock.now=3598
        # First clock reading defines job start; simulate passage within model step.
        p=Provider(clock,ModelStep("tool",name="read_file",arguments={},total_tokens=1),
                   elapsed=3599)
        b=Broker()
        r=AgentLoop(AgentLimits(runtime_profile="extended"),clock=clock).run(p,b)
        self.assertEqual(r.status,"TIMEOUT")
        self.assertEqual(b.calls,[])

if __name__=="__main__":
    unittest.main()
