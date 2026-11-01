"""Distribution contract: offline layout, hashes and wheel-version consistency."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('minionone_bundle_builder',ROOT/'scripts/build_system_test_bundle.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class SystemBundleTests(unittest.TestCase):
    def wheel(self,folder,version='0.19.0.dev0',internal=None):
        path=folder/f'minionone-{version}-py3-none-any.whl'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr(f'minionone-{version}.dist-info/METADATA','Name: minionone\nVersion: '+(internal or version)+'\n')
        return path
    def test_offline_bundle_has_complete_layout_and_verifiable_content(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);wheel=self.wheel(folder);output=folder/'system.zip'
            result=builder.build(wheel,output)
            self.assertFalse(result['native'])
            with zipfile.ZipFile(output) as archive:
                names=archive.namelist();prefix=names[0].split('/')[0]+'/'
                for required in ('1_START.cmd','2_UNSLOTH.cmd','3_SYSTEMTEST.cmd','bootstrap.py','README_DE.md','README_LEGACY_DE.md','WORKER_ROLES_DE.md','worker_profiles.local.example.json','worker_profiles.cloud.example.json','bundle.json','LEGACY_1_START.cmd','LEGACY_3_SYSTEMTEST.cmd'):
                    self.assertIn(prefix+required,names)
                manifest=json.loads(archive.read(prefix+'bundle.json'))
                self.assertEqual(manifest['wheel']['sha256'],sha256(wheel.read_bytes()).hexdigest())
                self.assertEqual(manifest['default_test'],'synthetic_sealed_workspace')
                self.assertEqual(manifest['product_release'],'NO_GO')
                self.assertFalse(manifest['boxed_execution_enabled'])
                for launcher in ('1_START.cmd','2_UNSLOTH.cmd','3_SYSTEMTEST.cmd'):
                    command=archive.read(prefix+launcher).decode('ascii')
                    self.assertIn('--state "%LOCALAPPDATA%\\MinionOne\\WorkspaceSystemTest"',command)
                    self.assertIn('exit /b %minionone_exitcode%',command)
                self.assertIn('serve --synthetic-workspace',archive.read(prefix+'1_START.cmd').decode())
                self.assertIn('workspace-self-test',archive.read(prefix+'3_SYSTEMTEST.cmd').decode())
                self.assertNotIn('WorkspaceSystemTest',archive.read(prefix+'LEGACY_1_START.cmd').decode())
                for line in archive.read(prefix+'SHA256SUMS.txt').decode().splitlines():
                    digest,name=line.split('  ',1)
                    self.assertEqual(sha256(archive.read(prefix+name)).hexdigest(),digest)
                self.assertFalse(any('credential.bin' in x or 'jobs.sqlite' in x or 'settings.json' in x for x in names))
    def test_mismatched_installed_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            with self.assertRaises(ValueError):builder.build(self.wheel(folder,internal='0.0.0'),folder/'bad.zip')
    def test_failed_executable_evidence_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);proof=folder/'proof.json';proof.write_text('{"passed":false}')
            with self.assertRaises(ValueError):builder.build(self.wheel(folder),folder/'bad.zip',evidence=proof)
