from __future__ import annotations

from collections import OrderedDict
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, RLock, Thread
import uuid

from PySide6.QtGui import QImage

from .file_preview import PreviewResult, PreviewResultKind, PreviewSource
from .thumbnail_render import ThumbnailRenderSpec
from .video_thumbnail_policy import VideoThumbnailPolicy


SIIGBF_RESIZETOFIT = 0x00000000
SIIGBF_BIGGER_SIZE_OK = 0x00000001
SIIGBF_MEMORYONLY = 0x00000002
SIIGBF_ICONONLY = 0x00000004
SIIGBF_THUMBNAILONLY = 0x00000008
SIIGBF_INCACHEONLY = 0x00000010
ASSOCSTR_EXECUTABLE = 2
ASSOCSTR_PROGID = 20


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> _GUID:
        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


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


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


IID_ISHELL_ITEM_IMAGE_FACTORY = _GUID.from_string(
    "BCC18B79-BA16-442F-80C4-8A59C30C463B"
)


class WindowsShellImageAdapter:
    """Thin native adapter. Every returned HBITMAP is copied and released."""

    def get_image(
        self,
        path: str | Path,
        width: int,
        height: int,
        flags: int,
    ) -> QImage | None:
        if sys.platform != "win32":
            return None
        return _get_shell_item_image(Path(path), width, height, flags)


def _assoc_query_string(extension: str, query: int) -> str:
    """Read one association field using the Windows Shell association API."""

    if sys.platform != "win32" or not extension:
        return ""
    try:
        function = ctypes.windll.shlwapi.AssocQueryStringW
        function.restype = ctypes.c_long
        function.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        length = wintypes.DWORD(0)
        result = int(
            function(
                0,
                int(query),
                extension,
                None,
                None,
                ctypes.byref(length),
            )
        )
        if result < 0 or length.value <= 1:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        result = int(
            function(
                0,
                int(query),
                extension,
                None,
                buffer,
                ctypes.byref(length),
            )
        )
        return "" if result < 0 else buffer.value.strip().casefold()
    except (AttributeError, OSError, ValueError):
        return ""


def _windows_association_fingerprint(
    path: str | Path,
) -> tuple[str, str, str]:
    extension = Path(path).suffix.casefold()
    if not extension:
        return "", "", ""
    return (
        extension,
        _assoc_query_string(extension, ASSOCSTR_PROGID),
        _assoc_query_string(extension, ASSOCSTR_EXECUTABLE),
    )


@dataclass
class _ShellPreviewTask:
    path: Path
    width: int
    height: int
    flags: int
    done: Event = field(default_factory=Event)
    cancelled: Event = field(default_factory=Event)
    image: QImage | None = None


class ShellPreviewLifecycle(StrEnum):
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    STUCK = "stuck"


class WindowsShellPreviewService:
    """Serialized Shell thumbnail access on one dedicated STA thread."""

    def __init__(
        self,
        adapter: WindowsShellImageAdapter | None = None,
        *,
        cache_capacity: int = 192,
    ) -> None:
        self._adapter = adapter or WindowsShellImageAdapter()
        self._lock = RLock()
        self._cache: OrderedDict[tuple[object, ...], QImage] = OrderedDict()
        self._cache_capacity = max(8, int(cache_capacity))
        self._closed = False
        self._state = ShellPreviewLifecycle.RUNNING
        self._tasks: dict[int, _ShellPreviewTask] = {}
        self.last_shutdown_error: str | None = None
        self.request_count = 0
        self._queue: Queue[_ShellPreviewTask | None] = Queue()
        self._thread = Thread(
            target=self._run_sta,
            name="NivisViewer-ShellPreview-STA",
            daemon=True,
        )
        self._thread.start()

    @property
    def state(self) -> ShellPreviewLifecycle:
        with self._lock:
            return self._state

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    @property
    def worker_alive(self) -> bool:
        return self._thread.is_alive()

    def request_thumbnail(
        self,
        path: str | Path,
        spec: ThumbnailRenderSpec,
        *,
        cache_only: bool,
        cancel_token: Event | None = None,
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
    ) -> PreviewResult:
        if self._closed or (cancel_token is not None and cancel_token.is_set()):
            return PreviewResult(PreviewResultKind.CANCELLED)
        target = Path(path)
        flags = SIIGBF_THUMBNAILONLY | SIIGBF_BIGGER_SIZE_OK
        if cache_only:
            flags |= SIIGBF_INCACHEONLY
        association = _windows_association_fingerprint(target)
        key = (
            str(target).casefold(),
            source_size,
            source_mtime_ns,
            int(spec.frame_width),
            int(spec.frame_height),
            association,
        )
        with self._lock:
            if self._closed:
                return PreviewResult(PreviewResultKind.CANCELLED)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return PreviewResult.ready_image(
                    cached,
                    source=PreviewSource.WINDOWS_SHELL,
                    persist_to_disk=False,
                )
            self.request_count += 1
        task = _ShellPreviewTask(
            target,
            max(1, int(spec.frame_width)),
            max(1, int(spec.frame_height)),
            flags,
        )
        with self._lock:
            if self._state is not ShellPreviewLifecycle.RUNNING:
                return PreviewResult(PreviewResultKind.CANCELLED)
            self._tasks[id(task)] = task
        self._queue.put(task)
        while not task.done.wait(0.05):
            if self._closed or (
                cancel_token is not None and cancel_token.is_set()
            ):
                task.cancelled.set()
                with self._lock:
                    self._tasks.pop(id(task), None)
                return PreviewResult(PreviewResultKind.CANCELLED)
        if self._closed or task.cancelled.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            with self._lock:
                self._tasks.pop(id(task), None)
            return PreviewResult(PreviewResultKind.CANCELLED)
        image = task.image
        with self._lock:
            self._tasks.pop(id(task), None)
            if image is None or image.isNull():
                return PreviewResult(PreviewResultKind.UNAVAILABLE)
            try:
                normalized = VideoThumbnailPolicy.render(
                    VideoThumbnailPolicy.qimage_to_pil(image),
                    spec,
                )
            except Exception:
                return PreviewResult(PreviewResultKind.UNAVAILABLE)
            self._cache[key] = normalized.copy()
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_capacity:
                self._cache.popitem(last=False)
            return PreviewResult.ready_image(
                normalized,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

    def clear_memory_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def shutdown(self, *, join_timeout: float = 0.25) -> bool:
        # Native GetImage calls are not force-stopped. Generation checks in the
        # owner discard any result that returns after shutdown.
        with self._lock:
            if self._state is ShellPreviewLifecycle.STOPPED:
                return True
            if self._state is ShellPreviewLifecycle.RUNNING:
                self._state = ShellPreviewLifecycle.SHUTTING_DOWN
            self._closed = True
            self._cache.clear()
        while True:
            try:
                task = self._queue.get_nowait()
            except Empty:
                break
            if task is not None:
                task.cancelled.set()
                task.done.set()
                with self._lock:
                    self._tasks.pop(id(task), None)
        self._queue.put(None)
        self._thread.join(timeout=max(0.0, float(join_timeout)))
        with self._lock:
            if self._thread.is_alive():
                self._state = ShellPreviewLifecycle.STUCK
                self.last_shutdown_error = (
                    "Windows Shell preview STA worker did not stop within "
                    f"{max(0.0, float(join_timeout)):.3f}s"
                )
                return False
            self._state = ShellPreviewLifecycle.STOPPED
            self.last_shutdown_error = None
            self._tasks.clear()
            return True

    def _run_sta(self) -> None:
        initialized = _co_initialize_sta()
        try:
            while True:
                task = self._queue.get()
                if task is None:
                    return
                if task.cancelled.is_set() or self._closed:
                    task.cancelled.set()
                    task.done.set()
                    continue
                try:
                    task.image = self._adapter.get_image(
                        task.path,
                        task.width,
                        task.height,
                        task.flags,
                    )
                except Exception:
                    task.image = None
                finally:
                    task.done.set()
                    with self._lock:
                        self._tasks.pop(id(task), None)
        finally:
            _co_uninitialize(initialized)
            with self._lock:
                if self._state is not ShellPreviewLifecycle.STUCK:
                    self._state = ShellPreviewLifecycle.STOPPED


def _co_initialize_sta() -> bool:
    if sys.platform != "win32":
        return False
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.restype = ctypes.c_long
        result = int(ole32.CoInitializeEx(None, 0x2))
        return result in {0, 1}
    except (AttributeError, OSError):
        return False


def _co_uninitialize(initialized: bool) -> None:
    if initialized and sys.platform == "win32":
        try:
            ctypes.windll.ole32.CoUninitialize()
        except (AttributeError, OSError):
            pass


def _get_shell_item_image(
    path: Path,
    width: int,
    height: int,
    flags: int,
) -> QImage | None:
    shell32 = ctypes.windll.shell32
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    interface = ctypes.c_void_p()
    result = int(
        shell32.SHCreateItemFromParsingName(
            str(path),
            None,
            ctypes.byref(IID_ISHELL_ITEM_IMAGE_FACTORY),
            ctypes.byref(interface),
        )
    )
    if result < 0 or not interface.value:
        return None
    function_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    try:
        vtable = ctypes.cast(
            interface,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        get_image = function_type(
            ctypes.c_long,
            ctypes.c_void_p,
            _SIZE,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
        )(vtable[3])
        release = function_type(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        bitmap = ctypes.c_void_p()
        result = int(
            get_image(
                interface,
                _SIZE(max(1, int(width)), max(1, int(height))),
                int(flags),
                ctypes.byref(bitmap),
            )
        )
        if result < 0 or not bitmap.value:
            return None
        try:
            return _copy_hbitmap(bitmap)
        finally:
            ctypes.windll.gdi32.DeleteObject(bitmap)
    finally:
        try:
            release(interface)
        except (UnboundLocalError, OSError):
            pass


def _copy_hbitmap(bitmap: ctypes.c_void_p) -> QImage | None:
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    gdi32.GetObjectW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    descriptor = _BITMAP()
    if not gdi32.GetObjectW(
        bitmap,
        ctypes.sizeof(descriptor),
        ctypes.byref(descriptor),
    ):
        return None
    width = abs(int(descriptor.bmWidth))
    height = abs(int(descriptor.bmHeight))
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        return None
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buffer = (ctypes.c_ubyte * (width * height * 4))()
    screen_dc = user32.GetDC(None)
    try:
        lines = gdi32.GetDIBits(
            screen_dc,
            bitmap,
            0,
            height,
            ctypes.byref(buffer),
            ctypes.byref(info),
            0,
        )
    finally:
        user32.ReleaseDC(None, screen_dc)
    if lines != height:
        return None
    raw = bytearray(buffer)
    if raw and not any(raw[index] for index in range(3, len(raw), 4)):
        for index in range(3, len(raw), 4):
            raw[index] = 255
    return QImage(
        bytes(raw),
        width,
        height,
        width * 4,
        QImage.Format.Format_ARGB32,
    ).copy()
