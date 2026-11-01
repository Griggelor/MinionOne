"""v0.39 native Windows fixed-child IPC proof; no executable worker tools."""
from dataclasses import asdict, fields
import inspect
import json
import os
from pathlib import Path
import sys
import unittest

from minionone.boxed_tool_policy import WorkerToolGrant
from minionone.boxed_ipc_v038 import ProbeGrant, request_frame, validate_reply, FrameRejected
from minionone.win32_boxed_ipc_v039 import (
    BoxedIPCEvidence, _fixed_helper, run_fixed_boxed_ipc_probe,
)
from minionone.win32_job_object_probe import JobProbeUnavailable


class ProbeSurfaceTests(unittest.TestCase):
    def test_no_caller_selected_program_or_tool(self):
        self.assertEqual(list(inspect.signature(run_fixed_boxed_ipc_probe).parameters), [])
        for invalid in ({"command": "powershell"}, {"executable": "cmd.exe"},
                        {"model_input": "run code"}, {"path": "C:\\secrets"},
                        {"network": "localhost"}):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                run_fixed_boxed_ipc_probe(**invalid)

    def test_evidence_explicitly_disallows_worker_execution_and_host_attestation(self):
        ev = {f.name: f.default for f in fields(BoxedIPCEvidence)}
        self.assertIs(ev["worker_tool_exposed"], False)
        self.assertIs(ev["os_sandbox_attested"], False)
        self.assertEqual(ev["product_release"], "NO_GO")
        for kind in ("ISOLATED_TEST", "WORKSPACE"):
            grant = WorkerToolGrant.for_job_kind(kind)
            self.assertFalse(grant.permits("run_tests"))
            self.assertFalse(grant.permits("execute_shell"))
            self.assertFalse(grant.execution_enabled)
            self.assertFalse(grant.sandbox_attested)

    def test_old_ipc_schema_still_rejects_execution_payload(self):
        g = ProbeGrant("JOB-" + "a"*32, "ATT-" + "b"*32,
                       "OP-" + "c"*32, "d"*32)
        with self.assertRaises(FrameRejected):
            validate_reply(request_frame(g), g)

    @unittest.skipIf(sys.platform == "win32", "non-Windows-only denial")
    def test_other_platform_does_not_claim_windows_probe(self):
        with self.assertRaises(JobProbeUnavailable):
            run_fixed_boxed_ipc_probe()


@unittest.skipUnless(
    sys.platform == "win32" and os.environ.get("MINIONONE_V039_NATIVE") == "1",
    "native v0.39 test only runs with exact MSVC helper in Windows CI",
)
class WindowsNativeProbeTests(unittest.TestCase):
    def test_real_child_pipe_roundtrip_and_durable_observation(self):
        self.assertTrue(_fixed_helper().is_file())
        evidence = run_fixed_boxed_ipc_probe()
        self.assertTrue(evidence.real_appcontainer_token)
        self.assertTrue(evidence.assigned_before_resume)
        self.assertTrue(evidence.explicit_two_handle_inheritance)
        self.assertEqual(evidence.ipc_transport, "anonymous_pipes_handle_list")
        self.assertEqual(evidence.observed_code, "PONG")
        self.assertEqual(evidence.journal_state, "RESPONSE_OBSERVED")
        self.assertEqual(evidence.child_exit_code, 0)
        self.assertFalse(evidence.worker_tool_exposed)
        self.assertFalse(evidence.os_sandbox_attested)
        self.assertEqual(evidence.product_release, "NO_GO")
        evidence_path = (Path(__file__).resolve().parent.parent / "evidence" /
                         "v039" / "IPC_RESULT.json")
        report = asdict(evidence)
        report["evidence_class"] = "WINDOWS_CI_FIXED_CHILD_IPC_ONLY"
        report["git_sha"] = os.environ.get("GITHUB_SHA", "UNRECORDED")
        evidence_path.write_text(json.dumps(report, sort_keys=True, indent=2),
                                 encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
