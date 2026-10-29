"""CLI tests: deterministic status and safe synthetic provider diagnostics."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from minionone import cli


class TestCLI(unittest.TestCase):
    def invoke(self,argv):
        b=io.StringIO()
        with redirect_stdout(b):
            rc=cli.main(argv)
        return rc,json.loads(b.getvalue())

    def test_status_is_explicit_pre_alpha(self):
        rc,data=self.invoke(['status'])
        self.assertEqual(rc,0)
        self.assertFalse(data['worker_tools_enabled'])
        self.assertFalse(data['mcp_server_enabled'])
        self.assertFalse(data['windows_broker_verified'])
        self.assertEqual(data['release_gate'],'NO_GO')

    def test_provider_probe_no_inference(self):
        class Probe:
            def __init__(self,*a,**k):pass
            def models(self):return ['model-one']
        with patch.object(cli,'LMStudioProbe',Probe),patch.object(cli,'LMStudioCompletion') as complete:
            rc,data=self.invoke(['probe-lmstudio'])
        self.assertEqual(rc,0)
        self.assertEqual(data['models'],['model-one'])
        complete.assert_not_called()

    def test_probe_failure_safe_and_no_secrets(self):
        class Probe:
            def __init__(self,*a,**k):raise ValueError('secret-token-test')
        with patch.object(cli,'LMStudioProbe',Probe):
            rc,data=self.invoke(['probe-lmstudio'])
        self.assertEqual(rc,2)
        self.assertNotIn('secret-token-test',repr(data))

    def test_smoke_is_fixed_synthetic_prompt_and_redacts_text(self):
        class Completion:
            def __init__(self,*a,**k):pass
            def complete(self,model,prompt,**k):
                assert 'MINIONONE_SMOKE_2026' in prompt
                assert model=='flint'
                return {'text':'example confidential response','trusted':False}
        with patch.object(cli,'LMStudioCompletion',Completion):
            rc,data=self.invoke(['smoke','--model','flint'])
        self.assertEqual(rc,0)
        self.assertTrue(data['responded'])
        self.assertNotIn('confidential',repr(data))
        self.assertFalse(data['worker_tools_enabled'])

    def test_unavailable_model_does_not_claim_success(self):
        class Completion:
            def __init__(self,*a,**k):pass
            def complete(self,*a,**k):raise RuntimeError('private internals')
        with patch.object(cli,'LMStudioCompletion',Completion):
            rc,data=self.invoke(['smoke','--model','flint'])
        self.assertEqual(rc,2)
        self.assertFalse(data['responded'])
        self.assertNotIn('private internals',repr(data))


if __name__=='__main__':
    unittest.main()
