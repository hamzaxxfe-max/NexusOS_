#!/usr/bin/env python3
"""Minimal, stdlib-only re-implementation of the inotify_simple API.

python-inotify_simple is an AUR-only package and cannot be installed from the
official Arch repositories during the ISO build. This module provides a
drop-in replacement exposing the subset of the API used by
security-bypass-daemon.py (INotify, add_watch, read, rm_watch, flags) using
libc's inotify_* syscalls via ctypes.

API compatibility:
    fd = INotify()
    wd = fd.add_watch(path, flags)
    events = fd.read(timeout_ms=100)   # list of Event(wd, mask, cookie, name)
    fd.rm_watch(wd)
    fd.close()
"""
import ctypes
import ctypes.util
import select
import struct
from typing import Dict, List, Optional, Tuple

_INOTIFY_EVENT_SIZE = struct.calcsize("iIII")
_IN_NONBLOCK = 0x00000800


class flags:
    ACCESS = 0x00000001
    MODIFY = 0x00000002
    ATTRIB = 0x00000004
    CLOSE_WRITE = 0x00000008
    CLOSE_NOWRITE = 0x00000010
    OPEN = 0x00000020
    MOVED_FROM = 0x00000040
    MOVED_TO = 0x00000080
    CREATE = 0x00000100
    DELETE = 0x00000200
    DELETE_SELF = 0x00000400
    MOVE_SELF = 0x00000800


class Event:
    __slots__ = ("wd", "mask", "cookie", "name")

    def __init__(self, wd: int, mask: int, cookie: int, name: str):
        self.wd = wd
        self.mask = mask
        self.cookie = cookie
        self.name = name

    def __repr__(self) -> str:
        return "Event(wd=%r, mask=%r, cookie=%r, name=%r)" % (
            self.wd, self.mask, self.cookie, self.name)


class INotify:
    def __init__(self, nonblocking: bool = False):
        self._libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                                 use_errno=True)
        self._libc.inotify_init1.restype = ctypes.c_int
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_add_watch.restype = ctypes.c_int
        self._libc.inotify_add_watch.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._libc.inotify_rm_watch.restype = ctypes.c_int
        self._libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self._libc.read.restype = ctypes.c_ssize_t
        self._libc.read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]

        mask = _IN_NONBLOCK if nonblocking else 0
        self._fd = self._libc.inotify_init1(mask)
        if self._fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, "inotify_init1 failed")

    def add_watch(self, path: str, mask: int) -> int:
        wd = self._libc.inotify_add_watch(
            self._fd, path.encode("utf-8"), ctypes.c_uint32(mask))
        if wd < 0:
            err = ctypes.get_errno()
            raise OSError(err, "inotify_add_watch failed for %s" % path)
        return wd

    def rm_watch(self, wd: int) -> None:
        if self._libc.inotify_rm_watch(self._fd, wd) < 0:
            err = ctypes.get_errno()
            raise OSError(err, "inotify_rm_watch failed")

    def read(self, timeout: int = 0) -> List[Event]:
        ready, _, _ = select.select([self._fd], [], [], timeout / 1000.0)
        if not ready:
            return []
        buf = ctypes.create_string_buffer(_INOTIFY_EVENT_SIZE * 16)
        nbytes = self._libc.read(self._fd, buf, len(buf))
        if nbytes <= 0:
            return []
        events: List[Event] = []
        offset = 0
        while offset + _INOTIFY_EVENT_SIZE <= nbytes:
            wd, mask, cookie, name_len = struct.unpack_from(
                "iIII", buf.raw, offset)
            offset += _INOTIFY_EVENT_SIZE
            name = ""
            if name_len > 0:
                name_bytes = buf.raw[offset:offset + name_len]
                name = name_bytes.rstrip(b"\x00").decode("utf-8", "replace")
                offset += name_len
            events.append(Event(wd, mask, cookie, name))
        return events

    def close(self) -> None:
        try:
            import os
            os.close(self._fd)
        except OSError:
            pass

    def __enter__(self) -> "INotify":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
