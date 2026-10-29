"""Normative v0.6 lifecycle invariants for a future canonical durable manager.

A pure decision policy, NOT a job manager, worker runtime or evidence verifier.
Every proof field MUST be derived/attested by a trusted manager component,
never copied from worker prose or untrusted MCP tool arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


class LifecycleRejected(ValueError):
    pass


_TRANSITIONS = {
 'ACCEPTED': ('VALIDATING','QUEUED','CANCEL_REQUESTED','FAILED'),
 'VALIDATING': ('QUEUED','CANCEL_REQUESTED','FAILED'),
 'QUEUED': ('RESOURCE_CHECK','RUNNING','CANCEL_REQUESTED','FAILED'),
 'RESOURCE_CHECK': ('RUNNING','CANCEL_REQUESTED','FAILED'),
 'RUNNING': ('WAITING_FOR_PARENT','PAUSED','INTERRUPTED','RESULT_READY','CANCEL_REQUESTED',
             'FAILED','RESTORATION_REQUIRED','NEEDS_RECONCILIATION'),
 'WAITING_FOR_PARENT': ('RESUMING','CANCEL_REQUESTED','FAILED','INTERRUPTED'),
 'PAUSED': ('RESUMING','CANCEL_REQUESTED'),
 'INTERRUPTED': ('RESUMING','CANCEL_REQUESTED','FAILED'),
 'RESUMING': ('RUNNING','CANCEL_REQUESTED','FAILED','INTERRUPTED','NEEDS_RECONCILIATION'),
 'CANCEL_REQUESTED': ('CANCELLED','FAILED','NEEDS_RECONCILIATION'),
 'RESULT_READY': ('INTEGRATING','COMPLETED','RESTORATION_REQUIRED','NEEDS_RECONCILIATION'),
 'INTEGRATING': ('COMPLETED','RESTORATION_REQUIRED','FAILED','NEEDS_RECONCILIATION'),
 'RESTORATION_REQUIRED': ('INTEGRATING','FAILED','NEEDS_RECONCILIATION'),
 'FAILED': ('RESUMING',),
 'NEEDS_RECONCILIATION': ('RUNNING','INTEGRATING','RESULT_READY','FAILED','CANCELLED',
                          'RESTORATION_REQUIRED'),
 'COMPLETED': (),
 'CANCELLED': (),
}
LEGAL_TRANSITIONS = {state: frozenset(next_states) for state,next_states in _TRANSITIONS.items()}
_HEX64 = re.compile(r'[a-f0-9]{64}\Z',re.ASCII)
_OUTCOMES = frozenset(('NOT_STARTED','RUNNING','QUESTION','DONE','CANCELLED','FAILED'))
_DELIVERY = frozenset(('NOT_NEEDED','PENDING','FAILED','DELIVERED'))
_RESTORE = frozenset(('NOT_NEEDED','PENDING','READY','FAILED'))
_ACK = frozenset(('NONE','ACKED'))


@dataclass(frozen=True, slots=True)
class Evidence:
    """Trusted *facts* from state owner, broker and provider attestation."""

    attempt_no: int = 0
    active_lease: bool = False
    worker_outcome: str = 'NOT_STARTED'
    result_digest: str | None = None
    integration_ack: str = 'NONE'
    notification_delivery: str = 'PENDING'
    parent_restore: str = 'NOT_NEEDED'
    error_code: str | None = None
    worker_quiescent: bool = False
    checkpoint_verified: bool = False
    reconciliation_required: bool = False
    reconciliation_verified: bool = False
    new_attempt: bool = False


def _require(cond: bool, reason: str):
    if not cond:
        raise LifecycleRejected(reason)


def check_evidence(e: Evidence):
    """Reject contradictory or malformed trusted-fact snapshots early."""
    _require(type(e) is Evidence,'evidence must be immutable trusted snapshot')
    for attr in ('active_lease','worker_quiescent','checkpoint_verified',
                 'reconciliation_required','reconciliation_verified','new_attempt'):
        _require(type(getattr(e,attr)) is bool,'invalid evidence boolean')
    _require(type(e.attempt_no) is int and 0 <= e.attempt_no <= 1000,
             'invalid attempt counter')
    _require(e.worker_outcome in _OUTCOMES, 'invalid worker outcome')
    _require(e.notification_delivery in _DELIVERY,'invalid delivery status')
    _require(e.parent_restore in _RESTORE,'invalid restoration status')
    _require(e.integration_ack in _ACK,'invalid integration acknowledgement')
    _require(e.result_digest is None or
             (type(e.result_digest) is str and _HEX64.fullmatch(e.result_digest) is not None),
             'invalid result digest')
    _require(e.error_code is None or
             (type(e.error_code) is str and 1 <= len(e.error_code) <= 64 and
              re.fullmatch(r'[A-Z0-9_]+',e.error_code,flags=re.ASCII) is not None),
             'invalid redacted error code')
    _require(not(e.active_lease and e.worker_quiescent),
             'quiescent worker cannot hold active lease')
    _require(not(e.reconciliation_required and e.reconciliation_verified),
             'unresolved and verified reconciliations conflict')
    return True


def check_transition(old: str, new: str, e: Evidence):
    """Validate a single requested state change; no implicit late repair."""
    _require(type(old) is str and type(new) is str and old in LEGAL_TRANSITIONS,
             'unknown source state')
    _require(new in LEGAL_TRANSITIONS[old], 'illegal or terminal state transition')
    check_evidence(e)

    if old == 'NEEDS_RECONCILIATION':
        _require(e.reconciliation_verified and not e.reconciliation_required,
                 'unverified external effect cannot resume automatically')
    if new == 'NEEDS_RECONCILIATION':
        _require(e.reconciliation_required and not e.active_lease,
                 'uncertain outcome must be fenced and quarantined')
    elif e.reconciliation_required:
        _require(False,'uncertain effect cannot be ignored')

    if new == 'RUNNING':
        _require(e.attempt_no >= 1 and e.active_lease and
                 e.worker_outcome == 'RUNNING', 'running requires an active fenced attempt')
    if new == 'RESUMING':
        _require(e.new_attempt and e.attempt_no >= 2 and
                 e.checkpoint_verified and not e.active_lease,
                 'resume requires verified checkpoint, epoch increment and no lease')
    if new == 'WAITING_FOR_PARENT':
        _require(e.worker_outcome == 'QUESTION','worker question required')
    if new == 'INTERRUPTED':
        _require(e.attempt_no > 0 and e.worker_outcome == 'RUNNING' and
                 e.error_code is not None and not e.active_lease,
                 'interruption must retain diagnostic and fence tool rights')
    if new == 'RESULT_READY':
        _require(e.worker_outcome == 'DONE' and e.result_digest is not None and
                 not e.active_lease and e.integration_ack == 'NONE',
                 'result requires verified manager digest and quiescent completed worker')
    if new == 'RESTORATION_REQUIRED':
        _require(e.parent_restore == 'FAILED' and
                 e.worker_outcome in ('DONE','FAILED'),
                 'restore failure requires actual error')
    if new == 'FAILED':
        _require(e.worker_outcome == 'FAILED' and e.error_code is not None,
                 'failure requires worker failure and redacted diagnostic')
    if new == 'CANCELLED':
        _require(e.worker_outcome == 'CANCELLED' and e.worker_quiescent and
                 not e.active_lease and e.attempt_no >= 0,
                 'cancelled requires verified termination/quiescence')
    if new == 'COMPLETED':
        _require(e.worker_outcome == 'DONE' and e.result_digest is not None and
                 e.integration_ack == 'ACKED' and
                 e.notification_delivery in ('NOT_NEEDED','DELIVERED') and
                 e.parent_restore in ('NOT_NEEDED','READY') and
                 not e.active_lease and e.error_code is None,
                 'completion requires independently verified integration and delivery')
    if new in ('COMPLETED','CANCELLED'):
        _require(not e.reconciliation_required,'cannot terminate uncertain effect')
    return True
