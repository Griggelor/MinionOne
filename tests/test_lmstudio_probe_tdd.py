"""Synthetic loopback HTTP tests, NOT live LM Studio evidence."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from minionone.lmstudio_probe import LMStudioProbe, ProbeError, InvalidProbeConfig


class FakeModelHandler(BaseHTTPRequestHandler):
    payload = b'{"object":"list","data":[{"id":"flint-local"},{"id":"other/model"}]}'
    response_status = 200
    location = None
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        if self.path != '/v1/models':
            self.send_error(404)
            return
        self.send_response(type(self).response_status)
        if type(self).location:
            self.send_header('Location',type(self).location)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self,*args):
        return


class TestLMStudioProbe(unittest.TestCase):
    def setUp(self):
        FakeModelHandler.payload=b'{"object":"list","data":[{"id":"flint-local"},{"id":"other/model"}]}'
        FakeModelHandler.response_status=200
        FakeModelHandler.location=None
        FakeModelHandler.hits=0
        self.srv=ThreadingHTTPServer(('127.0.0.1',0),FakeModelHandler)
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url=f'http://127.0.0.1:{self.srv.server_port}'

    def test_list_models(self):
        self.assertEqual(LMStudioProbe(self.url,timeout=2).models(),['flint-local','other/model'])
        self.assertEqual(FakeModelHandler.hits,1)

    def test_deny_off_host_access_before_network(self):
        for host in ['http://example.com:1234','http://localhost:1234',
                     'https://127.0.0.1:1234','http://127.0.0.2:1234',
                     'http://127.0.0.1:1234@evil.com',
                     'http://127.0.0.1:1234/path','file:///etc/passwd']:
            with self.subTest(host=host),self.assertRaises(InvalidProbeConfig):
                LMStudioProbe(host)
        self.assertEqual(FakeModelHandler.hits,0)

    def test_redirect_is_not_followed(self):
        FakeModelHandler.response_status=302
        FakeModelHandler.location='http://example.invalid/steal'
        with self.assertRaises(ProbeError):
            LMStudioProbe(self.url).models()
        self.assertEqual(FakeModelHandler.hits,1)

    def test_malformed_model_list_rejected(self):
        for p in [b'{}', b'not-json', b'{"data":[]}',
                  b'{"data":[{"id":"bad\\u0000model"}]}',
                  b'{"data":[{"id":"a"},{"id":"a"}]}', b'{"data":"list"}']:
            with self.subTest(p=p):
                FakeModelHandler.payload=p
                with self.assertRaises(ProbeError):
                    LMStudioProbe(self.url).models()

    def test_bounded_response(self):
        FakeModelHandler.payload=b' ' * 100_000
        with self.assertRaises(ProbeError):
            LMStudioProbe(self.url).models()

    def test_bad_status(self):
        FakeModelHandler.response_status=503
        with self.assertRaises(ProbeError):
            LMStudioProbe(self.url).models()

    def test_config_and_bad_port(self):
        for port in ['0','99999','abc']:
            with self.subTest(port=port),self.assertRaises(InvalidProbeConfig):
                LMStudioProbe(f'http://127.0.0.1:{port}')
        for x in [0,-1,999,True]:
            with self.subTest(timeout=x),self.assertRaises(InvalidProbeConfig):
                LMStudioProbe(self.url,timeout=x)


if __name__=='__main__':
    unittest.main()
