"""Create Windows directory junctions without requiring their targets to exist.

Uses the documented mount-point reparse buffer and FSCTL_SET_REPARSE_POINT.
Only a newly created link directory is written; the target is never opened.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import ntpath
import struct
import sys
from pathlib import Path


def _extended(path: str) -> str:
    value = ntpath.abspath(path)
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def create_junction(item: Path, target: str) -> None:
    """Create a fresh junction, including dangling targets and chained junctions."""
    if sys.platform != "win32":
        raise OSError(errno.ENOTSUP, "Directory junctions require Windows")
    else:
        from ctypes import wintypes

        # Offsets and lengths in REPARSE_DATA_BUFFER are bytes, excluding terminators.
        absolute = _extended(target)
        substitute = ("\\??\\" + absolute[4:]).encode("utf-16-le", errors="surrogatepass")
        display = ("\\\\" + absolute[8:] if absolute[:8].lower() == "\\\\?\\unc\\" else absolute[4:])
        printable = display.encode("utf-16-le", errors="surrogatepass")
        data_length = 8 + len(substitute) + len(printable) + 4
        if data_length + 8 > 16 * 1024:
            raise OSError(errno.ENAMETOOLONG, "Junction target exceeds the reparse buffer limit", target)
        payload = (struct.pack("<I6H", 0xA0000003, data_length, 0, 0, len(substitute), len(substitute) + 2, len(printable))
                   + substitute + b"\0\0" + printable + b"\0\0")

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        kernel.DeviceIoControl.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        directory = Path(_extended(str(item)))
        directory.mkdir()
        try:
            # Open the link itself, never follow a reparse point or replace an existing directory.
            handle = kernel.CreateFileW(str(directory), 0x40000000, 0, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                buffer = ctypes.create_string_buffer(payload)
                returned = wintypes.DWORD()
                if not kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(payload), None, 0, ctypes.byref(returned), None):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(handle)
        except BaseException:
            try:
                directory.rmdir()
            except OSError as exc:
                logging.getLogger(__name__).debug("Unable to remove failed junction %s: %s", directory, exc)
            raise
