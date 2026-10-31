"""Fail-closed logical tool boxing; executable OS sandbox remains disabled."""
import unittest
from dataclasses import FrozenInstanceError

from minionone.agent_loop import (
    AgentConfigurationError, AgentLoop, AgentLimits, BrokerReply, ModelStep,
)
from minionone.boxed_tool_policy import (
    BoxedRuntimeUnavailable, BoxedToolGateway, WorkerToolGrant, profiles,
)
from minionone.lmstudio_agent import _TOOLS, _WORKSPACE_TOOLS


class Provider:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.observed = []

    def next_step(self, *, timeout_seconds):
        return next(self.steps)

    def observe_tool(self, name, reply):
        self.observed.append((name, reply.status, reply.result_code))


class PermissiveBroker:
    """Deliberately overly permissive: AgentLoop MUST enforce the grant."""
    def __init__(self):
        self.executed = []

    def execute(self, name, args, *, timeout_seconds):
        self.executed.append(name)
        return BrokerReply('ok', 'OK', {'x': 1})


def tool(name):
    return ModelStep('tool', name=name, arguments={}, total_tokens=3)


def finish():
    return ModelStep('finish', text='untrusted final', total_tokens=3)


class BoxedToolGrantTests(unittest.TestCase):
    def test_provider_and_manager_have_the_identical_explicit_toolsets(self):
        isolated = WorkerToolGrant.for_job_kind('ISOLATED_TEST')
        workspace = WorkerToolGrant.for_job_kind('WORKSPACE')
        self.assertEqual({x['function']['name'] for x in _TOOLS},
                         isolated.allowed)
        self.assertEqual({x['function']['name'] for x in _WORKSPACE_TOOLS},
                         workspace.allowed)
        self.assertEqual(workspace.allowed, profiles()['WORKSPACE'])
        self.assertFalse(workspace.execution_enabled)
        self.assertFalse(workspace.sandbox_attested)

    def test_no_model_or_parent_can_choose_a_code_execution_profile(self):
        for mode in ('BOXED_EXEC', 'run_shell', 'developer', '', None):
            with self.subTest(mode=mode), self.assertRaises(BoxedRuntimeUnavailable):
                WorkerToolGrant.for_job_kind(mode)
        with self.assertRaises(BoxedRuntimeUnavailable):
            WorkerToolGrant('WORKSPACE', profiles()['WORKSPACE'], execution_enabled=True)
        with self.assertRaises(BoxedRuntimeUnavailable):
            WorkerToolGrant('WORKSPACE', profiles()['WORKSPACE'], sandbox_attested=True)
        with self.assertRaises(BoxedRuntimeUnavailable):
            WorkerToolGrant('WORKSPACE', profiles()['ISOLATED_TEST'])
        with self.assertRaises(FrozenInstanceError):
            WorkerToolGrant.for_job_kind('WORKSPACE').execution_enabled = True

    def test_legacy_job_cannot_use_workspace_mutations_even_if_broker_allows(self):
        provider = Provider([tool('delete_work'), tool('create_work'), finish()])
        broker = PermissiveBroker()
        grant = WorkerToolGrant.for_job_kind('ISOLATED_TEST')
        result = AgentLoop(AgentLimits(), allowed_tools=grant.allowed).run(provider, broker)
        self.assertEqual(result.status, 'AWAITING_REVIEW')
        self.assertEqual(broker.executed, ['create_work'])
        self.assertEqual(provider.observed[0][1:], ('DENIED', 'FORBIDDEN_TOOL'))
        self.assertEqual(result.denials, 1)

    def test_workspace_job_can_use_scoped_work_but_never_execute_shell(self):
        provider = Provider([tool('write_work'), tool('execute_shell'), finish()])
        broker = PermissiveBroker()
        grant = WorkerToolGrant.for_job_kind('WORKSPACE')
        result = AgentLoop(AgentLimits(), allowed_tools=grant.allowed).run(provider, broker)
        self.assertEqual(result.status, 'AWAITING_REVIEW')
        self.assertEqual(broker.executed, ['write_work'])
        self.assertEqual(provider.observed[1][1:], ('DENIED', 'FORBIDDEN_TOOL'))
        self.assertEqual(result.denials, 1)

    def test_three_forbidden_exec_attempts_abort_without_invoking_any_runner(self):
        provider = Provider([tool('execute_shell'), tool('run_tests'), tool('python'), finish()])
        broker = PermissiveBroker()
        grant = WorkerToolGrant.for_job_kind('WORKSPACE')
        result = AgentLoop(AgentLimits(), allowed_tools=grant.allowed).run(provider, broker)
        self.assertEqual(result.status, 'SECURITY_ABORT')
        self.assertEqual(result.denials, 3)
        self.assertEqual(broker.executed, [])

    def test_gateway_never_delegates_unapproved_tool_even_when_called_directly(self):
        broker = PermissiveBroker()
        box = BoxedToolGateway(WorkerToolGrant.for_job_kind('WORKSPACE'), broker)
        for name in ('execute_shell', 'run_tests', 'unknown', 'powershell'):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                box.execute(name, {}, timeout_seconds=10)
        self.assertEqual(broker.executed, [])
        allowed = box.execute('read_file', {'path':'input/sample.txt'}, timeout_seconds=10)
        self.assertEqual(allowed.status, 'ok')
        self.assertEqual(broker.executed, ['read_file'])
        with self.assertRaises(PermissionError):
            box.execute('read_file', [], timeout_seconds=10)
        with self.assertRaises(PermissionError):
            box.execute('read_file', {}, timeout_seconds=0)

    def test_invalid_dynamic_grant_fails_before_receiving_a_model_step(self):
        for bad in (set(['read_file']), frozenset(['execute_shell']), frozenset()):
            with self.subTest(bad=bad), self.assertRaises(AgentConfigurationError):
                AgentLoop(AgentLimits(), allowed_tools=bad)


if __name__ == '__main__':
    unittest.main()
