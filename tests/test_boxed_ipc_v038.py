"""Strict IPC parsing and crash-safe, *untrusted* fixed-probe receipts.

No real AppContainer IPC, Flint execution, output publishing or model tool use
is claimed. All tests use synthetic frame bytes or a temporary SQLite DB.
"""
import dataclasses
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from minionone.boxed_ipc_v038 import (
    FrameRejected, FrameReader, ProbeGrant, ProtocolUncertain,
    MAX_FRAME_BYTES, decode_frame, encode_frame, fixed_probe_reply_for_tests,
    new_probe_grant, request_frame, validate_reply, validate_request,
)
from minionone.boxed_ipc_journal_v038 import BoxedProbeJournal


JOB = "JOB-" + "a" * 32
ATT = "ATT-" + "b" * 32
OP = "OP-" + "c" * 32


def grant():
    return ProbeGrant(JOB, ATT, OP, "d" * 32)


class FramedProbeTests(unittest.TestCase):
    def test_canonical_roundtrip_and_untrusted_reply_does_not_attest_execution(self):
        expected = grant()
        req = request_frame(expected)
        self.assertLess(len(req), MAX_FRAME_BYTES)
        self.assertEqual(validate_request(req), expected)
        self.assertEqual(validate_reply(fixed_probe_reply_for_tests(expected),
                                        expected), "PONG")
        self.assertEqual(validate_reply(
            fixed_probe_reply_for_tests(expected, result="DENIED"), expected),
            "DENIED")

    def test_bad_ids_and_nonce_refused_before_encoding(self):
        for index, bad in enumerate((
            {"job_id": "JOB-../../x"},
            {"attempt_id": "ATT-" + "B"*32},
            {"operation_id": "OP-" + "c"*31},
            {"nonce": "x"*32},
        )):
            with self.subTest(index=index), self.assertRaises(FrameRejected):
                dataclasses.replace(grant(), **bad)

    def test_unknown_tools_and_extra_fields_cannot_be_smuggled(self):
        base = decode_frame(request_frame(grant()))
        for update in (
            {"capability": "run_tests"}, {"command": "powershell"},
            {"version": 2}, {"kind": "response"},
            {"nonce": "Z"*32}, {"capability": 1},
        ):
            with self.subTest(update=update):
                modified = dict(base, **update)
                wire = encode_frame(modified)
                with self.assertRaises(FrameRejected):
                    validate_request(wire)

    def test_refuse_wrong_scope_wrong_direction_wrong_digest_or_forged_result(self):
        reply = decode_frame(fixed_probe_reply_for_tests(grant()))
        for update in (
            {"job_id": "JOB-"+"e"*32},
            {"attempt_id": "ATT-"+"e"*32},
            {"operation_id": "OP-"+"e"*32},
            {"nonce": "e"*32},
            {"kind": "request"},
            {"observation_sha256": "0"*64},
            {"result": "SUCCESS"},
            {"result": True},
            {"text": "untrusted LLM final"},
        ):
            with self.subTest(update=update):
                with self.assertRaises(FrameRejected):
                    validate_reply(encode_frame(dict(reply, **update)), grant())

    def test_header_length_cannot_allocate_unbounded_memory(self):
        wire = request_frame(grant())
        for candidate in (
            b"", wire[:3],
            struct.pack(">I", MAX_FRAME_BYTES+1),
            struct.pack(">I", 0)+b"",
            struct.pack(">I", len(wire)-4+1)+wire[4:],
            struct.pack(">I", len(wire)-4-1)+wire[4:],
            wire+b"X",
        ):
            with self.subTest(candidate_len=len(candidate)):
                with self.assertRaises(FrameRejected):
                    decode_frame(candidate)

    def test_canonical_json_and_duplicate_keys_are_rejected(self):
        obj = decode_frame(request_frame(grant()))
        normal = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        pairs = normal.replace(b'"version":1', b'"version":1,"version":1')
        for raw in (b" "+normal, normal+b" ", pairs,
                    json.dumps(obj, indent=2).encode(),
                    b'[]', b'{"x":NaN}'):
            with self.subTest(raw=raw[:35]):
                with self.assertRaises(FrameRejected):
                    decode_frame(struct.pack(">I", len(raw))+raw)

    def test_canonical_float_version_is_rejected_by_schema_not_json_parser(self):
        obj = decode_frame(request_frame(grant()))
        obj["version"] = 1.0
        wire = encode_frame(obj)
        self.assertEqual(decode_frame(wire)["version"], 1.0)
        with self.assertRaises(FrameRejected):
            validate_request(wire)

    def test_streaming_reader_accepts_exactly_one_split_frame(self):
        wire = request_frame(grant())
        reader = FrameReader()
        self.assertIsNone(reader.feed(wire[:2]))
        self.assertIsNone(reader.feed(wire[2:12]))
        self.assertEqual(reader.feed(wire[12:]), decode_frame(wire))
        reader.finish()
        with self.assertRaises(FrameRejected):
            reader.feed(b"")

    def test_streaming_reader_rejects_truncated_extra_and_oversized(self):
        wire = request_frame(grant())
        r = FrameReader()
        self.assertIsNone(r.feed(wire[:-1]))
        with self.assertRaises(ProtocolUncertain):
            r.finish()
        r = FrameReader()
        with self.assertRaises(FrameRejected):
            r.feed(wire+wire)
        r = FrameReader()
        with self.assertRaises(FrameRejected):
            r.feed(struct.pack(">I", 0xffffffff))
        with self.assertRaises(FrameRejected):
            FrameReader().feed("untrusted string")

    def test_probes_accept_no_shell_or_dynamic_payload_inputs(self):
        with self.assertRaises(FrameRejected):
            fixed_probe_reply_for_tests(grant(), result="run_shell")
        with self.assertRaises(FrameRejected):
            request_frame({"job_id": JOB, "command": "powershell"})


class DurableReceiptJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "manager_only.sqlite3"
        self.j = BoxedProbeJournal(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _start(self):
        g = self.j.prepare(JOB, ATT, OP)
        frame = self.j.before_dispatch(g)
        self.assertEqual(validate_request(frame), g)
        return g

    def test_prepared_operations_are_durable_and_idempotent_before_dispatch(self):
        g = self.j.prepare(JOB, ATT, OP)
        self.assertEqual(self.j.prepare(JOB, ATT, OP), g)
        j2 = BoxedProbeJournal(self.db)
        self.assertEqual(j2.prepare(JOB, ATT, OP), g)
        s = j2.status(JOB, ATT, OP)
        self.assertEqual(s.state, "PREPARED")
        self.assertFalse(s.result_ready)
        self.assertFalse(s.worker_execution_enabled)
        self.assertFalse(s.os_sandbox_attested)
        self.assertFalse(s.automatic_replay_allowed)

    def test_persist_before_dispatch_and_never_replay_after_restart(self):
        g = self._start()
        self.assertEqual(BoxedProbeJournal(self.db).status(
            JOB, ATT, OP).state, "IN_FLIGHT")
        with self.assertRaises(ProtocolUncertain):
            BoxedProbeJournal(self.db).before_dispatch(g)
        with self.assertRaises(ProtocolUncertain):
            BoxedProbeJournal(self.db).prepare(JOB, ATT, OP)

    def test_wrong_nonce_cannot_dispatch_or_replay(self):
        g = self.j.prepare(JOB, ATT, OP)
        with self.assertRaises(ProtocolUncertain):
            self.j.before_dispatch(dataclasses.replace(g, nonce="e"*32))
        self.assertEqual(self.j.status(JOB, ATT, OP).state, "PREPARED")

    def test_no_conflicting_operation_id_across_jobs_or_attempts(self):
        self.j.prepare(JOB, ATT, OP)
        for job, attempt in (("JOB-"+"e"*32, ATT), (JOB, "ATT-"+"e"*32)):
            with self.subTest(job=job, attempt=attempt):
                with self.assertRaises(ProtocolUncertain):
                    self.j.prepare(job, attempt, OP)
                with self.assertRaises(ProtocolUncertain):
                    self.j.status(job, attempt, OP)

    def test_synthetic_observation_is_not_execution_ack_or_result_ready(self):
        g = self._start()
        wire = fixed_probe_reply_for_tests(g)
        observed = self.j.observe_response(g, wire)
        self.assertEqual(observed.state, "RESPONSE_OBSERVED")
        self.assertEqual(observed.observed_code, "PONG")
        self.assertEqual(observed.response_sha256, hashlib.sha256(wire).hexdigest())
        self.assertFalse(observed.result_ready)
        self.assertFalse(observed.os_sandbox_attested)
        self.assertFalse(observed.automatic_replay_allowed)
        self.assertEqual(BoxedProbeJournal(self.db).observe_response(g, wire),
                         observed)
        with self.assertRaises(ProtocolUncertain):
            self.j.before_dispatch(g)

    def test_denied_reply_is_observation_not_approval(self):
        g = self._start()
        s = self.j.observe_response(g, fixed_probe_reply_for_tests(
            g, result="DENIED"))
        self.assertEqual(s.observed_code, "DENIED")
        self.assertFalse(s.result_ready)

    def test_malformed_receipt_quarantines_durably_without_auto_restart(self):
        g = self._start()
        with self.assertRaises(ProtocolUncertain):
            self.j.observe_response(g, b"BAD")
        j2 = BoxedProbeJournal(self.db)
        self.assertEqual(j2.status(JOB, ATT, OP).state, "NEEDS_RECONCILIATION")
        with self.assertRaises(ProtocolUncertain):
            j2.prepare(JOB, ATT, OP)
        with self.assertRaises(ProtocolUncertain):
            j2.observe_response(g, fixed_probe_reply_for_tests(g))

    def test_cross_attempt_reply_quarantines_own_operation_not_other_job(self):
        g = self._start()
        wrong = dataclasses.replace(g, nonce="e"*32)
        with self.assertRaises(ProtocolUncertain):
            self.j.observe_response(g, fixed_probe_reply_for_tests(wrong))
        self.assertEqual(self.j.status(JOB, ATT, OP).state, "NEEDS_RECONCILIATION")

    def test_second_conflicting_receipt_quarantines_instead_of_overwriting(self):
        g = self._start()
        self.j.observe_response(g, fixed_probe_reply_for_tests(g))
        with self.assertRaises(ProtocolUncertain):
            self.j.observe_response(g, fixed_probe_reply_for_tests(
                g, result="DENIED"))
        self.assertEqual(self.j.status(JOB, ATT, OP).state,
                         "NEEDS_RECONCILIATION")

    def test_observation_without_prior_dispatch_is_never_trusted(self):
        g = self.j.prepare(JOB, ATT, OP)
        with self.assertRaises(ProtocolUncertain):
            self.j.observe_response(g, fixed_probe_reply_for_tests(g))
        self.assertEqual(self.j.status(JOB, ATT, OP).state, "PREPARED")

    def test_operation_nonce_is_unique_and_schema_rejects_duplicate_operation(self):
        g1 = self.j.prepare(JOB, ATT, OP)
        g2 = self.j.prepare("JOB-"+"e"*32, "ATT-"+"f"*32,
                            "OP-"+"1"*32)
        self.assertNotEqual(g1.nonce, g2.nonce)
        with sqlite3.connect(self.db) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO boxed_probe_receipts_v038 "
                    "(operation_id,job_id,attempt_id,nonce,state,request_sha256) "
                    "VALUES (?,?,?,?,?,?)",
                    ("OP-"+"2"*32, JOB, ATT, g1.nonce, "PREPARED", "0"*64))


if __name__ == "__main__":
    unittest.main()
