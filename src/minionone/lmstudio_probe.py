"""Read-only LM Studio model discovery using its documented GET /v1/models.

Conservative, literal-loopback-only probe. No model load, worker prompt,
provider auth attestation, or application-level worker permissions.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


class InvalidProbeConfig(ValueError):
    pass


class ProbeError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LMStudioProbe:
    def __init__(self, base_url='http://127.0.0.1:1234', *, timeout=2.0,
                 api_key=None, max_response_bytes=64*1024):
        if type(base_url) is not str or not 1 <= len(base_url) <= 150:
            raise InvalidProbeConfig('invalid configured provider endpoint')
        try:
            u = urlsplit(base_url)
            port = u.port
        except ValueError as e:
            raise InvalidProbeConfig('invalid provider port') from e
        if (u.scheme != 'http' or u.hostname not in ('127.0.0.1','::1')
                or u.username is not None or u.password is not None or not port
                or not 1 <= port <= 65535 or u.path not in ('','/')
                or u.query or u.fragment):
            raise InvalidProbeConfig('only literal loopback HTTP endpoints are allowed')
        if (type(timeout) not in (float,int) or not 0.25 <= timeout <= 10):
            raise InvalidProbeConfig('invalid provider timeout')
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024*1024:
            raise InvalidProbeConfig('invalid discovery response budget')
        if api_key is not None and (type(api_key) is not str or not 1 <= len(api_key) <= 512
                                    or any(ord(x)<33 or ord(x)>126 for x in api_key)):
            raise InvalidProbeConfig('invalid configured provider credential')
        self.base_url = 'http://' + ('[::1]' if u.hostname=='::1' else '127.0.0.1') + ':' + str(port)
        self.timeout = float(timeout)
        self.api_key = api_key
        self.max_response_bytes = max_response_bytes
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def models(self) -> list[str]:
        headers = {'Accept':'application/json'}
        if self.api_key is not None:
            headers['Authorization'] = 'Bearer ' + self.api_key
        req = Request(self.base_url+'/v1/models', headers=headers, method='GET')
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ProbeError('model discovery returned unexpected status')
                data=response.read(self.max_response_bytes+1)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError) as e:
            raise ProbeError('model discovery failed or was refused') from e
        if len(data)>self.max_response_bytes:
            raise ProbeError('model discovery response exceeded byte budget')
        try:
            parsed=json.loads(data.decode('utf-8'))
            if type(parsed) is not dict or type(parsed.get('data')) is not list:
                raise ValueError('model list shape')
            models=parsed['data']
            if not 1 <= len(models) <= 100:
                raise ValueError('empty or overlarge model list')
            ids=[]
            for obj in models:
                if type(obj) is not dict or type(obj.get('id')) is not str:
                    raise ValueError('invalid model record')
                ident=obj['id']
                if not 1 <= len(ident) <= 256 or any(ord(c)<32 or ord(c)==127 for c in ident):
                    raise ValueError('invalid model ID')
                ids.append(ident)
            if len(ids)!=len(set(ids)):
                raise ValueError('duplicate model ID')
        except (ValueError,UnicodeError,TypeError) as e:
            raise ProbeError('malformed model-discovery response') from e
        return sorted(ids)
