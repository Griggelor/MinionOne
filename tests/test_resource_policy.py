"""G1c conservative resource admission policy (synthetic telemetry only)."""
from dataclasses import replace
import unittest

from minionone.resource_policy import (
    ResourceGate, ResourceSnapshot, ResourceDenied, ADMISSION_STALE_NS,
)

GIB=1024**3
PARENT="rulith-parent"
WORKER="flint-worker"


class ResourceGateTests(unittest.TestCase):
    def setUp(self):
        self.gate=ResourceGate(
            parent_model_id=PARENT,
            estimated_worker_bytes={WORKER:20*GIB},
            required_headroom_bytes=8*GIB,
            max_workers=1)
        self.now=10_000_000_000
        self.good=ResourceSnapshot(
            measured_ns=self.now-1_000_000_000,
            total_bytes=128*GIB,
            available_bytes=48*GIB,
            parent_memory_bytes=64*GIB,
            parent_model_id=PARENT,
            parent_resident=True,
            active_workers=0)

    def assess(self,**fields):
        snap=replace(self.good,**fields)
        return self.gate.assess(snap,WORKER,now_monotonic_ns=self.now)

    def test_trusted_fresh_sample_with_sufficient_budget(self):
        result=self.assess()
        self.assertTrue(result.allowed)
        self.assertEqual(result.code,"BUDGET_ELIGIBLE")
        self.assertNotIn("token",str(result).lower())
        self.assertFalse(hasattr(self.gate,"load_model"))
        self.assertFalse(hasattr(self.gate,"unload_parent"))
        self.assertFalse(hasattr(self.gate,"launch_worker"))

    def test_must_not_evict_parent(self):
        for snap in [
            {"parent_resident":False},
            {"parent_model_id":"not-the-parent"},
            {"parent_memory_bytes":0},
        ]:
            with self.subTest(snap=snap):
                self.assertFalse(self.assess(**snap).allowed)

    def test_freshness_is_strict_and_fail_closed(self):
        self.assertFalse(self.assess(measured_ns=self.now-ADMISSION_STALE_NS-1).allowed)
        self.assertTrue(self.assess(measured_ns=self.now-ADMISSION_STALE_NS).allowed)
        self.assertFalse(self.assess(measured_ns=self.now+1).allowed)

    def test_unknown_model_and_no_measurement_denied(self):
        self.assertFalse(self.gate.assess(self.good,"unknown",now_monotonic_ns=self.now).allowed)
        self.assertFalse(self.gate.assess(None,WORKER,now_monotonic_ns=self.now).allowed)

    def test_memory_limit_preserves_margin(self):
        self.assertTrue(self.assess(available_bytes=28*GIB).allowed)
        self.assertFalse(self.assess(available_bytes=28*GIB-1).allowed)
        self.assertFalse(self.assess(available_bytes=25*GIB).allowed)

    def test_concurrency_is_bounded_even_when_memory_abundant(self):
        self.assertFalse(self.assess(active_workers=1).allowed)
        self.assertFalse(self.assess(active_workers=2).allowed)

    def test_telemetry_inconsistency_rejected(self):
        for change in [
            {"available_bytes":129*GIB},
            {"parent_memory_bytes":130*GIB},
            {"total_bytes":0},
            {"parent_memory_bytes":100*GIB,"available_bytes":48*GIB},
            {"available_bytes":-1},
            {"active_workers":-1},
        ]:
            with self.subTest(change=change):
                self.assertFalse(self.assess(**change).allowed)

    def test_invalid_types_do_not_escape_as_uncaught_typeerrors(self):
        for change in [
            {"available_bytes":True},
            {"parent_resident":1},
            {"active_workers":False},
            {"measured_ns":0.3},
            {"parent_model_id":[]},
            {"total_bytes":object()},
        ]:
            with self.subTest(change=change):
                self.assertFalse(self.assess(**change).allowed)
        self.assertFalse(self.gate.assess(self.good,WORKER,now_monotonic_ns=True).allowed)

    def test_denials_are_safe_and_bounded(self):
        result=self.assess(parent_resident=False)
        self.assertEqual(result.code,"RESOURCE_UNAVAILABLE")
        self.assertNotIn(PARENT,str(result))
        self.assertNotIn(WORKER,str(result))
        self.assertFalse(result.allowed)

    def test_estimate_must_be_configured_not_filled_from_model_response(self):
        with self.assertRaises(ResourceDenied):
            ResourceGate(parent_model_id=PARENT,estimated_worker_bytes={},
                         required_headroom_bytes=1,max_workers=1)
        with self.assertRaises(ResourceDenied):
            ResourceGate(parent_model_id=PARENT,estimated_worker_bytes={WORKER:True},
                         required_headroom_bytes=1,max_workers=1)

    def test_invalid_limits_cannot_weaken_policy(self):
        bad=[{"required_headroom_bytes":0},
             {"required_headroom_bytes":True},
             {"max_workers":0},
             {"max_workers":4},
             {"max_workers":True}]
        for change in bad:
            config={"parent_model_id":PARENT,"estimated_worker_bytes":{WORKER:20*GIB},
                    "required_headroom_bytes":8*GIB,"max_workers":1}
            config.update(change)
            with self.subTest(change=change),self.assertRaises(ResourceDenied):
                ResourceGate(**config)

    def test_sample_is_frozen_and_no_runtime_mutation(self):
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            self.good.available_bytes=0

    def test_validated_config_rejects_overflow_and_malicious_identifiers(self):
        for name in ("", "flint\ninjected", "f"*257):
            with self.subTest(name=name),self.assertRaises(ResourceDenied):
                ResourceGate(parent_model_id=PARENT,estimated_worker_bytes={name:1},
                             required_headroom_bytes=1,max_workers=1)
        for bytes_value in (-1,0,2**63):
            with self.subTest(value=bytes_value),self.assertRaises(ResourceDenied):
                ResourceGate(parent_model_id=PARENT,estimated_worker_bytes={WORKER:bytes_value},
                             required_headroom_bytes=1,max_workers=1)


if __name__=="__main__":
    unittest.main()
