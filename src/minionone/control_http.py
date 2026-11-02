"""Bounded manager-only HTTP control plane. No retries or worker-selected URLs."""
import json
import multiprocessing
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from .worker_profiles import ConfiguredWorker, _NoRedirect, WorkerProviderError
from .diagnostics import classify_provider_failure

MAX_CONTROL_BYTES=2*1024*1024


def _exchange(send,url,method,body,key,timeout):
    try:
        headers={'Accept':'application/json','Content-Type':'application/json'}
        if key:headers['Authorization']='Bearer '+key
        request=Request(url,data=body,headers=headers,method=method)
        try:response=build_opener(ProxyHandler({}),_NoRedirect()).open(request,timeout=timeout)
        except HTTPError as exc:response=exc
        with response:
            raw=response.read(MAX_CONTROL_BYTES+1)
            if len(raw)>MAX_CONTROL_BYTES:raise ValueError()
            send.send_bytes(json.dumps({'status':response.code,'body':raw.decode('utf-8')}).encode())
    except Exception as exc:
        code='PROVIDER_RESPONSE_INVALID' if isinstance(exc,(ValueError,UnicodeError)) else classify_provider_failure(exc).code
        send.send_bytes(json.dumps({'error':code}).encode())
    finally:send.close()


class ControlHTTP:
    def __init__(self,profile,*,transport=None):
        if profile.provider not in ('lmstudio','unsloth'):raise ValueError('local control plane required')
        self.profile=profile;self.transport=transport
        self.root=profile.endpoint.removesuffix('/v1')
    def request(self,method,path,payload=None,*,timeout=5,missing=False):
        if method not in ('GET','POST','PUT') or not path.startswith('/') or path.startswith('//'):
            raise ValueError('invalid manager control request')
        body=None if payload is None else json.dumps(payload,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
        if body is not None and len(body)>MAX_CONTROL_BYTES:raise ValueError('control request too large')
        key=ConfiguredWorker(self.profile)._key()
        if self.transport is not None:
            status,raw=self.transport(method,self.root+path,payload,key,timeout)
        else:
            ctx=multiprocessing.get_context('spawn');receive,send=ctx.Pipe(duplex=False)
            process=ctx.Process(target=_exchange,args=(send,self.root+path,method,body,key,timeout),daemon=True)
            end=time.monotonic()+timeout
            try:
                process.start();send.close()
                if not receive.poll(max(0,end-time.monotonic())):raise WorkerProviderError('PROVIDER_TIMEOUT')
                value=json.loads(receive.recv_bytes(4*MAX_CONTROL_BYTES))
                if 'error' in value:raise WorkerProviderError(value['error'])
                status=value['status'];raw=json.loads(value['body']) if value['body'] else {}
            finally:
                receive.close();send.close()
                if process.pid is not None:
                    process.join(.1)
                    if process.is_alive():process.terminate();process.join(1)
                    if process.is_alive():process.kill();process.join(1)
                    if process.is_alive():raise WorkerProviderError('PROVIDER_TIMEOUT')
                    process.close()
        if status==404 and missing:return None
        if not 200<=status<300:
            code={401:'PROVIDER_AUTH_FAILED',403:'PROVIDER_AUTH_FAILED',404:'PROVIDER_ENDPOINT_NOT_FOUND',
                  409:'PROVIDER_REQUEST_REJECTED',422:'PROVIDER_REQUEST_REJECTED',400:'PROVIDER_REQUEST_REJECTED'}.get(status,'PROVIDER_SERVER_ERROR')
            raise WorkerProviderError(code)
        if type(raw) is not dict:raise WorkerProviderError('PROVIDER_RESPONSE_INVALID')
        return raw
