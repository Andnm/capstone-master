"""Job Object Windows (ctypes) de CONTAIN mot workload: moi process con/chau sinh ra tu child deu thuoc job, `kill_tree()` giet het, dong handle cung giet het (KILL_ON_JOB_CLOSE), KHONG cho breakaway.

Tao child o trang thai SUSPENDED -> gan vao job -> moi RESUME: child khong the sinh process con truoc khi duoc gan (khong co khe race). Khong goi DB, khong doc secret; env cua child do nguoi goi truyen RO RANG
(khong ke thua im lang). Pham vi: PURE/OS — chi sinh process local do nguoi goi chi dinh; khong tu chay bat ky workload nao.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes as wt
from pathlib import Path
from typing import Mapping, Sequence

IS_WINDOWS = sys.platform == "win32"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0x0, 0x102, 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
MAX_PIDS = 4096


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64), ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD), ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def _kernel32():
    if not IS_WINDOWS:
        raise OSError("windows_job_v1 chi chay tren Windows")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wt.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    k.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
    k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    k.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    k.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(_StartupInfo),
                                 ctypes.POINTER(_ProcessInformation)]
    k.ResumeThread.argtypes = [wt.HANDLE]
    k.ResumeThread.restype = wt.DWORD
    k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k.WaitForSingleObject.restype = wt.DWORD
    k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    return k


def _env_block(env: Mapping[str, str]) -> ctypes.Array:
    """Khoi moi truong UTF-16 (key=value\\0 ... \\0\\0), sap xep khong phan biet hoa thuong nhu Windows yeu cau."""
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    text = "".join(f"{k}={v}\0" for k, v in items) + "\0"
    return ctypes.create_unicode_buffer(text, len(text))


class JobChild:
    """Child da duoc gan vao job (dang chay)."""

    def __init__(self, job: "Job", pid: int, process_handle, thread_handle, files):
        self.job, self.pid, self._hp, self._ht, self._files = job, pid, process_handle, thread_handle, files
        self._exit_code: int | None = None

    def wait(self, timeout_s: float | None) -> int | None:
        """Doi ROOT process ket thuc; tra exit code, hoac None neu het thoi gian (khong giet gi)."""
        k = self.job._k
        ms = INFINITE if timeout_s is None else max(0, int(timeout_s * 1000))
        result = k.WaitForSingleObject(self._hp, ms)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise OSError(f"WaitForSingleObject that bai: {result}")
        code = wt.DWORD()
        k.GetExitCodeProcess(self._hp, ctypes.byref(code))
        self._exit_code = int(code.value)
        return self._exit_code

    def close_handles(self) -> None:
        k = self.job._k
        for handle in (self._hp, self._ht):
            if handle:
                k.CloseHandle(handle)
        self._hp = self._ht = None
        for handle in self._files:
            try:
                handle.close()
            except OSError:
                pass
        self._files = []


class Job:
    def __init__(self) -> None:
        self._k = _kernel32()
        self._h = self._k.CreateJobObjectW(None, None)
        if not self._h:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE                       # KHONG co BREAKAWAY_OK / SILENT_BREAKAWAY_OK: khong the thoat khoi job
        if not self._k.SetInformationJobObject(self._h, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.children: list[JobChild] = []

    # ---- tao child TRONG job (suspended -> assign -> resume)
    def spawn(self, argv: Sequence[str], *, cwd: str | os.PathLike[str], env: Mapping[str, str], stdout_path: str | os.PathLike[str], stderr_path: str | os.PathLike[str],
              before_resume=None) -> JobChild:
        """`before_resume(pid)` (CHI DE TEST) duoc goi SAU khi gan vao job va TRUOC ResumeThread: child chua chay lenh nao."""
        import msvcrt
        k = self._k
        opened = []
        try:
            for path, mode in ((stdout_path, "xb"), (stderr_path, "xb"), (os.devnull, "rb")):                 # mo lan luot; loi giua chung dong cac tep da mo (khong ro ri handle)
                opened.append(open(path, mode))
        except BaseException:
            for f in opened:
                f.close()
            raise
        out_f, err_f, in_f = opened
        handles = []
        for f in (out_f, err_f, in_f):
            h = msvcrt.get_osfhandle(f.fileno())
            os.set_handle_inheritable(h, True)
            handles.append(h)
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles[2], handles[0], handles[1]
        info = _ProcessInformation()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)), 32768)
        ok = k.CreateProcessW(None, cmdline, None, None, True, CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, ctypes.cast(_env_block(env), ctypes.c_void_p), str(cwd),
                              ctypes.byref(startup), ctypes.byref(info))
        if not ok:
            error = ctypes.get_last_error()
            for f in (out_f, err_f, in_f):
                f.close()
            raise ctypes.WinError(error)
        try:
            if not k.AssignProcessToJobObject(self._h, info.hProcess):
                raise ctypes.WinError(ctypes.get_last_error())
            if before_resume is not None:
                before_resume(int(info.dwProcessId))
            if k.ResumeThread(info.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            k.TerminateProcess(info.hProcess, 1)                                                      # chua gan/chua resume duoc => khong de lai process mo coi
            k.CloseHandle(info.hProcess)
            k.CloseHandle(info.hThread)
            for f in (out_f, err_f, in_f):
                f.close()
            raise
        child = JobChild(self, int(info.dwProcessId), info.hProcess, info.hThread, [out_f, err_f, in_f])
        self.children.append(child)
        return child

    def assign_process_handle(self, process_handle: int) -> None:
        """Gan mot process DA TON TAI (vd `subprocess.Popen` voi pipe) vao job. Khe race: process co the sinh con TRUOC khi duoc gan — chi dung cho worker ma ta tu viet (khong spawn con).
        Process da thuoc job khac => OS cho phep long nhau (Win8+) hoac loi => ngoai le."""
        if not self._k.AssignProcessToJobObject(self._h, wt.HANDLE(int(process_handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    # ---- quan sat / giet
    def active_process_count(self) -> int:
        acct = _BasicAccounting()
        if not self._k.QueryInformationJobObject(self._h, JobObjectBasicAccountingInformation, ctypes.byref(acct), ctypes.sizeof(acct), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(acct.ActiveProcesses)

    def pids(self, max_pids: int = MAX_PIDS) -> list[int]:
        size = 8 + 8 * max_pids
        buffer = ctypes.create_string_buffer(size)
        if not self._k.QueryInformationJobObject(self._h, JobObjectBasicProcessIdList, buffer, size, None):
            raise ctypes.WinError(ctypes.get_last_error())
        assigned, listed = ctypes.c_uint32.from_buffer(buffer, 0).value, ctypes.c_uint32.from_buffer(buffer, 4).value
        if assigned > listed:
            raise OSError(f"danh sach pid bi cat: {assigned} > {listed}")                              # khong tin danh sach thieu
        return sorted(int(ctypes.c_size_t.from_buffer(buffer, 8 + 8 * i).value) for i in range(listed))

    def kill_tree(self, exit_code: int = 1) -> None:
        if self._h and not self._k.TerminateJobObject(self._h, exit_code):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_empty(self, timeout_s: float, poll_s: float = 0.05) -> bool:
        """Doi toi khi job KHONG con process nao (True) hoac het gio (False)."""
        import time
        deadline = time.monotonic() + timeout_s
        while True:
            if self.active_process_count() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)

    def close(self) -> None:
        """Dong handle job => KILL_ON_JOB_CLOSE giet moi process con lai."""
        for child in self.children:
            child.close_handles()
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = None

    def __enter__(self) -> "Job":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
