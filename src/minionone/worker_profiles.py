"""Trusted text-worker profiles. Never reads a parent chat or project files."""
from dataclasses import dataclass
import json
import multiprocessing
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .admission import InputRejected
from .diagnostics import diagnostic, classify_provider_failure


class WorkerProviderError(RuntimeError):
    def __init__(self, code):
        self.diagnostic = diagnostic(code)
        super().__init__(self.diagnostic.code)


@dataclass(frozen=True)
class WorkerProfile:
    profile_id: str
    provider: str
    model_id: str
    base_url: str
    instructions: str
    description: str = ''
    secret_env: str | None = None
    timeout_seconds: int = 120
    max_input_bytes: int = 16384
    max_output_tokens: int = 2048
    cloud_approved: bool = False
    token_parameter: str = 'max_tokens'

    def __post_init__(self):
        if (type(self.profile_id) is not str or
                re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', self.profile_id) is None or
                self.provider not in ('lmstudio', 'unsloth', 'cloud')):
            raise InputRejected('invalid configured worker profile')
        if type(self.description) is not str or len(self.description)>256 or '\x00' in self.description:
            raise InputRejected('invalid configured worker description')
        for value, ceiling in ((self.model_id, 256), (self.instructions, 8000)):
            if type(value) is not str or not value.strip() or len(value) > ceiling or '\x00' in value:
                raise InputRejected('invalid configured model or role')
        if type(self.base_url) is not str:
            raise InputRejected('invalid configured provider URL')
        try:
            u = urlsplit(self.base_url)
            port = u.port
        except ValueError:
            raise InputRejected('invalid configured provider URL') from None
        if (not u.hostname or u.username or u.password or u.query or u.fragment or
                u.path not in ('', '/', '/v1', '/v1/') or len(self.base_url) > 512):
            raise InputRejected('invalid configured provider URL')
        if self.provider == 'cloud':
            if u.scheme != 'https' or self.cloud_approved is not True or not self.secret_env:
                raise InputRejected('cloud needs explicit operator approval and HTTPS credentials')
        elif u.scheme != 'http' or u.hostname not in ('127.0.0.1', '::1') or not port:
            raise InputRejected('local workers require literal loopback HTTP')
        if type(self.cloud_approved) is not bool:
            raise InputRejected('invalid cloud permission')
        if self.secret_env is not None and (type(self.secret_env) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', self.secret_env) is None):
            raise InputRejected('credentials must reference an environment variable')
        for value, high in ((self.timeout_seconds, 300), (self.max_input_bytes, 65536),
                            (self.max_output_tokens, 8192)):
            if type(value) is not int or not 1 <= value <= high:
                raise InputRejected('invalid configured worker budget')
        if self.token_parameter not in ('max_tokens', 'max_completion_tokens'):
            raise InputRejected('unsupported provider token parameter')

    @property
    def endpoint(self):
        return self.base_url.rstrip('/').removesuffix('/v1') + '/v1'

    def catalog(self):
        return {'profile_id': self.profile_id, 'provider': self.provider,
                'description':self.description,
                'model_id': self.model_id, 'readiness': 'CONFIGURED_NOT_PROBED',
                'context_source': 'EXPLICIT_JOB_INPUT_ONLY', 'worker_tools': [],
                'automatic_model_loading': False,
                'max_input_bytes': self.max_input_bytes,
                'max_output_tokens': self.max_output_tokens}


def load_profiles(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputRejected('duplicate worker configuration key')
            result[key] = value
        return result
    path = Path(path)
    if path.stat().st_size > 128 * 1024:
        raise InputRejected('worker configuration too large')
    try:
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
        if type(data) is not dict or set(data) != {'profiles'} or type(data['profiles']) is not list:
            raise InputRejected('invalid worker configuration')
        profiles = [WorkerProfile(**item) for item in data['profiles']]
    except (TypeError, ValueError) as exc:
        raise InputRejected('invalid worker configuration') from None
    if not 1 <= len(profiles) <= 16 or len({p.profile_id for p in profiles}) != len(profiles):
        raise InputRejected('worker profiles must be unique and bounded')
    return {p.profile_id: p for p in profiles}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_child(send, url, body, key, timeout):
    try:
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(Request(url, data=body, headers=headers), timeout=timeout) as response:
            raw = response.read(128 * 1024 + 1)
            if response.status != 200 or len(raw) > 128 * 1024:
                raise ValueError('invalid bounded response')
        send.send_bytes(b'O' + raw)
    except Exception as exc:
        code = 'PROVIDER_RESPONSE_INVALID' if isinstance(exc, ValueError) else classify_provider_failure(exc).code
        try: send.send_bytes(b'E' + code.encode('ascii'))
        except (OSError, EOFError): pass
    finally:
        send.close()


def bounded_request(url, body, key, timeout):
    """Manager-selected endpoint only; absolute deadline includes process startup."""
    ctx = multiprocessing.get_context('spawn')
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_http_child, args=(send, url, body, key, timeout), daemon=True)
    deadline = time.monotonic() + timeout
    try:
        process.start()
        send.close()
        if not receive.poll(max(0, deadline - time.monotonic())):
            raise WorkerProviderError('PROVIDER_TIMEOUT')
        try: raw = receive.recv_bytes(128 * 1024 + 4096)
        except (EOFError, OSError): raise WorkerProviderError('PROVIDER_CONNECTION_FAILED') from None
        if not raw or raw[:1] != b'O':
            code = raw[1:].decode('ascii', errors='ignore') if raw else 'PROVIDER_RESPONSE_INVALID'
            raise WorkerProviderError(code)
        return raw[1:]
    finally:
        receive.close(); send.close()
        if process.pid is not None:
            process.join(.2)
            if process.is_alive(): process.terminate(); process.join(1)
            if process.is_alive(): process.kill(); process.join(1)
            if process.is_alive(): raise WorkerProviderError('PROVIDER_TIMEOUT')
            process.close()


class TextWorker:
    """One text inference; no agent loop, files, hidden context or executable tools."""
    def __init__(self, profile, *, transport=bounded_request):
        if type(profile) is not WorkerProfile:
            raise InputRejected('trusted worker profile required')
        self.profile = profile
        self.transport = transport

    def _key(self):
        key = os.environ.get(self.profile.secret_env) if self.profile.secret_env else None
        if self.profile.secret_env and (not key or len(key) > 512 or
                any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise WorkerProviderError('PROVIDER_AUTH_FAILED')
        return key

    def preflight(self):
        key = self._key()
        if self.profile.provider != 'cloud':
            root=self.profile.endpoint.removesuffix('/v1')
            path='/api/v0/models' if self.profile.provider=='lmstudio' else '/api/inference/status'
            raw = self.transport(root + path, None, key, self.profile.timeout_seconds)
            try:
                state=json.loads(raw)
                if self.profile.provider=='lmstudio':
                    models=state['data']
                    ready=(type(models) is list and any(type(x) is dict and
                           x.get('id')==self.profile.model_id and x.get('state')=='loaded' for x in models))
                else:
                    # Native Studio status schema, pinned in the v0.44 ADR.
                    # Refuse a loaded-but-inactive model to avoid implicit swaps.
                    ready=(state.get('model_identifier')==self.profile.model_id and
                           type(state.get('loaded')) is list and self.profile.model_id in state['loaded']
                           and state.get('loading')==[])
                if not ready:
                    raise WorkerProviderError('PILOT_MODEL_NOT_LOADED')
            except (ValueError, KeyError, TypeError):
                raise WorkerProviderError('PROVIDER_RESPONSE_INVALID') from None

    def complete(self, task, *, sink):
        payload = {'model': self.profile.model_id,
                   'messages': [{'role': 'system', 'content': self.profile.instructions},
                                {'role': 'user', 'content': task}],
                   'stream': False, self.profile.token_parameter: self.profile.max_output_tokens}
        if self.profile.provider=='unsloth':
            # Native Studio has its own tool loop and per-request inheritance.
            # Omitting OpenAI tools alone does not explicitly disable that loop.
            # Fields verified against the pinned ChatCompletionRequest schema.
            payload.update({'tools':[], 'tool_choice':'none',
                'enable_tools':False, 'enabled_tools':[], 'mcp_enabled':False,
                'max_tool_calls_per_message':0, 'deep_research_armed':False,
                'run_tools_locally':False, 'auto_heal_tool_calls':False,
                'nudge_tool_calls':False, 'context_overflow':'error',
                'thread_id':None, 'session_id':None, 'rag_scope':None})
        body = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        if len(body) > 96 * 1024:
            raise InputRejected('serialized text input exceeds transport budget')
        sink('MODEL_REQUEST', body.decode('ascii'))
        raw = self.transport(self.profile.endpoint + '/chat/completions', body,
                             self._key(), self.profile.timeout_seconds)
        try:
            decoded = raw.decode('utf-8')
            sink('MODEL_RESPONSE', decoded)
            result = json.loads(decoded)
            if type(result) is not dict or len(result['choices']) != 1:
                raise ValueError()
            choice = result['choices'][0]
            message = choice['message']
            text = message['content']
            tokens = result['usage']['total_tokens']
            if (choice.get('finish_reason') != 'stop' or message.get('role') != 'assistant' or
                    message.get('tool_calls') or message.get('function_call') or
                    type(text) is not str or not text.strip() or '\x00' in text or
                    len(text.encode('utf-8')) > 64 * 1024 or
                    type(tokens) is not int or not 1 <= tokens <= 150000):
                raise ValueError()
            # Unsloth versions may ignore a requested model. Never attest a
            # successful native-model selection from the request field alone.
            if self.profile.provider == 'unsloth' and result.get('model') != self.profile.model_id:
                raise ValueError()
            return text, tokens
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise WorkerProviderError('PROVIDER_RESPONSE_INVALID') from None
