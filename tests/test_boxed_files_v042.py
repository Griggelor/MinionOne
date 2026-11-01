"""v0.42 synthetic staged file rights, bounded receipt, no-replay gates."""
from dataclasses import asdict, fields
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from minionone.boxed_file_policy_v042 import (
    INPUT, RESULT, MANAGER_CANARY, SyntheticArtifactRejected,
    inspect_fixed_result, sha,
)
from minionone.boxed_ipc_journal_v038 import BoxedProbeJournal
from minionone.boxed_ipc_v038 import fixed_probe_reply_for_tests, ProtocolUncertain
from minionone.boxed_tool_policy import WorkerToolGrant
from minionone.win32_boxed_ipc_v039 import (
    BoxedFileEvidence, _FILE_MODES, _run_fixed_boxed_ipc_case,
)
from minionone.win32_job_object_probe import JobProbeUnavailable


class SyntheticFilePolicyTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.stage = Path(self._temp.name) / "stage"
        for name in ("input", "work", "output"):
            (self.stage / name).mkdir(parents=True)
        (self.stage / "input" / "sealed.bin").write_bytes(INPUT)
        (self.stage / "output" / "immutable.bin").write_bytes(MANAGER_CANARY)
        self.result = self.stage / "work" / "result.bin"
        self.result.write_bytes(RESULT)

    def test_accept_exact_synthetic_snapshot_only(self):
        self.assertEqual(inspect_fixed_result(self.stage), RESULT)
        self.assertEqual(sha(inspect_fixed_result(self.stage)), sha(RESULT))

    def test_wrong_and_oversized_contents_denied(self):
        for bad in (b"wrong", RESULT + b"x", b"A" * 4097, b""):
            with self.subTest(size=len(bad)):
                self.result.write_bytes(bad)
                with self.assertRaises(SyntheticArtifactRejected):
                    inspect_fixed_result(self.stage)

    def test_extra_work_artifacts_denied(self):
        (self.stage / "work" / "surprise.txt").write_bytes(b"x")
        with self.assertRaises(SyntheticArtifactRejected):
            inspect_fixed_result(self.stage)

    def test_unexpected_manager_output_file_denied(self):
        (self.stage / "output" / "unauthorized.bin").write_bytes(b"bad")
        with self.assertRaises(SyntheticArtifactRejected):
            inspect_fixed_result(self.stage)

    def test_input_or_manager_output_tampering_denied(self):
        for target, original in (
            (self.stage / "input" / "sealed.bin", INPUT),
            (self.stage / "output" / "immutable.bin", MANAGER_CANARY),
        ):
            target.write_bytes(b"tampered")
            with self.assertRaises(SyntheticArtifactRejected):
                inspect_fixed_result(self.stage)
            target.write_bytes(original)

    def test_result_symlink_denied_when_supported(self):
        target = self.stage / "link-target.bin"
        target.write_bytes(RESULT)
        self.result.unlink()
        try:
            self.result.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this runner")
        with self.assertRaises(SyntheticArtifactRejected):
            inspect_fixed_result(self.stage)

    def test_hardlinked_result_denied(self):
        alias = self.stage / "alias.bin"
        try:
            os.link(self.result, alias)
        except OSError:
            self.skipTest("hardlink creation unavailable")
        with self.assertRaises(SyntheticArtifactRejected):
            inspect_fixed_result(self.stage)

    def test_journal_quarantine_stays_durable_and_cannot_be_redispatched(self):
        path = Path(self._temp.name) / "receipts.sqlite3"
        journal = BoxedProbeJournal(path)
        grant = journal.prepare("JOB-" + "a" * 32, "ATT-" + "b" * 32,
                                "OP-" + "c" * 32)
        journal.before_dispatch(grant)
        journal.observe_response(grant, fixed_probe_reply_for_tests(grant))
        rejected = journal.quarantine_artifact(grant)
        self.assertEqual(rejected.state, "NEEDS_RECONCILIATION")
        self.assertFalse(rejected.automatic_replay_allowed)
        reopened = BoxedProbeJournal(path)
        self.assertEqual(
            reopened.status(grant.job_id, grant.attempt_id, grant.operation_id).state,
            "NEEDS_RECONCILIATION",
        )
        with self.assertRaises(ProtocolUncertain):
            reopened.before_dispatch(grant)
        with self.assertRaises(ProtocolUncertain):
            reopened.prepare(grant.job_id, grant.attempt_id, grant.operation_id)

    def test_cannot_quarantine_before_receipt(self):
        journal = BoxedProbeJournal(Path(self._temp.name) / "no-receipt.sqlite3")
        grant = journal.prepare("JOB-" + "1" * 32, "ATT-" + "2" * 32,
                                "OP-" + "3" * 32)
        with self.assertRaises(ProtocolUncertain):
            journal.quarantine_artifact(grant)

    def test_no_worker_execution_or_dynamic_mode(self):
        self.assertEqual(_FILE_MODES, frozenset({"file_ok", "file_bad"}))
        for kind in ("ISOLATED_TEST", "WORKSPACE"):
            grant = WorkerToolGrant.for_job_kind(kind)
            self.assertFalse(grant.permits("run_tests"))
            self.assertFalse(grant.permits("execute_shell"))
            self.assertFalse(grant.execution_enabled)
            self.assertFalse(grant.sandbox_attested)
        for value in ("file", "open", "../scratch", "execute_shell", None, b"file_ok"):
            with self.assertRaises(JobProbeUnavailable):
                _run_fixed_boxed_ipc_case(value)
        defaults = {field.name: field.default for field in fields(BoxedFileEvidence)}
        self.assertFalse(defaults["worker_tool_exposed"])
        self.assertFalse(defaults["target_host_attested"])
        self.assertFalse(defaults["os_sandbox_attested"])
        self.assertEqual(defaults["product_release"], "NO_GO")


@unittest.skipUnless(
    sys.platform == "win32" and os.getenv("MINIONONE_V042_NATIVE") == "1",
    "native fixed-child synthetic file attempts only in Windows CI",
)
class NativeWindowsSyntheticFileTests(unittest.TestCase):
    def test_real_appcontainer_file_rights_and_manager_publication(self):
        reports = []
        for mode in ("file_ok", "file_bad"):
            with self.subTest(mode=mode):
                report = _run_fixed_boxed_ipc_case(mode)
                self.assertTrue(report.appcontainer_token_verified)
                self.assertEqual(report.input_digest, sha(INPUT))
                self.assertEqual(report.product_release, "NO_GO")
                self.assertFalse(report.worker_tool_exposed)
                self.assertFalse(report.os_sandbox_attested)
                self.assertFalse(report.target_host_attested)
                if mode == "file_ok":
                    self.assertTrue(report.synthetic_artifact_published)
                    self.assertFalse(report.result_validation_rejected)
                    self.assertEqual(report.result_digest, sha(RESULT))
                    self.assertEqual(report.journal_state, "RESPONSE_OBSERVED")
                else:
                    self.assertFalse(report.synthetic_artifact_published)
                    self.assertTrue(report.result_validation_rejected)
                    self.assertIsNone(report.result_digest)
                    self.assertEqual(report.journal_state, "NEEDS_RECONCILIATION")
                reports.append(asdict(report))
        out = (Path(__file__).resolve().parent.parent / "evidence" / "v042" /
               "FILE_HANDOFF_EVIDENCE.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "git_sha": os.environ.get("GITHUB_SHA", "UNRECORDED"),
            "evidence_class": "WINDOWS_CI_FIXED_SYNTHETIC_FILE_HANDOFF_ONLY",
            "worker_execution_enabled": False,
            "target_host_attested": False,
            "product_release": "NO_GO",
            "scenarios": reports,
        }, sort_keys=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
