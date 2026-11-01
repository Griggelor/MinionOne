"""v0.40 real-process hostile IPC/crash probes, no model execution granted."""
from dataclasses import asdict
import inspect
import json
import os
from pathlib import Path
import sys
import unittest

from minionone.win32_boxed_ipc_v039 import (
    _run_fixed_boxed_ipc_case, _FAULT_MODES, BoxedFaultEvidence,
    run_fixed_boxed_ipc_probe,
)
from minionone.win32_job_object_probe import JobProbeUnavailable
from minionone.boxed_tool_policy import WorkerToolGrant

EXPECTED = {
    "crash": "IN_FLIGHT",
    "hang": "IN_FLIGHT",
    "truncated": "NEEDS_RECONCILIATION",
    "oversized": "NEEDS_RECONCILIATION",
    "wrong_nonce": "NEEDS_RECONCILIATION",
    "trailing": "NEEDS_RECONCILIATION",
}


class FixedFaultPolicyTests(unittest.TestCase):
    def test_public_probe_accepts_no_model_controlled_mode_or_command(self):
        self.assertEqual(list(inspect.signature(run_fixed_boxed_ipc_probe).parameters), [])
        with self.assertRaises(TypeError):
            run_fixed_boxed_ipc_probe(command="powershell")

    def test_only_six_fixed_internal_modes_without_shell_or_worker_rights(self):
        self.assertEqual(_FAULT_MODES, frozenset(EXPECTED))
        for unapproved in ("execute_shell", "run_tests", "cmd.exe", "powershell",
                           "../anything", "", None, b"crash", 42):
            with self.subTest(value=unapproved), self.assertRaises(JobProbeUnavailable):
                _run_fixed_boxed_ipc_case(unapproved)
        for kind in ("ISOLATED_TEST", "WORKSPACE"):
            g = WorkerToolGrant.for_job_kind(kind)
            for name in ("execute_shell", "run_tests", "python", "cmd"):
                self.assertFalse(g.permits(name))
            self.assertFalse(g.sandbox_attested)
            self.assertFalse(g.execution_enabled)

    def test_negative_evidence_defaults_are_not_product_or_sandbox_approval(self):
        ev = BoxedFaultEvidence("crash", "IN_FLIGHT", False)
        self.assertTrue(ev.failed_closed)
        self.assertFalse(ev.worker_tool_exposed)
        self.assertFalse(ev.os_sandbox_attested)
        self.assertEqual(ev.product_release, "NO_GO")


@unittest.skipUnless(
    sys.platform == "win32" and os.environ.get("MINIONONE_V040_NATIVE") == "1",
    "native hostile IPC cases require exact Windows CI MSVC fixed helper",
)
class RealAppContainerFaultTests(unittest.TestCase):
    def test_all_six_faults_are_durably_fenced_not_false_accepted(self):
        reports = []
        for mode, expected_state in EXPECTED.items():
            with self.subTest(mode=mode):
                evidence = _run_fixed_boxed_ipc_case(mode)
                self.assertEqual(evidence.mode, mode)
                self.assertEqual(evidence.journal_state, expected_state)
                self.assertTrue(evidence.failed_closed)
                self.assertFalse(evidence.automatic_replay_allowed)
                self.assertFalse(evidence.worker_tool_exposed)
                self.assertFalse(evidence.os_sandbox_attested)
                self.assertEqual(evidence.product_release, "NO_GO")
                reports.append(asdict(evidence))
        path = (Path(__file__).resolve().parent.parent / "evidence" /
                "v040" / "HOSTILE_IPC_EVIDENCE.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1,
            "git_sha": os.environ.get("GITHUB_SHA", "UNRECORDED"),
            "evidence_class": "WINDOWS_CI_FIXED_CHILD_FAULT_CONTRACT_ONLY",
            "product_release": "NO_GO",
            "host_security_attested": False,
            "worker_executable_tools_enabled": False,
            "faults": reports,
        }, sort_keys=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
