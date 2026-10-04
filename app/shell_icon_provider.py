from __future__ import annotations

from collections import OrderedDict
import ctypes
from ctypes import wintypes
from math import ceil
from pathlib import Path
import sys
import uuid

from PySide6.QtCore import QFileInfo, QSize
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from .browser_model import BrowserItem, BrowserItemKind


_ICON_BUCKETS = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def physical_icon_bucket(logical_size: int, device_pixel_ratio: float) -> int:
    requested = max(1, ceil(int(logical_size) * max(1.0, float(device_pixel_ratio))))
    return next((size for size in _ICON_BUCKETS if size >= requested), _ICON_BUCKETS[-1])


class ShellAssociatedIconProvider:
    """High-DPI Explorer association icons cached as physical QImages."""

    def __init__(self, *, cache_capacity: int = 192) -> None:
        self._image_cache: OrderedDict[tuple[str, int], QImage] = OrderedDict()
        self._icon_cache: dict[tuple[str, int], QIcon] = {}
        self._cache_capacity = max(16, int(cache_capacity))
        self._fallback = QFileIconProvider()

    @property
    def cache_size(self) -> int:
        return len(set(self._image_cache) | set(self._icon_cache))

    def image_for(
        self,
        item: BrowserItem,
        *,
        logical_size: int = 32,
        device_pixel_ratio: float = 1.0,
    ) -> QImage:
        if item.kind is BrowserItemKind.FOLDER:
            return self.image_for_extension(
                "",
                folder=True,
                logical_size=logical_size,
                device_pixel_ratio=device_pixel_ratio,
            )
        suffix = item.path.suffix.casefold()
        image = self.image_for_extension(
            suffix,
            logical_size=logical_size,
            device_pixel_ratio=device_pixel_ratio,
        )
        if not image.isNull() or suffix not in {".cbz", ".cbr", ".cb7"}:
            return image
        return self.image_for_extension(
            {".cbz": ".zip", ".cbr": ".rar", ".cb7": ".7z"}[suffix],
            logical_size=logical_size,
            device_pixel_ratio=device_pixel_ratio,
        )

    def image_for_extension(
        self,
        extension: str,
        *,
        folder: bool = False,
        logical_size: int = 32,
        device_pixel_ratio: float = 1.0,
    ) -> QImage:
        physical_size = physical_icon_bucket(logical_size, device_pixel_ratio)
        association = "folder" if folder else extension.casefold()
        key = (association, physical_size)
        cached = self._image_cache.get(key)
        if cached is not None:
            self._image_cache.move_to_end(key)
            return cached.copy()
        image = self._windows_image(
            extension,
            folder=folder,
            physical_size=physical_size,
        )
        if image.isNull():
            fallback_icon = self._fallback.icon(
                QFileIconProvider.IconType.Folder
                if folder
                else QFileIconProvider.IconType.File
            )
            pixmap = fallback_icon.pixmap(QSize(physical_size, physical_size))
            image = pixmap.toImage() if not pixmap.isNull() else QImage()
        if not image.isNull():
            self._image_cache[key] = image.copy()
            self._image_cache.move_to_end(key)
            while len(self._image_cache) > self._cache_capacity:
                old_key, _old_image = self._image_cache.popitem(last=False)
                self._icon_cache.pop(old_key, None)
        return image

    def icon_for(
        self,
        item: BrowserItem,
        *,
        logical_size: int = 32,
        device_pixel_ratio: float = 1.0,
    ) -> QIcon:
        folder = item.kind is BrowserItemKind.FOLDER
        extension = "" if folder else item.path.suffix.casefold()
        return self.icon_for_extension(
            extension,
            folder=folder,
            logical_size=logical_size,
            device_pixel_ratio=device_pixel_ratio,
        )

    def icon_for_extension(
        self,
        extension: str,
        *,
        folder: bool = False,
        logical_size: int = 32,
        device_pixel_ratio: float = 1.0,
    ) -> QIcon:
        physical_size = physical_icon_bucket(logical_size, device_pixel_ratio)
        key = ("folder" if folder else extension.casefold(), physical_size)
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        icon = self._windows_icon(extension, folder=folder)
        if icon.isNull():
            image = self.image_for_extension(
                extension,
                folder=folder,
                logical_size=logical_size,
                device_pixel_ratio=device_pixel_ratio,
            )
            icon = (
                QIcon(QPixmap.fromImage(image))
                if not image.isNull()
                else QIcon()
            )
        self._icon_cache[key] = icon
        return icon

    @staticmethod
    def _windows_icon(extension: str, *, folder: bool) -> QIcon:
        if sys.platform != "win32":
            return QIcon()
        try:
            return _query_windows_shell_icon(extension, folder=folder)
        except (AttributeError, OSError, ValueError):
            return QIcon()

    @staticmethod
    def _windows_image(
        extension: str,
        *,
        folder: bool,
        physical_size: int,
    ) -> QImage:
        if sys.platform != "win32":
            return QImage()
        try:
            return _query_windows_shell_icon_image(
                extension,
                folder=folder,
                physical_size=physical_size,
            )
        except (AttributeError, OSError, ValueError):
            return QImage()


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> _GUID:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


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


IID_IIMAGELIST = _GUID.from_string("46EB5926-582E-4017-9FDF-E8998DAA0950")


def _query_windows_shell_icon(
    extension: str,
    *,
    folder: bool,
) -> QIcon:
    """Compatibility wrapper retained for existing integrations/tests."""
    image = _query_windows_shell_icon_image(
        extension,
        folder=folder,
        physical_size=32,
    )
    return QIcon(QPixmap.fromImage(image)) if not image.isNull() else QIcon()


def _query_windows_shell_icon_image(
    extension: str,
    *,
    folder: bool,
    physical_size: int,
) -> QImage:
    shell32 = ctypes.windll.shell32
    ctypes.windll.user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    shell32.SHGetFileInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_SHFILEINFOW),
        wintypes.UINT,
        wintypes.UINT,
    ]
    attributes = 0x00000010 if folder else 0x00000080
    name = "folder" if folder else f"file{extension or '.bin'}"
    info = _SHFILEINFOW()
    # SHGFI_SYSICONINDEX | SHGFI_USEFILEATTRIBUTES avoids opening the target.
    result = shell32.SHGetFileInfoW(
        name,
        attributes,
        ctypes.byref(info),
        ctypes.sizeof(info),
        0x000004000 | 0x000000010,
    )
    if not result:
        return QImage()
    image_list_kind = (
        0
        if physical_size <= 16
        else 1
        if physical_size <= 32
        else 2
        if physical_size <= 48
        else 4
    )
    interface = ctypes.c_void_p()
    shell32.SHGetImageList.restype = ctypes.c_long
    shell32.SHGetImageList.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    hresult = int(
        shell32.SHGetImageList(
            image_list_kind,
            ctypes.byref(IID_IIMAGELIST),
            ctypes.byref(interface),
        )
    )
    if hresult < 0 or not interface.value:
        return QImage()
    function_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    try:
        vtable = ctypes.cast(
            interface,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        get_icon = function_type(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
        )(vtable[10])
        release = function_type(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        icon_handle = ctypes.c_void_p()
        hresult = int(
            get_icon(
                interface,
                int(info.iIcon),
                0x00000001,
                ctypes.byref(icon_handle),
            )
        )
        if hresult < 0 or not icon_handle.value:
            return QImage()
        try:
            return _copy_hicon(icon_handle, max(1, int(physical_size)))
        finally:
            ctypes.windll.user32.DestroyIcon(icon_handle)
    finally:
        try:
            release(interface)
        except (UnboundLocalError, OSError):
            pass


def _copy_hicon(icon_handle: ctypes.c_void_p, size: int) -> QImage:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
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
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = size
    info.bmiHeader.biHeight = -size
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bitmap = gdi32.CreateDIBSection(
        memory_dc,
        ctypes.byref(info),
        0,
        ctypes.byref(bits),
        None,
        0,
    )
    if not bitmap or not bits:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)
        return QImage()
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        user32.DrawIconEx(
            memory_dc,
            0,
            0,
            icon_handle,
            size,
            size,
            0,
            None,
            0x0003,
        )
        raw = bytearray(ctypes.string_at(bits, size * size * 4))
        if raw and not any(raw[index] for index in range(3, len(raw), 4)):
            for index in range(3, len(raw), 4):
                raw[index] = 255
        return QImage(
            bytes(raw),
            size,
            size,
            size * 4,
            QImage.Format.Format_ARGB32,
        ).copy()
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
