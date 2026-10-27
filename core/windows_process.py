"""Windows Job Object 归属：拥有者关闭句柄时回收全部子进程。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class _Limits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _Limits), ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


if sys.platform == "win32":
    class WindowsProcessJob:
        """将待执行工作进程纳入不可脱离的进程组，归属建立失败时拒绝继续执行。"""

        def __init__(self, pid: int) -> None:
            self._api = ctypes.WinDLL("kernel32", use_last_error=True)
            api = self._api
            api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            api.CreateJobObjectW.restype = wintypes.HANDLE
            api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            api.SetInformationJobObject.restype = wintypes.BOOL
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            api.AssignProcessToJobObject.restype = wintypes.BOOL
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            self._handle = api.CreateJobObjectW(None, None)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            process = None
            try:
                limits = _ExtendedLimits()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    raise ctypes.WinError(ctypes.get_last_error())
                process = api.OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
                if not process or not api.AssignProcessToJobObject(self._handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException:
                self.close()
                raise
            finally:
                if process:
                    api.CloseHandle(process)

        def close(self) -> None:
            """关闭不继承的唯一 Job 句柄，终止仍归属其中的进程。"""
            if self._handle:
                self._api.CloseHandle(self._handle)
                self._handle = None
else:
    class WindowsProcessJob:
        """非 Windows 平台不提供 Job Object；调用方使用原生进程组。"""

        def __init__(self, pid: int) -> None:
            raise OSError("Windows Job Object 仅可在 Windows 上创建")

        def close(self) -> None:
            return None
