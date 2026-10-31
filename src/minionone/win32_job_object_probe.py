"""Win32 Job Object process-lifecycle feasibility probe; NEVER a worker tool.

Runs ONLY hard-coded benign self-tests with fixed local Python code. Does not
accept model text, executable paths, arguments, scripts, or environment.
Windows Job Objects constrain process trees and resources, but do NOT
isolate filesystem or network access. Not an AppContainer or security sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys

# Fixed developer-owned probe programs. No dynamic code, host paths, worker
# content, or network permissions are accepted from a caller.
_PROBES = {
    "exit": "import sys; sys.exit(0)",
    "sleep": "import time; time.sleep(15)",
    "no-parent-env": (
        "import os,sys; "
        "sys.exit(0 if 'MINIONONE_SANDBOX_SECRET_CANARY' not in os.environ else 23)"
    ),
}
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_JOB_EXTENDED_LIMITS = 9
_JOB_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_LIMIT_KILL_ON_CLOSE = 0x00002000
_WAIT_TIMEOUT = 258
_WAIT_OBJECT_0 = 0
_MAX_PROBE_MS = 5000


class JobProbeUnavailable(RuntimeError):
    """The fixed probe cannot be run with all requested lifecycle controls."""


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


@dataclass(frozen=True, slots=True)
class JobProbeEvidence:
    mode: str
    job_attached_before_resume: bool
    timed_out: bool
    exit_code: int | None
    process_memory_limit_mb: int
    active_process_limit: int
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False


def run_fixed_job_probe(mode, *, timeout_ms=2000):
    """Windows-native suspended-start -> assign -> resume -> bounded wait.

    On any failure, abort and close the Job Object, whose KILL_ON_JOB_CLOSE
    flag terminates associated processes. This method is deliberately NOT
    wired into AgentLoop, BoxedToolGateway, the MCP API, or LM Studio.
    """
    if type(mode) is not str or mode not in _PROBES:
        raise JobProbeUnavailable("unknown fixed native probe operation")
    if type(timeout_ms) is not int or not 50 <= timeout_ms <= _MAX_PROBE_MS:
        raise JobProbeUnavailable("unsafe native probe deadline")
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        raise JobProbeUnavailable("native Windows source interpreter required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                           ctypes.c_void_p, wintypes.DWORD]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo),
    ]
    api.CreateProcessW.restype = wintypes.BOOL
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    api.GetWindowsDirectoryW.restype = wintypes.UINT

    windir_buf = ctypes.create_unicode_buffer(32768)
    length = api.GetWindowsDirectoryW(windir_buf, len(windir_buf))
    if not 1 <= length < len(windir_buf):
        raise JobProbeUnavailable("system Windows path unavailable")
    windir = windir_buf.value
    # No inherited credentials, PYTHONPATH or application environment.
    env = ctypes.create_unicode_buffer(
        "SystemRoot=" + windir + "\0WINDIR=" + windir + "\0\0")
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(
        [sys.executable, "-I", "-S", "-c", _PROBES[mode]]))
    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    process = _ProcessInfo()
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise JobProbeUnavailable("native Job Object creation failed")
    created = False
    attached = False
    finished = False
    try:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_LIMIT_KILL_ON_CLOSE | _JOB_LIMIT_PROCESS_MEMORY |
            _JOB_LIMIT_ACTIVE_PROCESS)
        limits.BasicLimitInformation.ActiveProcessLimit = 2
        limits.ProcessMemoryLimit = 256 * 1024 * 1024
        if not api.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS,
                                           ctypes.byref(limits),
                                           ctypes.sizeof(limits)):
            raise JobProbeUnavailable("native Job Object limits rejected")
        flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT
        if not api.CreateProcessW(None, command, None, None, False, flags,
                                  ctypes.cast(env, ctypes.c_void_p), None,
                                  ctypes.byref(startup), ctypes.byref(process)):
            raise JobProbeUnavailable("fixed native probe launch refused")
        created = True
        if not api.AssignProcessToJobObject(job, process.hProcess):
            raise JobProbeUnavailable("cannot attach suspended process to Job Object")
        attached = True
        if api.ResumeThread(process.hThread) == 0xffffffff:
            raise JobProbeUnavailable("cannot resume confined probe")
        state = api.WaitForSingleObject(process.hProcess, timeout_ms)
        if state == _WAIT_TIMEOUT:
            if not api.TerminateJobObject(job, 125):
                raise JobProbeUnavailable("Job Object timeout termination failed")
            if api.WaitForSingleObject(process.hProcess, 2000) != _WAIT_OBJECT_0:
                raise JobProbeUnavailable("terminated native probe did not exit")
            finished = True
            return JobProbeEvidence(mode, attached, True, None, 256, 2)
        if state != _WAIT_OBJECT_0:
            raise JobProbeUnavailable("native probe wait failed")
        code = wintypes.DWORD()
        if not api.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
            raise JobProbeUnavailable("native probe exit state unavailable")
        finished = True
        return JobProbeEvidence(mode, attached, False, int(code.value), 256, 2)
    finally:
        if created and not finished:
            api.TerminateJobObject(job, 126)
        # Closing the Job Object also terminates any remaining descendants.
        api.CloseHandle(job)
        if created:
            api.CloseHandle(process.hThread)
            api.CloseHandle(process.hProcess)
