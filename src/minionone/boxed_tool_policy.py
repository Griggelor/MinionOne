"""Trusted, immutable worker-tool grants; foundation for a future boxed runner.

This is a *logical capability boundary*, not a claim of OS process isolation.
The currently shipped runtime can only perform the eleven bounded broker tools
(or the six legacy document-pilot tools); arbitrary execution is NEVER enabled.
Untrusted model/parent text cannot select or expand a profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .agent_loop import ALLOWED_WORKER_TOOLS

_LEGACY = frozenset({
    "list_files", "read_file", "create_work", "publish_artifact",
    "report_progress", "ask_question",
})
_WORKSPACE = _LEGACY | frozenset({
    "write_work", "delete_work", "rename_work", "mkdir_work", "rmdir_work",
})
_PROFILES = MappingProxyType({
    "ISOLATED_TEST": _LEGACY,
    "WORKSPACE": _WORKSPACE,
})
# Explicitly non-existent at this stage; an application-level tool contract
# is no substitute for an OS sandbox with independently verified isolation.
_FORBIDDEN_EXEC_TOOLS = frozenset({
    "execute_shell", "run_shell", "run_command", "run_code", "execute_code",
    "run_tests", "spawn_process", "python", "powershell", "cmd", "bash",
})


class BoxedRuntimeUnavailable(RuntimeError):
    """Code execution requires separately designed and attested OS isolation."""


@dataclass(frozen=True, slots=True)
class WorkerToolGrant:
    """Single manager-selected job-kind grant; no caller-selected extra rights."""

    job_kind: str
    allowed: frozenset[str]
    execution_enabled: bool = False
    sandbox_attested: bool = False

    def __post_init__(self):
        if (type(self.job_kind) is not str or self.job_kind not in _PROFILES or
                type(self.allowed) is not frozenset or
                self.allowed != _PROFILES[self.job_kind] or
                self.allowed - ALLOWED_WORKER_TOOLS or
                self.execution_enabled is not False or
                self.sandbox_attested is not False):
            raise BoxedRuntimeUnavailable(
                "unapproved worker tool grant or unverified execution sandbox")

    @classmethod
    def for_job_kind(cls, job_kind):
        if type(job_kind) is not str or job_kind not in _PROFILES:
            raise BoxedRuntimeUnavailable("unknown or non-attested worker profile")
        return cls(job_kind, _PROFILES[job_kind])

    def permits(self, tool_name):
        return (type(tool_name) is str and tool_name in self.allowed
                and tool_name not in _FORBIDDEN_EXEC_TOOLS)

    def require(self, tool_name):
        if not self.permits(tool_name):
            raise BoxedRuntimeUnavailable("tool outside manager-approved profile")
        return tool_name


def profiles():
    """Frozen source-of-truth for provider-tool advertisement and AgentLoop."""
    return _PROFILES
