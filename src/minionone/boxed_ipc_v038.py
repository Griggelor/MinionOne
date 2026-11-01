"""Bounded versioned boxed-runner IPC *contract*, not an OS channel or worker tool.

Only a fixed PING capability is represented. No shell, arbitrary script,
filesystem path, provider data, credentials or host-command selection exists
in the protocol. A valid response is UNTRUSTED until independently bound to
the OS child/attempt and durable manager journal; it never grants RESULT_READY.
The transport and real AppContainer execution are subsequent isolated gates.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import struct

VERSION = 1
MAX_FRAME_BYTES = 2048
_PREFIX_BYTES = 4
_JOB = re.compile(r"JOB-[0-9a-f]{32}\Z", re.ASCII)
_ATTEMPT = re.compile(r"ATT-[0-9a-f]{32}\Z", re.ASCII)
_OPERATION = re.compile(r"OP-[0-9a-f]{32}\Z", re.ASCII)
_NONCE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_REQUEST_KEYS = frozenset({
    "version", "kind", "job_id", "attempt_id", "operation_id", "nonce",
    "capability",
})
_REPLY_KEYS = frozenset({
    "version", "kind", "job_id", "attempt_id", "operation_id", "nonce",
    "capability", "result", "observation_sha256",
})
_PONG_DIGEST = hashlib.sha256(b"MINIONONE_V038_FIXED_PONG").hexdigest()


class FrameRejected(ValueError):
    """Malformed or unexpected peer frame; no side effects authorized."""


class ProtocolUncertain(RuntimeError):
    """Ambiguous transport/receipt; the manager must not auto-replay."""


def _obj_no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise FrameRejected("duplicate IPC field")
        out[key] = value
    return out


def _json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def encode_frame(document):
    if type(document) is not dict:
        raise FrameRejected("IPC frame must be an object")
    try:
        raw = _json(document)
    except (TypeError, ValueError, OverflowError):
        raise FrameRejected("unsupported IPC value") from None
    if not 1 <= len(raw) <= MAX_FRAME_BYTES:
        raise FrameRejected("IPC frame exceeds strict byte quota")
    return struct.pack(">I", len(raw)) + raw


def decode_frame(wire):
    if type(wire) is not bytes or len(wire) < _PREFIX_BYTES:
        raise FrameRejected("missing IPC frame header")
    size = struct.unpack(">I", wire[:4])[0]
    if not 1 <= size <= MAX_FRAME_BYTES or len(wire) != size + 4:
        raise FrameRejected("invalid IPC length or trailing data")
    try:
        raw = wire[4:]
        obj = json.loads(raw.decode("ascii"), object_pairs_hook=_obj_no_duplicates,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             FrameRejected("nonfinite IPC number")))
        if (type(obj) is not dict or _json(obj) != raw):
            raise FrameRejected("IPC must use canonical JSON object")
    except (UnicodeError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, FrameRejected):
            raise
        raise FrameRejected("invalid IPC JSON") from None
    return obj


def _id(value, pattern, name):
    if type(value) is not str or not pattern.fullmatch(value):
        raise FrameRejected("invalid " + name)
    return value


def _fields(doc, keys, kind):
    if type(doc) is not dict or frozenset(doc) != keys:
        raise FrameRejected("unsupported IPC fields")
    if type(doc["version"]) is not int or doc["version"] != VERSION:
        raise FrameRejected("unsupported IPC version")
    if type(doc["kind"]) is not str or doc["kind"] != kind:
        raise FrameRejected("unexpected IPC direction")
    _id(doc["job_id"], _JOB, "job")
    _id(doc["attempt_id"], _ATTEMPT, "attempt")
    _id(doc["operation_id"], _OPERATION, "operation")
    _id(doc["nonce"], _NONCE, "nonce")
    if type(doc["capability"]) is not str or doc["capability"] != "PROBE_PING":
        raise FrameRejected("executable capability is forbidden")


@dataclass(frozen=True, slots=True)
class ProbeGrant:
    """Manager-originated attempt identity. The model cannot select it."""

    job_id: str
    attempt_id: str
    operation_id: str
    nonce: str

    def __post_init__(self):
        _id(self.job_id, _JOB, "job")
        _id(self.attempt_id, _ATTEMPT, "attempt")
        _id(self.operation_id, _OPERATION, "operation")
        _id(self.nonce, _NONCE, "nonce")


def new_probe_grant(job_id, attempt_id, operation_id):
    return ProbeGrant(job_id, attempt_id, operation_id, secrets.token_hex(16))


def request_frame(grant):
    if type(grant) is not ProbeGrant:
        raise FrameRejected("manager probe grant required")
    return encode_frame({
        "version": VERSION, "kind": "request",
        "job_id": grant.job_id, "attempt_id": grant.attempt_id,
        "operation_id": grant.operation_id, "nonce": grant.nonce,
        "capability": "PROBE_PING",
    })


def validate_request(wire):
    doc = decode_frame(wire)
    _fields(doc, _REQUEST_KEYS, "request")
    return ProbeGrant(doc["job_id"], doc["attempt_id"],
                      doc["operation_id"], doc["nonce"])


def fixed_probe_reply_for_tests(grant, *, result="PONG"):
    """Synthetic fixed-child fixture only; it provides NO proof of OS identity."""
    if type(grant) is not ProbeGrant or type(result) is not str or result not in (
            "PONG", "DENIED"):
        raise FrameRejected("unsupported probe fixture")
    return encode_frame({
        "version": VERSION, "kind": "response",
        "job_id": grant.job_id, "attempt_id": grant.attempt_id,
        "operation_id": grant.operation_id, "nonce": grant.nonce,
        "capability": "PROBE_PING", "result": result,
        "observation_sha256": _PONG_DIGEST if result == "PONG" else "0" * 64,
    })


def validate_reply(wire, expected_grant):
    """Validate *bytes only*. Does not attest process, isolation or execution."""
    if type(expected_grant) is not ProbeGrant:
        raise FrameRejected("trusted expected probe grant required")
    doc = decode_frame(wire)
    _fields(doc, _REPLY_KEYS, "response")
    for field in ("job_id", "attempt_id", "operation_id", "nonce"):
        if doc[field] != getattr(expected_grant, field):
            raise FrameRejected("IPC response outside current attempt scope")
    if type(doc["result"]) is not str or doc["result"] not in ("PONG", "DENIED"):
        raise FrameRejected("invalid fixed IPC result")
    expected = _PONG_DIGEST if doc["result"] == "PONG" else "0" * 64
    if type(doc["observation_sha256"]) is not str or (
            doc["observation_sha256"] != expected):
        raise FrameRejected("IPC observation digest mismatch")
    return doc["result"]


class FrameReader:
    """Incremental exactly-one-frame decoder with a hard allocation ceiling."""

    def __init__(self):
        self._buffer = bytearray()
        self._expected = None
        self._complete = False
        self._closed = False

    def feed(self, chunk):
        if self._closed or self._complete or type(chunk) is not bytes:
            raise FrameRejected("frame already finished or nonbyte input")
        if len(self._buffer) + len(chunk) > MAX_FRAME_BYTES + 4:
            self._closed = True
            raise FrameRejected("stream exceeds maximum IPC frame")
        self._buffer.extend(chunk)
        if len(self._buffer) >= 4 and self._expected is None:
            n = struct.unpack(">I", self._buffer[:4])[0]
            if not 1 <= n <= MAX_FRAME_BYTES:
                self._closed = True
                raise FrameRejected("oversized IPC header")
            self._expected = n + 4
        if self._expected is not None and len(self._buffer) > self._expected:
            self._closed = True
            raise FrameRejected("multiple frames or trailing bytes")
        if self._expected is not None and len(self._buffer) == self._expected:
            try:
                result = decode_frame(bytes(self._buffer))
            except FrameRejected:
                self._closed = True
                raise
            self._complete = True
            return result
        return None

    def finish(self):
        self._closed = True
        if not self._complete:
            raise ProtocolUncertain("incomplete IPC transport; preserve job attempt")
