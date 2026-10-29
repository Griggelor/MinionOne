"""Conservative G1c eligibility policy for one prospective local worker.

A pure, fail-closed calculation from trusted, freshly measured telemetry.
No host metrics, LM Studio calls, model loading/unloading, operating-system
reservations or actual worker authorization are implemented here.
A positive result is NOT a permit to start a worker: real target hardware
measurement, parent-model residency and transactional admission are separate.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


ADMISSION_STALE_NS = 5_000_000_000
_MAX = 2**63 - 1
_MODEL_ID = re.compile(r'[A-Za-z0-9_./:-]{1,256}\Z', re.ASCII)


class ResourceDenied(ValueError):
    """Trusted operator configuration lacks required safety bounds."""


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Manager-trusted *candidate* telemetry; not a proof of host origin."""
    measured_ns: int
    total_bytes: int
    available_bytes: int
    parent_memory_bytes: int
    parent_model_id: str
    parent_resident: bool
    active_workers: int


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    allowed: bool
    code: str


_UNAVAILABLE = ResourceDecision(False, "RESOURCE_UNAVAILABLE")
_ELIGIBLE = ResourceDecision(True, "BUDGET_ELIGIBLE")


def _valid_int(value, low=0):
    return type(value) is int and low <= value <= _MAX


def _model_name(value):
    return type(value) is str and _MODEL_ID.fullmatch(value) is not None


class ResourceGate:
    """Single-worker conservative UMA budget evaluation; no side effects.

    The caller MUST supply telemetry measured by a trusted host component,
    not from model output, remote MCP client or provider self-reported text.
    Model estimate must be calibrated on the target hardware, including
    context/KV cache, not invented at runtime.
    """

    def __init__(self, *, parent_model_id, estimated_worker_bytes,
                 required_headroom_bytes, max_workers=1):
        if not _model_name(parent_model_id):
            raise ResourceDenied("invalid trusted parent identity")
        if (type(estimated_worker_bytes) is not dict or
                not 1 <= len(estimated_worker_bytes) <= 16):
            raise ResourceDenied("calibrated worker model estimates required")
        safe = {}
        for name, estimate in estimated_worker_bytes.items():
            if not _model_name(name) or not _valid_int(estimate, 1):
                raise ResourceDenied("invalid worker model estimate")
            if name == parent_model_id:
                raise ResourceDenied("worker cannot be parent model")
            safe[name] = estimate
        if not _valid_int(required_headroom_bytes,1):
            raise ResourceDenied("reserved headroom required")
        # Until real target G1c is proven, concurrency > 1 remains disabled.
        if type(max_workers) is not int or max_workers != 1:
            raise ResourceDenied("single-worker policy required")
        self._parent = parent_model_id
        self._worker_estimates = safe
        self._headroom = required_headroom_bytes
        self._max_workers = max_workers

    def assess(self, snapshot, requested_worker_id, *, now_monotonic_ns):
        """Eligibility only, not an executable lease or resource reservation.

        Always returns a short, non-sensitive result, including on malformed
        telemetry; this avoids exception-based fail-open pathways.
        """
        if (type(snapshot) is not ResourceSnapshot or
                not _valid_int(now_monotonic_ns) or
                not _model_name(requested_worker_id) or
                requested_worker_id not in self._worker_estimates):
            return _UNAVAILABLE
        s = snapshot
        if (not _valid_int(s.measured_ns) or
                not _valid_int(s.total_bytes,1) or
                not _valid_int(s.available_bytes) or
                not _valid_int(s.parent_memory_bytes,1) or
                type(s.parent_resident) is not bool or
                not _valid_int(s.active_workers) or
                not _model_name(s.parent_model_id)):
            return _UNAVAILABLE
        if (s.measured_ns > now_monotonic_ns or
                now_monotonic_ns-s.measured_ns > ADMISSION_STALE_NS):
            return _UNAVAILABLE
        if (not s.parent_resident or s.parent_model_id != self._parent or
                s.available_bytes > s.total_bytes or
                s.parent_memory_bytes > s.total_bytes or
                s.available_bytes+s.parent_memory_bytes > s.total_bytes):
            return _UNAVAILABLE
        if s.active_workers >= self._max_workers:
            return _UNAVAILABLE
        minimum = self._worker_estimates[requested_worker_id] + self._headroom
        if minimum > _MAX or s.available_bytes < minimum:
            return _UNAVAILABLE
        return _ELIGIBLE
