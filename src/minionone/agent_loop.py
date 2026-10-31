"""Internal G2 agent-step coordinator: bounded loops, time, tool and security budgets.

Generic policy used by synthetic tests and the opt-in SQLite-document pilot.
Callbacks are trusted manager dependencies, NEVER MCP/worker-provided code.
This coordinator alone cannot stop a blocking callback: the pilot provides
an isolated, absolute-deadline network client and scoped transactional broker.
It does not establish native Windows filesystem or Unsloth chat attestation.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import time
from types import MappingProxyType


# Hard upper limits for a SINGLE broker request; not a per-job duration.
# These short operations are metadata/file tasks, NEVER script execution.
TOOL_TIMEOUT_CAP_SECONDS = MappingProxyType({
    'list_files': 10,
    'read_file': 30,
    'create_work': 60,
    'write_work': 60,
    'delete_work': 30,
    'rename_work': 30,
    'mkdir_work': 30,
    'rmdir_work': 30,
    'publish_artifact': 120,
    'report_progress': 5,
    'ask_question': 5,
})
ALLOWED_WORKER_TOOLS = frozenset(TOOL_TIMEOUT_CAP_SECONDS)


class AgentConfigurationError(ValueError):
    pass


class BrokerScopeDenied(PermissionError):
    """A trusted future file broker denied a requested operation."""


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_loops: int = 50
    max_tool_calls: int = 60
    max_tokens: int = 150_000
    wall_seconds: int | None = None
    runtime_profile: str = 'standard'
    model_call_seconds: int = 120
    tool_call_seconds: int = 30
    max_denials: int = 3
    max_idle_loops: int = 8

    def __post_init__(self):
        # v0.6 legacy contract is unchanged. 'extended' is a NEW
        # manager-only, explicitly selected v0.7 policy; never infer it
        # from an untrusted model request or MCP tool argument.
        if type(self.runtime_profile) is not str or self.runtime_profile not in (
                'standard', 'extended'):
            raise AgentConfigurationError('invalid trusted duration profile')
        if self.wall_seconds is None:
            object.__setattr__(
                self, 'wall_seconds',
                900 if self.runtime_profile == 'standard' else 3600)
        wall_min, wall_max = (
            (1, 1200) if self.runtime_profile == 'standard' else (3600, 14400)
        )
        checks = (
            ('max_loops', 1, 200),
            ('max_tool_calls', 1, 60),
            ('max_tokens', 1, 2_000_000),
            ('wall_seconds', wall_min, wall_max),
            ('model_call_seconds', 1, 300),
            ('tool_call_seconds', 1, 300),
            ('max_denials', 1, 3),
            ('max_idle_loops', 1, 200),
        )
        for name, low, high in checks:
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise AgentConfigurationError('unsafe agent limit')


@dataclass(frozen=True, slots=True)
class ModelStep:
    """Provider output is untrusted. Reasoning tokens are PART of total_tokens.

    The coordinator never publishes untrusted model text as a result. In the
    explicitly enabled workspace mode, the transport archives the exact
    provider JSON response (including any returned thinking fields) in a
    sensitive worker-I/O transcript, separate from redacted audit logs.
    """
    action: str
    name: str | None = None
    arguments: dict | None = None
    text: str | None = None
    total_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class BrokerReply:
    """Only accepted from the trusted manager-side broker, never model output."""
    status: str
    result_code: str = 'OK'
    data: dict | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    status: str
    loops: int
    tool_calls: int
    denials: int
    tokens_used: int
    candidate_sha256: str | None = None
    delivered: bool = False


def _valid_step(step):
    if type(step) is not ModelStep or type(step.action) is not str:
        return False
    if (type(step.total_tokens) is not int or step.total_tokens < 1 or
            type(step.reasoning_tokens) is not int or
            step.reasoning_tokens < 0 or
            step.reasoning_tokens > step.total_tokens):
        return False
    if step.action == 'tool':
        if (type(step.name) is not str or not 1 <= len(step.name) <= 128 or
                type(step.arguments) is not dict or step.text is not None):
            return False
        try:
            serialized = json.dumps(step.arguments, ensure_ascii=True,
                                    allow_nan=False, separators=(',', ':'))
        except (ValueError, TypeError, OverflowError):
            return False
        return len(serialized) <= 4096
    if step.action in ('finish', 'question'):
        return (step.name is None and step.arguments is None and
                type(step.text) is str and 1 <= len(step.text) <= 8192)
    if step.action == 'continue':
        return step.name is None and step.arguments is None and step.text is None
    return False


class AgentLoop:
    """Manager-internal finite state coordinator for bounded worker turns.

    This deliberately has NO default broker or provider and NO methods to
    publish, perform disk I/O, launch models, or notify an Unsloth chat.
    """

    def __init__(self, limits: AgentLimits | None = None, *, clock=None):
        self.limits = AgentLimits() if limits is None else limits
        if type(self.limits) is not AgentLimits:
            raise AgentConfigurationError('validated immutable limits required')
        self._clock = clock if clock is not None else time.monotonic

    def run(self, provider, broker, *, cancelled=None):
        loops = tools = denials = total = idle = 0
        end = self._clock() + self.limits.wall_seconds

        def receipt(status, *, candidate_sha256=None):
            return AgentRun(status, loops, tools, denials, total, candidate_sha256)

        for i in range(self.limits.max_loops):
            try:
                if cancelled is not None and cancelled():
                    return receipt('CANCELLED')
                remaining = end - self._clock()
            except Exception:
                return receipt('CANCELLED')
            if not math.isfinite(remaining) or remaining <= 0:
                return receipt('TIMEOUT')
            timeout = min(self.limits.model_call_seconds, remaining)
            try:
                start_model = self._clock()
                step = provider.next_step(timeout_seconds=timeout)
            except Exception:
                return receipt('PROVIDER_FAILURE')
            loops += 1
            try:
                after = self._clock()
            except Exception:
                return receipt('TIMEOUT')
            if (not math.isfinite(after) or after >= end or
                    after - start_model >= timeout):
                return receipt('TIMEOUT')
            if not _valid_step(step):
                return receipt('PROVIDER_INVALID')
            total += step.total_tokens
            if total > self.limits.max_tokens:
                return receipt('TOKEN_LIMIT')
            if step.action == 'finish':
                # A model saying 'done' is untrusted evidence, NOT completion.
                # Digest only; trusted artifact publisher and parent ACK absent.
                return receipt('AWAITING_REVIEW',candidate_sha256=sha256(
                    step.text.encode('utf-8')).hexdigest())
            if step.action == 'question':
                return receipt('WAITING_FOR_PARENT')
            if step.action == 'continue':
                idle += 1
                if idle >= self.limits.max_idle_loops:
                    return receipt('NO_PROGRESS')
                continue

            idle = 0
            if tools >= self.limits.max_tool_calls:
                return receipt('TOOL_LIMIT')
            tools += 1
            start_tool = None
            tool_timeout = None
            if step.name not in ALLOWED_WORKER_TOOLS:
                denials += 1
                reply = BrokerReply('DENIED', 'FORBIDDEN_TOOL')
            else:
                try:
                    tool_remaining = end - self._clock()
                except Exception:
                    return receipt('TIMEOUT')
                if not math.isfinite(tool_remaining) or tool_remaining <= 0:
                    return receipt('TIMEOUT')
                tool_timeout = min(self.limits.tool_call_seconds,
                                   TOOL_TIMEOUT_CAP_SECONDS[step.name],
                                   tool_remaining)
                try:
                    start_tool = self._clock()
                    reply = broker.execute(step.name, step.arguments,
                                           timeout_seconds=tool_timeout)
                except BrokerScopeDenied:
                    denials += 1
                    reply = BrokerReply('DENIED', 'SCOPE_DENIED')
                except Exception:
                    return receipt('BROKER_FAILURE')
                else:
                    if type(reply) is not BrokerReply or reply.status != 'ok':
                        return receipt('BROKER_FAILURE')
            try:
                tool_after = self._clock()
            except Exception:
                return receipt('TIMEOUT')
            if (not math.isfinite(tool_after) or tool_after >= end or
                    (start_tool is not None and
                     tool_after - start_tool >= tool_timeout)):
                return receipt('TIMEOUT')
            try:
                provider.observe_tool(step.name, reply)
            except Exception:
                return receipt('PROVIDER_FAILURE')
            if denials >= self.limits.max_denials:
                return receipt('SECURITY_ABORT')
            if step.name == 'ask_question' and reply.status == 'ok':
                return receipt('WAITING_FOR_PARENT')
            if tools >= self.limits.max_tool_calls:
                return receipt('TOOL_LIMIT')
        return receipt('LOOP_LIMIT')
