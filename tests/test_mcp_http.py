"""Wire-level MCP tests; real loopback HTTP server and durable SQLite."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from minionone.mcp_server import LocalMCP

TOKEN="integration-testing-"+("A"*48)

class TestMCPHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.srv=LocalMCP(("127.0.0.1",0),token=TOKEN,
                          database_path=Path(self.temp.name)/"state.sqlite")
        worker=threading.Thread(target=self.srv.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def send(self,message,*,token=TOKEN,headers=None,path="/mcp",method="POST"):
        h={"Host":f"127.0.0.1:{self.srv.server_port}",
           "Content-Type":"application/json",
           "Accept":"application/json, text/event-stream",
           "Authorization":"Bearer "+token}
        if headers: h.update(headers)
        connection=http.client.HTTPConnection("127.0.0.1",self.srv.server_port,timeout=3)
        try:
            body=json.dumps(message).encode() if message is not None else None
            connection.request(method,path,body=body,headers=h)
            response=connection.getresponse()
            raw=response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            connection.close()

    def rpc(self,method,params=None):
        return self.send({"jsonrpc":"2.0","id":1,"method":method,"params":params or {}})

    def tool(self,name,args=None):
        status,data=self.rpc("tools/call",{"name":name,"arguments":args or {}})
        self.assertEqual(status,200)
        return data["result"]

    def test_handshake_tool_list_and_gate_truth(self):
        status,resp=self.rpc("initialize",{"protocolVersion":"2025-06-18",
                  "clientInfo":{"name":"wire-test","version":"1"},"capabilities":{}})
        self.assertEqual(status,200)
        self.assertEqual(resp["result"]["protocolVersion"],"2025-06-18")
        from minionone import __version__
        self.assertEqual(resp["result"]["serverInfo"]["version"],__version__)
        _,listed=self.rpc("tools/list")
        names={tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(names,{"minionone_status","minionone_models",
            "minionone_admit","minionone_job_status","minionone_job_events","minionone_job_trace","minionone_abort",
            "minionone_resume_request"})
        state=json.loads(self.tool("minionone_status")["content"][0]["text"])
        self.assertFalse(state["worker_execution_enabled"])
        self.assertFalse(state["parent_thread_attested"])

    def test_auth_origin_host_and_path(self):
        req={"jsonrpc":"2.0","id":1,"method":"tools/list"}
        self.assertEqual(self.send(req,token="wrong")[0],401)
        self.assertEqual(self.send(req,headers={"Origin":"https://example.invalid"})[0],403)
        self.assertEqual(self.send(req,headers={"Host":"malicious.invalid"})[0],403)
        self.assertEqual(self.send(req,path="/mcp?token=public")[0],404)

    def test_create_status_abort_cas_and_idempotency(self):
        args={"input_digest":"e"*64,"operation_id":"request-0001"}
        receipt=json.loads(self.tool("minionone_admit",args)["content"][0]["text"])
        self.assertEqual(receipt["state"],"ACCEPTED")
        self.assertEqual(receipt,json.loads(self.tool("minionone_admit",args)["content"][0]["text"]))
        status=json.loads(self.tool("minionone_job_status",{"job_id":receipt["job_id"]})["content"][0]["text"])
        self.assertEqual(status["version"],0)
        abort={"job_id":receipt["job_id"],"expected_version":0,"operation_id":"abort-0001"}
        cancelled=json.loads(self.tool("minionone_abort",abort)["content"][0]["text"])
        self.assertEqual(cancelled["state"],"CANCEL_REQUESTED")
        self.assertTrue(self.tool("minionone_abort",dict(abort,operation_id="abort-0002"))["isError"])
        events=json.loads(self.tool("minionone_job_events",{"job_id":receipt["job_id"]})["content"][0]["text"])
        self.assertEqual(len(events["events"]),3)
        error=events["events"][-1]
        self.assertEqual(error["type"],"CONTROL_REJECTED")
        self.assertEqual(error["error"]["code"],"STALE_VERSION")
        self.assertEqual((error["status"],error["state_version"]),("CANCEL_REQUESTED",1))

    def test_forbidden_tools_and_wrong_params(self):
        _,resp=self.rpc("tools/call",{"name":"shell","arguments":{"command":"dir"}})
        self.assertEqual(resp["error"]["code"],-32602)
        self.assertTrue(self.tool("minionone_status",{"path":"C:/Windows"})["isError"])
        self.assertTrue(self.tool("minionone_admit",{"input_digest":"bad","operation_id":"request-0001"})["isError"])
        self.assertTrue(self.tool("minionone_job_status",{"job_id":"JOB-unknown"})["isError"])

    def test_protocol_failures(self):
        req={"jsonrpc":"2.0","id":1,"method":"tools/list"}
        self.assertEqual(self.send(req,headers={"Accept":"application/json"})[0],406)
        self.assertEqual(self.send(req,headers={"Content-Type":"text/plain"})[0],415)
        self.assertEqual(self.send(req,headers={"MCP-Protocol-Version":"1999"})[0],400)
        self.assertEqual(self.send(None,method="GET")[0],405)
        self.assertEqual(self.send({"jsonrpc":"2.0","method":"notifications/initialized"})[0],202)
        self.assertEqual(self.rpc("other-method")[1]["error"]["code"],-32601)

    def test_slow_partial_body_releases_connection(self):
        import socket
        with socket.create_connection(("127.0.0.1",self.srv.server_port),timeout=3) as sock:
            sock.settimeout(5)
            data=(f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{self.srv.server_port}\r\n"
                  f"Authorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\n"
                  "Accept: application/json, text/event-stream\r\n"
                  "Content-Length: 99\r\n\r\n{}")
            sock.sendall(data.encode("ascii"))
            response=sock.recv(1024)
            self.assertTrue(response == b"" or b"408" in response)

    def test_excess_connections_are_refused(self):
        import socket
        capacity=self.srv._slots
        for _ in range(8):
            self.assertTrue(capacity.acquire(blocking=False))
        try:
            with socket.create_connection(("127.0.0.1",self.srv.server_port),timeout=2) as sock:
                sock.settimeout(3)
                sock.sendall(b"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                try:
                    response=sock.recv(100)
                except (ConnectionResetError, ConnectionAbortedError):
                    # Windows aborts rejected sockets; POSIX often returns EOF.
                    response=b""
                self.assertEqual(response,b"")
        finally:
            for _ in range(8):
                capacity.release()

    def test_non_loopback_and_weak_secret_denied(self):
        with self.assertRaises(ValueError):
            LocalMCP(("0.0.0.0",0),token=TOKEN,database_path=Path(self.temp.name)/"a.db")
        with self.assertRaises(ValueError):
            LocalMCP(("127.0.0.1",0),token="weak",database_path=Path(self.temp.name)/"b.db")

if __name__=="__main__":
    unittest.main()
