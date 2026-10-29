"""Synthetic, tool-free LM Studio completion tests: not a real model run."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from minionone.lmstudio_completion import LMStudioCompletion, CompletionRejected


class FakeCompletion(BaseHTTPRequestHandler):
    payload = {'model':'flint-local','choices':[{'finish_reason':'stop',
                                                'message':{'role':'assistant','content':'Ready.'}}]}
    status = 200
    last_request = None

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404)
            return
        n=int(self.headers.get('Content-Length','0'))
        type(self).last_request=json.loads(self.rfile.read(n))
        b=json.dumps(type(self).payload).encode('utf8')
        self.send_response(type(self).status)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(b)

    def log_message(self,*args):
        pass


class TestCompletion(unittest.TestCase):
    def setUp(self):
        FakeCompletion.status=200
        FakeCompletion.last_request=None
        FakeCompletion.payload={'model':'flint-local','choices':[{'finish_reason':'stop',
                                'message':{'role':'assistant','content':'Ready.'}}]}
        self.srv=ThreadingHTTPServer(('127.0.0.1',0),FakeCompletion)
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.cli=LMStudioCompletion(f'http://127.0.0.1:{self.srv.server_port}')

    def test_positive_text_only(self):
        self.assertEqual(self.cli.complete('flint-local','Say ready')['text'],'Ready.')
        request=FakeCompletion.last_request
        self.assertEqual(request['messages'],[{'role':'user','content':'Say ready'}])
        self.assertFalse(request['stream'])
        self.assertNotIn('tools',request)
        self.assertNotIn('tool_choice',request)

    def test_malicious_model_tool_call_is_never_admitted(self):
        FakeCompletion.payload={'choices':[{'finish_reason':'tool_calls',
            'message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'abc','type':'function','function':{'name':'shell','arguments':'{}'}}]}}]}
        with self.assertRaises(CompletionRejected):
            self.cli.complete('flint-local','hello')

    def test_provider_malformed_response_fails_closed(self):
        for p in [{},{'choices':[]},
            {'choices':[{'message':{'role':'user','content':'bad'},'finish_reason':'stop'}]},
            {'choices':[{'message':{'role':'assistant','content':'ok'},'finish_reason':'length'}]},
            {'choices':[{'message':{'role':'assistant','content':'x'*100000},'finish_reason':'stop'}]}]:
            with self.subTest(p=str(p)[:40]):
                FakeCompletion.payload=p
                with self.assertRaises(CompletionRejected):
                    self.cli.complete('flint-local','hello')

    def test_reject_malicious_inference_arguments(self):
        for model,prompt,tokens in [('x','',256),('bad\nmodel','hello',256),
                                      ('x','hello',True),('x','hello',0),
                                      ('x','hi'*5000,256)]:
            with self.subTest(model=model,tokens=tokens),self.assertRaises(CompletionRejected):
                self.cli.complete(model,prompt,max_tokens=tokens)
        self.assertIsNone(FakeCompletion.last_request)

    def test_bad_status(self):
        FakeCompletion.status=503
        with self.assertRaises(CompletionRejected):
            self.cli.complete('flint-local','hello')


if __name__=='__main__':
    unittest.main()
