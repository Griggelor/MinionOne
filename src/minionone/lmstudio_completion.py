"""Fail-closed text-only local LM Studio adapter; no worker tools or integration.

Uses the documented POST /v1/chat/completions endpoint through a strictly
loopback-only provider. Caller must approve prompts before sending them.
"""
from __future__ import annotations
import json
from urllib.request import Request
from urllib.error import HTTPError, URLError
from .lmstudio_probe import LMStudioProbe


class CompletionRejected(RuntimeError):
    pass


class LMStudioCompletion:
    def __init__(self, base_url='http://127.0.0.1:1234', *, timeout=10,
                 api_key=None, max_response_bytes=128*1024):
        self._provider = LMStudioProbe(base_url, timeout=timeout,
                                       api_key=api_key, max_response_bytes=max_response_bytes)

    def complete(self, model_id: str, prompt: str, *, max_tokens: int=256) -> dict:
        if (type(model_id) is not str or not 1 <= len(model_id) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in model_id)):
            raise CompletionRejected('invalid configured model ID')
        if type(prompt) is not str or not 1 <= len(prompt) <= 8000:
            raise CompletionRejected('invalid approved prompt budget')
        if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
            raise CompletionRejected('invalid token budget')
        payload = {'model':model_id, 'messages':[{'role':'user','content':prompt}],
                   'stream':False, 'max_tokens':max_tokens}
        body=json.dumps(payload,sort_keys=True,ensure_ascii=True,allow_nan=False).encode('ascii')
        headers={'Content-Type':'application/json','Accept':'application/json'}
        if self._provider.api_key is not None:
            headers['Authorization']='Bearer '+self._provider.api_key
        req=Request(self._provider.base_url+'/v1/chat/completions',data=body,
                    headers=headers,method='POST')
        try:
            with self._provider._opener.open(req,timeout=self._provider.timeout) as response:
                if response.status!=200:
                    raise CompletionRejected('inference refused')
                raw=response.read(self._provider.max_response_bytes+1)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError) as e:
            raise CompletionRejected('provider unavailable or refused') from e
        if len(raw)>self._provider.max_response_bytes:
            raise CompletionRejected('inference response too large')
        try:
            data=json.loads(raw.decode('utf-8'))
            if type(data) is not dict or type(data.get('choices')) is not list or len(data['choices'])!=1:
                raise ValueError('invalid choices')
            choice=data['choices'][0]
            if type(choice) is not dict or choice.get('finish_reason')!='stop':
                raise ValueError('incomplete or tool-call response')
            msg=choice.get('message')
            if type(msg) is not dict or msg.get('role')!='assistant' or msg.get('tool_calls'):
                raise ValueError('not a plain text assistant response')
            output=msg.get('content')
            if type(output) is not str or not 1 <= len(output) <= 16_384:
                raise ValueError('empty or overlong text')
        except (ValueError,TypeError,UnicodeError,KeyError) as e:
            raise CompletionRejected('malformed or unsafe inference response') from e
        # No execution, import, integration or automatic journaling of model prose.
        return {'text':output,'trusted':False}
