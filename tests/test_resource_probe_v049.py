"""G1c read-only telemetry provenance; synthetic samples, not a hardware gate."""
import json
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch

from minionone.admission import InputRejected
from minionone.model_lifecycle import MemoryReading
from minionone.pilot_cli import main
from minionone.resource_probe import probe_resources
from minionone.worker_profiles import WorkerProviderError

NOW = 1025.0


class ResourceProbeTests(unittest.TestCase):
    def test_os_shared_counts_one_ram_pool_and_never_queries_nvml(self):
        requested = []
        def measure(index, gpu):
            requested.append((index, gpu))
            return MemoryReading(23552, None, NOW - 1)
        value = probe_resources('os-shared', measure=measure, clock=lambda: NOW)
        self.assertEqual(requested, [(0, False)])
        self.assertEqual(value['ram_available_mb'], 23552)
        self.assertIsNone(value['separate_vram_available_mb'])
        self.assertIsNone(value['separate_vram_source'])
        self.assertFalse(value['resource_admission_granted'])
        self.assertFalse(value['memory_topology_attested'])
        self.assertFalse(value['provider_model_residency_attested'])
        self.assertEqual(value['release_gate'], 'NO_GO')

    def test_explicit_nvidia_query_reports_discrete_vram_separately(self):
        requested = []
        def measure(index, gpu):
            requested.append((index, gpu))
            return MemoryReading(35000, 8000, NOW - 0.5)
        value = probe_resources('nvidia-nvml', 2, measure=measure, clock=lambda: NOW)
        self.assertEqual(requested, [(2, True)])
        self.assertEqual(value['separate_vram_available_mb'], 8000)
        self.assertEqual(value['separate_vram_source'], 'nvidia_nvml')
        self.assertFalse(value['resource_admission_granted'])

    def test_unknown_modes_and_silent_gpu_index_ignored_are_rejected_before_query(self):
        def unexpected(*args):
            self.fail('must not access driver/OS on invalid arguments')
        for mode, index in [('amd-discrete', 0), ('shared', 0), ('os-shared', 1),
                            ('os-shared', -1), ('nvidia-nvml', 16), ('nvidia-nvml', True),
                            (None, 0)]:
            with self.subTest(mode=mode, index=index), self.assertRaises(InputRejected):
                probe_resources(mode, index, measure=unexpected, clock=lambda: NOW)

    def test_malformed_telemetry_never_claims_success(self):
        bad = [MemoryReading(1000, None, NOW - 6), MemoryReading(1000, None, NOW + .01),
               MemoryReading(True, None, NOW - 1), MemoryReading(-1, None, NOW - 1),
               MemoryReading(1000, 11, NOW - 1), MemoryReading(1000, None, True),
               MemoryReading(1000, None, 'stale'), None]
        for reading in bad:
            with self.subTest(reading=reading), self.assertRaises(WorkerProviderError):
                probe_resources('os-shared', measure=lambda *_: reading, clock=lambda: NOW)
        for reading in [MemoryReading(1000, None, NOW - 1),
                        MemoryReading(1000, -1, NOW - 1),
                        MemoryReading(1000, True, NOW - 1)]:
            with self.subTest(reading=reading), self.assertRaises(WorkerProviderError):
                probe_resources('nvidia-nvml', measure=lambda *_: reading, clock=lambda: NOW)

    def test_cli_requires_no_state_or_model_and_returns_redacted_snapshot(self):
        buf = StringIO()
        with redirect_stdout(buf):
            with patch('minionone.resource_probe.probe_resources', return_value={
                    'release_gate': 'NO_GO', 'resource_admission_granted': False,
                    'ram_available_mb': 8192}) as probe:
                rc = main(['resource-probe', '--mode', 'os-shared'])
        self.assertEqual(rc, 0)
        self.assertEqual(probe.call_args.args, ('os-shared', 0))
        self.assertEqual(json.loads(buf.getvalue())['release_gate'], 'NO_GO')
        self.assertNotIn('token', buf.getvalue().lower())

    def test_cli_fails_closed_without_sensitive_diagnostics(self):
        err = StringIO()
        with (patch('minionone.resource_probe.probe_resources',
                    side_effect=WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')),
              redirect_stderr(err)):
            rc = main(['resource-probe', '--mode', 'os-shared'])
        self.assertEqual(rc, 2)
        self.assertNotIn('Traceback', err.getvalue())
        self.assertNotIn('token', err.getvalue().lower())


if __name__ == '__main__':
    unittest.main()
