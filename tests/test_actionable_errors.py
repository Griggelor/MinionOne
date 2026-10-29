"""Red-first public diagnostics taxonomy: safe, actionable, no leaked secrets."""
import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from minionone.diagnostics import classify_provider_failure, safe_tool_fault
from minionone.lmstudio_probe import LMStudioProbe, ProbeError
from minionone.lmstudio_completion import LMStudioCompletion, CompletionRejected
from minionone.mcp_server import LocalMCP

SECRET="password=do-not-print-token-in-a-diagnostic"
TOKEN="synthetic-secret-"+"Z"*48


def http_error(status):
    return HTTPError("http://127.0.0.1:1234/private?token="+SECRET,
                     status,"upstream secret "+SECRET,{},io.BytesIO(SECRET.encode()))


class DiagnosticUnitTests(unittest.TestCase):
    def test_fixed_provider_http_taxonomy_and_safe_operator_actions(self):
        for status,code,retry in [
            (400,"PROVIDER_REQUEST_REJECTED",False),
            (401,"PROVIDER_AUTH_FAILED",False),
            (403,"PROVIDER_AUTH_FAILED",False),
            (404,"PROVIDER_ENDPOINT_NOT_FOUND",False),
            (408,"PROVIDER_TIMEOUT",True),
            (422,"PROVIDER_REQUEST_REJECTED",False),
            (429,"PROVIDER_RATE_LIMITED",True),
            (500,"PROVIDER_SERVER_ERROR",True),
            (503,"PROVIDER_SERVER_ERROR",True),
            (418,"PROVIDER_HTTP_ERROR",False),
        ]:
            with self.subTest(status=status):
                d=classify_provider_failure(http_error(status))
                self.assertEqual(d.code,code)
                self.assertIs(d.retryable,retry)
                self.assertGreater(len(d.message),8)
                self.assertGreater(len(d.action),8)
                self.assertNotIn(SECRET,str(d))
                self.assertNotIn("127.0.0.1",str(d))
                self.assertNotIn("upstream",str(d))

    def test_transport_failure_categories(self):
        cases=[
            (URLError(ConnectionRefusedError(111,SECRET)),"PROVIDER_CONNECTION_REFUSED"),
            (ConnectionRefusedError(111,SECRET),"PROVIDER_CONNECTION_REFUSED"),
            (URLError(socket.timeout(SECRET)),"PROVIDER_TIMEOUT"),
            (TimeoutError(SECRET),"PROVIDER_TIMEOUT"),
            (URLError(OSError(1234,SECRET)),"PROVIDER_CONNECTION_FAILED"),
            (OSError(1234,SECRET),"PROVIDER_CONNECTION_FAILED"),
        ]
        for exc,code in cases:
            with self.subTest(exc=type(exc).__name__):
                d=classify_provider_failure(exc)
                self.assertEqual(d.code,code)
                self.assertNotIn(SECRET,str(d))
                self.assertTrue(d.retryable)

    def test_safe_fault_mapping_does_not_echo_attacker_messages(self):
        from minionone.admission import (
            PermissionDenied, InputRejected, VersionConflict, IdempotencyConflict
        )
        from minionone.timeout_control import TimeoutControlDenied
        for error,code in [
            (PermissionDenied(SECRET),"ACCESS_DENIED"),
            (InputRejected(SECRET),"INVALID_INPUT"),
            (VersionConflict(SECRET),"STALE_VERSION"),
            (IdempotencyConflict(SECRET),"IDEMPOTENCY_CONFLICT"),
            (TimeoutControlDenied(SECRET),"RESUME_NOT_READY"),
            (RuntimeError(SECRET),"INTERNAL_ERROR"),
        ]:
            with self.subTest(code=code):
                d=safe_tool_fault(error)
                self.assertEqual(d.code,code)
                self.assertNotIn(SECRET,json.dumps(d.to_dict()))
                self.assertEqual(set(d.to_dict()),{"code","message","action","retryable"})

    def test_diagnostics_reject_untrusted_provider_raw_error_body(self):
        d=classify_provider_failure(http_error(401))
        self.assertNotIn(SECRET,json.dumps(d.to_dict()))
        self.assertFalse(d.retryable)


class FakeOpener:
    def __init__(self,exception):
        self.exception=exception
    def open(self,*args,**kwargs):
        raise self.exception


class ClientBoundaryTests(unittest.TestCase):
    def test_probe_keeps_classified_diagnostic_through_exception_boundary(self):
        for exc,code in [(http_error(401),"PROVIDER_AUTH_FAILED"),
                         (http_error(503),"PROVIDER_SERVER_ERROR"),
                         (URLError(ConnectionRefusedError(10061,SECRET)),"PROVIDER_CONNECTION_REFUSED"),
                         (URLError(socket.timeout(SECRET)),"PROVIDER_TIMEOUT")]:
            with self.subTest(code=code):
                probe=LMStudioProbe()
                probe._opener=FakeOpener(exc)
                with self.assertRaises(ProbeError) as cm:
                    probe.models()
                self.assertEqual(cm.exception.diagnostic.code,code)
                self.assertNotIn(SECRET,str(cm.exception))

    def test_tool_planner_keeps_classified_provider_api_failure(self):
        from minionone.lmstudio_agent import LMStudioAgentProvider, PlannerRejected
        for status,code in ((401,"PROVIDER_AUTH_FAILED"),
                            (429,"PROVIDER_RATE_LIMITED"),
                            (503,"PROVIDER_SERVER_ERROR")):
            with self.subTest(status=status):
                adapter=LMStudioAgentProvider(
                    "http://127.0.0.1:1234",model_id="synthetic",
                    approved_prompt="synthetic non-file job")
                adapter._provider._opener=FakeOpener(http_error(status))
                with self.assertRaises(PlannerRejected) as cm:
                    adapter.next_step(timeout_seconds=10)
                self.assertEqual(cm.exception.diagnostic.code,code)
                self.assertNotIn(SECRET,str(cm.exception))

    def test_completion_keeps_classified_diagnostic(self):
        for status,code in [(401,"PROVIDER_AUTH_FAILED"),(429,"PROVIDER_RATE_LIMITED"),
                            (503,"PROVIDER_SERVER_ERROR")]:
            with self.subTest(status=status):
                client=LMStudioCompletion()
                client._provider._opener=FakeOpener(http_error(status))
                with self.assertRaises(CompletionRejected) as cm:
                    client.complete("local-model","approved synthetic prompt")
                self.assertEqual(cm.exception.diagnostic.code,code)
                self.assertNotIn(SECRET,str(cm.exception))


class MCPDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.srv=LocalMCP(("127.0.0.1",0),token=TOKEN,
                          database_path=Path(self.tmp.name)/"pilot.sqlite")
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def rpc(self,method,args=None,token=TOKEN):
        import http.client
        c=http.client.HTTPConnection("127.0.0.1",self.srv.server_port,timeout=5)
        try:
            data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,
                             "params":args or {}}).encode()
            c.request("POST","/mcp",body=data,headers={
                "Host":f"127.0.0.1:{self.srv.server_port}",
                "Content-Type":"application/json",
                "Accept":"application/json, text/event-stream",
                "Authorization":"Bearer "+token
            })
            resp=c.getresponse()
            return resp.status,json.loads(resp.read())
        finally:
            c.close()

    def test_wrong_mcp_password_has_non_leaking_actionable_code(self):
        status,body=self.rpc("tools/list",token="bad-password-"+SECRET)
        self.assertEqual(status,401)
        self.assertEqual(body["error"]["code"],"MCP_AUTH_FAILED")
        self.assertIn("token",body["error"]["action"].lower())
        self.assertNotIn(SECRET,json.dumps(body))
        self.assertNotIn(TOKEN,json.dumps(body))

    def test_mcp_models_tool_returns_provider_specific_fault(self):
        with patch.object(self.srv.probe,"models",
                          side_effect=ProbeError("raw secret "+SECRET,
                              diagnostic=classify_provider_failure(http_error(401)))):
            status,body=self.rpc("tools/call",{"name":"minionone_models","arguments":{}})
        self.assertEqual(status,200)
        self.assertTrue(body["result"]["isError"])
        info=json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(info["error"]["code"],"PROVIDER_AUTH_FAILED")
        self.assertIn("action",info["error"])
        self.assertNotIn(SECRET,json.dumps(body))

    def test_input_and_conflicts_not_undifferentiated(self):
        status,body=self.rpc("tools/call",{"name":"minionone_admit",
             "arguments":{"input_digest":"invalid","operation_id":"admit-0001"}})
        self.assertEqual(status,200)
        info=json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(info["error"]["code"],"INVALID_INPUT")

    def test_unexpected_error_is_safe_and_does_not_kill_server(self):
        with patch.object(self.srv.store,"status",side_effect=RuntimeError(SECRET)):
            status,body=self.rpc("tools/call",{"name":"minionone_job_status",
                                               "arguments":{"job_id":"JOB-123456"}})
        self.assertEqual(status,200)
        info=json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(info["error"]["code"],"INTERNAL_ERROR")
        self.assertNotIn(SECRET,json.dumps(body))
        self.assertEqual(self.rpc("ping")[1]["result"],{})


if __name__=="__main__":
    unittest.main()
