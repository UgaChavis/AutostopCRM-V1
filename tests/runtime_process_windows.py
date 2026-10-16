"""Windows test process ownership, assigned atomically at CreateProcess time.

https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
"""

from __future__ import annotations

import ctypes
import math
import os
import shutil
import subprocess
import time
from ctypes import wintypes as w

SIZE = ctypes.c_size_t
POINTER = ctypes.c_void_p


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", w.DWORD),
        ("MinimumWorkingSetSize", SIZE),
        ("MaximumWorkingSetSize", SIZE),
        ("ActiveProcessLimit", w.DWORD),
        ("Affinity", SIZE),
        ("PriorityClass", w.DWORD),
        ("SchedulingClass", w.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", SIZE),
        ("JobMemoryLimit", SIZE),
        ("PeakProcessMemoryUsed", SIZE),
        ("PeakJobMemoryUsed", SIZE),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("lpReserved", w.LPWSTR),
        ("lpDesktop", w.LPWSTR),
        ("lpTitle", w.LPWSTR),
        ("dwX", w.DWORD),
        ("dwY", w.DWORD),
        ("dwXSize", w.DWORD),
        ("dwYSize", w.DWORD),
        ("dwXCountChars", w.DWORD),
        ("dwYCountChars", w.DWORD),
        ("dwFillAttribute", w.DWORD),
        ("dwFlags", w.DWORD),
        ("wShowWindow", w.WORD),
        ("cbReserved2", w.WORD),
        ("lpReserved2", POINTER),
        ("hStdInput", w.HANDLE),
        ("hStdOutput", w.HANDLE),
        ("hStdError", w.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", POINTER)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", w.HANDLE),
        ("hThread", w.HANDLE),
        ("dwProcessId", w.DWORD),
        ("dwThreadId", w.DWORD),
    ]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([POINTER, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, POINTER, w.DWORD], w.BOOL),
        "QueryInformationJobObject": (
            [w.HANDLE, ctypes.c_int, POINTER, w.DWORD, ctypes.POINTER(w.DWORD)],
            w.BOOL,
        ),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "InitializeProcThreadAttributeList": (
            [POINTER, w.DWORD, w.DWORD, ctypes.POINTER(SIZE)],
            w.BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [POINTER, w.DWORD, SIZE, POINTER, SIZE, POINTER, POINTER],
            w.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([POINTER], None),
        "CreateProcessW": (
            [
                w.LPCWSTR,
                w.LPWSTR,
                POINTER,
                POINTER,
                w.BOOL,
                w.DWORD,
                POINTER,
                w.LPCWSTR,
                ctypes.POINTER(_StartupInfoEx),
                ctypes.POINTER(_ProcessInfo),
            ],
            w.BOOL,
        ),
        "GetStdHandle": ([w.DWORD], w.HANDLE),
        "GetCurrentProcess": ([], w.HANDLE),
        "DuplicateHandle": (
            [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD],
            w.BOOL,
        ),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


def _check(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


class WindowsProcess:
    def __init__(self, command, *, cwd):
        self.command = command
        self.kernel = _kernel()
        self.job = None
        self.process = None
        self.returncode = None
        try:
            self.job = _check(self.kernel.CreateJobObjectW(None, None))
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            _check(
                self.kernel.SetInformationJobObject(
                    self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            self._spawn(command, cwd)
        except BaseException:
            self.close()
            raise

    def _standard_handles(self):
        import msvcrt

        handles = []
        try:
            current = self.kernel.GetCurrentProcess()
            for code in (-10, -11, -12):
                source = self.kernel.GetStdHandle(code)
                fallback = None
                try:
                    if not source or source == POINTER(-1).value:
                        fallback = open(os.devnull, "rb" if code == -10 else "wb")
                        source = msvcrt.get_osfhandle(fallback.fileno())
                    duplicate = w.HANDLE()
                    _check(
                        self.kernel.DuplicateHandle(
                            current, source, current, ctypes.byref(duplicate), 0, True, 2
                        )
                    )
                    handles.append(duplicate.value)
                finally:
                    if fallback is not None:
                        fallback.close()
            return handles
        except BaseException:
            for handle in handles:
                self.kernel.CloseHandle(handle)
            raise

    def _spawn(self, command, cwd):
        handles = self._standard_handles()
        initialized = False
        attributes = None
        try:
            size = SIZE()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = ctypes.create_string_buffer(size.value)
            _check(
                self.kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size))
            )
            initialized = True
            jobs = (w.HANDLE * 1)(self.job)
            inherited = (w.HANDLE * len(handles))(*handles)
            for key, values in ((0x2000D, jobs), (0x20002, inherited)):
                _check(
                    self.kernel.UpdateProcThreadAttribute(
                        attributes, 0, key, values, ctypes.sizeof(values), None, None
                    )
                )
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            (
                startup.StartupInfo.hStdInput,
                startup.StartupInfo.hStdOutput,
                startup.StartupInfo.hStdError,
            ) = handles
            startup.lpAttributeList = ctypes.cast(attributes, POINTER)
            information = _ProcessInfo()
            executable = shutil.which(command[0]) or command[0]
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = ctypes.create_unicode_buffer(
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(os.environ.items(), key=lambda item: item[0].upper())
                )
                + "\0\0"
            )
            # Attribute JOB_LIST contains the venv launcher before it can spawn
            # the real Python process. Only duplicated stdio handles are inherited.
            _check(
                self.kernel.CreateProcessW(
                    executable,
                    command_line,
                    None,
                    None,
                    True,
                    0x00080000 | 0x00000400 | 0x08000000,
                    environment,
                    str(cwd),
                    ctypes.byref(startup),
                    ctypes.byref(information),
                )
            )
            self.process = information.hProcess
            self.pid = information.dwProcessId
            _check(self.kernel.CloseHandle(information.hThread))
        finally:
            if initialized:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            for handle in handles:
                self.kernel.CloseHandle(handle)

    def wait(self, timeout):
        deadline = time.monotonic() + max(0, timeout)
        while True:
            milliseconds = max(0, math.ceil((deadline - time.monotonic()) * 1000))
            # Let Python deliver cancellation while the same native child runs.
            status = self.kernel.WaitForSingleObject(self.process, min(250, milliseconds))
            if status == 0x102:
                # Kernel timer resolution must not reject a still-live budget.
                # Continue waiting on the same handle, never restarting a test.
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.command, timeout)
                continue
            if status != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = w.DWORD()
            _check(self.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)))
            self.returncode = code.value
            return self.returncode

    def members(self):
        capacity = 64
        while capacity <= 65536:
            buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(SIZE) * capacity)
            if self.kernel.QueryInformationJobObject(self.job, 3, buffer, len(buffer), None):
                count = ctypes.cast(buffer, ctypes.POINTER(w.DWORD))[1]
                ids = (SIZE * count).from_buffer(buffer, 8)
                return [{"pid": int(pid)} for pid in ids]
            if ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(ctypes.get_last_error())
            capacity *= 2
        raise RuntimeError("Owned Windows job process list exceeded its diagnostic limit")

    def kill(self, exit_code):
        _check(self.kernel.TerminateJobObject(self.job, exit_code))

    def close(self):
        try:
            if self.job is not None:
                self.kernel.CloseHandle(self.job)
                self.job = None
        finally:
            if self.process is not None:
                self.kernel.CloseHandle(self.process)
                self.process = None
