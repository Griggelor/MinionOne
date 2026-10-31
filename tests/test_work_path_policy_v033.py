"""Shared Work transaction recovery and output artifact-name policy tests."""
import unittest

from minionone.agent_loop import BrokerScopeDenied
from minionone.work_path_policy import (
    WorkTransactionUncertain, artifact_target, ensure_clean_work_entry,
    ensure_unique_output_name, ensure_worker_work_name,
)


class WorkPolicyTest(unittest.TestCase):
    def test_worker_cannot_create_an_internal_mutation_name(self):
        for name in ("pending-123", "PENDING-a.txt", "pending-result"):
            with self.subTest(name=name), self.assertRaises(BrokerScopeDenied):
                ensure_worker_work_name(name)
        ensure_worker_work_name("ordinary.txt")

    def test_orphan_work_transaction_triggers_stable_reconciliation_code(self):
        for name in ("pending-abc", "PENDING-123"):
            with self.subTest(name=name), self.assertRaises(WorkTransactionUncertain) as observed:
                ensure_clean_work_entry(name)
            self.assertEqual(observed.exception.diagnostic.code, "PILOT_FILE_CHANGED")
            self.assertFalse(observed.exception.diagnostic.retryable)
        ensure_clean_work_entry("ordinary.txt")

    def test_manager_output_and_pending_names_are_never_publishable(self):
        for name in ("worker-io.jsonl", "Worker-IO.JSONL", "job.log.jsonl",
                     "minionone-process.jsonl", "pending-123", "PENDING-temp.txt",
                     ".pending-abc"):
            with self.subTest(name=name), self.assertRaises(BrokerScopeDenied):
                artifact_target(name)
        self.assertEqual(artifact_target("result.txt"), "result.txt")

    def test_output_artifacts_cannot_have_casefold_aliases(self):
        with self.assertRaises(BrokerScopeDenied):
            ensure_unique_output_name(["Result.txt"], "result.txt")
        with self.assertRaises(BrokerScopeDenied):
            ensure_unique_output_name(["result.txt"], "result.txt")
        with self.assertRaises(BrokerScopeDenied):
            ensure_unique_output_name([], "WORKER-IO.JSONL")
        ensure_unique_output_name(["result.txt"], "other.txt")


if __name__ == "__main__":
    unittest.main()
