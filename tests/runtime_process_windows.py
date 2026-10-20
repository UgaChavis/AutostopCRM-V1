"""Windows test process ownership, assigned atomically at CreateProcess time.

https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes as w
from datetime import UTC, datetime
from pathlib import Path

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
        "GetProcessId": ([w.HANDLE], w.DWORD),
        "GetProcessTimes": (
            [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)],
            w.BOOL,
        ),
        "QueryFullProcessImageNameW": (
            [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)],
            w.BOOL,
        ),
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


def _driver_popen(playwright):
    """Pinned Playwright 1.60 / CPython 3.12 shape; never look up a PID.

    PipeTransport owns asyncio.Process; BaseSubprocessTransport exposes the
    original Popen as get_extra_info('subprocess'). Do not call poll()/wait()
    on that Popen, mutate its transport, or consume its protocol streams.
    """
    process = playwright._impl_obj._connection._transport._proc
    popen = process._transport.get_extra_info("subprocess")
    if (
        type(process.pid) is not int
        or type(popen.pid) is not int
        or process.pid <= 0
        or popen.pid != process.pid
    ):
        raise ValueError("driver_process_identity")
    if isinstance(popen._handle, bool) or not isinstance(popen._handle, int) or popen._handle <= 0:
        raise ValueError("driver_native_handle")
    return popen


class _DriverProcess:
    """A noninheritable query/synchronize duplicate, with no terminate rights."""

    def __init__(self, playwright):
        popen = _driver_popen(playwright)
        self.kernel = _kernel()
        self.pid, self.birth, self.handle = popen.pid, None, None
        current, duplicate = self.kernel.GetCurrentProcess(), w.HANDLE()
        _check(
            self.kernel.DuplicateHandle(
                current, popen._handle, current, ctypes.byref(duplicate), 0x00101000, False, 0
            )
        )
        self.handle = duplicate.value
        try:
            creation, exit_time, kernel_time, user_time = (w.FILETIME() for _ in range(4))
            _check(
                self.kernel.GetProcessTimes(
                    current,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
            )
            self.owner_birth = creation.dwLowDateTime | creation.dwHighDateTime << 32
            if self.owner_birth <= 0:
                raise ValueError("driver_owner_birth")
            self.read()
            size = w.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            _check(
                self.kernel.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size))
            )
            self.image = image.value
            if not self.image:
                raise ValueError("driver_image_missing")
        except BaseException:
            self.close()
            raise

    def read(self):
        if self.kernel.GetProcessId(self.handle) != self.pid:
            raise ValueError("driver_native_pid_changed")
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status not in (0, 0x102):
            raise ctypes.WinError(ctypes.get_last_error())
        creation, exit_time, kernel_time, user_time = (w.FILETIME() for _ in range(4))
        _check(
            self.kernel.GetProcessTimes(
                self.handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
        )
        birth = creation.dwLowDateTime | creation.dwHighDateTime << 32
        if birth <= 0 or self.birth is not None and birth != self.birth:
            raise ValueError("driver_native_birth_changed")
        self.birth = birth
        code = w.DWORD()
        if status == 0:
            _check(self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)))
        return {
            "pid": self.pid,
            "creation_filetime": birth,
            "signaled": status == 0,
            "exit_code": code.value if status == 0 else None,
            "exit_filetime": (exit_time.dwLowDateTime | exit_time.dwHighDateTime << 32)
            if status == 0
            else None,
        }

    def close(self):
        if self.handle is not None:
            _check(self.kernel.CloseHandle(self.handle))
            self.handle = None


class DriverWitness:
    """Passive Windows-only test telemetry; never a process controller.

    A retained process handle avoids PID reuse. Sampling cannot identify a
    terminating actor, and no exit sample is a quiescence/no-effect proof.
    Driver stderr retains Playwright's original inherited console destination.
    """

    def __init__(self, playwright, *, destination, label):
        if sys.platform != "win32":
            raise RuntimeError("driver_witness_windows_only")
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._process, self._thread, self._closed = None, None, False
        self.path = Path(destination) / f"driver-{os.getpid()}-{uuid.uuid4().hex}.json"
        self.record = {
            "schema": "autostopcrm.windows-driver-witness.v1",
            "scope": "owned_test_driver_only",
            "owner_pid": os.getpid(),
            "label": label,
            "valid_witness": True,
            "sample_interval_ns": 250_000_000,
            "stderr": "unchanged inherited test console",
            "current_test": None,
            "last_alive_ns": None,
            "native_exit": None,
            "intentional_cleanup_begin_ns": None,
            "errors": [],
            "started_ns": time.monotonic_ns(),
            "started_at": datetime.now(UTC).isoformat(),
            "sample_count": 0,
            "max_sample_gap_ns": 0,
        }
        try:
            self._process = _DriverProcess(playwright)
            self.record.update(
                owner_creation_filetime=self._process.owner_birth,
                driver_pid=self._process.pid,
                driver_creation_filetime=self._process.birth,
                driver_image=self._process.image,
            )
            self._sample()
            self._publish()
            self._thread = threading.Thread(
                target=self._watch, daemon=True, name="owned-playwright-driver-witness"
            )
            self._thread.start()
        except Exception as error:
            self._error(error)
            try:
                self._publish()
            finally:
                if self._process is not None:
                    self._process.close()
            raise RuntimeError("driver_witness_invalid") from error

    def _error(self, error):
        self.record["valid_witness"] = False
        if len(self.record["errors"]) < 8:
            self.record["errors"].append(
                {"type": type(error).__name__, "winerror": getattr(error, "winerror", None)}
            )

    def _sample(self):
        value, now = self._process.read(), time.monotonic_ns()
        previous = self.record.get("last_sample_ns", now)
        self.record["max_sample_gap_ns"] = max(self.record["max_sample_gap_ns"], now - previous)
        self.record["sample_count"] += 1
        self.record["last_sample_ns"] = now
        if value["signaled"] and self.record["native_exit"] is None:
            self.record["native_exit"] = {
                **value,
                "observed_ns": now,
                "observed_utc": datetime.now(UTC).isoformat(),
            }
        elif not value["signaled"]:
            self.record["last_alive_ns"] = now

    def _publish(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.record, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def _watch(self):
        last_write = time.monotonic_ns()
        while not self._stop.wait(0.25):
            with self._lock:
                try:
                    self._sample()
                    now = time.monotonic_ns()
                    if self.record["native_exit"] is not None or now - last_write >= 1_000_000_000:
                        self._publish()
                        last_write = now
                    if self.record["native_exit"] is not None:
                        return
                except Exception as error:
                    self._error(error)
                    self._publish()
                    return

    def case(self, identity):
        with self._lock:
            self.record["current_test"] = identity

    def close(self):
        if self._closed:
            return
        # Called before ANY normal class cleanup, including setup-failure cleanup.
        with self._lock:
            self.record["intentional_cleanup_begin_ns"] = time.monotonic_ns()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(0.5)  # telemetry join inside the original group budget
        with self._lock:
            try:
                if self._thread is not None and self._thread.is_alive():
                    raise RuntimeError("driver_witness_thread_not_stopped")
                self._sample()
                self._process.close()
                self._closed = True
            except Exception as error:
                self._error(error)
            self._publish()
        if not self.record["valid_witness"]:
            raise RuntimeError("driver_witness_invalid")


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
