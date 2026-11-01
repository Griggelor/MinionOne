"""v0.41 real native AppContainer inheritance, Job and loopback controls.

No model-selected command, destination, executable, test code or worker tool.
Only a fixed trusted MSVC child performs the three bounded synthetic probes.
"""
from dataclasses import asdict, fields
import inspect
import json
import os
from pathlib import Path
import sys
import unittest

from minionone.win32_boxed_ipc_v039 import (
    BoxedBoundaryEvidence, _BOUNDARY_MODES, _run_fixed_boxed_ipc_case,
    run_fixed_boxed_ipc_probe,
)
from minionone.win32_job_object_probe import JobProbeUnavailable
from minionone.boxed_tool_policy import WorkerToolGrant


class BoundaryPolicyTests(unittest.TestCase):
    def test_boundary_scenarios_are_fixed_and_not_worker_tools(self):
        self.assertEqual(_BOUNDARY_MODES, frozenset({"handle", "breakaway", "network"}))
        self.assertEqual(list(inspect.signature(run_fixed_boxed_ipc_probe).parameters), [])
        for value in ("cmd.exe", "python", "execute_shell", "run_tests",
                      "host:443", "http://example.com", "", None, 3, b"network"):
            with self.subTest(value=value), self.assertRaises(JobProbeUnavailable):
                _run_fixed_boxed_ipc_case(value)
        for kind in ("ISOLATED_TEST", "WORKSPACE"):
            grant = WorkerToolGrant.for_job_kind(kind)
            self.assertFalse(grant.permits("run_tests"))
            self.assertFalse(grant.permits("execute_shell"))
            self.assertFalse(grant.execution_enabled)
            self.assertFalse(grant.sandbox_attested)

    def test_evidence_never_claims_global_network_or_release(self):
        fields_by_name = {field.name: field for field in fields(BoxedBoundaryEvidence)}
        for key in ("network_isolation_attested", "worker_tool_exposed",
                    "os_sandbox_attested"):
            self.assertIs(fields_by_name[key].default, False)
        self.assertEqual(fields_by_name["product_release"].default, "NO_GO")

    @unittest.skipIf(sys.platform == "win32", "negative non-Windows platform test")
    def test_fails_closed_when_windows_security_is_unavailable(self):
        with self.assertRaises(JobProbeUnavailable):
            _run_fixed_boxed_ipc_case("handle")


@unittest.skipUnless(
    sys.platform == "win32" and os.environ.get("MINIONONE_V041_NATIVE") == "1",
    "real v0.41 boundary scenarios run only on Windows with fixed MSVC child",
)
class RealWindowsBoundaryTests(unittest.TestCase):
    def test_actual_appcontainer_rejects_manager_handle_and_job_breakaway(self):
        observed = []
        for mode in ("handle", "breakaway", "network"):
            with self.subTest(mode=mode):
                result = _run_fixed_boxed_ipc_case(mode)
                self.assertTrue(result.real_appcontainer_token)
                self.assertTrue(result.job_assigned_before_resume)
                self.assertTrue(result.manager_canary_positive_control)
                self.assertEqual(result.mode, mode)
                self.assertEqual(result.journal_state, "RESPONSE_OBSERVED")
                self.assertFalse(result.network_isolation_attested)
                self.assertFalse(result.worker_tool_exposed)
                self.assertFalse(result.os_sandbox_attested)
                self.assertEqual(result.product_release, "NO_GO")
                if mode == "network":
                    self.assertIn(result.network_result, {
                        "EXPLICIT_LOOPBACK_WSAEACCES", "LOOPBACK_TIMEOUT_UNATTESTED",
                    })
                    self.assertIn(result.observed_code, ("PONG", "DENIED"))
                else:
                    self.assertEqual(result.network_result, "NOT_TESTED")
                    self.assertEqual(result.observed_code, "PONG")
                observed.append(asdict(result))
                if mode == "network":
                    print("V041_LOOPBACK_OUTCOME=" + result.network_result)
        output = (Path(__file__).resolve().parent.parent / "evidence" / "v041" /
                  "BOUNDARY_EVIDENCE.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "schema_version": 1,
            "git_sha": os.getenv("GITHUB_SHA", "UNRECORDED"),
            "evidence_kind": "REAL_WINDOWS_FIXED_CHILD_BOUNDARY_ATTEMPTS",
            "target_host_attested": False,
            "general_network_denial_attested": False,
            "generic_worker_execution_enabled": False,
            "release": "NO_GO",
            "probes": observed,
        }, sort_keys=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
