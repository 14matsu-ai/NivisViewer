from app.page_model import PageModel


class LazySource:
    load_sizes_lazily = True

    def __init__(self, count):
        self.images = [f"{index}.jpg" for index in range(count)]

    def list_images(self):
        return list(self.images)

    def page_identity(self, image_id):
        return image_id

    def index_for_identity(self, identity):
        return self.images.index(identity) if identity in self.images else -1

    def logical_size(self, image_id):
        raise AssertionError("Lazy metadata must not be read synchronously")


def test_metadata_batch_rebuilds_once_and_rejoins_existing_boundaries():
    model = PageModel()
    model.update_options(reading_direction="ltr")
    model.set_source(LazySource(10000))
    original_get_size = model.get_image_size
    visits = 0

    def counted_get_size(index):
        nonlocal visits
        visits += 1
        return original_get_size(index)

    model.get_image_size = counted_get_size
    revision = model.topology_revision
    changed = tuple((index, (2016, 1152)) for index in range(1, 33))

    assert model.set_image_sizes(changed, preserve_position=True)

    assert model.topology_revision == revision + 1
    assert visits < 100
    assert model.current_index == 0
    assert model.spread_at(1).is_single
    assert model.spread_start_for_index(33) == 33
    assert model.spread_at(33).slots[1].page_index == 34


def test_known_portrait_batch_keeps_canonical_boundaries_and_position():
    model = PageModel()
    model.set_source(LazySource(129))
    model.go_to_raw_index(8)
    anchor = model.current_index
    focused = model.focused_index
    revision = model.topology_revision

    assert model.set_image_sizes(
        ((index, (1152, 2016)) for index in range(1, 33)),
        preserve_position=True,
    )

    assert model.topology_revision == revision
    assert model.current_index == anchor
    assert model.focused_index == focused
    assert model.spread_start_for_index(8) == 7


def test_incremental_metadata_updates_keep_unit_ordinal_index_consistent():
    model = PageModel()
    model.set_source(LazySource(4096))
    boundary_index = model._spread_start_by_index

    model.set_image_sizes(
        ((index, (2016, 1152)) for index in range(63, 96)),
        preserve_position=True,
    )
    assert model._spread_start_by_index is boundary_index

    model.set_image_sizes(
        ((index, (1152, 2016)) for index in range(70, 96)),
        preserve_position=True,
    )
    starts = tuple(
        index
        for index, start in enumerate(model._spread_start_by_index)
        if index == start
    )
    assert model.display_unit_count == len(starts)
    assert tuple(
        model.display_unit_start_at_ordinal(ordinal)
        for ordinal in range(model.display_unit_count)
    ) == starts
    rank_by_start = {start: ordinal for ordinal, start in enumerate(starts)}
    for page_index, start in enumerate(model._spread_start_by_index):
        assert model.display_unit_ordinal_for_page(page_index) == rank_by_start[start]


def test_metadata_boundary_updates_are_logarithmic_even_when_pairing_phase_shifts():
    model = PageModel()
    model.set_source(LazySource(65536))
    calls = 0
    combine = model._combine_spread_node

    def counted_combine(node):
        nonlocal calls
        calls += 1
        combine(node)

    model._combine_spread_node = counted_combine
    changes = tuple(
        (index, (2016, 1152))
        for index in range(32768, 32800)
    )

    assert model.set_image_sizes(changes, preserve_position=True)

    assert calls <= len(changes) * (model.total_pages.bit_length())
    assert model.spread_start_for_index(60000) == 60000
