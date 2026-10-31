"""v0.37 trusted native AppContainer adversarial-access experiment (NOT worker tool).

Tests real kernel denials using a fixed Visual C++ probe executable built by CI.
Accepts only the fixed helper path from a trusted harness; no model text,
script, shell, worker task, arbitrary executable or output material is accepted.
No verified target-host executable sandbox or commercial release is implied.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

from .win32_appcontainer_probe import (
    _StartupInfoEx, _SecurityCapabilities, _trusted_local_appdata,
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    _EXTENDED_STARTUPINFO_PRESENT, _TOKEN_QUERY, _TOKEN_IS_APPCONTAINER,
)
from .win32_job_object_probe import (
    JobProbeUnavailable, _ProcessInfo, _ExtendedLimits, _StartupInfo,
    _CREATE_SUSPENDED, _CREATE_NO_WINDOW, _CREATE_UNICODE_ENVIRONMENT,
    _JOB_EXTENDED_LIMITS, _JOB_LIMIT_KILL_ON_CLOSE,
    _JOB_LIMIT_ACTIVE_PROCESS, _JOB_LIMIT_PROCESS_MEMORY, _WAIT_OBJECT_0,
)


@dataclass(frozen=True, slots=True)
class AdversarialAccessEvidence:
    real_appcontainer_token: bool
    assigned_before_resume: bool
    manager_canary_read_denied: bool
    sibling_canary_read_denied: bool
    output_canary_write_denied: bool
    loopback_connect_denied: bool
    ordinary_host_positive_control: bool
    canary_integrity_preserved: bool
    process_exited: bool
    worker_tool_exposed: bool = False
    host_security_attested: bool = False
    product_release: str = "NO_GO"


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _apis():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u = ctypes.WinDLL("userenv", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    k.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
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
    k.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    k.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    k.GetWindowsDirectoryW.restype = wintypes.UINT
    u.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    u.CreateAppContainerProfile.restype = ctypes.c_long
    u.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    u.DeleteAppContainerProfile.restype = ctypes.c_long
    a.FreeSid.argtypes = [ctypes.c_void_p]
    a.FreeSid.restype = ctypes.c_void_p
    a.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    a.ConvertSidToStringSidW.restype = wintypes.BOOL
    a.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    a.OpenProcessToken.restype = wintypes.BOOL
    a.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    a.GetTokenInformation.restype = wintypes.BOOL
    return k, u, a


def _sid_string(k, a, sid):
    pointer = ctypes.c_void_p()
    if not a.ConvertSidToStringSidW(sid, ctypes.byref(pointer)):
        raise JobProbeUnavailable("cannot identify isolated package SID")
    try:
        return ctypes.wstring_at(pointer.value)
    finally:
        k.LocalFree(pointer)


def _allow_probe_executable_only(sid_string, directory, executable):
    """Grant RX only to disposable helper binary/ancestors, never canaries."""
    if not sid_string.startswith("S-1-15-2-"):
        raise JobProbeUnavailable("invalid AppContainer package identity")
    for path in (directory.parent, directory, executable):
        subprocess.run(
            ["icacls.exe", str(path), "/grant", "*" + sid_string + ":(RX)"],
            check=True, timeout=12, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _appcontainer_exit(helper, arguments, runnable, timeout_ms=4000):
    """Return the fixed helper's exit code after real token/Job checks."""
    k, u, a = _apis()
    sid = ctypes.c_void_p()
    moniker = "MinionOne.Adversarial." + secrets.token_hex(12)
    profile = False
    initialized = False
    attr_mem = None
    process = _ProcessInfo()
    started = False
    finished = False
    token = wintypes.HANDLE()
    job = None
    real_token = False
    assigned = False
    try:
        hr = u.CreateAppContainerProfile(
            moniker, "MinionOne synthetic denial probe",
            "Test-only disposable package; no worker execution", None, 0,
            ctypes.byref(sid))
        if hr != 0 or not sid.value:
            raise JobProbeUnavailable("native AppContainer profile unavailable")
        profile = True
        _allow_probe_executable_only(
            _sid_string(k, a, sid), runnable, helper)

        required = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(required))
        if not 1 <= required.value <= 65536:
            raise JobProbeUnavailable("attribute memory request invalid")
        attr_mem = ctypes.create_string_buffer(required.value)
        if not k.InitializeProcThreadAttributeList(
                attr_mem, 1, 0, ctypes.byref(required)):
            raise JobProbeUnavailable("AppContainer attributes unavailable")
        initialized = True
        caps = _SecurityCapabilities(sid, None, 0, 0)
        if not k.UpdateProcThreadAttribute(
                attr_mem, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(caps), ctypes.sizeof(caps), None, None):
            raise JobProbeUnavailable("AppContainer identity attribute rejected")
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.cast(attr_mem, ctypes.c_void_p)

        job = k.CreateJobObjectW(None, None)
        if not job:
            raise JobProbeUnavailable("bounded native Job Object unavailable")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_LIMIT_KILL_ON_CLOSE | _JOB_LIMIT_ACTIVE_PROCESS |
            _JOB_LIMIT_PROCESS_MEMORY)
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = 128 * 1024 * 1024
        if not k.SetInformationJobObject(
                job, _JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                ctypes.sizeof(limits)):
            raise JobProbeUnavailable("native Job Object limits rejected")

        windows = ctypes.create_unicode_buffer(32768)
        n = k.GetWindowsDirectoryW(windows, len(windows))
        if not 1 <= n < len(windows):
            raise JobProbeUnavailable("Windows root unavailable")
        env = ctypes.create_unicode_buffer(
            "LOCALAPPDATA=" + _trusted_local_appdata() + "\0SystemRoot=" +
            windows.value + "\0WINDIR=" + windows.value + "\0\0")

        command = ctypes.create_unicode_buffer(
            subprocess.list2cmdline([str(helper), *arguments]))
        flags = (_CREATE_SUSPENDED | _CREATE_NO_WINDOW |
                 _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT)
        if not k.CreateProcessW(
                str(helper), command, None, None, False, flags,
                ctypes.cast(env, ctypes.c_void_p), windows.value,
                ctypes.cast(ctypes.byref(startup),
                            ctypes.POINTER(_StartupInfo)),
                ctypes.byref(process)):
            raise JobProbeUnavailable(
                "native fixed helper creation failed; win32=" +
                str(ctypes.get_last_error()))
        started = True
        if not k.AssignProcessToJobObject(job, process.hProcess):
            raise JobProbeUnavailable("fixed helper not assigned before resume")
        assigned = True
        if not a.OpenProcessToken(process.hProcess, _TOKEN_QUERY,
                                  ctypes.byref(token)):
            raise JobProbeUnavailable("cannot inspect child token")
        marker = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not a.GetTokenInformation(
                token, _TOKEN_IS_APPCONTAINER, ctypes.byref(marker),
                ctypes.sizeof(marker), ctypes.byref(returned)):
            raise JobProbeUnavailable("cannot inspect native AppContainer bit")
        real_token = marker.value == 1
        if not real_token:
            raise JobProbeUnavailable("child is not an AppContainer")
        if k.ResumeThread(process.hThread) == 0xffffffff:
            raise JobProbeUnavailable("confined helper cannot start")
        if k.WaitForSingleObject(process.hProcess, timeout_ms) != _WAIT_OBJECT_0:
            raise JobProbeUnavailable("confined helper did not finish in time")
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
            raise JobProbeUnavailable("helper exit receipt unavailable")
        finished = True
        return int(code.value), real_token, assigned
    finally:
        if job and not finished:
            k.TerminateJobObject(job, 137)
            if started:
                k.WaitForSingleObject(process.hProcess, 2000)
        if token:
            k.CloseHandle(token)
        if job:
            k.CloseHandle(job)
        if started:
            k.CloseHandle(process.hThread)
            k.CloseHandle(process.hProcess)
        if initialized:
            k.DeleteProcThreadAttributeList(attr_mem)
        if sid.value:
            a.FreeSid(sid)
        if profile:
            if u.DeleteAppContainerProfile(moniker) != 0:
                raise JobProbeUnavailable("ephemeral AppContainer identity not removed")


def run_fixed_adversarial_access_probe():
    """Verify four synthetic denials WITH an unsandboxed positive control.

    The ONE permitted exe is the fixed C test helper built into the exact
    repository checkout by trusted Windows CI. No caller-selected executable,
    task text, target path, command or network endpoint is accepted.
    """
    if sys.platform != "win32":
        raise JobProbeUnavailable("native Windows required")
    # Resolve only the expected test binary in this source checkout.
    # No API parameter can point the trusted manager at an attacker binary.
    expected = (Path(__file__).resolve().parents[2] / "evidence" / "v037" /
                "appcontainer_adversary_v037.exe")
    if not expected.is_file() or expected.is_symlink():
        raise JobProbeUnavailable("trusted fixed MSVC test helper unavailable")
    exe = expected.resolve(strict=True)
    if exe != expected:
        raise JobProbeUnavailable("unexpected native probe executable path")
    if not exe.is_file() or exe.stat().st_size > 2 * 1024 * 1024:
        raise JobProbeUnavailable("invalid or oversized native test helper")
    with tempfile.TemporaryDirectory(prefix="minionone-v037-") as root:
        root = Path(root)
        runnable = root / "runnable"
        runnable.mkdir()
        deployed = runnable / "appcontainer_adversary_v037.exe"
        deployed.write_bytes(exe.read_bytes())
        if _hash(deployed) != _hash(exe):
            raise JobProbeUnavailable("native probe executable copied incorrectly")
        files = []
        for zone, name, data in (
            ("manager", "manager.db", b"V037_SYNTHETIC_MANAGER_SECRET"),
            ("sibling", "other_job.txt", b"V037_SYNTHETIC_FOREIGN_JOB"),
            ("published", "immutable.bin", b"V037_SYNTHETIC_OUTPUT"),
        ):
            folder = root / zone
            folder.mkdir()
            path = folder / name
            path.write_bytes(data)
            files.append(path)
        before = tuple(_hash(path) for path in files)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            args = [*(str(path) for path in files), str(port)]
            # The positive control proves the same fixed helper can open
            # the actual canaries and reach a verified listening endpoint.
            control = subprocess.run(
                [str(deployed), *args], timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False)
            if control.returncode != 0x4f:
                raise JobProbeUnavailable(
                    "normal host positive control did not exercise all four accesses; code=" +
                    str(control.returncode))
            if tuple(_hash(path) for path in files) != before:
                raise JobProbeUnavailable("positive-control data unexpectedly changed")
            bootstrap, boot_token, boot_assigned = _appcontainer_exit(
                deployed, [], runnable)
            if bootstrap != 0x51 or not boot_token or not boot_assigned:
                raise JobProbeUnavailable(
                    "fixed native helper bootstrap did not execute; code=" +
                    str(bootstrap))
            result, token, assigned = _appcontainer_exit(
                deployed, args, runnable)
        if result != 0x40:
            raise JobProbeUnavailable(
                "AppContainer access-denial contract failed; helper code=" +
                str(result))
        preserved = tuple(_hash(path) for path in files) == before
        if not preserved:
            raise JobProbeUnavailable("synthetic canary integrity violated")
        return AdversarialAccessEvidence(
            real_appcontainer_token=token,
            assigned_before_resume=assigned,
            manager_canary_read_denied=True,
            sibling_canary_read_denied=True,
            output_canary_write_denied=True,
            loopback_connect_denied=True,
            ordinary_host_positive_control=True,
            canary_integrity_preserved=True,
            process_exited=True,
        )
