"""Fixed Windows AppContainer child IPC probe (v0.39), never a worker tool.

Two anonymous pipes with explicit HANDLE_LIST inheritance replace a network
listener. Only a fixed MSVC test helper is permitted, no model-selected process,
script, file or command. v0.38 journal fences the attempt before resume.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

from .boxed_ipc_v038 import MAX_FRAME_BYTES, ProtocolUncertain, FrameRejected
from .boxed_ipc_journal_v038 import BoxedProbeJournal
from .boxed_file_policy_v042 import (
    INPUT, MANAGER_CANARY, SyntheticArtifactRejected, inspect_fixed_result, sha,
)
from .win32_appcontainer_adversarial_v037 import (
    _apis, _sid_string, _allow_probe_executable_only,
)
from .win32_appcontainer_probe import (
    _StartupInfoEx, _SecurityCapabilities, _trusted_local_appdata,
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    _EXTENDED_STARTUPINFO_PRESENT, _TOKEN_QUERY, _TOKEN_IS_APPCONTAINER,
)
from .win32_job_object_probe import (
    JobProbeUnavailable, _ProcessInfo, _StartupInfo, _ExtendedLimits,
    _CREATE_SUSPENDED, _CREATE_NO_WINDOW, _CREATE_UNICODE_ENVIRONMENT,
    _JOB_EXTENDED_LIMITS, _JOB_LIMIT_KILL_ON_CLOSE, _JOB_LIMIT_ACTIVE_PROCESS,
    _JOB_LIMIT_PROCESS_MEMORY, _WAIT_OBJECT_0,
)

_HANDLE_LIST = 0x00020002
_HANDLE_FLAG_INHERIT = 1
_STARTF_USESTDHANDLES = 0x00000100
_ERROR_BROKEN_PIPE = 109


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


@dataclass(frozen=True, slots=True)
class BoxedIPCEvidence:
    real_appcontainer_token: bool
    assigned_before_resume: bool
    explicit_two_handle_inheritance: bool
    observed_code: str
    journal_state: str
    child_exit_code: int
    ipc_transport: str = "anonymous_pipes_handle_list"
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False
    product_release: str = "NO_GO"


@dataclass(frozen=True, slots=True)
class BoxedFaultEvidence:
    """A deliberately failed fixed native attempt; never a sandbox PASS."""
    mode: str
    journal_state: str
    automatic_replay_allowed: bool
    failed_closed: bool = True
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False
    product_release: str = "NO_GO"


@dataclass(frozen=True, slots=True)
class BoxedBoundaryEvidence:
    """Fixed native negative observation; not a sandbox/product attestation."""
    mode: str
    observed_code: str
    journal_state: str
    real_appcontainer_token: bool
    job_assigned_before_resume: bool
    manager_canary_positive_control: bool
    network_result: str = "NOT_TESTED"
    network_isolation_attested: bool = False
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False
    product_release: str = "NO_GO"


@dataclass(frozen=True, slots=True)
class BoxedFileEvidence:
    """Manager-only disposable file proof, NEVER a worker output publication."""
    mode: str
    input_digest: str
    result_digest: str | None
    journal_state: str
    synthetic_artifact_published: bool
    result_validation_rejected: bool
    appcontainer_token_verified: bool = True
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False
    target_host_attested: bool = False
    product_release: str = "NO_GO"


# Internal-only, fixed CI adversarial scenarios. Not accepted from model or MCP.
_FILE_MODES = frozenset({"file_ok", "file_bad"})
_BOUNDARY_MODES = frozenset({"handle", "breakaway", "network"})
_FAULT_MODES = frozenset({
    "crash", "hang", "truncated", "oversized", "wrong_nonce", "trailing",
})


def _configure_pipe_apis(k):
    k.CreateEventW.argtypes = [ctypes.POINTER(_SecurityAttributes),
                               wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetHandleInformation.restype = wintypes.BOOL
    k.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                             ctypes.POINTER(wintypes.HANDLE),
                             ctypes.POINTER(_SecurityAttributes), wintypes.DWORD]
    k.CreatePipe.restype = wintypes.BOOL
    k.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    k.SetHandleInformation.restype = wintypes.BOOL
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.WriteFile.restype = wintypes.BOOL
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.ReadFile.restype = wintypes.BOOL


def _write_request(k, handle, wire):
    if type(wire) is not bytes or not 5 <= len(wire) <= MAX_FRAME_BYTES + 4:
        raise ProtocolUncertain("invalid fixed IPC probe request")
    buf = ctypes.create_string_buffer(wire)
    sent = wintypes.DWORD()
    if not k.WriteFile(handle, buf, len(wire), ctypes.byref(sent), None) or sent.value != len(wire):
        raise ProtocolUncertain("fixed child stdin write failed")


def _read_reply(k, handle):
    # Read only after child exit; the manager closes its copy of the child
    # write handle before resume, so EOF is genuine. Extra bytes fail closed.
    result = bytearray()
    while True:
        remaining = MAX_FRAME_BYTES + 5 - len(result)
        if remaining <= 0:
            raise ProtocolUncertain("oversized fixed child response")
        buffer = ctypes.create_string_buffer(remaining)
        received = wintypes.DWORD()
        if not k.ReadFile(handle, buffer, remaining, ctypes.byref(received), None):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                break
            raise ProtocolUncertain("fixed child stdout read failed")
        if received.value == 0:
            break
        result.extend(buffer.raw[:received.value])
        if len(result) > MAX_FRAME_BYTES + 4:
            raise ProtocolUncertain("oversized fixed child response")
    if not result:
        raise ProtocolUncertain("fixed child returned no IPC frame")
    return bytes(result)


def _fixed_helper():
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        raise JobProbeUnavailable("fixed IPC probe requires Windows source and MSVC helper")
    expected = (Path(__file__).resolve().parents[2] / "evidence" / "v039" /
                "boxed_ipc_child_v039.exe")
    if (not expected.is_file() or expected.is_symlink() or
            not 1024 <= expected.stat().st_size <= 2 * 1024 * 1024):
        raise JobProbeUnavailable("trusted fixed v0.39 IPC helper unavailable")
    return expected


def _run_fixed_boxed_ipc_case(case):
    """Internal fixed CI child modes; never exposed to any model caller.

    RESPONSE_OBSERVED is explicitly not sandbox attestation, result publication,
    generic test execution, a parent ACK or safe target-host execution.
    """
    if type(case) is not str or case not in _FAULT_MODES | _BOUNDARY_MODES | _FILE_MODES | {"ok"}:
        raise JobProbeUnavailable("unknown or unapproved fixed native CI probe mode")
    exe = _fixed_helper()
    k, u, a = _apis()
    _configure_pipe_apis(k)
    sid = ctypes.c_void_p()
    token = wintypes.HANDLE()
    process = _ProcessInfo()
    job = None
    attr_memory = None
    attr_initialized = False
    profile_created = False
    process_started = False
    process_exited = False
    handles = []
    listener = None
    stage = None
    canary_confirmed = False
    boundary_arg = None
    moniker = "MinionOne.IPC." + secrets.token_hex(12)
    temp = tempfile.TemporaryDirectory(prefix="minionone-v039-")
    try:
        root = Path(temp.name)
        runnable = root / "runnable"
        manager_state = root / "manager"
        runnable.mkdir()
        manager_state.mkdir()
        helper = runnable / "boxed_ipc_child_v039.exe"
        original = exe.read_bytes()
        helper.write_bytes(original)
        if hashlib.sha256(helper.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise JobProbeUnavailable("trusted fixed helper copy changed")
        hr = u.CreateAppContainerProfile(moniker, "MinionOne synthetic IPC proof",
                                         "Disposable fixed PING child only", None, 0,
                                         ctypes.byref(sid))
        if hr != 0 or not sid.value:
            raise JobProbeUnavailable("cannot provision per-attempt AppContainer identity")
        profile_created = True
        # Only the runnable ancestor/executable, NOT the manager state folder.
        package_sid = _sid_string(k, a, sid)
        _allow_probe_executable_only(package_sid, runnable, helper)
        if case in _FILE_MODES:
            # Only synthetic bytes, exact filenames and per-attempt paths.
            # No model/user-provided path may reach the child or the ACL grant.
            stage = root / "stage"
            input_folder = stage / "input"
            work_folder = stage / "work"
            output_folder = stage / "output"
            for folder in (stage, input_folder, work_folder, output_folder):
                folder.mkdir()
            (input_folder / "sealed.bin").write_bytes(INPUT)
            (output_folder / "immutable.bin").write_bytes(MANAGER_CANARY)
            # Explicit package SID grants: traversal/read for stage/input,
            # modify only for scratch work. NO SID grant to output directory.
            for target, rights in (
                    (stage, "(RX)"), (input_folder, "(RX)"),
                    (input_folder / "sealed.bin", "(R)"),
                    (work_folder, "(OI)(CI)(M)")):
                subprocess.run(
                    ["icacls.exe", str(target), "/grant", "*" + package_sid + ":" + rights],
                    check=True, timeout=12, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)

        journal = BoxedProbeJournal(manager_state / "manager_control.sqlite3")
        grant = journal.prepare("JOB-" + secrets.token_hex(16),
                                "ATT-" + secrets.token_hex(16),
                                "OP-" + secrets.token_hex(16))
        attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
        child_in = wintypes.HANDLE()
        manager_in_write = wintypes.HANDLE()
        manager_out_read = wintypes.HANDLE()
        child_out = wintypes.HANDLE()
        if not k.CreatePipe(ctypes.byref(child_in), ctypes.byref(manager_in_write),
                            ctypes.byref(attrs), 4096):
            raise JobProbeUnavailable("cannot create input pipe")
        handles += [child_in, manager_in_write]
        if not k.CreatePipe(ctypes.byref(manager_out_read), ctypes.byref(child_out),
                            ctypes.byref(attrs), 4096):
            raise JobProbeUnavailable("cannot create output pipe")
        handles += [manager_out_read, child_out]
        for manager_end in (manager_in_write, manager_out_read):
            if not k.SetHandleInformation(manager_end, _HANDLE_FLAG_INHERIT, 0):
                raise JobProbeUnavailable("manager pipe end remains inheritable")

        if case == "handle":
            # This signalled and deliberately INHERITABLE manager-owned event
            # is NOT listed in the restricted child HANDLE_LIST.
            sentinel = k.CreateEventW(ctypes.byref(attrs), True, True, None)
            if not sentinel:
                raise JobProbeUnavailable("cannot allocate manager handle canary")
            handles.append(sentinel)
            flags = wintypes.DWORD()
            if (k.WaitForSingleObject(sentinel, 0) != _WAIT_OBJECT_0 or
                    not k.GetHandleInformation(sentinel, ctypes.byref(flags)) or
                    not flags.value & _HANDLE_FLAG_INHERIT):
                raise JobProbeUnavailable("manager inheritable event positive control failed")
            canary_confirmed = True
            boundary_arg = str(sentinel.value if hasattr(sentinel, "value") else int(sentinel))
        elif case == "network":
            # Live listener, local-only; positive control proves target exists.
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            listener.settimeout(1)
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                canary_confirmed = True
            boundary_arg = str(port)
        elif case == "breakaway":
            canary_confirmed = True  # confirmed via actual child IsProcessInJob

        required = ctypes.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(required))
        if not 1 <= required.value <= 65536:
            raise JobProbeUnavailable("invalid attribute-list allocation")
        attr_memory = ctypes.create_string_buffer(required.value)
        if not k.InitializeProcThreadAttributeList(attr_memory, 2, 0,
                                                   ctypes.byref(required)):
            raise JobProbeUnavailable("cannot create security/handle attributes")
        attr_initialized = True
        caps = _SecurityCapabilities(sid, None, 0, 0)
        if not k.UpdateProcThreadAttribute(
                attr_memory, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(caps), ctypes.sizeof(caps), None, None):
            raise JobProbeUnavailable("cannot bind AppContainer token")
        inherit = (wintypes.HANDLE * 2)(child_in.value, child_out.value)
        if not k.UpdateProcThreadAttribute(
                attr_memory, 0, _HANDLE_LIST, ctypes.byref(inherit),
                ctypes.sizeof(inherit), None, None):
            raise JobProbeUnavailable("cannot restrict inherited pipe handles")
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = child_in
        startup.StartupInfo.hStdOutput = child_out
        startup.StartupInfo.hStdError = child_out
        startup.lpAttributeList = ctypes.cast(attr_memory, ctypes.c_void_p)

        job = k.CreateJobObjectW(None, None)
        if not job:
            raise JobProbeUnavailable("cannot create bounding Job Object")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_LIMIT_KILL_ON_CLOSE | _JOB_LIMIT_ACTIVE_PROCESS |
            _JOB_LIMIT_PROCESS_MEMORY)
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = 128 * 1024 * 1024
        if not k.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS,
                                         ctypes.byref(limits), ctypes.sizeof(limits)):
            raise JobProbeUnavailable("cannot configure process/resource bounds")
        windows = ctypes.create_unicode_buffer(32768)
        n = k.GetWindowsDirectoryW(windows, len(windows))
        if not 1 <= n < len(windows):
            raise JobProbeUnavailable("Windows root unavailable")
        env = ctypes.create_unicode_buffer(
            "LOCALAPPDATA=" + _trusted_local_appdata() + "\0SystemRoot=" +
            windows.value + "\0WINDIR=" + windows.value + "\0\0")
        flags = (_CREATE_SUSPENDED | _CREATE_NO_WINDOW |
                 _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT)
        case_args = ([str(helper)] if case == "ok" else [str(helper), case])
        if case in ("handle", "network"):
            if boundary_arg is None:
                raise JobProbeUnavailable("native probe needs fixed manager-only canary")
            case_args.append(boundary_arg)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(case_args))
        if not k.CreateProcessW(str(helper), command, None, None, True, flags,
                                ctypes.cast(env, ctypes.c_void_p),
                                str(stage) if stage is not None else windows.value,
                                ctypes.cast(ctypes.byref(startup),
                                            ctypes.POINTER(_StartupInfo)),
                                ctypes.byref(process)):
            raise JobProbeUnavailable("fixed AppContainer child start failed; win32=" +
                                      str(ctypes.get_last_error()))
        process_started = True
        # Close child-only copies in the manager immediately after CreateProcess.
        for child_end in (child_in, child_out):
            k.CloseHandle(child_end)
            handles.remove(child_end)
        if not k.AssignProcessToJobObject(job, process.hProcess):
            raise JobProbeUnavailable("IPC child not assigned to Job before resume")
        if not a.OpenProcessToken(process.hProcess, _TOKEN_QUERY, ctypes.byref(token)):
            raise JobProbeUnavailable("cannot inspect IPC child token")
        bit = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not a.GetTokenInformation(token, _TOKEN_IS_APPCONTAINER,
                                     ctypes.byref(bit), ctypes.sizeof(bit),
                                     ctypes.byref(returned)) or bit.value != 1:
            raise JobProbeUnavailable("IPC child is not a real AppContainer process")

        # Commit the no-replay fence before untrusted child resumes.
        request = journal.before_dispatch(grant)
        try:
            if k.ResumeThread(process.hThread) == 0xffffffff:
                raise ProtocolUncertain("IPC child resume failed after dispatch fence")
            _write_request(k, manager_in_write, request)
            k.CloseHandle(manager_in_write)
            handles.remove(manager_in_write)
            if k.WaitForSingleObject(process.hProcess, 4000) != _WAIT_OBJECT_0:
                raise ProtocolUncertain("IPC child deadline exceeded: attempt uncertain")
            process_exited = True
            exit_code = wintypes.DWORD()
            if not k.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)):
                raise ProtocolUncertain("IPC child exit receipt unavailable")
            if exit_code.value != 0:
                if case in _BOUNDARY_MODES:
                    raise JobProbeUnavailable(
                        "fixed v041 native boundary child rejected attempt; exit=" +
                        str(exit_code.value))
                raise ProtocolUncertain("IPC child failed or exit receipt unavailable")
            response = _read_reply(k, manager_out_read)
            # Record hostile reply bytes in the durable journal. Malformed,
            # wrong-scope and extra data must persist NEEDS_RECONCILIATION.
            receipt = journal.observe_response(grant, response)
            if receipt.state != "RESPONSE_OBSERVED" or (
                    receipt.observed_code != "PONG" and
                    not (case == "network" and receipt.observed_code == "DENIED")):
                raise ProtocolUncertain("durable bounded IPC observation missing")
        except (FrameRejected, ProtocolUncertain):
            if case == "ok" or case in _FILE_MODES:
                raise
            state = journal.status(grant.job_id, grant.attempt_id, grant.operation_id)
            expected = ("IN_FLIGHT" if case in ("crash", "hang")
                        else "NEEDS_RECONCILIATION")
            if state.state != expected or state.automatic_replay_allowed:
                raise JobProbeUnavailable("native failure did not remain durably fenced")
            return BoxedFaultEvidence(case, state.state, False)
        if case in _FILE_MODES:
            if stage is None:
                raise JobProbeUnavailable("trusted synthetic file stage missing")
            try:
                payload = inspect_fixed_result(stage)
            except (SyntheticArtifactRejected, OSError) as exc:
                state = journal.quarantine_artifact(grant)
                if case != "file_bad" or state.state != "NEEDS_RECONCILIATION":
                    raise JobProbeUnavailable("unexpected synthetic artifact rejection") from exc
                if (stage / "output" / "result.bin").exists():
                    raise JobProbeUnavailable("rejected synthetic artifact was published")
                return BoxedFileEvidence(
                    mode=case, input_digest=sha(INPUT), result_digest=None,
                    journal_state=state.state, synthetic_artifact_published=False,
                    result_validation_rejected=True,
                )
            if case != "file_ok":
                journal.quarantine_artifact(grant)
                raise JobProbeUnavailable("corrupted synthetic output unexpectedly accepted")
            published = stage / "output" / "result.bin"
            with published.open("xb") as sink:
                sink.write(payload)
                sink.flush()
                os.fsync(sink.fileno())
            if published.read_bytes() != payload:
                journal.quarantine_artifact(grant)
                raise JobProbeUnavailable("manager-only synthetic publication changed")
            return BoxedFileEvidence(
                mode=case, input_digest=sha(INPUT), result_digest=sha(payload),
                journal_state=receipt.state, synthetic_artifact_published=True,
                result_validation_rejected=False,
            )
        if case in _BOUNDARY_MODES:
            network_status = "NOT_TESTED"
            if case == "network":
                # Fixed child PONG means the WinSock error was WSAEACCES.
                # DENIED here is only a live-listener connection TIMEOUT.
                # Neither says anything about other egress pathways.
                network_status = ("EXPLICIT_LOOPBACK_WSAEACCES" if
                                  receipt.observed_code == "PONG" else
                                  "LOOPBACK_TIMEOUT_UNATTESTED")
            return BoxedBoundaryEvidence(
                mode=case, observed_code=receipt.observed_code,
                journal_state=receipt.state, real_appcontainer_token=True,
                job_assigned_before_resume=True,
                manager_canary_positive_control=canary_confirmed,
                network_result=network_status,
            )
        if case != "ok":
            raise JobProbeUnavailable("hostile fixed child incorrectly accepted as PONG")
        return BoxedIPCEvidence(True, True, True, receipt.observed_code,
                                receipt.state, exit_code.value)
    finally:
        if listener is not None:
            listener.close()
        if job and process_started and not process_exited:
            k.TerminateJobObject(job, 137)
            k.WaitForSingleObject(process.hProcess, 2000)
        if token:
            k.CloseHandle(token)
        for handle in handles:
            k.CloseHandle(handle)
        if process_started:
            k.CloseHandle(process.hThread)
            k.CloseHandle(process.hProcess)
        if job:
            k.CloseHandle(job)
        if attr_initialized:
            k.DeleteProcThreadAttributeList(attr_memory)
        if sid.value:
            a.FreeSid(sid)
        if profile_created and u.DeleteAppContainerProfile(moniker) != 0:
            raise JobProbeUnavailable("disposable AppContainer profile cleanup failed")
        temp.cleanup()


def run_fixed_boxed_ipc_probe():
    """Normal fixed PROBE_PING child with no caller-controlled executable/tool."""
    return _run_fixed_boxed_ipc_case("ok")
