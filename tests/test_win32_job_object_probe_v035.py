"""Windows Job Object lifecycle feasibility, NOT an OS access sandbox."""
import os
import sys
import unittest
from unittest.mock import patch

from minionone.win32_job_object_probe import (
    JobProbeUnavailable, JobProbeEvidence, run_fixed_job_probe,
)


class JobObjectPolicyTests(unittest.TestCase):
    def test_only_hardcoded_modes_and_bounded_deadlines_are_accepted(self):
        for mode in ("python", "execute_shell", "run_tests", "-c print(1)",
                     "exit;rm -rf", "", None, {"code": "print(1)"}):
            with self.subTest(mode=mode), self.assertRaises(JobProbeUnavailable):
                run_fixed_job_probe(mode)
        for milliseconds in (0, 49, 5001, 100000, True, "100"):
            with self.subTest(ms=milliseconds), self.assertRaises(JobProbeUnavailable):
                run_fixed_job_probe("exit", timeout_ms=milliseconds)

    def test_no_os_sandbox_attestation_is_possible_from_probe_evidence(self):
        ev = JobProbeEvidence("exit", True, False, 0, 256, 2)
        self.assertTrue(ev.job_attached_before_resume)
        self.assertFalse(ev.worker_tool_exposed)
        self.assertFalse(ev.os_sandbox_attested)


@unittest.skipUnless(sys.platform == "win32",
                     "actual native Windows process/job-object API required")
class NativeJobObjectTests(unittest.TestCase):
    def test_suspended_child_is_assigned_before_execution(self):
        result = run_fixed_job_probe("exit", timeout_ms=4000)
        self.assertTrue(result.job_attached_before_resume)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.active_process_limit, 2)
        self.assertEqual(result.process_memory_limit_mb, 256)
        self.assertFalse(result.os_sandbox_attested)

    def test_only_minimal_explicit_environment_reaches_probe(self):
        with patch.dict(os.environ, {"MINIONONE_SANDBOX_SECRET_CANARY":
                                    "must-never-pass-into-child"}):
            result = run_fixed_job_probe("no-parent-env", timeout_ms=4000)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)

    def test_timeout_kills_the_job_without_claiming_a_result(self):
        result = run_fixed_job_probe("sleep", timeout_ms=100)
        self.assertTrue(result.job_attached_before_resume)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertFalse(result.worker_tool_exposed)


if __name__ == "__main__":
    unittest.main()
