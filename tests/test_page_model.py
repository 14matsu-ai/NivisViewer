from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.image_source import ImageSource
from app.page_model import PageModel


class MemoryImageSource(ImageSource):
    def __init__(self, sizes: list[tuple[int, int]]) -> None:
        super().__init__(Path("memory"))
        self._sizes = sizes
        self.closed = False

    def list_images(self) -> list[str]:
        return [f"{index}.png" for index in range(len(self._sizes))]

    def open_image(self, image_id: str) -> Image.Image:
        return Image.new("RGB", self._sizes[int(Path(image_id).stem)])

    def display_path(self, image_id: str) -> str:
        return image_id

    def close(self) -> None:
        self.closed = True


class LazyMemoryImageSource(MemoryImageSource):
    load_sizes_lazily = True


def make_model(
    sizes: list[tuple[int, int]],
    *,
    view_mode: str = "spread",
    reading_direction: str = "rtl",
    single_first_page: bool = True,
) -> PageModel:
    model = PageModel()
    model.update_options(
        view_mode=view_mode,
        reading_direction=reading_direction,
        single_first_page=single_first_page,
        treat_wide_image_as_single=True,
    )
    model.set_source(MemoryImageSource(sizes))
    return model


def logical_indexes(model: PageModel) -> list[int]:
    return sorted(slot.page_index for slot in model.spread_at().slots)


def display_units(model: PageModel) -> list[list[int]]:
    units: list[list[int]] = []
    while True:
        units.append(logical_indexes(model))
        previous = model.current_index
        model.next()
        if model.current_index == previous:
            return units


def test_single_page_mode_advances_one_page_at_a_time() -> None:
    model = make_model([(800, 1200)] * 4, view_mode="single")

    assert display_units(model) == [[0], [1], [2], [3]]


def test_focused_index_uses_current_identity_fast_path_and_falls_back() -> None:
    class CountingIdentitySource(ImageSource):
        def __init__(self) -> None:
            super().__init__(Path("counting"))
            self.ids = [f"{index}.png" for index in range(1000)]
            self.list_calls = 0
            self.index_calls = 0

        def list_images(self) -> list[str]:
            self.list_calls += 1
            return list(self.ids)

        def index_for_identity(self, identity: str) -> int:
            self.index_calls += 1
            return super().index_for_identity(identity)

        def open_image(self, _image_id: str) -> Image.Image:
            return Image.new("RGB", (8, 12), "white")

        def display_path(self, image_id: str) -> str:
            return image_id

    source = CountingIdentitySource()
    model = PageModel()
    model.update_options(view_mode="single")
    model.set_prepared_source(source, list(source.ids))
    model.go_to_raw_index(500)
    source.list_calls = 0
    source.index_calls = 0

    model.next_single()

    assert model.focused_index == 501
    assert source.index_calls == 0
    assert source.list_calls == 0

    model._focused_page_identity = source.page_identity(source.ids[700])
    assert model.focused_index == 700
    assert source.index_calls == 1
    assert source.list_calls == 1


def test_spread_mode_keeps_cover_single() -> None:
    model = make_model([(800, 1200)] * 5)

    assert display_units(model) == [[0], [1, 2], [3, 4]]


def test_reading_direction_only_changes_slot_order() -> None:
    rtl = make_model([(800, 1200)] * 3, reading_direction="rtl")
    ltr = make_model([(800, 1200)] * 3, reading_direction="ltr")
    rtl.go_to_index(1)
    ltr.go_to_index(1)

    assert [slot.page_index for slot in rtl.spread_at().slots] == [2, 1]
    assert [slot.page_index for slot in ltr.spread_at().slots] == [1, 2]
    assert rtl.next_index_from(1) == ltr.next_index_from(1)
    assert rtl.previous_index_from(1) == ltr.previous_index_from(1)


def test_trailing_unpaired_page_is_single() -> None:
    model = make_model([(800, 1200)] * 4)

    assert display_units(model) == [[0], [1, 2], [3]]


def test_wide_image_is_single() -> None:
    model = make_model([(800, 1200), (1600, 900), (800, 1200)])

    assert display_units(model) == [[0], [1], [2]]


def test_normal_page_is_not_paired_with_following_wide_image() -> None:
    model = make_model(
        [(800, 1200), (800, 1200), (1600, 900), (800, 1200)],
    )

    assert display_units(model) == [[0], [1], [2], [3]]


def test_go_to_index_uses_start_of_containing_display_unit() -> None:
    model = make_model([(800, 1200)] * 5)

    model.go_to_index(2)
    assert model.current_index == 1
    assert logical_indexes(model) == [1, 2]

    model.go_to_index(4)
    assert model.current_index == 3
    assert logical_indexes(model) == [3, 4]


def test_deep_spread_lookup_uses_constant_spread_at_calls() -> None:
    for page_count in (100, 1000, 5000):
        model = PageModel()
        model.set_source(LazyMemoryImageSource([(800, 1200)] * page_count))
        original_spread_at = model.spread_at
        spread_at_calls = 0

        def counting_spread_at(start_index: int | None = None):
            nonlocal spread_at_calls
            spread_at_calls += 1
            return original_spread_at(start_index)

        model.spread_at = counting_spread_at  # type: ignore[method-assign]

        model.go_to_index(page_count - 1)
        assert model.current_index == page_count - 1
        assert spread_at_calls == 0

        model.previous()
        assert model.current_index == page_count - 3
        assert spread_at_calls == 0

        model.next()
        assert model.current_index == page_count - 1
        assert spread_at_calls == 1

        spread_at_calls = 0
        jump_target = page_count // 2 + 20
        model.go_to_index(jump_target)
        expected_start = jump_target if jump_target % 2 else jump_target - 1
        assert model.current_index == expected_start
        assert spread_at_calls == 0


def test_spread_boundaries_rebuild_when_layout_inputs_change() -> None:
    model = PageModel()
    source = LazyMemoryImageSource([(800, 1200)] * 6)
    model.set_source(source)

    model.go_to_index(2)
    assert model.current_index == 1

    model.set_image_size(2, (1600, 900))
    assert model.current_index == 2
    assert logical_indexes(model) == [2]

    model.set_image_size(2, (800, 1200))
    assert model.current_index == 1
    assert logical_indexes(model) == [1, 2]

    model.update_options(view_mode="single")
    assert model.current_index == 2
    assert logical_indexes(model) == [2]

    model.update_options(
        view_mode="spread",
        reading_direction="ltr",
        single_first_page=False,
    )
    assert model.current_index == 2
    assert [slot.page_index for slot in model.spread_at().slots] == [2, 3]

    model.update_options(reading_direction="rtl")
    assert model.current_index == 2
    assert [slot.page_index for slot in model.spread_at().slots] == [3, 2]

    replacement = LazyMemoryImageSource([(800, 1200)] * 5)
    model.set_source(replacement)
    model.go_to_index(4)
    assert model.current_index == 4

    shorter_ids = replacement.list_images()[:3]
    model.set_prepared_source(replacement, shorter_ids)
    model.go_to_index(2)
    assert model.current_index == 2
    assert len(model._spread_start_by_index) == 3


def test_identity_fallback_uses_current_spread_after_rebuild() -> None:
    model = PageModel()
    model.set_source(LazyMemoryImageSource([(800, 1200)] * 8))
    model.go_to_index(6)
    assert model.current_index == 5

    model._focused_page_identity = "missing-page"
    model.update_options(reading_direction="ltr")

    assert model.current_index == 5
    assert logical_indexes(model) == [5, 6]


def test_repeated_navigation_neither_skips_nor_duplicates_pages() -> None:
    sizes = [
        (800, 1200),
        (800, 1200),
        (1600, 900),
        (800, 1200),
        (800, 1200),
        (1600, 900),
        (800, 1200),
        (800, 1200),
    ]
    model = make_model(sizes)

    forward_units = display_units(model)
    assert forward_units == [[0], [1], [2], [3, 4], [5], [6, 7]]
    assert [page for unit in forward_units for page in unit] == list(range(len(sizes)))

    backward_units: list[list[int]] = []
    while True:
        backward_units.append(logical_indexes(model))
        previous = model.current_index
        model.previous()
        if model.current_index == previous:
            break
    assert backward_units == list(reversed(forward_units))
