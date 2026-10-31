"""v0.37 adversarial AppContainer rights regression (synthetic only)."""
import inspect
from pathlib import Path
import sys
import tempfile
import unittest

from minionone.win32_appcontainer_adversarial_v037 import (
    AdversarialAccessEvidence, run_fixed_adversarial_access_probe,
)
from minionone.win32_job_object_probe import JobProbeUnavailable


class AdversarialProbePolicyTests(unittest.TestCase):
    def test_evidence_cannot_claim_product_or_host_attestation(self):
        fields = inspect.signature(AdversarialAccessEvidence).parameters
        self.assertEqual(fields["host_security_attested"].default, False)
        self.assertEqual(fields["product_release"].default, "NO_GO")
        self.assertEqual(fields["worker_tool_exposed"].default, False)

    def test_never_exposes_caller_selected_program_arguments(self):
        signature = inspect.signature(run_fixed_adversarial_access_probe)
        self.assertEqual(list(signature.parameters), ["helper_executable"])
        for extras in ({"command": "cmd.exe"}, {"python": "print('hi')"},
                       {"model_input": "run tests"}, {"network": "example.com"},
                       {"manager_path": "C:\\Users\\Other\\secret"}):
            with self.subTest(extras=extras), self.assertRaises(TypeError):
                run_fixed_adversarial_access_probe(None, **extras)

    def test_ordinary_non_windows_host_refuses_to_claim_os_isolation(self):
        if sys.platform != "win32":
            with self.assertRaises(JobProbeUnavailable):
                run_fixed_adversarial_access_probe("appcontainer_adversary_v037.exe")


@unittest.skipUnless(sys.platform == "win32",
                     "real native AppContainer execution requires Windows")
class NativeAdversarialBoundaryTests(unittest.TestCase):
    def test_fixed_compiled_helper_enforces_four_denials_with_positive_controls(self):
        # The trusted CI build step supplies the fixed artifact path,
        # not an arbitrary model-controlled Windows executable.
        root = Path(__file__).resolve().parent.parent
        helper = root / "evidence" / "v037" / "appcontainer_adversary_v037.exe"
        self.assertTrue(helper.is_file(), "trusted MSVC helper must be compiled first")
        ev = run_fixed_adversarial_access_probe(helper)
        self.assertTrue(ev.real_appcontainer_token)
        self.assertTrue(ev.assigned_before_resume)
        self.assertTrue(ev.manager_canary_read_denied)
        self.assertTrue(ev.sibling_canary_read_denied)
        self.assertTrue(ev.output_canary_write_denied)
        self.assertTrue(ev.loopback_connect_denied)
        self.assertTrue(ev.ordinary_host_positive_control)
        self.assertTrue(ev.canary_integrity_preserved)
        self.assertTrue(ev.process_exited)
        self.assertFalse(ev.host_security_attested)
        self.assertFalse(ev.worker_tool_exposed)
        self.assertEqual(ev.product_release, "NO_GO")


if __name__ == "__main__":
    unittest.main()
