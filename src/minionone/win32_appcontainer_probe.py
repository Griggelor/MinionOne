"""Strictly internal Windows AppContainer-token startup experiment.

Only a pinned Windows system diagnostic binary is executed with an AppContainer
security token and a bounded Job Object. Worker tools, model text and arbitrary
code are NOT involved. A true access-isolation attestation requires additional
separate-process filesystem/network denial tests; do NOT advertise this probe
as an executable sandbox or worker capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import secrets
import subprocess
import sys

from .win32_job_object_probe import (
    JobProbeUnavailable, _ExtendedLimits, _StartupInfo, _ProcessInfo,
    _CREATE_SUSPENDED, _CREATE_NO_WINDOW, _CREATE_UNICODE_ENVIRONMENT,
    _JOB_EXTENDED_LIMITS, _JOB_LIMIT_KILL_ON_CLOSE,
    _JOB_LIMIT_ACTIVE_PROCESS, _JOB_LIMIT_PROCESS_MEMORY,
    _WAIT_OBJECT_0,
)

_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_TOKEN_IS_APPCONTAINER = 29
_TOKEN_QUERY = 0x0008


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


@dataclass(frozen=True, slots=True)
class AppContainerTokenEvidence:
    security_capabilities_attached: bool
    token_is_appcontainer: bool
    no_declared_network_capabilities: bool
    job_attached_before_resume: bool
    process_exited: bool
    output_validated: bool = False
    foreign_fs_denial_verified: bool = False
    network_denial_verified: bool = False
    worker_tool_exposed: bool = False
    os_sandbox_attested: bool = False


def run_fixed_appcontainer_token_probe():
    """Start a single OS-owned fixed binary under an actual AppContainer token.

    No ability to select an executable/args, no stdout or file output, no
    host mount, and no generic execution capability. Creation/assignment/
    verification all happen BEFORE the suspended process is resumed.
    """
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        raise JobProbeUnavailable("Windows source-native AppContainer probe required")

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    envapi = ctypes.WinDLL("userenv", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    envapi.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    envapi.CreateAppContainerProfile.restype = ctypes.c_long
    envapi.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    envapi.DeleteAppContainerProfile.restype = ctypes.c_long
    advapi.FreeSid.argtypes = [ctypes.c_void_p]
    advapi.FreeSid.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL

    k.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    k.GetWindowsDirectoryW.restype = wintypes.UINT
    k.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    k.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k.DeleteProcThreadAttributeList.restype = None
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo),
    ]
    k.CreateProcessW.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL

    winbuf = ctypes.create_unicode_buffer(32768)
    n = k.GetWindowsDirectoryW(winbuf, len(winbuf))
    if not 1 <= n < len(winbuf):
        raise JobProbeUnavailable("Windows system directory unavailable")
    windir = winbuf.value
    # No command parsing, PATH lookup, editable script or arbitrary host binary.
    executable = os.path.join(windir, "System32", "whoami.exe")
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([executable]))
    environment = ctypes.create_unicode_buffer(
        "SystemRoot=" + windir + "\0WINDIR=" + windir + "\0\0")

    sid = ctypes.c_void_p()
    name = "MinionOne.Probe." + secrets.token_hex(12)
    profile_created = False
    attr_initialized = False
    attr_mem = None
    token = wintypes.HANDLE()
    process = _ProcessInfo()
    process_created = False
    job = None
    process_finished = False
    try:
        hr = envapi.CreateAppContainerProfile(
            name, "MinionOne isolated token probe",
            "Fixed non-worker test; deleted after execution", None, 0,
            ctypes.byref(sid))
        if hr != 0 or not sid.value:
            raise JobProbeUnavailable("AppContainer profile creation denied")
        profile_created = True

        required = ctypes.c_size_t()
        k.InitializeProcThreadAttributeList(None, 1, 0,
                                            ctypes.byref(required))
        if not 1 <= required.value <= 65536:
            raise JobProbeUnavailable("AppContainer attribute size invalid")
        attr_mem = ctypes.create_string_buffer(required.value)
        if not k.InitializeProcThreadAttributeList(attr_mem, 1, 0,
                                                  ctypes.byref(required)):
            raise JobProbeUnavailable("AppContainer attribute initialization denied")
        attr_initialized = True
        security = _SecurityCapabilities(sid, None, 0, 0)
        if not k.UpdateProcThreadAttribute(
                attr_mem, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(security), ctypes.sizeof(security), None, None):
            raise JobProbeUnavailable("AppContainer security capabilities denied")
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.cast(attr_mem, ctypes.c_void_p)

        job = k.CreateJobObjectW(None, None)
        if not job:
            raise JobProbeUnavailable("AppContainer Job Object unavailable")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_LIMIT_KILL_ON_CLOSE | _JOB_LIMIT_PROCESS_MEMORY |
            _JOB_LIMIT_ACTIVE_PROCESS)
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = 128 * 1024 * 1024
        if not k.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS,
                                         ctypes.byref(limits), ctypes.sizeof(limits)):
            raise JobProbeUnavailable("AppContainer resource limits unavailable")

        flags = (_CREATE_SUSPENDED | _CREATE_NO_WINDOW |
                 _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT)
        if not k.CreateProcessW(
                executable, command, None, None, False, flags,
                ctypes.cast(environment, ctypes.c_void_p), windir,
                ctypes.cast(ctypes.byref(startup),
                            ctypes.POINTER(_StartupInfo)),
                ctypes.byref(process)):
            raise JobProbeUnavailable("fixed AppContainer process creation denied")
        process_created = True
        if not k.AssignProcessToJobObject(job, process.hProcess):
            raise JobProbeUnavailable("suspended AppContainer could not join Job Object")
        if not advapi.OpenProcessToken(process.hProcess, _TOKEN_QUERY,
                                      ctypes.byref(token)):
            raise JobProbeUnavailable("cannot inspect AppContainer child token")
        marker = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not advapi.GetTokenInformation(
                token, _TOKEN_IS_APPCONTAINER, ctypes.byref(marker),
                ctypes.sizeof(marker), ctypes.byref(returned)):
            raise JobProbeUnavailable("AppContainer token inspection failed")
        if marker.value != 1:
            raise JobProbeUnavailable("child is NOT an actual AppContainer")
        if k.ResumeThread(process.hThread) == 0xffffffff:
            raise JobProbeUnavailable("AppContainer process resume failed")
        if k.WaitForSingleObject(process.hProcess, 3500) != _WAIT_OBJECT_0:
            raise JobProbeUnavailable("fixed AppContainer process did not exit")
        process_finished = True
        return AppContainerTokenEvidence(True, True, True, True, True)
    finally:
        if job and not process_finished:
            k.TerminateJobObject(job, 137)
            if process_created:
                k.WaitForSingleObject(process.hProcess, 2000)
        if token:
            k.CloseHandle(token)
        if job:
            k.CloseHandle(job)
        if process_created:
            k.CloseHandle(process.hThread)
            k.CloseHandle(process.hProcess)
        if attr_initialized:
            k.DeleteProcThreadAttributeList(attr_mem)
        if sid.value:
            advapi.FreeSid(sid)
        if profile_created:
            # No persistent per-test security identity left on the target host.
            if envapi.DeleteAppContainerProfile(name) != 0:
                # Avoid declaring a clean run if per-user profile cleanup failed.
                raise JobProbeUnavailable("AppContainer profile cleanup unverified")
