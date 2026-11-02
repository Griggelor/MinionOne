from hashlib import sha1
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from minionone import unsloth_ui_setup as setup

class NativeUISetupTests(unittest.TestCase):
    def test_real_RAM_probe_runs_in_the_bounded_fixed_child(self):
        import time
        from minionone.model_lifecycle import measure_memory,MemoryReading
        value=measure_memory()
        self.assertIs(type(value),MemoryReading)
        self.assertGreater(value.ram_mb,0)
        self.assertLess(time.monotonic()-value.measured,5)

    def test_source_guard_backup_poll_and_cleanup_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/setup.PATH;target.parent.mkdir(parents=True)
            source='function example() {\n  windowTarget.addEventListener("online", recover);\n  return () => {\n    windowTarget.removeEventListener("online", recover);\n  };\n}\n'
            target.write_text(source);data=target.read_bytes()
            with self.assertRaises(ValueError):setup.install(tmp)
            blob=sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            with patch.object(setup,'BLOB',blob):
                self.assertTrue(setup.install(tmp)['changed'])
                self.assertFalse(setup.install(tmp)['changed'])
            changed=target.read_text()
            self.assertIn('setInterval(onFocus, 2000)',changed)
            self.assertIn('clearInterval(minionOneRecovery)',changed)
            self.assertEqual(target.with_suffix(target.suffix+'.minionone-v046.bak').read_bytes(),data)
