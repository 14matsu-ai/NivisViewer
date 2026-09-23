from __future__ import annotations

from .i18n import tr


from array import array
from dataclasses import dataclass
from typing import Iterable

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


class _SpreadStartIndexView:
    """Compatibility view over lazy, indexed spread boundaries."""

    __slots__ = ("_model",)

    def __init__(self, model: PageModel) -> None:
        self._model = model

    def __len__(self) -> int:
        return self._model.total_pages

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(
                self._model.spread_start_for_index(value)
                for value in range(*index.indices(len(self)))
            )
        value = int(index)
        if value < 0:
            value += len(self)
        if not 0 <= value < len(self):
            raise IndexError(value)
        return self._model.spread_start_for_index(value)

    def __iter__(self):
        for index in range(len(self)):
            yield self._model.spread_start_for_index(index)


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
        self._spread_start_by_index = _SpreadStartIndexView(self)
        self._spread_tree_base = 1
        self._spread_out_zero = bytearray(2)
        self._spread_out_one = bytearray(2)
        self._spread_count_zero = array("I", [0, 0])
        self._spread_count_one = array("I", [0, 0])
        self._spread_start_count = 0
        self._focused_page_identity: str | None = None
        self._sliding_spread = False
        self._topology_revision = 0

    @property
    def total_pages(self) -> int:
        return len(self.image_ids)

    @property
    def focused_index(self) -> int:
        if (
            self._focused_page_identity is not None
            and 0 <= self.current_index < self.total_pages
            and self.page_identity(self.current_index)
            == self._focused_page_identity
        ):
            return self.current_index
        resolved = self.index_for_identity(self._focused_page_identity)
        return resolved if resolved >= 0 else self.current_index

    @property
    def focused_page_identity(self) -> str | None:
        return self._focused_page_identity

    @property
    def logical_page_anchor(self) -> int:
        return self.current_index

    @property
    def sliding_spread(self) -> bool:
        return self._sliding_spread

    @property
    def topology_revision(self) -> int:
        """Revision of display-unit boundaries/order, not page position."""

        return self._topology_revision

    def set_source(self, source: ImageSource, selected_image: str | None = None) -> None:
        image_ids = source.list_images()
        self.set_prepared_source(source, image_ids, selected_image)

    def set_prepared_source(
        self,
        source: ImageSource,
        image_ids: list[str],
        selected_image: str | None = None,
    ) -> None:
        self.source = source
        self.image_ids = list(image_ids)
        self._size_cache.clear()
        self._sliding_spread = False
        self._rebuild_spread_boundaries()

        if not self.image_ids:
            self.current_index = 0
            self._focused_page_identity = None
            return

        selected_index = self.index_for_identity(selected_image)
        if selected_index >= 0:
            self._focused_page_identity = self.page_identity(selected_index)
            # A lazily-sized selected page must be the first decode request.
            # Its final DisplayUnit is reconciled by set_image_size().
            self.current_index = selected_index
            if not source.load_sizes_lazily:
                self.current_index = self.spread_start_for_index(selected_index)
        else:
            self.current_index = 0
            self._focused_page_identity = self.page_identity(0)

    def clear_source(self) -> None:
        self.source = None
        self.image_ids = []
        self.current_index = 0
        self._size_cache.clear()
        self._focused_page_identity = None
        self._sliding_spread = False
        self._rebuild_spread_boundaries()

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
        self._sliding_spread = False
        self._rebuild_spread_boundaries()
        self.current_index = self.spread_start_for_index(self.focused_index)

    def image_id_at(self, index: int) -> str | None:
        if 0 <= index < self.total_pages:
            return self.image_ids[index]
        return None

    def page_identity(self, index: int) -> str | None:
        image_id = self.image_id_at(index)
        if image_id is None:
            return None
        if self.source is not None:
            return self.source.page_identity(image_id)
        return image_id

    def index_for_identity(self, identity: str | None) -> int:
        if not identity:
            return -1
        if self.source is not None:
            resolved = self.source.index_for_identity(identity)
            if 0 <= resolved < self.total_pages:
                return resolved
        for index, image_id in enumerate(self.image_ids):
            if image_id == identity:
                return index
        return -1

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
            raise ImageSourceError(tr('画像が選択されていません。'))
        return self.source.open_image(image_id)

    def get_image_size(self, index: int) -> tuple[int, int] | None:
        if index in self._size_cache:
            return self._size_cache[index]
        if self.source is not None and self.source.load_sizes_lazily:
            return None
        image_id = self.image_id_at(index)
        if self.source is not None and image_id is not None:
            logical_size = self.source.logical_size(image_id)
            if logical_size is not None:
                self._size_cache[index] = logical_size
                return logical_size
        try:
            image = self.load_image_at(index)
        except ImageSourceError:
            self._size_cache[index] = None
            return None

        size = image.size
        image.close()
        self._size_cache[index] = size
        return size

    def set_image_size(self, index: int, size: tuple[int, int] | None) -> bool:
        previous = self.current_index
        self.set_image_sizes(((index, size),))
        return previous != self.current_index

    def set_image_sizes(
        self,
        sizes: Iterable[tuple[int, tuple[int, int] | None]],
        *,
        preserve_position: bool = False,
    ) -> bool:
        """Apply a metadata batch with one topology update at most.

        The size cache remains the source of truth. A batch can refresh frozen
        raster descriptors without changing page/history/progress position.
        """

        changes = {
            int(index): size
            for index, size in dict(sizes).items()
            if 0 <= int(index) < self.total_pages
            and (
                int(index) not in self._size_cache
                or self._size_cache[int(index)] != size
            )
        }
        if not changes:
            return False
        changed_boundaries = tuple(
            index
            for index, size in changes.items()
            if self._cached_size_is_wide(index)
            != (self.treat_wide_image_as_single and self._size_is_wide(size))
        )
        self._size_cache.update(changes)
        if changed_boundaries and self.view_mode == "spread":
            for index in changed_boundaries:
                self._set_spread_leaf(index)
            self._topology_revision += 1
            self._spread_start_count = int(self._spread_count_zero[1])
        if not preserve_position:
            focused = self.focused_index
            if focused >= 0:
                self.current_index = (
                    focused
                    if self._sliding_spread
                    else self.spread_start_for_index(focused)
                )
        return True

    def is_wide_image(self, index: int) -> bool:
        if not self.treat_wide_image_as_single:
            return False
        size = self.get_image_size(index)
        return self._size_is_wide(size)

    @staticmethod
    def _size_is_wide(size: tuple[int, int] | None) -> bool:
        if size is None:
            return False
        width, height = size
        return height > 0 and width / height >= 1.25

    def _cached_size_is_wide(self, index: int) -> bool:
        if not self.treat_wide_image_as_single:
            return False
        return self._size_is_wide(self._size_cache.get(index))

    def is_single_at(self, index: int) -> bool:
        if self.view_mode == "single":
            return True
        if self.single_first_page and index == 0:
            return True
        return self.is_wide_image(index)

    def spread_start_for_index(self, target_index: int) -> int:
        if self.total_pages == 0:
            return 0
        target = max(0, min(int(target_index), self.total_pages - 1))
        if self.is_single_at(target):
            return target
        phase, _count = self._spread_prefix(target)
        return target - 1 if phase else target

    @property
    def display_unit_count(self) -> int:
        return self._spread_start_count

    def display_unit_start_at_ordinal(self, ordinal: int) -> int:
        """Resolve a canonical display-unit ordinal in logarithmic time."""
        target = int(ordinal)
        if not 0 <= target < self._spread_start_count:
            raise IndexError(target)
        node = 1
        phase = 0
        while node < self._spread_tree_base:
            left = node * 2
            left_count = (
                self._spread_count_one[left]
                if phase
                else self._spread_count_zero[left]
            )
            if target < left_count:
                node = left
                continue
            target -= left_count
            phase = (
                self._spread_out_one[left]
                if phase
                else self._spread_out_zero[left]
            )
            node = left + 1
        return node - self._spread_tree_base

    def display_unit_ordinal_for_page(self, page_index: int) -> int | None:
        if not 0 <= int(page_index) < self.total_pages:
            return None
        start = self.spread_start_for_index(int(page_index))
        _phase, count = self._spread_prefix(start)
        return count

    def _spread_prefix(self, stop: int) -> tuple[int, int]:
        """Return pairing phase and unit count after pages in [0, stop)."""

        left = self._spread_tree_base
        right = left + max(0, min(int(stop), self.total_pages))
        left_nodes: list[int] = []
        right_nodes: list[int] = []
        while left < right:
            if left & 1:
                left_nodes.append(left)
                left += 1
            if right & 1:
                right -= 1
                right_nodes.append(right)
            left //= 2
            right //= 2
        phase = 0
        count = 0
        for node in (*left_nodes, *reversed(right_nodes)):
            if phase:
                count += self._spread_count_one[node]
                phase = self._spread_out_one[node]
            else:
                count += self._spread_count_zero[node]
                phase = self._spread_out_zero[node]
        return int(phase), int(count)

    def _set_spread_leaf(self, index: int) -> None:
        node = self._spread_tree_base + index
        if self.is_single_at(index):
            self._spread_out_zero[node] = 0
            self._spread_out_one[node] = 0
            self._spread_count_zero[node] = 1
            self._spread_count_one[node] = 1
        else:
            self._spread_out_zero[node] = 1
            self._spread_out_one[node] = 0
            self._spread_count_zero[node] = 1
            self._spread_count_one[node] = 0
        node //= 2
        while node:
            self._combine_spread_node(node)
            node //= 2

    def _combine_spread_node(self, node: int) -> None:
        left = node * 2
        right = left + 1
        for phase in (0, 1):
            left_phase = (
                self._spread_out_one[left]
                if phase
                else self._spread_out_zero[left]
            )
            left_count = (
                self._spread_count_one[left]
                if phase
                else self._spread_count_zero[left]
            )
            right_phase = (
                self._spread_out_one[right]
                if left_phase
                else self._spread_out_zero[right]
            )
            right_count = (
                self._spread_count_one[right]
                if left_phase
                else self._spread_count_zero[right]
            )
            if phase:
                self._spread_out_one[node] = right_phase
                self._spread_count_one[node] = left_count + right_count
            else:
                self._spread_out_zero[node] = right_phase
                self._spread_count_zero[node] = left_count + right_count

    def _rebuild_spread_boundaries(self) -> None:
        self._topology_revision += 1
        total = self.total_pages
        base = 1
        while base < total:
            base <<= 1
        self._spread_tree_base = base
        size = base * 2
        self._spread_out_zero = bytearray(size)
        self._spread_out_one = bytearray(size)
        self._spread_count_zero = array("I", [0]) * size
        self._spread_count_one = array("I", [0]) * size
        for index in range(total):
            node = base + index
            if self.is_single_at(index):
                self._spread_count_zero[node] = 1
                self._spread_count_one[node] = 1
            else:
                self._spread_out_zero[node] = 1
                self._spread_count_zero[node] = 1
        for index in range(total, base):
            node = base + index
            self._spread_out_zero[node] = 0
            self._spread_out_one[node] = 1
        for node in range(base - 1, 0, -1):
            self._combine_spread_node(node)
        self._spread_start_count = (
            int(self._spread_count_zero[1]) if total else 0
        )

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

    def previous_index_from(
        self,
        start_index: int,
        *,
        preserve_alignment: bool = False,
    ) -> int:
        if self.total_pages == 0:
            return 0
        if preserve_alignment:
            start_index = max(0, min(start_index, self.total_pages - 1))
        if preserve_alignment and start_index > 0:
            # A one-page command may deliberately shift a spread away from
            # the fixed topology. Find the preceding unit whose normal
            # forward edge reaches this exact anchor, rather than snapping
            # through the fixed-boundary lookup below.
            candidate = start_index - 1
            while candidate >= 0:
                candidate_next = self.next_index_from(candidate)
                if candidate_next == start_index:
                    return candidate
                if candidate_next < start_index:
                    break
                candidate -= 1
        start_index = self.spread_start_for_index(start_index)
        if start_index <= 0:
            return 0
        return self.spread_start_for_index(start_index - 1)

    def prefetch_spreads(
        self,
        *,
        direction: int = 1,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> tuple[DisplaySpread, ...]:
        """Preview bounded real navigation edges without moving this model.

        Keep canonical book boundaries unchanged. Include normal forward/back
        edges and the immediate one-page edges, even when they overlap current
        pages or start on the other spread parity. Lazy raster sources do not
        perform header/decode I/O here; unknown geometry is reconciled by the
        existing runtime metadata phase before preparing final pixels.
        """

        if not self.total_pages:
            return ()
        step = -1 if int(direction) < 0 else 1
        forward_count = max(0, min(4, int(preferred_units)))
        reverse_count = max(0, min(4, int(opposite_units)))
        current = self.spread_at()
        seen = {tuple((slot.page_index, slot.image_id) for slot in current.slots)}
        result: list[DisplaySpread] = []

        def append_at(index: int) -> None:
            spread = self.spread_at(index)
            identity = tuple((slot.page_index, slot.image_id) for slot in spread.slots)
            if spread.slots and identity not in seen:
                seen.add(identity)
                result.append(spread)

        def normal_edges(sign: int, count: int) -> list[int]:
            index, sliding = self.current_index, self._sliding_spread
            edges: list[int] = []
            for _ in range(count):
                target = (
                    self.next_index_from(index) if sign > 0
                    else self.previous_index_from(index, preserve_alignment=sliding)
                )
                if target == index:
                    break
                index = target
                if sliding:
                    sliding = self._uses_shifted_spread_anchor(index)
                edges.append(index)
            return edges

        preferred = normal_edges(step, forward_count)
        opposite = normal_edges(-step, reverse_count)
        # The immediate normal forward/reverse units retain ranks 1 and 2.
        if preferred:
            append_at(preferred[0])
        if opposite:
            append_at(opposite[0])
        # Resolve a focus on either current slot in O(1); focused_index's
        # generic source lookup can scan the whole book for the second slot.
        focused = next(
            (slot.page_index for slot in current.slots
             if self.page_identity(slot.page_index) == self._focused_page_identity),
            None,
        )
        if focused is None:
            focused = self.focused_index
        for sign in (step, -step):
            target = focused + sign
            if 0 <= target < self.total_pages:
                append_at(target)
        for distance in range(1, max(len(preferred), len(opposite))):
            if distance < len(preferred):
                append_at(preferred[distance])
            if distance < len(opposite):
                append_at(opposite[distance])
        return tuple(result)

    def _uses_shifted_spread_anchor(self, index: int) -> bool:
        return bool(
            self.view_mode == "spread"
            and self.total_pages > 0
            and index != self.spread_start_for_index(index)
        )

    def go_to_index(self, index: int) -> None:
        if not self.total_pages:
            return
        target = max(0, min(index, self.total_pages - 1))
        self._focused_page_identity = self.page_identity(target)
        self._sliding_spread = False
        self.current_index = self.spread_start_for_index(target)

    def go_to_raw_index(self, index: int) -> None:
        if self.total_pages:
            target = max(0, min(index, self.total_pages - 1))
            self.current_index = target
            self._focused_page_identity = self.page_identity(target)
            self._sliding_spread = self.view_mode == "spread"

    def go_to_focused_page_index(self, index: int) -> None:
        """Make *index* the logical anchor without snapping to a fixed spread."""

        self.go_to_raw_index(index)

    def next_single(self) -> None:
        if self.total_pages and self.focused_index < self.total_pages - 1:
            self.go_to_raw_index(min(self.total_pages - 1, self.focused_index + 1))

    def previous_single(self) -> None:
        if self.total_pages and self.focused_index > 0:
            self.go_to_raw_index(max(0, self.focused_index - 1))

    def next(self) -> None:
        if self.total_pages:
            preserve_alignment = self._sliding_spread
            self.current_index = self.next_index_from(self.current_index)
            self._focused_page_identity = self.page_identity(self.current_index)
            if preserve_alignment:
                self._sliding_spread = self._uses_shifted_spread_anchor(
                    self.current_index
                )
            else:
                self._sliding_spread = False

    def previous(self) -> None:
        if self.total_pages:
            preserve_alignment = self._sliding_spread
            if preserve_alignment:
                self.current_index = self.previous_index_from(
                    self.current_index,
                    preserve_alignment=True,
                )
            else:
                self.current_index = self.previous_index_from(
                    self.current_index
                )
            self._focused_page_identity = self.page_identity(self.current_index)
            if preserve_alignment:
                self._sliding_spread = self._uses_shifted_spread_anchor(
                    self.current_index
                )
            else:
                self._sliding_spread = False

    def first(self) -> None:
        self.current_index = 0
        self._focused_page_identity = self.page_identity(0)
        self._sliding_spread = False

    def last(self) -> None:
        if self.total_pages:
            self.current_index = self.spread_start_for_index(self.total_pages - 1)
            self._focused_page_identity = self.page_identity(self.total_pages - 1)
            self._sliding_spread = False
