from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

from PySide6.QtCore import QFileInfo, QSize
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from .browser_model import BrowserItem, BrowserItemKind


class ShellAssociatedIconProvider:
    """Returns Explorer association icons without opening target files."""

    def __init__(self) -> None:
        self._cache: dict[str, QIcon] = {}
        self._fallback = QFileIconProvider()

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def icon_for(self, item: BrowserItem) -> QIcon:
        if item.kind is BrowserItemKind.FOLDER:
            return self.icon_for_extension("", folder=True)
        suffix = item.path.suffix.casefold()
        icon = self.icon_for_extension(suffix)
        if not icon.isNull() or suffix not in {".cbz", ".cbr", ".cb7"}:
            return icon
        fallback = {".cbz": ".zip", ".cbr": ".rar", ".cb7": ".7z"}[suffix]
        return self.icon_for_extension(fallback)

    def icon_for_extension(self, extension: str, *, folder: bool = False) -> QIcon:
        key = "folder" if folder else extension.casefold()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        icon = self._windows_icon(extension, folder=folder)
        if icon.isNull():
            icon = self._fallback.icon(
                QFileIconProvider.IconType.Folder
                if folder
                else QFileIconProvider.IconType.File
            )
        self._cache[key] = icon
        return icon

    @staticmethod
    def _windows_icon(extension: str, *, folder: bool) -> QIcon:
        if sys.platform != "win32":
            return QIcon()
        try:
            return _query_windows_shell_icon(extension, folder=folder)
        except (AttributeError, OSError, ValueError):
            return QIcon()


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def _query_windows_shell_icon(extension: str, *, folder: bool) -> QIcon:
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    shell32.SHGetFileInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_SHFILEINFOW),
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    user32.DrawIconEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    flags = 0x000000100 | 0x000000001 | 0x000000010
    attributes = 0x00000010 if folder else 0x00000080
    name = "folder" if folder else f"file{extension or '.bin'}"
    info = _SHFILEINFOW()
    result = shell32.SHGetFileInfoW(
        name,
        attributes,
        ctypes.byref(info),
        ctypes.sizeof(info),
        flags,
    )
    if not result or not info.hIcon:
        return QIcon()
    size = 32
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    bitmap_info = _BITMAPINFO()
    bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bitmap_info.bmiHeader.biWidth = size
    bitmap_info.bmiHeader.biHeight = -size
    bitmap_info.bmiHeader.biPlanes = 1
    bitmap_info.bmiHeader.biBitCount = 32
    bitmap_info.bmiHeader.biCompression = 0
    bitmap = gdi32.CreateDIBSection(
        memory_dc,
        ctypes.byref(bitmap_info),
        0,
        ctypes.byref(bits),
        None,
        0,
    )
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        user32.DrawIconEx(memory_dc, 0, 0, info.hIcon, size, size, 0, None, 0x0003)
        raw = ctypes.string_at(bits, size * size * 4)
        image = QImage(
            raw,
            size,
            size,
            size * 4,
            QImage.Format.Format_ARGB32,
        ).copy()
        return QIcon(QPixmap.fromImage(image))
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
        user32.DestroyIcon(info.hIcon)
