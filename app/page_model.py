from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .image_source import ImageSource, ImageSourceError


@dataclass(frozen=True)
class PageSlot:
    image_id: str
    page_index: int


@dataclass(frozen=True)
class DisplaySpread:
    start_index: int
    slots: tuple[PageSlot, ...]
    is_single: bool


class PageModel:
    def __init__(self) -> None:
        self.source: ImageSource | None = None
        self.image_ids: list[str] = []
        self.current_index = 0
        self.view_mode = "spread"
        self.reading_direction = "rtl"
        self.single_first_page = True
        self.treat_wide_image_as_single = True
        self._size_cache: dict[int, tuple[int, int] | None] = {}

    @property
    def total_pages(self) -> int:
        return len(self.image_ids)

    def set_source(self, source: ImageSource, selected_image: str | None = None) -> None:
        image_ids = source.list_images()
        self.source = source
        self.image_ids = image_ids
        self._size_cache.clear()

        if not self.image_ids:
            self.current_index = 0
            return

        if selected_image and selected_image in self.image_ids:
            self.current_index = self.image_ids.index(selected_image)
        else:
            self.current_index = 0
        self.current_index = self.spread_start_for_index(self.current_index)

    def clear_source(self) -> None:
        self.source = None
        self.image_ids = []
        self.current_index = 0
        self._size_cache.clear()

    def update_options(
        self,
        *,
        view_mode: str | None = None,
        reading_direction: str | None = None,
        single_first_page: bool | None = None,
        treat_wide_image_as_single: bool | None = None,
    ) -> None:
        if view_mode is not None:
            self.view_mode = view_mode
        if reading_direction is not None:
            self.reading_direction = reading_direction
        if single_first_page is not None:
            self.single_first_page = single_first_page
        if treat_wide_image_as_single is not None:
            self.treat_wide_image_as_single = treat_wide_image_as_single
        self.current_index = self.spread_start_for_index(self.current_index)

    def image_id_at(self, index: int) -> str | None:
        if 0 <= index < self.total_pages:
            return self.image_ids[index]
        return None

    def display_path_for_index(self, index: int) -> str:
        image_id = self.image_id_at(index)
        if image_id is None or self.source is None:
            return ""
        return self.source.display_path(image_id)

    def file_size_for_index(self, index: int) -> int | None:
        image_id = self.image_id_at(index)
        if image_id is None or self.source is None:
            return None
        return self.source.file_size(image_id)

    def load_image_at(self, index: int) -> Image.Image:
        image_id = self.image_id_at(index)
        if image_id is None or self.source is None:
            raise ImageSourceError("画像が選択されていません。")
        return self.source.open_image(image_id)

    def get_image_size(self, index: int) -> tuple[int, int] | None:
        if index in self._size_cache:
            return self._size_cache[index]

        try:
            image = self.load_image_at(index)
        except ImageSourceError:
            self._size_cache[index] = None
            return None

        size = image.size
        image.close()
        self._size_cache[index] = size
        return size

    def is_wide_image(self, index: int) -> bool:
        if not self.treat_wide_image_as_single:
            return False
        size = self.get_image_size(index)
        if size is None:
            return False
        width, height = size
        return height > 0 and width / height >= 1.25

    def is_single_at(self, index: int) -> bool:
        if self.view_mode == "single":
            return True
        if self.single_first_page and index == 0:
            return True
        return self.is_wide_image(index)

    def spread_start_for_index(self, target_index: int) -> int:
        if self.total_pages == 0:
            return 0
        target_index = max(0, min(target_index, self.total_pages - 1))
        start = 0
        while start < self.total_pages:
            spread = self.spread_at(start)
            covered = [slot.page_index for slot in spread.slots]
            if target_index in covered:
                return spread.start_index
            next_start = self.next_index_from(start)
            if next_start <= start:
                break
            if next_start > target_index:
                return start
            start = next_start
        return target_index

    def spread_at(self, start_index: int | None = None) -> DisplaySpread:
        if self.total_pages == 0:
            return DisplaySpread(0, tuple(), True)

        start = self.current_index if start_index is None else start_index
        start = max(0, min(start, self.total_pages - 1))

        if (
            self.is_single_at(start)
            or start + 1 >= self.total_pages
            or self.is_wide_image(start + 1)
        ):
            image_id = self.image_ids[start]
            return DisplaySpread(start, (PageSlot(image_id, start),), True)

        first = PageSlot(self.image_ids[start], start)
        second = PageSlot(self.image_ids[start + 1], start + 1)
        slots = (first, second)
        if self.reading_direction == "rtl":
            slots = (second, first)
        return DisplaySpread(start, slots, False)

    def next_index_from(self, start_index: int) -> int:
        if self.total_pages == 0:
            return 0
        spread = self.spread_at(start_index)
        if not spread.slots:
            return 0
        max_index = max(slot.page_index for slot in spread.slots)
        next_index = max_index + 1
        if next_index >= self.total_pages:
            return spread.start_index
        return next_index

    def previous_index_from(self, start_index: int) -> int:
        if self.total_pages == 0:
            return 0
        start_index = self.spread_start_for_index(start_index)
        if start_index <= 0:
            return 0

        probe = 0
        previous = 0
        while probe < start_index:
            previous = probe
            next_probe = self.next_index_from(probe)
            if next_probe <= probe:
                break
            probe = next_probe
        return previous

    def go_to_index(self, index: int) -> None:
        self.current_index = self.spread_start_for_index(index)

    def go_to_raw_index(self, index: int) -> None:
        if self.total_pages:
            self.current_index = max(0, min(index, self.total_pages - 1))

    def next(self) -> None:
        if self.total_pages:
            self.current_index = self.next_index_from(self.current_index)

    def previous(self) -> None:
        if self.total_pages:
            self.current_index = self.previous_index_from(self.current_index)

    def first(self) -> None:
        self.current_index = 0

    def last(self) -> None:
        if self.total_pages:
            self.current_index = self.spread_start_for_index(self.total_pages - 1)
