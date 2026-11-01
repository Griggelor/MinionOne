# v0.41 — Native AppContainer inherited-handle, breakaway and loopback boundary probes

**Scope:** Fixed Windows CI probe only. Product PRE-ALPHA, NO_GO.

## What is tested

v0.39 proved an anonymous-pipe roundtrip to a real AppContainer child. v0.40
proved six crash/invalid-frame paths fail closed. v0.41 deliberately attacks
the next Windows OS boundaries with a fixed MSVC child using the same suspended
start, verified AppContainer token, one-process Job Object and two-handle
STARTUPINFOEX HANDLE_LIST.

- **Manager handle leakage:** Manager creates a signalled, explicitly
  inheritable Win32 event, verifies it is signalled AND actually marked
  inheritable, passes its numeric value only as a test marker, but does not
  include it in the child HANDLE_LIST. The real AppContainer child must
  observe ERROR_INVALID_HANDLE (or the exact equivalent Windows
  STATUS_INVALID_HANDLE exception) from GetHandleInformation. The parent
  separately checks that its valid event is signalled and inheritable. The two anonymous IPC pipe handles remain the
  only intentional inherited handles. This tests a known canary, not all
  possible handles or handle-duplication attacks.
- **Process breakaway attempt:** Child first verifies IsProcessInJob(TRUE)
  and attempts CreateProcessW of its own fixed trusted test EXE with
  CREATE_BREAKAWAY_FROM_JOB | CREATE_SUSPENDED. No grandchild may run.
  A successful spawn is immediately terminated and fails the test. The
  test requires ERROR_ACCESS_DENIED or ERROR_NOT_ENOUGH_QUOTA for the
  rejected attempt. This is evidence for this fixed attempt, not all
  process creation or inheritance techniques.
- **Live local TCP connection:** Manager binds a fresh localhost TCP
  listener and confirms that an ordinary host-side client can connect.
  The child attempts ONLY the manager-selected 127.0.0.1/ephemeral port,
  with a 500ms deadline. An actual WSAEACCES is recorded as
  EXPLICIT_LOOPBACK_WSAEACCES; a timeout is separately recorded as
  LOOPBACK_TIMEOUT_UNATTESTED. An actual successful child connection
  FAILS the test. DNS, remote addresses, IPv6 and alternative network
  transports are not exercised: **overall network isolation is always
  NOT ATTESTED**, even when localhost yields WSAEACCES.

The fixed C helper accepts no model text, host path, remote URL, script or
arbitrary command. Its 3 new modes are selected exclusively by the trusted
native CI harness, never by a worker-facing or MCP action. All successful
observations still use the v0.38 immutable PROBE_PING/PONG contract and a
manager-only journal; even PONG is not a RESULT_READY, ACK or security approval.

## Evidence

[The dedicated Windows workflow](https://github.com/Griggelor/MinionOne/actions/runs/41312207155) passed its real-child handle, breakaway and loopback cases at implementation SHA f69d60889b8935c62034745330224cd22e7406d9. It compiles the fixed child with MSVC, runs
all three native scenarios, v0.39 success and v0.40 attack regressions,
and v0.38 durable journal tests. Linux tests fail-closed policy only.
The Windows synthetic observation is redacted at
evidence/v041/BOUNDARY_EVIDENCE.json and includes the exact CI SHA.

## Still needed

These tests do not prove unrestricted host isolation, sandboxing any
worker-supplied code, complete handle graph confinement, remote network
denial, process-tree breakaway resistance to arbitrary binaries, or
security on the operator's target Windows host. Also open: job-scoped
ACLs, safe input/work/output handling, long-lived manager receipt
integration, real Flint/Unsloth end-to-end verification, and CI
intermittency issue #59.

**Worker shell/run_tests = DISABLED. boxed_execution_enabled=false;
os_sandbox_attested=false. Product NO_GO.**
