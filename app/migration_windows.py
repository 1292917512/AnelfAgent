"""Read Windows file change metadata and probe rename access without changing the filesystem."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    from functools import cache

    class _BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]

    @cache
    def _kernel() -> ctypes.WinDLL:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        return kernel

    def _open(path: Path, access: int) -> int:
        value = str(path.absolute())
        extended = value if value.startswith("\\\\?\\") else "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
        handle = _kernel().CreateFileW(extended, access, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def change_time(path: Path) -> int:
        """Return ChangeTime, which differs from the creation time in Python's Windows stat."""
        handle = _open(path, 0)
        try:
            info = _BasicInfo()
            if not _kernel().GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                error = ctypes.WinError(ctypes.get_last_error())
                if error.winerror in {1, 50, 87}:
                    return 0
                raise error
            return int(info.change)
        finally:
            _kernel().CloseHandle(handle)

    def check_move_access(source: Path) -> None:
        """Request rename access and immediately release it; never rename during a probe."""
        try:
            handle = _open(source, 0x10000)
        except OSError as error:
            if error.winerror in {5, 32, 33}:
                raise RuntimeError(f"原项目目录被占用或无移动权限：{source}。请关闭以该目录工作的编辑器、终端及开发进程后重试；已完成的迁移进度会保留。") from error
            raise
        _kernel().CloseHandle(handle)
else:
    def change_time(path: Path) -> int:
        """POSIX stat already provides change time; no extra native metadata is needed."""
        return 0

    def check_move_access(source: Path) -> None:
        """POSIX permits renaming open directories; errors are handled at the real rename."""
        return None
