"""Real Windows AppContainer token feasibility, NOT job/host sandbox GO."""
import inspect
import sys
import unittest

from minionone.win32_appcontainer_probe import (
    AppContainerTokenEvidence, run_fixed_appcontainer_token_probe,
)


class AppContainerContract(unittest.TestCase):
    def test_probe_accepts_no_tool_input_or_executable_name(self):
        signature = inspect.signature(run_fixed_appcontainer_token_probe)
        self.assertEqual(len(signature.parameters), 0)
        for kw in ({"executable":"cmd.exe"}, {"command":"whoami /all"},
                   {"script":"print('unsafe')"}, {"model_text":"run tests"}):
            with self.subTest(arguments=kw), self.assertRaises(TypeError):
                run_fixed_appcontainer_token_probe(**kw)

    def test_output_is_not_misrepresented_as_host_access_attestation(self):
        ev = AppContainerTokenEvidence(True, True, True, True, True)
        self.assertFalse(ev.output_validated)
        self.assertFalse(ev.foreign_fs_denial_verified)
        self.assertFalse(ev.network_denial_verified)
        self.assertFalse(ev.worker_tool_exposed)
        self.assertFalse(ev.os_sandbox_attested)


@unittest.skipUnless(sys.platform == "win32",
                     "Native CreateAppContainerProfile and child token required")
class WindowsNativeAppContainerToken(unittest.TestCase):
    def test_real_child_token_is_appcontainer_and_job_attached_before_resume(self):
        result = run_fixed_appcontainer_token_probe()
        self.assertTrue(result.security_capabilities_attached)
        self.assertTrue(result.token_is_appcontainer)
        self.assertTrue(result.no_declared_network_capabilities)
        self.assertTrue(result.job_attached_before_resume)
        self.assertTrue(result.process_exited)
        self.assertFalse(result.os_sandbox_attested)
        self.assertFalse(result.worker_tool_exposed)


if __name__ == "__main__":
    unittest.main()
