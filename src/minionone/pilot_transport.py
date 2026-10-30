"""Absolute-deadline loopback HTTP in a disposable network-client process.

Terminating this client does NOT prove the model server stopped generating.
The job manager must quarantine uncertain provider outcomes accordingly.
"""
from contextlib import contextmanager
from io import BytesIO
import json
import multiprocessing
import time
from urllib.request import Request
from .diagnostics import classify_provider_failure, diagnostic
from .lmstudio_probe import LMStudioProbe
from .lmstudio_agent import PlannerRejected

MAX_BYTES=128*1024

def _exchange(send, base_url, path, method, body, api_key, timeout):
    try:
        probe=LMStudioProbe(base_url,timeout=min(timeout,10),api_key=api_key,
                            max_response_bytes=MAX_BYTES)
        headers={'Accept':'application/json','Content-Type':'application/json'}
        if api_key is not None:headers['Authorization']='Bearer '+api_key
        request=Request(probe.base_url+path,data=body,headers=headers,method=method)
        try:
            with probe._opener.open(request,timeout=timeout) as response:
                raw=response.read(MAX_BYTES+1)
                if response.status!=200 or len(raw)>MAX_BYTES:
                    raise ValueError('invalid bounded provider response')
            send.send_bytes(b'O'+raw)
        except Exception as exc:
            code=classify_provider_failure(exc).code
            if isinstance(exc,ValueError):code='PROVIDER_RESPONSE_INVALID'
            send.send_bytes(b'E'+json.dumps({'code':code}).encode('ascii'))
    except BaseException:
        try:send.send_bytes(b'E{"code":"INTERNAL_ERROR"}')
        except BaseException:pass
    finally:
        send.close()

class DeadlineOpener:
    def __init__(self,base_url,api_key=None):
        self.base=LMStudioProbe(base_url,api_key=api_key).base_url
        self.key=api_key

    @contextmanager
    def open(self,request,*,timeout):
        path=request.full_url[len(self.base):] if request.full_url.startswith(self.base) else ''
        method=request.get_method()
        if (path,method) not in (('/api/v0/models','GET'),('/v1/chat/completions','POST')):
            raise PlannerRejected('provider operation denied')
        if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not 0<timeout<=300:
            raise PlannerRejected('invalid absolute deadline')
        context=multiprocessing.get_context('spawn')
        receive,send=context.Pipe(duplex=False)
        process=context.Process(target=_exchange,
            args=(send,self.base,path,method,request.data,self.key,float(timeout)),daemon=True)
        deadline=time.monotonic()+float(timeout)
        try:
            process.start();send.close()
            remaining=max(0,deadline-time.monotonic())
            if not receive.poll(remaining):
                raise PlannerRejected('provider deadline elapsed',diagnostic=diagnostic('PROVIDER_TIMEOUT'))
            try:data=receive.recv_bytes(MAX_BYTES+4096)
            except (EOFError,OSError):
                raise PlannerRejected('provider client failed') from None
            if not data or data[:1]!=b'O':
                try:code=json.loads(data[1:])['code']
                except (ValueError,KeyError,TypeError):code='PROVIDER_RESPONSE_INVALID'
                raise PlannerRejected('provider failure',diagnostic=diagnostic(code))
            response=BytesIO(data[1:]);response.status=200
            try:yield response
            finally:response.close()
        finally:
            receive.close();send.close()
            if process.pid is not None:
                process.join(timeout=0.2)
                if process.is_alive():process.terminate();process.join(timeout=1)
                if process.is_alive():process.kill();process.join(timeout=1)
                if process.is_alive():
                    raise PlannerRejected('network client quiescence unavailable',
                                          diagnostic=diagnostic('PROVIDER_TIMEOUT'))
                process.close()
