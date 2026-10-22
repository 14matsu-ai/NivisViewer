from __future__ import annotations

from .browser_workflow_policy import SelectionAppearance

from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from .browser_grid_metrics import build_browser_grid_metrics
from .browser_thumbnail_surface import (
    DisplayThumbnailSurface, prepare_display_thumbnail_surface,
    thumbnail_image_rects, thumbnail_content_rect, surface_geometry_key,
)
from .browser_icon_size import (
    BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
    BROWSER_ICON_SIZE_DEFAULT_PRESET,
    browser_icon_size_scale,
    normalize_browser_icon_size_custom_percent,
    normalize_browser_icon_size_preset,
    normalize_browser_icon_margin,
)
from .browser_sort import BrowserDisplayDensity
from .shell_icon_provider import ShellAssociatedIconProvider
from .thumbnail_render import (
    BROWSER_THUMBNAIL_DISPLAY_MODES,
    THUMBNAIL_SIZE_BUCKETS,
    frame_size_from_long_edge,
    quantize_thumbnail_size,
    snap_logical_rect_to_physical_pixels,
)


@dataclass(frozen=True)
class BrowserGridProfile:
    horizontal_margin: int
    vertical_margin: int
    spacing: int
    font_size: int
    title_lines: int


GRID_PROFILES = {
    # Keep a two-pixel side allowance per cell at every density. Wider legacy
    # title allowances inflated horizontal gaps even with a fixed thumbnail
    # size; title elision already handles the available cell width.
    BrowserDisplayDensity.EXTRA_COMPACT: BrowserGridProfile(4, 22, 0, 7, 1),
    BrowserDisplayDensity.COMPACT: BrowserGridProfile(4, 32, 2, 8, 1),
    # The medium preset changes only the thumbnail content scale.  Reuse the
    # compact text/spacing contract instead of inventing a parallel layout.
    BrowserDisplayDensity.MEDIUM: BrowserGridProfile(4, 32, 2, 8, 1),
    BrowserDisplayDensity.STANDARD: BrowserGridProfile(4, 58, 6, 9, 2),
    BrowserDisplayDensity.COMFORTABLE: BrowserGridProfile(4, 88, 12, 10, 2),
    BrowserDisplayDensity.LARGE: BrowserGridProfile(4, 118, 16, 11, 2),
}

GRID_PRESET_THUMBNAIL_SIZES = {
    BrowserDisplayDensity.EXTRA_COMPACT: 96,
    BrowserDisplayDensity.COMPACT: 128,
    BrowserDisplayDensity.MEDIUM: 149,
    BrowserDisplayDensity.STANDARD: 180,
    BrowserDisplayDensity.COMFORTABLE: 240,
    BrowserDisplayDensity.LARGE: 320,
}

BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR = "#ffffe0"
BROWSER_FILE_FALLBACK_DEFAULT_COLOR = "#c1c1c1"
BROWSER_PLACEHOLDER_ICON_MAX_RATIO = 0.50
BROWSER_DISPLAY_SURFACE_CACHE_MAX_ITEMS = 96
BROWSER_DISPLAY_SURFACE_CACHE_MAX_BYTES = 32 * 1024 * 1024
BROWSER_BADGE_INSET_CACHE_MAX_ITEMS = 192
BROWSER_RATING_OVERLAY_DEFAULT_OPACITY = 85
BROWSER_TAG_OVERLAY_DEFAULT_OPACITY = 100
BROWSER_TAG_TEXT_LUMINANCE_THRESHOLD_DEFAULT = 150


class BrowserDisplaySurfaceCache:
    """Bounded, paint-driven cache of final physical Browser thumbnail pixels."""

    def __init__(
        self,
        *,
        max_items: int = BROWSER_DISPLAY_SURFACE_CACHE_MAX_ITEMS,
        max_bytes: int = BROWSER_DISPLAY_SURFACE_CACHE_MAX_BYTES,
    ) -> None:
        self.max_items = max(1, int(max_items))
        self.max_bytes = max(1, int(max_bytes))
        self._items: OrderedDict[tuple[object, ...], QImage] = OrderedDict()
        self._byte_cost = 0
        self._device_pixel_ratio: float | None = None
        self._memory_owner = None
        self._by_source = {}
        self.hits = 0
        self.preparations = 0
        self.adopted_surfaces = 0
        self._revision = 0

    def has_plan(self, image, plan):
        pixel = int(image.cacheKey())
        return all((pixel, *geometry) in self._items for geometry, _ in plan.geometries(image))

    def adopt(self, image, plan, surfaces):
        """Alias owned pixels, or reserve a new worker-completed batch."""
        owner = self._memory_owner
        if owner is None or not owner.owns_memory_image(image):
            return 0
        pixel = int(image.cacheKey())
        additions = [((pixel, *key), surface) for key, surface in surfaces
                     if (pixel, *key) not in self._items]
        if not additions:
            self._device_pixel_ratio = plan.dpr
            return 0
        revision = self._revision
        aliases = all(int(surface.cacheKey()) in owner.artifact_memory.pixels for _, surface in additions)
        admitted = aliases or owner.reserve_display_surfaces(image, *(surface for _, surface in additions))
        if (self._revision != revision or self._memory_owner is not owner
                or not admitted or not owner.owns_memory_image(image)):
            return 0
        count = 0
        # No callback occurs between entry/index and ledger registration.
        for key, surface in additions:
            if key in self._items:
                continue
            self._items[key] = surface
            self._by_source.setdefault(pixel, set()).add(key)
            self._byte_cost += max(1, int(surface.sizeInBytes()))
            owner.retain_surface_reference(('surface', id(self), key), surface)
            count += 1
        self.adopted_surfaces += count
        self._device_pixel_ratio = plan.dpr
        if not aliases:
            owner.enforce_display_budget()
        return count

    def bind_memory(self, owner):
        self.clear()
        self._memory_owner = owner
        owner.memory_image_released.connect(self._release_source)
        owner.display_artifact_released.connect(self._release_display)

    def _release_display(self, pixel):
        self._release_source(None, pixel)

    def _remove(self, key):
        image = self._items.pop(key, None)
        if image is None:
            return
        self._byte_cost -= max(1, int(image.sizeInBytes()))
        keys = self._by_source.get(key[0])
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._by_source.pop(key[0], None)
        if self._memory_owner is not None:
            self._memory_owner.release_surface_reference(('surface', id(self), key))

    def _release_source(self, _path, pixel):
        for key in tuple(self._by_source.get(pixel, ())):
            self._remove(key)

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def byte_cost(self) -> int:
        return self._byte_cost

    def clear(self) -> None:
        self._revision += 1
        for key in tuple(self._items):
            self._remove(key)

    def prepare(
        self,
        image: QImage,
        source_rect: QRectF,
        target_rect: QRectF,
        device_pixel_ratio: float,
        *,
        displayed: bool = True,
    ) -> DisplayThumbnailSurface:
        dpr = max(0.5, float(device_pixel_ratio))
        if (
            self._device_pixel_ratio is not None
            and abs(self._device_pixel_ratio - dpr) >= 0.001
        ):
            self.clear()
        self._device_pixel_ratio = dpr
        if displayed and self._memory_owner is not None:
            self._memory_owner.mark_displayed_image(image)
        key = (int(image.cacheKey()), *surface_geometry_key(source_rect, target_rect, dpr))
        cached = self._items.get(key)
        if cached is None and displayed and self._memory_owner is not None:
            owner = self._memory_owner
            plan = getattr(owner, '_display_plan', None)
            if plan is not None and abs(plan.dpr-dpr) < .001:
                surfaces = owner.display_artifact(image, plan)
                if surfaces is not None and any((key[0], *geometry) == key for geometry, _ in surfaces):
                    self.adopt(image, plan, surfaces)
                    cached = self._items.get(key)
        if cached is not None:
            self.hits += 1
            self._items.move_to_end(key)
            return DisplayThumbnailSurface(cached, True, False)

        self.preparations += 1
        surface = prepare_display_thumbnail_surface(
            image,
            source_rect,
            target_rect,
            dpr,
        )
        cost = max(1, int(surface.image.sizeInBytes()))
        if self._memory_owner is not None:
            owner, revision = self._memory_owner, self._revision
            admitted = owner.reserve_display_surfaces(image, surface.image, require_source=False)
            if not admitted or self._memory_owner is not owner or self._revision != revision:
                return surface  # An unadmitted paint result stays transient.
            plan = getattr(owner, '_display_plan', None)
            if plan is not None and owner.owns_memory_image(image):
                geometries = plan.geometries(image)
                if len(geometries) == 1 and (key[0], *geometries[0][0]) == key:
                    # A first-paint fallback is also a complete final image.
                    # Give it the same lifetime as worker output; otherwise a
                    # GUI index clear would discard the only finished pixels.
                    ready = ((geometries[0][0], surface.image),)
                    if owner._store_display_artifact(image, plan, ready):
                        self.adopt(image, plan, owner.display_artifact(image, plan))
                    return surface
        if self._memory_owner is not None or cost <= self.max_bytes:
            self._items[key] = surface.image
            self._by_source.setdefault(key[0], set()).add(key)
            self._items.move_to_end(key)
            self._byte_cost += cost
            if self._memory_owner is not None:
                owner = self._memory_owner
                owner.retain_surface_reference(('surface', id(self), key), surface.image)
                owner.enforce_display_budget()
            while self._memory_owner is None and (
                len(self._items) > self.max_items
                or self._byte_cost > self.max_bytes
            ):
                self._remove(next(iter(self._items)))
        return surface


def browser_item_type_key(item: BrowserItem) -> str:
    if item.kind is BrowserItemKind.FOLDER:
        return "folder"
    if item.kind is BrowserItemKind.IMAGE:
        return "image"
    if item.kind is BrowserItemKind.PDF:
        return "pdf"
    suffix = item.path.suffix.casefold()
    if suffix in {".zip", ".cbz"}:
        return "zip"
    if suffix in {".rar", ".cbr"}:
        return "rar"
    if suffix in {".7z", ".cb7"}:
        return "7z"
    return "archive"


def type_badge_rect(thumbnail_rect: QRect, badge_size: int = 16) -> QRect:
    size = max(8, int(badge_size))
    return QRect(
        thumbnail_rect.left() + 4,
        thumbnail_rect.bottom() - size - 3,
        size,
        size,
    )


def thumbnail_rect_for_cell(
    cell_rect: QRect,
    thumbnail_size: int | QSize,
    cell_padding: int,
) -> QRect:
    if isinstance(thumbnail_size, QSize):
        size = QSize(
            max(1, thumbnail_size.width()),
            max(1, thumbnail_size.height()),
        )
    else:
        edge = max(1, int(thumbnail_size))
        size = QSize(edge, edge)
    return QRect(
        cell_rect.left() + (cell_rect.width() - size.width()) // 2,
        cell_rect.top() + max(0, int(cell_padding)),
        size.width(),
        size.height(),
    )


_COMPOUND_FILENAME_EXTENSIONS = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tar.lz",
    ".tar.lzma",
)


def split_filename_extension(text: str) -> tuple[str, str]:
    """Split one display filename without treating a leading-dot name as an extension."""
    name = str(text)
    folded = name.casefold()
    for suffix in _COMPOUND_FILENAME_EXTENSIONS:
        if len(name) > len(suffix) and folded.endswith(suffix):
            return name[:-len(suffix)], name[-len(suffix):]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot:]


def filename_for_display(
    text: str,
    *,
    show_extension: bool = True,
    is_folder: bool = False,
) -> str:
    """Format only the Browser title; the model name and file path stay intact."""
    name = str(text)
    if show_extension or is_folder:
        return name
    stem, extension = split_filename_extension(name)
    return stem if extension else name


def elide_filename_line(
    metrics: QFontMetrics,
    text: str,
    width: int,
    elide_mode: str = "right",
    *,
    preserve_extension: bool = False,
) -> str:
    """Elide a display line, reserving space for a normal suffix in right mode."""
    value = str(text)
    available = max(1, int(width))
    mode = (
        Qt.TextElideMode.ElideMiddle
        if elide_mode == "middle"
        else Qt.TextElideMode.ElideRight
    )
    if metrics.horizontalAdvance(value) <= available:
        return value
    if elide_mode == "right" and preserve_extension:
        stem, suffix = split_filename_extension(value)
        if suffix:
            suffix_width = metrics.horizontalAdvance(suffix)
            ellipsis_width = metrics.horizontalAdvance("…")
            stem_width = available - suffix_width
            if stem_width >= ellipsis_width:
                shortened_stem = metrics.elidedText(
                    stem,
                    Qt.TextElideMode.ElideRight,
                    stem_width,
                )
                if shortened_stem:
                    return shortened_stem + suffix
    shortened = metrics.elidedText(value, mode, available)
    return shortened or "…"


def elided_title_lines(
    metrics: QFontMetrics,
    text: str,
    width: int,
    maximum_lines: int,
    elide_mode: str = "right",
    *,
    preserve_extension: bool = False,
) -> tuple[str, ...]:
    remaining = str(text).strip()
    if not remaining:
        return ("",)
    line_count = max(1, int(maximum_lines))
    available = max(1, int(width))
    _, original_extension = split_filename_extension(remaining)
    lines: list[str] = []
    for line_number in range(line_count):
        if line_number == line_count - 1:
            lines.append(
                elide_filename_line(
                    metrics,
                    remaining,
                    available,
                    elide_mode,
                    preserve_extension=(
                        preserve_extension
                        and bool(original_extension)
                        and remaining.casefold().endswith(
                            original_extension.casefold()
                        )
                    ),
                )
            )
            break
        fit = 0
        for index in range(1, len(remaining) + 1):
            if metrics.horizontalAdvance(remaining[:index]) > available:
                break
            fit = index
        if fit >= len(remaining):
            lines.append(remaining)
            break
        if fit <= 0:
            lines.append(
                elide_filename_line(
                    metrics,
                    remaining,
                    available,
                    elide_mode,
                    preserve_extension=(
                        preserve_extension
                        and bool(original_extension)
                        and remaining.casefold().endswith(
                            original_extension.casefold()
                        )
                    ),
                )
            )
            break
        split = max(remaining.rfind(" ", 0, fit + 1), remaining.rfind("_", 0, fit + 1))
        if split <= 0:
            split = fit
        lines.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip(" _")
    return tuple(lines)


class BrowserItemDelegate(QStyledItemDelegate):
    selection_appearance = SelectionAppearance()

    def __init__(
        self,
        parent=None,
        *,
        thumbnail_size: int = 180,
        density: BrowserDisplayDensity = BrowserDisplayDensity.STANDARD,
        frame_ratio_id: str = "portrait_1_sqrt2",
        thumbnail_display_mode: str = "fit",
        cell_padding: int = 0,
        filename_display: str = "one_line",
        filename_elide_mode: str = "right",
        filename_font_size: int = 0,
        show_filename_extension: bool = True,
        filename_gap: int = 0,
        filename_padding_y: int = 0,
        item_spacing_x: int = 0,
        item_spacing_y: int = 0,
        folder_fallback_background: str = "auto",
        file_fallback_background: str = "auto",
        center_folder_icon_size: str = BROWSER_ICON_SIZE_DEFAULT_PRESET,
        center_folder_icon_custom_percent: int = BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
        center_file_icon_size: str = BROWSER_ICON_SIZE_DEFAULT_PRESET,
        center_file_icon_custom_percent: int = BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
        badge_folder_icon_size: str = BROWSER_ICON_SIZE_DEFAULT_PRESET,
        badge_folder_icon_custom_percent: int = BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
        badge_file_icon_size: str = BROWSER_ICON_SIZE_DEFAULT_PRESET,
        badge_file_icon_custom_percent: int = BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
        shell_icon_provider: ShellAssociatedIconProvider | None = None,
        badge_icon_left_margin: int = -1,
        badge_icon_bottom_margin: int = -1,
        show_rating_overlay: bool = True,
        show_type_badge: bool = True,
        show_tag_overlay: bool = True,
        rating_overlay_opacity: int = BROWSER_RATING_OVERLAY_DEFAULT_OPACITY,
        tag_overlay_opacity: int = BROWSER_TAG_OVERLAY_DEFAULT_OPACITY,
        tag_auto_text_color: bool = True,
        tag_text_luminance_threshold: int = BROWSER_TAG_TEXT_LUMINANCE_THRESHOLD_DEFAULT,
        tag_font_size: int = 0,
        tag_max_characters: int = 0,
        tag_text_color: str = "#ffffff",
    ) -> None:
        super().__init__(parent)
        self.thumbnail_size = int(thumbnail_size)
        self.tag_registry = []
        self.density = density
        self.frame_ratio_id = frame_ratio_id
        self.thumbnail_display_mode = (
            thumbnail_display_mode
            if thumbnail_display_mode in BROWSER_THUMBNAIL_DISPLAY_MODES
            else "fit"
        )
        self.cell_padding = max(0, min(12, int(cell_padding)))
        self.filename_display = filename_display
        self.filename_elide_mode = self._normalize_filename_elide_mode(filename_elide_mode)
        self.filename_font_size = self._normalize_filename_font_size(filename_font_size)
        self.show_filename_extension = bool(show_filename_extension)
        self.filename_gap = max(0, min(32, int(filename_gap)))
        self.filename_padding_y = max(0, min(16, int(filename_padding_y)))
        self.item_spacing_x = max(0, min(32, int(item_spacing_x)))
        self.item_spacing_y = max(0, min(32, int(item_spacing_y)))
        self.folder_fallback_background = self._normalize_folder_fallback_background(
            folder_fallback_background
        )
        self.file_fallback_background = self._normalize_folder_fallback_background(
            file_fallback_background
        )
        self.center_folder_icon_size = normalize_browser_icon_size_preset(
            center_folder_icon_size
        )
        self.center_folder_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            center_folder_icon_custom_percent
        )
        self.center_file_icon_size = normalize_browser_icon_size_preset(
            center_file_icon_size
        )
        self.center_file_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            center_file_icon_custom_percent
        )
        self.badge_folder_icon_size = normalize_browser_icon_size_preset(
            badge_folder_icon_size
        )
        self.badge_folder_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            badge_folder_icon_custom_percent
        )
        self.badge_file_icon_size = normalize_browser_icon_size_preset(
            badge_file_icon_size
        )
        self.badge_file_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            badge_file_icon_custom_percent
        )
        self.shell_icon_provider = (
            shell_icon_provider or ShellAssociatedIconProvider()
        )
        self._display_surface_cache = BrowserDisplaySurfaceCache()
        self._exposure_plans = {}
        self._grid_metrics_key = None
        self._grid_metrics_value = None
        from .filename_fallback import FilenameFallback
        self._filename_fallback = FilenameFallback()
        self._title_fallback_layouts = OrderedDict()
        self._title_lines_cache: OrderedDict[tuple, tuple[str, ...]] = OrderedDict()
        self.badge_icon_left_margin = normalize_browser_icon_margin(badge_icon_left_margin)
        self.badge_icon_bottom_margin = normalize_browser_icon_margin(badge_icon_bottom_margin)
        self.show_rating_overlay = bool(show_rating_overlay)
        self.show_type_badge = bool(show_type_badge)
        self.show_tag_overlay = bool(show_tag_overlay)
        self.rating_overlay_opacity = max(0, min(100, int(rating_overlay_opacity)))
        self.tag_overlay_opacity = max(0, min(100, int(tag_overlay_opacity)))
        self.tag_auto_text_color = bool(tag_auto_text_color)
        self.tag_text_luminance_threshold = max(
            0, min(255, int(tag_text_luminance_threshold))
        )
        self.tag_font_size = max(0, min(24, int(tag_font_size)))
        self.tag_max_characters = max(0, min(100, int(tag_max_characters)))
        self.tag_text_color = QColor(tag_text_color) if QColor(tag_text_color).isValid() else QColor('#ffffff')
        self._badge_inset_cache: OrderedDict[
            tuple[str, int, int], tuple[int, int]
        ] = OrderedDict()
        self._badge_reference_cache: OrderedDict[
            tuple[str, int, float], tuple[int, int, int, int]
        ] = OrderedDict()

    @property
    def profile(self) -> BrowserGridProfile:
        return GRID_PROFILES[self.density]

    @property
    def effective_filename_font_size(self) -> int:
        return self.filename_font_size or self.profile.font_size

    @staticmethod
    def _normalize_filename_elide_mode(value: object) -> str:
        return value if isinstance(value, str) and value in {"right", "middle"} else "right"

    @staticmethod
    def _normalize_filename_font_size(value: object) -> int:
        try:
            size = int(value)
        except (TypeError, ValueError):
            return 0
        return size if size == 0 or 6 <= size <= 24 else 0

    @property
    def cell_size(self) -> QSize:
        return self.grid_metrics.cell_size

    @property
    def frame_size(self) -> QSize:
        return frame_size_from_long_edge(
            self.thumbnail_size,
            self.frame_ratio_id,
        )

    @property
    def grid_metrics(self):
        font = QFont()
        font.setPointSize(self.effective_filename_font_size)
        # Read the current font height even on a hit: application font and
        # logical DPI can change without configure() being called.
        font_height = QFontMetrics(font).height()
        key = (
            self.thumbnail_size, self.frame_ratio_id, font_height,
            self.filename_display, self.filename_gap, self.filename_padding_y,
            self.effective_filename_font_size, self.profile.horizontal_margin,
            self.cell_padding, self.item_spacing_x, self.item_spacing_y,
        )
        if key == self._grid_metrics_key:
            return self._grid_metrics_value
        value = build_browser_grid_metrics(
            thumbnail_size=self.thumbnail_size,
            frame_ratio_id=self.frame_ratio_id,
            font_height=font_height,
            filename_display=self.filename_display,
            filename_gap=self.filename_gap,
            filename_padding_y=self.filename_padding_y,
            filename_font_size=self.effective_filename_font_size,
            horizontal_margin=self.profile.horizontal_margin,
            cell_padding=self.cell_padding,
            item_spacing_x=self.item_spacing_x,
            item_spacing_y=self.item_spacing_y,
        )
        self._grid_metrics_key = key
        self._grid_metrics_value = value
        return value

    def configure(
        self,
        *,
        thumbnail_size: int,
        density: BrowserDisplayDensity,
        frame_ratio_id: str | None = None,
        thumbnail_display_mode: str | None = None,
        cell_padding: int | None = None,
        filename_display: str | None = None,
        filename_elide_mode: str | None = None,
        filename_font_size: int | None = None,
        show_filename_extension: bool | None = None,
        filename_gap: int | None = None,
        filename_padding_y: int | None = None,
        item_spacing_x: int | None = None,
        item_spacing_y: int | None = None,
        folder_fallback_background: str | None = None,
        file_fallback_background: str | None = None,
        center_folder_icon_size: str | None = None,
        center_folder_icon_custom_percent: int | None = None,
        center_file_icon_size: str | None = None,
        center_file_icon_custom_percent: int | None = None,
        badge_folder_icon_size: str | None = None,
        badge_folder_icon_custom_percent: int | None = None,
        badge_file_icon_size: str | None = None,
        badge_file_icon_custom_percent: int | None = None,
        badge_icon_left_margin: int | None = None,
        badge_icon_bottom_margin: int | None = None,
        show_rating_overlay: bool | None = None,
        show_type_badge: bool | None = None,
        show_tag_overlay: bool | None = None,
        rating_overlay_opacity: int | None = None,
        tag_overlay_opacity: int | None = None,
        tag_auto_text_color: bool | None = None,
        tag_text_luminance_threshold: int | None = None,
        tag_font_size: int | None = None,
        tag_max_characters: int | None = None,
        tag_text_color: str | None = None,
    ) -> None:
        previous_surface_geometry = (
            self.thumbnail_size,
            self.frame_ratio_id,
            self.thumbnail_display_mode,
        )
        self.thumbnail_size = int(thumbnail_size)
        self.density = density
        if frame_ratio_id is not None:
            self.frame_ratio_id = frame_ratio_id
        if badge_icon_left_margin is not None:
            self.badge_icon_left_margin = normalize_browser_icon_margin(badge_icon_left_margin)
        if badge_icon_bottom_margin is not None:
            self.badge_icon_bottom_margin = normalize_browser_icon_margin(badge_icon_bottom_margin)
        if show_rating_overlay is not None:
            self.show_rating_overlay = bool(show_rating_overlay)
        if show_type_badge is not None:
            self.show_type_badge = bool(show_type_badge)
        if show_tag_overlay is not None:
            self.show_tag_overlay = bool(show_tag_overlay)
        if rating_overlay_opacity is not None:
            self.rating_overlay_opacity = max(0, min(100, int(rating_overlay_opacity)))
        if tag_overlay_opacity is not None:
            self.tag_overlay_opacity = max(0, min(100, int(tag_overlay_opacity)))
        if tag_auto_text_color is not None:
            self.tag_auto_text_color = bool(tag_auto_text_color)
        if tag_text_luminance_threshold is not None:
            self.tag_text_luminance_threshold = max(
                0, min(255, int(tag_text_luminance_threshold))
            )
        if tag_font_size is not None:
            self.tag_font_size = max(0, min(24, int(tag_font_size)))
        if tag_max_characters is not None:
            self.tag_max_characters = max(0, min(100, int(tag_max_characters)))
        if tag_text_color is not None and QColor(tag_text_color).isValid():
            self.tag_text_color = QColor(tag_text_color)
        if thumbnail_display_mode is not None:
            self.thumbnail_display_mode = (
                thumbnail_display_mode
                if thumbnail_display_mode in BROWSER_THUMBNAIL_DISPLAY_MODES
                else "fit"
            )
        if cell_padding is not None:
            self.cell_padding = max(0, min(12, int(cell_padding)))
        if filename_display is not None:
            self.filename_display = filename_display
        if filename_elide_mode is not None:
            self.filename_elide_mode = self._normalize_filename_elide_mode(
                filename_elide_mode
            )
        if filename_font_size is not None:
            self.filename_font_size = self._normalize_filename_font_size(
                filename_font_size
            )
        if show_filename_extension is not None:
            self.show_filename_extension = bool(show_filename_extension)
        if filename_gap is not None:
            self.filename_gap = max(0, min(32, int(filename_gap)))
        if filename_padding_y is not None:
            self.filename_padding_y = max(0, min(16, int(filename_padding_y)))
        if item_spacing_x is not None:
            self.item_spacing_x = max(0, min(32, int(item_spacing_x)))
        if item_spacing_y is not None:
            self.item_spacing_y = max(0, min(32, int(item_spacing_y)))
        if file_fallback_background is not None:
            self.file_fallback_background = self._normalize_folder_fallback_background(
                file_fallback_background
            )
        if folder_fallback_background is not None:
            self.folder_fallback_background = (
                self._normalize_folder_fallback_background(
                    folder_fallback_background
                )
            )
        for name, value in (
            ("center_folder_icon_size", center_folder_icon_size),
            ("center_file_icon_size", center_file_icon_size),
            ("badge_folder_icon_size", badge_folder_icon_size),
            ("badge_file_icon_size", badge_file_icon_size),
        ):
            if value is not None:
                setattr(self, name, normalize_browser_icon_size_preset(value))
        for name, value in (
            ("center_folder_icon_custom_percent", center_folder_icon_custom_percent),
            ("center_file_icon_custom_percent", center_file_icon_custom_percent),
            ("badge_folder_icon_custom_percent", badge_folder_icon_custom_percent),
            ("badge_file_icon_custom_percent", badge_file_icon_custom_percent),
        ):
            if value is not None:
                setattr(
                    self,
                    name,
                    normalize_browser_icon_size_custom_percent(value),
                )
        if previous_surface_geometry != (
            self.thumbnail_size,
            self.frame_ratio_id,
            self.thumbnail_display_mode,
        ):
            self._display_surface_cache.clear()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        del option, index
        return self.cell_size

    @staticmethod
    def content_opacity(index: QModelIndex, item: BrowserItem) -> float:
        if bool(index.data(BrowserItemModel.CutRole)):
            return 0.52
        if item.hidden:
            return 0.62
        return 1.0

    def paint(self, painter, option, index) -> None:
        trace = getattr(self.parent(), '_scroll_trace', None)
        if trace is None or not trace._active or trace._started is None:
            self._paint_item(painter, option, index)
            return
        started = perf_counter()
        try:
            self._paint_item(painter, option, index)
        finally:
            elapsed = (perf_counter() - started) * 1000
            if elapsed >= 8:
                trace.mark('item.paint.slow', row=index.row(), duration_ms=round(elapsed, 3))

    def _paint_item(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        try:
            self._paint_background(painter, option)
            item = index.data(BrowserItemModel.ItemRole)
            if not isinstance(item, BrowserItem):
                return
            painter.setOpacity(self.content_opacity(index, item))
            cell = option.rect
            thumbnail_rect = self.grid_metrics.thumbnail_frame_rect(cell)
            thumbnail_image = index.data(BrowserItemModel.ThumbnailImageRole)
            thumbnail_error = index.data(BrowserItemModel.ThumbnailErrorRole)
            uses_placeholder = self._uses_placeholder_canvas(
                item,
                thumbnail_image,
                thumbnail_error,
            )
            dpr = max(0.5, painter.device().devicePixelRatioF())
            content_rect = thumbnail_content_rect(
                thumbnail_rect,
                display_mode=self.thumbnail_display_mode,
                device_pixel_ratio=dpr,
            )
            snapped_frame = snap_logical_rect_to_physical_pixels(
                QRectF(thumbnail_rect),
                dpr,
            )
            painter.fillRect(snapped_frame, option.palette.base())
            if uses_placeholder:
                self._paint_placeholder_canvas(
                    painter,
                    content_rect,
                    item=item,
                )
            painter.setPen(QPen(option.palette.mid().color(), 1))
            painter.drawRect(snapped_frame.adjusted(0, 0, -1 / dpr, -1 / dpr))
            if isinstance(thumbnail_image, QImage) and not thumbnail_image.isNull():
                self._paint_thumbnail_image(
                    painter,
                    thumbnail_rect,
                    thumbnail_image,
                    enabled=bool(option.state & QStyle.StateFlag.State_Enabled),
                    display_mode=self.thumbnail_display_mode,
                )
            else:
                # Keep the pending-preview icon consistent with the lower-left
                # type badge.  DecorationRole remains the local fallback when
                # the shell association lookup cannot supply an image.
                center_scale = self._icon_scale(item, badge=False)
                icon_size = max(
                    8,
                    min(
                        96 * center_scale,
                        min(thumbnail_rect.width(), thumbnail_rect.height()) - 8,
                    ),
                )
                icon_size = max(8, round(icon_size))
                association_image = self._association_image(
                    item,
                    icon_size,
                    dpr,
                )
                icon = (
                    association_image
                    if not association_image.isNull()
                    else index.data(Qt.ItemDataRole.DecorationRole)
                )
                self._paint_fallback_icon(
                    painter,
                    content_rect,
                    icon,
                    option,
                    icon_scale=center_scale,
                )
            if bool(index.data(BrowserItemModel.ThumbnailLowResolutionRole)):
                color = option.palette.highlight().color()
                color.setAlpha(190)
                painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(
                    snap_logical_rect_to_physical_pixels(
                        QRectF(thumbnail_rect.adjusted(2, 2, -3, -3)),
                        dpr,
                    )
                )
            if self.show_type_badge:
                self._paint_type_icon(painter, thumbnail_rect, item)
            if item.online_only:
                badge = QRect(thumbnail_rect.right() - 24, thumbnail_rect.top() + 3, 22, 20)
                painter.fillRect(badge, option.palette.base())
                painter.setPen(option.palette.text().color())
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, '☁')
            if thumbnail_error:
                self._paint_error_badge(painter, thumbnail_rect)
            if self.show_rating_overlay and self.rating_overlay_opacity > 0:
                self._paint_rating(painter, option, index)
            if self.show_tag_overlay and self.tag_overlay_opacity > 0:
                self._paint_tags(painter, option, thumbnail_rect, item)
            self._paint_title(
                painter,
                option,
                thumbnail_rect,
                item.display_name,
                is_folder=item.kind is BrowserItemKind.FOLDER,
            )
            painter.setOpacity(1.0)
            self._paint_interaction_frame(
                painter,
                option,
                self.grid_metrics.selection_rect(cell),
            )
        finally:
            painter.restore()

    @staticmethod
    def _uses_placeholder_canvas(
        item: BrowserItem,
        thumbnail_image: object,
        thumbnail_error: object = None,
    ) -> bool:
        has_thumbnail = bool(
            isinstance(thumbnail_image, QImage)
            and not thumbnail_image.isNull()
        )
        # Pending/unrequested/cancelled and failed previews share the neutral
        # fallback. A valid image always wins, even with a stale error flag.
        return not has_thumbnail

    def _paint_placeholder_canvas(
        self,
        painter: QPainter,
        content_rect: QRectF,
        *,
        item: BrowserItem | None = None,
    ) -> None:
        """Fill the same physical-pixel-snapped content rect as real images."""
        color = (
            self.file_fallback_background_color()
            if item is not None and item.kind is not BrowserItemKind.FOLDER
            else self.folder_fallback_background_color()
        )
        painter.fillRect(content_rect, color)

    def file_fallback_background_color(self) -> QColor:
        return QColor(
            BROWSER_FILE_FALLBACK_DEFAULT_COLOR
            if self.file_fallback_background == "auto"
            else self.file_fallback_background
        )

    def folder_fallback_background_color(self) -> QColor:
        value = (
            BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR
            if self.folder_fallback_background == "auto"
            else self.folder_fallback_background
        )
        return QColor(value)

    @staticmethod
    def _normalize_folder_fallback_background(value: object) -> str:
        text = str(value or "auto").strip().casefold()
        if text == "auto":
            return "auto"
        color = QColor(text)
        return color.name() if color.isValid() else "auto"

    @staticmethod
    def _paint_fallback_icon(
        painter: QPainter,
        content_rect: QRectF,
        icon: object,
        option: QStyleOptionViewItem,
        *,
        icon_scale: float = 1.0,
    ) -> None:
        icon_scale = max(0.25, min(3.0, float(icon_scale)))
        icon_ratio = min(1.0, BROWSER_PLACEHOLDER_ICON_MAX_RATIO * icon_scale)
        available_width = max(
            1.0,
            content_rect.width() * icon_ratio,
        )
        available_height = max(
            1.0,
            content_rect.height() * icon_ratio,
        )
        if isinstance(icon, QImage) and not icon.isNull():
            dpr = max(0.5, painter.device().devicePixelRatioF())
            logical_width = icon.width() / dpr
            logical_height = icon.height() / dpr
            scale = min(
                1.0,
                available_width / max(1.0, logical_width),
                available_height / max(1.0, logical_height),
            )
            width = logical_width * scale
            height = logical_height * scale
            center = content_rect.center()
            target = QRectF(
                center.x() - width / 2,
                center.y() - height / 2,
                width,
                height,
            )
            painter.drawImage(
                target,
                icon,
                QRectF(0, 0, icon.width(), icon.height()),
            )
            return
        if isinstance(icon, QIcon) and not icon.isNull():
            mode = (
                QIcon.Mode.Disabled
                if not (option.state & QStyle.StateFlag.State_Enabled)
                else QIcon.Mode.Normal
            )
            requested = QSize(
                max(1, round(available_width)),
                max(1, round(available_height)),
            )
            pixmap = icon.pixmap(requested, mode)
            if pixmap.isNull():
                return
            source = QRectF(pixmap.rect())
            source_ratio = source.width() / max(1.0, source.height())
            if source_ratio > available_width / available_height:
                width = available_width
                height = width / source_ratio
            else:
                height = available_height
                width = height * source_ratio
            center = content_rect.center()
            target = QRectF(
                center.x() - width / 2,
                center.y() - height / 2,
                width,
                height,
            )
            painter.drawPixmap(target, pixmap, source)

    def _paint_thumbnail_image(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        image: QImage,
        *,
        enabled: bool,
        display_mode: str | None = None,
    ) -> None:
        dpr = max(0.5, painter.device().devicePixelRatioF())
        mode = display_mode or self.thumbnail_display_mode
        cache = self._display_surface_cache
        planned = self._exposure_plans.get((int(image.cacheKey()), *thumbnail_rect.getRect(), round(dpr, 6), mode))
        cached = cache._items.get(planned[0]) if planned is not None else None
        if cached is not None:
            target = planned[1]
            cache.hits += 1
            cache._items.move_to_end(planned[0])
            if cache._memory_owner is not None:
                cache._memory_owner.mark_displayed_image(image)
            surface = DisplayThumbnailSurface(cached, True, False)
        else:
            target, source = thumbnail_image_rects(thumbnail_rect, image.size(), mode, device_pixel_ratio=dpr)
            surface = cache.prepare(image, source, target, dpr)
        if not enabled:
            painter.setOpacity(0.55)
        painter.drawImage(
            target.topLeft(),
            surface.image,
        )
        if not enabled:
            painter.setOpacity(1.0)

    def begin_exposure_preparation(self):
        # Metadata only: no hidden QImage owner outside the shared ledger.
        self._exposure_plans.clear()

    def prepare_exposure_image(self, image, dpr, cell_rect):
        frame = self.grid_metrics.thumbnail_frame_rect(cell_rect)
        target, source = thumbnail_image_rects(frame, image.size(), self.thumbnail_display_mode,
                                               device_pixel_ratio=dpr)
        key = (int(image.cacheKey()), *surface_geometry_key(source, target, dpr))
        if key not in self._display_surface_cache._items:
            return False
        self._exposure_plans[(int(image.cacheKey()), *frame.getRect(), round(dpr, 6),
                              self.thumbnail_display_mode)] = (key, target)
        return True

    def prepare_item_decoration(self, item, dpr):
        """Request both placeholder and badge sizes before their first paint."""
        metrics = self.grid_metrics
        frame = metrics.thumbnail_frame_rect(metrics.cell_rect())
        center_size = max(8, round(min(96 * self._icon_scale(item, badge=False),
                                      min(frame.width(), frame.height()) - 8)))
        self._association_image(item, center_size, dpr)
        if not self.show_type_badge:
            return
        size = self._type_badge_size(frame, item)
        image = self._association_image(item, size, dpr)
        base_size = self._type_badge_size(frame, item, scale=1.0)
        if (not image.isNull() and (size != base_size
                or self.badge_icon_left_margin >= 0 or self.badge_icon_bottom_margin >= 0)):
            self._badge_reference_insets(item, base_size, dpr)
            self._badge_visible_insets(item, image)

    def prepare_thumbnail_image(
        self, image: QImage, device_pixel_ratio: float, cell_rect: QRect | None = None,
    ) -> DisplayThumbnailSurface:
        """Warm only image pixels; selection/hover/title stay live in paint()."""
        cell = cell_rect if cell_rect is not None else QRect(0, 0, self.cell_size.width(), self.cell_size.height())
        thumbnail_rect = self.grid_metrics.thumbnail_frame_rect(cell)
        target, source = thumbnail_image_rects(
            thumbnail_rect, image.size(), self.thumbnail_display_mode,
            device_pixel_ratio=device_pixel_ratio,
        )
        return self._display_surface_cache.prepare(
            image, source, target, device_pixel_ratio, displayed=False,
        )

    @staticmethod
    def _paint_background(
        painter: QPainter,
        option: QStyleOptionViewItem,
    ) -> None:
        if option.state & QStyle.StateFlag.State_MouseOver:
            color = option.palette.highlight().color()
            color.setAlpha(24)
            painter.fillRect(option.rect, color)

    def _paint_title(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        thumbnail_rect: QRect,
        title: str,
        *,
        is_folder: bool = False,
    ) -> None:
        grid = self.grid_metrics
        if grid.title_lines == 0:
            return
        font = QFont(option.font)
        font.setPointSize(self.effective_filename_font_size)
        painter.setFont(font)
        painter.setPen(
            self._selection_text_color(option)
            if option.state & QStyle.StateFlag.State_Selected
            else option.palette.text().color()
        )
        metrics = QFontMetrics(font)
        title_rect = grid.title_rect(option.rect)
        display_title = filename_for_display(
            title,
            show_extension=self.show_filename_extension,
            is_folder=is_folder,
        )
        preserve_extension = self.show_filename_extension and not is_folder
        if option.state & QStyle.StateFlag.State_Selected:
            selected_background = self._selection_color(option)
            selected_background.setAlpha(self.selection_appearance.alpha)
            painter.fillRect(
                grid.selected_title_rect(option.rect),
                selected_background,
            )
        # The same labels are painted on every scroll and thumbnail arrival.
        # Cache text only, not item pixels; include all layout inputs so font,
        # DPI, rename and settings changes take effect on the next paint.
        title_key = (
            font, metrics.fontDpi(), display_title, preserve_extension,
            title_rect.width(), grid.title_lines, self.filename_elide_mode,
        )
        if grid.title_lines == 1 and self.filename_elide_mode == 'right':
            if title_key not in self._title_fallback_layouts:
                suffix = split_filename_extension(display_title)[1] if preserve_extension else ''
                self._title_fallback_layouts[title_key] = self._filename_fallback.prepare(
                    font, display_title, title_rect.width(), suffix=suffix)
                while len(self._title_fallback_layouts) > 512:
                    self._title_fallback_layouts.popitem(last=False)
            else:
                self._title_fallback_layouts.move_to_end(title_key)
            layout = self._title_fallback_layouts[title_key]
            if layout is not None:
                line_rect = QRect(title_rect.left(), grid.title_text_rect(option.rect).top(),
                                  title_rect.width(), metrics.height())
                self._filename_fallback.draw(painter, line_rect, layout)
                return
        lines = self._title_lines_cache.get(title_key)
        if lines is None:
            lines = self._elided_title_lines(
                metrics, display_title, title_rect.width(), grid.title_lines,
                preserve_extension=preserve_extension,
            )
            self._title_lines_cache[title_key] = lines
            while len(self._title_lines_cache) > 512:
                self._title_lines_cache.popitem(last=False)
        else:
            self._title_lines_cache.move_to_end(title_key)
        y = grid.title_text_rect(option.rect).top()
        for line in lines:
            line_rect = QRect(title_rect.left(), y, title_rect.width(), metrics.height())
            painter.drawText(
                line_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                line,
            )
            y += metrics.lineSpacing()

    def _elided_title_lines(
        self, metrics, display_title: str, width: int, line_count: int,
        *, preserve_extension: bool,
    ) -> tuple[str, ...]:
        return tuple(
            (
                elide_filename_line(
                    metrics,
                    display_title.replace("\n", " "),
                    width,
                    self.filename_elide_mode,
                    preserve_extension=preserve_extension,
                ),
            )
            if line_count == 1
            else elided_title_lines(
                metrics,
                display_title,
                width,
                2,
                self.filename_elide_mode,
                preserve_extension=preserve_extension,
            )
        )

    def _icon_scale(self, item: BrowserItem, *, badge: bool) -> float:
        if item.kind is BrowserItemKind.FOLDER:
            prefix = "badge_folder" if badge else "center_folder"
        else:
            prefix = "badge_file" if badge else "center_file"
        return browser_icon_size_scale(
            getattr(self, f"{prefix}_icon_size"),
            getattr(self, f"{prefix}_icon_custom_percent"),
        )

    def _type_badge_size(
        self,
        thumbnail_rect: QRect,
        item: BrowserItem,
        *,
        scale: float | None = None,
    ) -> int:
        badge_sizes = {
            BrowserDisplayDensity.EXTRA_COMPACT: 14,
            BrowserDisplayDensity.COMPACT: 16,
            BrowserDisplayDensity.MEDIUM: 16,
            BrowserDisplayDensity.STANDARD: 18,
            BrowserDisplayDensity.COMFORTABLE: 20,
            BrowserDisplayDensity.LARGE: 22,
        }
        base_size = badge_sizes[self.density]
        size = round(
            base_size * (self._icon_scale(item, badge=True) if scale is None else scale)
        )
        cell_cap = min(
            max(8, thumbnail_rect.width() - 4),
            max(8, thumbnail_rect.height() - 3),
        )
        return max(8, min(cell_cap, size))

    def _type_badge_rect(self, thumbnail_rect: QRect, item: BrowserItem) -> QRect:
        rect = type_badge_rect(
            thumbnail_rect,
            self._type_badge_size(thumbnail_rect, item),
        )
        if self.badge_icon_left_margin >= 0:
            margin = min(self.badge_icon_left_margin, max(0, thumbnail_rect.width() - rect.width()))
            rect.moveLeft(thumbnail_rect.left() + margin)
        if self.badge_icon_bottom_margin >= 0:
            margin = min(self.badge_icon_bottom_margin, max(0, thumbnail_rect.height() - rect.height()))
            rect.moveBottom(thumbnail_rect.bottom() - margin)
        return rect

    def _paint_tags(self, painter, option, rect, item) -> None:
        if not self.show_tag_overlay or self.tag_overlay_opacity <= 0:
            return
        from .browser_tags import filename_tags
        registered = {}
        for tag in self.tag_registry:
            tokens = tag.get("tokens")
            if not isinstance(tokens, list):
                tokens = [tag.get("name")]
            for token in tokens:
                if token:
                    registered[str(token)] = tag
        # Filename order is retained, but multiple physical strings belonging
        # to one logical tag paint only one display badge.
        tags = []
        seen_ids = set()
        for token in reversed(filename_tags(str(item.path))):
            tag = registered.get(token)
            if tag is None:
                continue
            tag_id = str(tag.get("id", tag.get("name", "")))
            if tag_id in seen_ids:
                continue
            tags.append(tag)
            seen_ids.add(tag_id)
        if not tags:
            return
        painter.save()
        try:
            painter.setClipRect(rect)
            font = QFont(option.font)
            font.setPointSize(self.tag_font_size or max(7, min(10, self.profile.font_size)))
            painter.setFont(font)
            metrics = QFontMetrics(font)
            ink = metrics.tightBoundingRect(
                ''.join(
                    str(tag.get("display_name", tag.get("name", "")))
                    for tag in tags
                )
            )
            height = max(1, ink.height()) + 4  # Small rasterization guard, not line spacing.
            rating_font = QFont(option.font)
            rating_font.setPointSize(max(8, min(11, self.profile.font_size)))
            top_limit = rect.top() + QFontMetrics(rating_font).height() + 8
            # Keep the bottom-left type icon and the upper rating band clear.
            right = rect.right() - 2
            x, y = right, rect.bottom() - height - 3
            badge = self._type_badge_rect(rect, item)
            for tag in tags:
                text = str(
                    tag.get("display_name", tag.get("name", ""))
                )
                if self.tag_max_characters and len(text) > self.tag_max_characters:
                    text = text[:self.tag_max_characters] + '…'
                tag_band = QRect(rect.left(), y, rect.width(), height)
                left = (
                    badge.right() + 7
                    if tag_band.intersects(badge)
                    else rect.left() + 2
                )
                desired = metrics.horizontalAdvance(text) + 6
                if x < right and x - left < desired:
                    x, y = right, y - height - 3
                    tag_band = QRect(rect.left(), y, rect.width(), height)
                    left = (
                        badge.right() + 7
                        if tag_band.intersects(badge)
                        else rect.left() + 2
                    )
                if y < top_limit:
                    break
                available = x - left
                if available < 12:
                    break
                label = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available - 6)
                width = min(available, metrics.horizontalAdvance(label) + 6)
                box = QRect(x - width, y, width, height)
                opaque = QColor(tag['color'])
                color = QColor(opaque)
                color.setAlpha(round(255 * self.tag_overlay_opacity / 100))
                painter.fillRect(box, color)
                if self.tag_auto_text_color:
                    luminance = (
                        opaque.red() * .299
                        + opaque.green() * .587
                        + opaque.blue() * .114
                    )
                    text_color = (
                        'black'
                        if luminance > self.tag_text_luminance_threshold
                        else 'white'
                    )
                else:
                    text_color = self.tag_text_color
                painter.setPen(QColor(text_color))
                painter.drawText(box.left() + 3, box.top() + 2 - ink.top(), label)
                x -= width + 3
        finally:
            painter.restore()

    def rating_overlay_rect(
        self,
        cell_rect: QRect,
        font: QFont | None = None,
    ) -> QRect:
        thumbnail_rect = self.grid_metrics.thumbnail_frame_rect(cell_rect)
        rating_font = QFont(font or QFont())
        rating_font.setPointSize(max(8, min(11, self.profile.font_size)))
        metrics = QFontMetrics(rating_font)
        return QRect(
            thumbnail_rect.left() + 2,
            thumbnail_rect.top() + 2,
            metrics.horizontalAdvance("★★★★★") + 8,
            metrics.height() + 4,
        ).intersected(thumbnail_rect)

    def rating_at_position(
        self,
        cell_rect: QRect,
        position,
        font: QFont | None = None,
    ) -> int | None:
        if not self.show_rating_overlay or self.rating_overlay_opacity <= 0:
            return None
        overlay = self.rating_overlay_rect(cell_rect, font)
        stars = overlay.adjusted(4, 0, -4, 0)
        if stars.isEmpty() or not overlay.contains(position):
            return None
        relative = position.x() - stars.left()
        return max(1, min(5, (5 * relative // max(1, stars.width())) + 1))

    def _paint_rating(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if not self.show_rating_overlay or self.rating_overlay_opacity <= 0:
            return
        item = index.data(BrowserItemModel.ItemRole)
        if item is None:
            return
        current = index.data(BrowserItemModel.RatingRole)
        preview = index.data(BrowserItemModel.RatingPreviewRole)
        rating = preview if isinstance(preview, int) else current
        rating = int(rating) if isinstance(rating, int) else 0
        rating = max(0, min(5, rating))
        rating_font = QFont(option.font)
        rating_font.setPointSize(max(8, min(11, self.profile.font_size)))
        overlay = self.rating_overlay_rect(option.rect, rating_font)
        if overlay.isEmpty():
            return
        painter.save()
        painter.setFont(rating_font)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(0, 0, 0, round(255 * self.rating_overlay_opacity / 100))
        )
        painter.drawRoundedRect(QRectF(overlay), 3, 3)
        text_rect = overlay.adjusted(4, 2, -4, -2)
        painter.setPen(QColor("#778899"))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "★★★★★",
        )
        if rating:
            painter.setPen(QColor("#ffd700"))
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "★★★★★"[:rating],
            )
        painter.restore()

    def _paint_type_icon(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        item: BrowserItem,
    ) -> None:
        badge_size = self._type_badge_size(thumbnail_rect, item)
        badge = self._type_badge_rect(thumbnail_rect, item)
        dpr = max(0.5, painter.device().devicePixelRatioF())
        badge_target = snap_logical_rect_to_physical_pixels(QRectF(badge), dpr)
        image = self._association_image(item, badge_size, dpr)
        if image.isNull():
            return
        base_size = self._type_badge_size(thumbnail_rect, item, scale=1.0)
        custom_left = self.badge_icon_left_margin >= 0
        custom_bottom = self.badge_icon_bottom_margin >= 0
        if badge_size != base_size or custom_left or custom_bottom:
            # Preserve the old medium-preset position. Only compensate for the
            # difference in visible margins when the icon size changes.
            reference = self._badge_reference_insets(item, base_size, dpr)
            if reference is not None:
                ref_width, ref_height, ref_left, ref_bottom = reference
                left_inset, bottom_inset = self._badge_visible_insets(item, image)
                ref_badge = type_badge_rect(thumbnail_rect, base_size)
                ref_target = snap_logical_rect_to_physical_pixels(
                    QRectF(ref_badge), dpr
                )
                current_left = round(
                    left_inset * badge_target.width() * dpr / image.width()
                )
                current_bottom = round(
                    bottom_inset * badge_target.height() * dpr / image.height()
                )
                original_left = round(
                    ref_left * ref_target.width() * dpr / ref_width
                )
                original_bottom = round(
                    ref_bottom * ref_target.height() * dpr / ref_height
                )
                if custom_left:
                    original_left = 0
                if custom_bottom:
                    original_bottom = 0
                badge_target.translate(
                    (original_left - current_left) / dpr,
                    (current_bottom - original_bottom) / dpr,
                )
        painter.save()
        try:
            painter.setClipRect(thumbnail_rect, Qt.ClipOperation.IntersectClip)
            painter.drawImage(
                badge_target,
                image,
                QRectF(0, 0, image.width(), image.height()),
            )
        finally:
            painter.restore()

    def _badge_reference_insets(
        self, item: BrowserItem, base_size: int, dpr: float
    ) -> tuple[int, int, int, int] | None:
        association = self._badge_association(item)
        key = (association, base_size, dpr)
        cached = self._badge_reference_cache.get(key)
        if cached is not None:
            self._badge_reference_cache.move_to_end(key)
            return cached
        image = self._association_image(item, base_size, dpr)
        if image.isNull():
            return None
        left, bottom = self._badge_visible_insets(item, image)
        result = (image.width(), image.height(), left, bottom)
        self._badge_reference_cache[key] = result
        if len(self._badge_reference_cache) > BROWSER_BADGE_INSET_CACHE_MAX_ITEMS:
            self._badge_reference_cache.popitem(last=False)
        return result

    @staticmethod
    def _badge_association(item: BrowserItem) -> str:
        return (
            "folder"
            if item.kind is BrowserItemKind.FOLDER
            else item.path.suffix.casefold()
        )

    def _badge_visible_insets(self, item: BrowserItem, image: QImage) -> tuple[int, int]:
        # Shell icons are cached per file association and physical size. Their
        # transparent margins vary with size, so anchor the painted shape rather
        # than the square image while keeping the requested icon scale intact.
        association = self._badge_association(item)
        key = (association, image.width(), image.height())
        cached = self._badge_inset_cache.get(key)
        if cached is not None:
            self._badge_inset_cache.move_to_end(key)
            return cached

        # Ignore faint shadow pixels when choosing the visible edge.
        def visible(x: int, y: int) -> bool:
            return image.pixelColor(x, y).alpha() >= 16

        left = next(
            (
                x
                for x in range(image.width())
                if any(visible(x, y) for y in range(image.height()))
            ),
            0,
        )
        bottom = next(
            (
                offset
                for offset in range(image.height())
                if any(
                    visible(x, image.height() - 1 - offset)
                    for x in range(image.width())
                )
            ),
            0,
        )
        result = (left, bottom)
        self._badge_inset_cache[key] = result
        if len(self._badge_inset_cache) > BROWSER_BADGE_INSET_CACHE_MAX_ITEMS:
            self._badge_inset_cache.popitem(last=False)
        return result

    def _association_image(
        self,
        item: BrowserItem,
        logical_size: int,
        dpr: float,
    ) -> QImage:
        image_for = getattr(self.shell_icon_provider, "image_for", None)
        if callable(image_for):
            image = image_for(
                item,
                logical_size=logical_size,
                device_pixel_ratio=dpr,
            )
            if isinstance(image, QImage) and not image.isNull():
                return image
        icon_for = getattr(self.shell_icon_provider, "icon_for", None)
        icon = icon_for(item) if callable(icon_for) else QIcon()
        if not isinstance(icon, QIcon) or icon.isNull():
            return QImage()
        pixel_size = max(1, round(logical_size * dpr))
        pixmap = icon.pixmap(QSize(pixel_size, pixel_size))
        return pixmap.toImage() if not pixmap.isNull() else QImage()

    @staticmethod
    def _paint_error_badge(painter: QPainter, thumbnail_rect: QRect) -> None:
        size = 16
        badge = QRect(
            thumbnail_rect.right() - size - 4,
            thumbnail_rect.top() + 4,
            size,
            size,
        )
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(QColor(190, 40, 40, 225))
        painter.drawEllipse(badge)
        painter.drawText(
            badge,
            int(Qt.AlignmentFlag.AlignCenter),
            "!",
        )

    def _paint_interaction_frame(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        selection_rect: QRect,
    ) -> None:
        dpr = max(0.5, painter.device().devicePixelRatioF())

        def snapped(adjusted: QRect) -> QRectF:
            return snap_logical_rect_to_physical_pixels(QRectF(adjusted), dpr)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        if selected:
            pen = QPen(
                self._selection_color(option),
                self.selection_appearance.border_width,
            )
            pen.setJoinStyle(
                Qt.PenJoinStyle.RoundJoin
                if self.selection_appearance.rounded_frame
                else Qt.PenJoinStyle.MiterJoin
            )
            pen.setCapStyle(
                Qt.PenCapStyle.RoundCap
                if self.selection_appearance.rounded_frame
                else Qt.PenCapStyle.SquareCap
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = max(0.0, (self.selection_appearance.border_width - 2) / 2)
            target = snap_logical_rect_to_physical_pixels(QRectF(selection_rect).adjusted(inset, inset, -inset, -inset), dpr)
            selection_path = QPainterPath()
            if self.selection_appearance.rounded_frame:
                radius = min(5.0, min(target.width(), target.height()) * 0.08)
                selection_path.addRoundedRect(target, radius, radius)
            else:
                selection_path.addRect(target)
            painter.drawPath(selection_path)
        elif hovered:
            color = option.palette.highlight().color()
            color.setAlpha(170)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(snapped(selection_rect))
        if focused:
            painter.setPen(QPen(option.palette.highlightedText().color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(snapped(selection_rect.adjusted(3, 3, -3, -3)))

    def _selection_color(self, option) -> QColor:
        value = self.selection_appearance.color
        return QColor(option.palette.highlight().color()) if value == "auto" else QColor(value)

    def _selection_text_color(self, option) -> QColor:
        if not self.selection_appearance.auto_adjust_text_color:
            return QColor(option.palette.text().color())
        base = option.palette.base().color()
        selected = self._selection_color(option)
        alpha = self.selection_appearance.alpha / 255.0
        channels = [(1-alpha)*getattr(base, c)()+alpha*getattr(selected, c)()
                    for c in ("redF", "greenF", "blueF")]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055)**2.4 for c in channels]
        light = sum(c*w for c,w in zip(linear, (.2126,.7152,.0722)))
        return QColor("#000000" if light > .179 else "#ffffff")
