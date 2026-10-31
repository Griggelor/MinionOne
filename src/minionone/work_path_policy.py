"""Uniform job Work/output name rules and interrupted-transaction quarantine.

All worker-supplied paths are still validated by the respective OS-native
broker. This module is only a *defense-in-depth* shared policy, not a
substitute for pinned handles or a recovered operation journal.
"""
from __future__ import annotations

from .agent_loop import BrokerScopeDenied
from .diagnostics import diagnostic

_INTERNAL_OUTPUT_NAMES = frozenset({
    "worker-io.jsonl", "job.log.jsonl", "minionone-process.jsonl",
    "minionone-errors.jsonl",
})


class WorkTransactionUncertain(RuntimeError):
    """An unfinished broker-owned temporary file requires operator reconciliation."""

    def __init__(self):
        super().__init__("unfinished Work mutation; preserve job filesystem")
        self.diagnostic = diagnostic("PILOT_FILE_CHANGED")


def is_pending_work_name(name):
    return type(name) is str and name.casefold().startswith("pending-")


def ensure_clean_work_entry(name):
    """An interrupted write is never ordinary Work data or a publishable source."""
    if is_pending_work_name(name):
        raise WorkTransactionUncertain()


def ensure_worker_work_name(name):
    """Reserve broker transaction filenames even if syntactically valid."""
    if is_pending_work_name(name):
        raise BrokerScopeDenied("internal Work transaction name denied")


def artifact_target(name):
    """Reject collisions with internal logs and all OS publisher staging names."""
    if type(name) is not str:
        raise BrokerScopeDenied("output artifact name denied")
    folded = name.casefold()
    if (folded in _INTERNAL_OUTPUT_NAMES or folded.startswith(("pending-", ".pending-"))):
        raise BrokerScopeDenied("manager-owned output name denied")
    return name


def ensure_unique_output_name(names, target):
    """No hidden casefold aliases even when storing receipts in case-sensitive SQLite."""
    artifact_target(target)
    if any(type(name) is not str or name.casefold() == target.casefold() for name in names):
        raise BrokerScopeDenied("immutable or ambiguous output artifact already exists")
