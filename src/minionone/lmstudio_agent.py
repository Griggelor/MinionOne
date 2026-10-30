"""Manager-only LM Studio tool-call planner for the bounded AgentLoop.

Only translates constrained localhost OpenAI-compatible chat responses into
untrusted ModelStep objects. NEVER executes tools, touches files, publishes
results, authorizes model text, or sends messages to Unsloth.
"""
from __future__ import annotations

import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .agent_loop import ModelStep, BrokerReply, ALLOWED_WORKER_TOOLS
from .lmstudio_probe import LMStudioProbe
from .diagnostics import classify_provider_failure, diagnostic as provider_diagnostic


class PlannerRejected(RuntimeError):
    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic if diagnostic is not None else provider_diagnostic(
            "PROVIDER_RESPONSE_INVALID")


_MODEL = re.compile(r"[A-Za-z0-9_./:-]{1,256}\Z", re.ASCII)
_TOOL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z", re.ASCII)
_CODE = re.compile(r"[A-Z0-9_]{1,64}\Z", re.ASCII)
_MAX_HISTORY_MESSAGES = 512
_MAX_BODY_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 128 * 1024


def _safe_arguments(value, depth=0):
    if depth > 8:
        return False
    if type(value) is dict:
        if len(value)>64:
            return False
        return all(type(k) is str and 1<=len(k)<=128 and
                   _safe_arguments(v,depth+1) for k,v in value.items())
    if type(value) is list:
        return len(value)<=64 and all(_safe_arguments(v,depth+1) for v in value)
    if type(value) is str:
        return len(value)<=4096
    if type(value) is int:
        return -(2**63) <= value <= 2**63-1
    if type(value) is float:
        return math.isfinite(value)
    if type(value) in (bool,type(None)):
        return True
    return False


_TOOLS = [
    {"type":"function",
     "function":{
       "name":name,
       "description":"Manager-broker controlled job-scoped operation; always subject to authorization.",
       "parameters":{"type":"object","properties":{},
                     "additionalProperties":True}
     }}
    for name in sorted(ALLOWED_WORKER_TOOLS)
]

_PARAMETERS = {
    'list_files': {'zone': {'type':'string','enum':['input','work']}},
    'read_file': {'path': {'type':'string','maxLength':96}},
    'create_work': {'name': {'type':'string','maxLength':64},
                    'content': {'type':'string','maxLength':3500}},
    'publish_artifact': {'name': {'type':'string','maxLength':64}},
    'report_progress': {'percent': {'type':'integer','minimum':0,'maximum':100}},
    'ask_question': {'question': {'type':'string','maxLength':1000}},
}
for _tool in _TOOLS:
    _properties = _PARAMETERS[_tool['function']['name']]
    _tool['function']['parameters'] = {
        'type':'object','properties':_properties,
        'required':list(_properties),'additionalProperties':False,
    }


class LMStudioAgentProvider:
    """Adapter for ONE tool request per completion. No model load or worker I/O.

    Strict validation; a provider asking for multiple tool calls in one
    completion is REFUSED rather than bypassing per-operation budgets.
    A future trusted manager/broker must do all capability checks.
    """

    def __init__(self, base_url, *, model_id, approved_prompt,
                 max_output_tokens=1024, api_key=None, system_prompt=None):
        if type(model_id) is not str or _MODEL.fullmatch(model_id) is None:
            raise PlannerRejected("invalid model identifier")
        if type(approved_prompt) is not str or not 1 <= len(approved_prompt) <= 8000:
            raise PlannerRejected("invalid approved prompt budget")
        if type(max_output_tokens) is not int or not 1<=max_output_tokens<=4096:
            raise PlannerRejected("invalid model output token limit")
        if (system_prompt is not None and
                (type(system_prompt) is not str or not 1<=len(system_prompt)<=8000)):
            raise PlannerRejected("invalid immutable worker system policy")
        self._provider=LMStudioProbe(base_url, timeout=2, api_key=api_key,
                                     max_response_bytes=_MAX_RESPONSE_BYTES)
        self._model_id=model_id
        self._max_output_tokens=max_output_tokens
        self._messages=(
            [{"role":"system","content":system_prompt}] if system_prompt is not None else []
        ) + [{"role":"user","content":approved_prompt}]
        self._pending=None

    def next_step(self, *, timeout_seconds):
        if (type(timeout_seconds) not in (int,float) or
                not math.isfinite(timeout_seconds) or
                not 0 < timeout_seconds <= 300):
            raise PlannerRejected("invalid inference deadline")
        if self._pending is not None:
            raise PlannerRejected("pending tool reply must be observed")
        if len(self._messages)>_MAX_HISTORY_MESSAGES-2:
            raise PlannerRejected("model context history is exhausted")
        payload={
            "model":self._model_id,
            "messages":self._messages,
            "tools":_TOOLS,
            "tool_choice":"auto",
            "stream":False,
            "max_tokens":self._max_output_tokens,
        }
        data=json.dumps(payload,sort_keys=True,allow_nan=False,
                        ensure_ascii=True,separators=(",",":")).encode("ascii")
        if len(data)>_MAX_BODY_BYTES:
            raise PlannerRejected("model history byte limit reached")
        headers={"Accept":"application/json","Content-Type":"application/json"}
        if self._provider.api_key is not None:
            headers["Authorization"]="Bearer "+self._provider.api_key
        req=Request(self._provider.base_url+"/v1/chat/completions",data=data,
                    headers=headers,method="POST")
        try:
            with self._provider._opener.open(req,timeout=float(timeout_seconds)) as response:
                if response.status!=200:
                    raise PlannerRejected("provider refused request")
                raw=response.read(_MAX_RESPONSE_BYTES+1)
        except (HTTPError,URLError,OSError,TimeoutError,ValueError) as exc:
            raise PlannerRejected("provider unavailable or refused",
                                  diagnostic=classify_provider_failure(exc)) from exc
        if len(raw)>_MAX_RESPONSE_BYTES:
            raise PlannerRejected("response exceeds byte budget")
        try:
            obj=json.loads(raw.decode("utf-8"))
            if type(obj) is not dict:
                raise ValueError("response not object")
            choices=obj.get("choices")
            if type(choices) is not list or len(choices)!=1:
                raise ValueError("invalid choices")
            choice=choices[0]
            if type(choice) is not dict or type(choice.get("message")) is not dict:
                raise ValueError("invalid message")
            message=choice["message"]
            if message.get("role")!="assistant":
                raise ValueError("wrong speaker")
            usage=obj.get("usage")
            if type(usage) is not dict:
                raise ValueError("missing accounting")
            total=usage.get("total_tokens")
            if type(total) is not int or not 1<=total<=2_000_000:
                raise ValueError("invalid token usage")
            details=usage.get("completion_tokens_details") or {}
            if type(details) is not dict:
                raise ValueError("malformed thinking usage")
            reasoning=details.get("reasoning_tokens",0)
            if (type(reasoning) is not int or reasoning<0 or reasoning>total):
                raise ValueError("invalid thinking tokens")
            finish_reason=choice.get("finish_reason")
            if finish_reason=="stop":
                if message.get("tool_calls"):
                    raise ValueError("unexpected unaccounted tool calls")
                text=message.get("content")
                if type(text) is not str or not 1<=len(text)<=8192:
                    raise ValueError("invalid final output")
                # No chain-of-thought fields retained in model history.
                return ModelStep("finish",text=text,total_tokens=total,
                                 reasoning_tokens=reasoning)
            if finish_reason!="tool_calls":
                raise ValueError("unfinished inference or unexpected format")
            calls=message.get("tool_calls")
            if type(calls) is not list or len(calls)!=1:
                raise ValueError("multi-tool batch denied")
            call=calls[0]
            if type(call) is not dict or call.get("type")!="function":
                raise ValueError("invalid function call")
            function=call.get("function")
            if type(function) is not dict:
                raise ValueError("invalid function")
            call_id=call.get("id")
            name=function.get("name")
            if (type(call_id) is not str or _TOOL_ID.fullmatch(call_id) is None or
                    type(name) is not str or not 1<=len(name)<=128):
                raise ValueError("invalid tool metadata")
            arguments=function.get("arguments")
            if type(arguments) is not str or len(arguments)>4096:
                raise ValueError("invalid serialized tool args")
            parsed=json.loads(arguments)
            if type(parsed) is not dict or not _safe_arguments(parsed):
                raise ValueError("invalid structured tool args")
            # Unknown tool name still reaches AgentLoop for counted denial,
            # but NEVER to its broker: the loop checks the strict allowlist.
            step=ModelStep("tool",name=name,arguments=parsed,
                           total_tokens=total,reasoning_tokens=reasoning)
            # The assistant function-call turn is required by LM Studio's
            # OpenAI-compatible history, but private reasoning is stripped.
            assistant={"role":"assistant","content":None,"tool_calls":[{
                "id":call_id,"type":"function",
                "function":{"name":name,"arguments":arguments}
            }]}
            self._messages.append(assistant)
            self._pending=(name,call_id)
            return step
        except (ValueError,TypeError,KeyError,UnicodeError,OverflowError) as exc:
            raise PlannerRejected("malformed or unsafe provider response") from exc

    def observe_tool(self, name, reply):
        """Accept manager-verified outcome data, including approved job documents.

        The pilot supplies sealed scoped UTF-8 content; arbitrary host data and
        provider reasoning are never inferred or authorized by this adapter.
        """
        if (self._pending is None or name != self._pending[0] or
                type(reply) is not BrokerReply or
                reply.status not in ("ok","DENIED") or
                type(reply.result_code) is not str or
                _CODE.fullmatch(reply.result_code) is None):
            raise PlannerRejected("unverified or unmatched tool response")
        if reply.data is not None and (type(reply.data) is not dict or not _safe_arguments(reply.data)):
            raise PlannerRejected('invalid scoped tool data')
        _,call_id=self._pending
        self._messages.append({
            "role":"tool",
            "tool_call_id":call_id,
            "content":json.dumps({"status":reply.status,
                                  "result_code":reply.result_code,
                                  **({'data':reply.data} if reply.data is not None else {})},
                                 sort_keys=True,separators=(",",":")),
        })
        self._pending=None
