"""Regression tests for real PageModel / RasterWarmupPlan integration."""
from __future__ import annotations

from copy import copy
import pytest
from app.page_model import PageModel
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.viewer_window import _PageModelRasterTopology
from app.zip_raster_book_runtime import ZipRasterDisplayUnit, ZipRasterPage


class LazySource:
    load_sizes_lazily = True
    def list_images(self):
        return [f'{i}.jpg' for i in range(129)]
    def page_identity(self, image_id):
        return image_id
    def index_for_identity(self, identity):
        return int(identity.split('.')[0]) if identity else -1
    def logical_size(self, image_id):
        raise AssertionError('No metadata I/O on the GUI path')


def identity(spread):
    return tuple((slot.page_index, slot.image_id) for slot in spread.slots)


def page_indexes(spread):
    return tuple(slot.page_index for slot in spread.slots)


def _canonical_identities(model):
    identities = []
    start = 0
    while start < model.total_pages:
        spread = model.spread_at(start)
        identities.append(identity(spread))
        next_start = model.next_index_from(start)
        if next_start == start:
            break
        start = next_start
    return tuple(identities)


@pytest.mark.parametrize('view_mode', ['single', 'spread'])
@pytest.mark.parametrize('direction', ['ltr', 'rtl'])
@pytest.mark.parametrize('cover', [False, True])
def test_live_topology_index_tracks_incremental_page_model_layout(
    view_mode, direction, cover,
):
    model = PageModel()
    model.update_options(
        view_mode=view_mode,
        reading_direction=direction,
        single_first_page=cover,
        treat_wide_image_as_single=True,
    )
    model.set_source(LazySource())
    model.set_image_sizes(
        ((i, (2016, 1152) if i % 7 == 3 else (1152, 2016)) for i in range(129)),
        preserve_position=True,
    )

    def make_unit(indexes, *, start_index, is_single):
        return ZipRasterDisplayUnit(
            start_index,
            tuple(ZipRasterPage(i, model.image_id_at(i), model.get_image_size(i)) for i in indexes),
            is_single,
        )

    topology = _PageModelRasterTopology(model, make_unit)

    def assert_matches_page_model():
        expected = _canonical_identities(model)
        assert len(topology) == len(expected)
        for ordinal, unit_identity in enumerate(expected):
            assert topology.identity_at(ordinal) == unit_identity
            assert topology.unit_at(ordinal).identity == unit_identity
            assert topology.page_indexes_at(ordinal) == tuple(i for i, _ in unit_identity)
            assert topology.ordinal_for_identity(unit_identity) == ordinal
            for page_index, _image_id in unit_identity:
                assert topology.ordinal_for_page(page_index) == ordinal

    assert_matches_page_model()
    model.set_image_sizes(((64, (2016, 1152)),), preserve_position=True)
    assert_matches_page_model()


@pytest.mark.parametrize('reading_direction', ['ltr', 'rtl'])
@pytest.mark.parametrize('cover', [False, True])
def test_shifted_and_overlap_units_are_both_eligible(reading_direction, cover):
    model = PageModel()
    model.update_options(reading_direction=reading_direction, single_first_page=cover)
    model.set_source(LazySource())
    for i in range(129):
        model.set_image_size(i, (1152, 2016))
    units = []
    start = 0
    while True:
        units.append(model.spread_at(start))
        target = model.next_index_from(start)
        if target == start:
            break
        start = target
    topology = RasterBookTopology(units, identity_of=identity,
        page_indexes_of=page_indexes, page_count=129)
    model.go_to_raw_index(2 if cover else 3)
    before = (model.current_index, model.focused_index, model.topology_revision)
    nearby = model.prefetch_spreads(direction=1)
    plan = RasterWarmupPlan(topology, current=model.spread_at(),
        identity_of=identity, page_indexes_of=page_indexes, direction=1,
        background_enabled=True, nearby_units=nearby)
    ready = {identity(unit) for unit in plan.iter_continuous_units()}
    for command in ('next', 'previous', 'next_single', 'previous_single'):
        candidate = copy(model)
        getattr(candidate, command)()
        key = identity(candidate.spread_at())
        assert key in ready
        assert plan.contains_identity(key)
        assert plan.unit_for_identity(key) is not None
        assert plan.rank_for_identity(key) is not None
        assert key in plan.priority_band_identities()
    assert before == (model.current_index, model.focused_index, model.topology_revision)
