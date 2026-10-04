from __future__ import annotations

from app.raster_layout_metadata import select_layout_metadata_pages
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import ZipRasterDisplayUnit, ZipRasterPage


def _unit(index: int) -> ZipRasterDisplayUnit:
    page = ZipRasterPage(index, f"page-{index}.jpg")
    return ZipRasterDisplayUnit(index, (page,), True)


def test_current_metadata_preflight_does_not_read_a_neighbor_batch():
    units = tuple(_unit(index) for index in range(96))
    topology = RasterBookTopology(
        units,
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (page.page_index for page in unit.pages),
        page_count=len(units),
    )
    plan = RasterWarmupPlan(
        topology,
        current=units[48],
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (page.page_index for page in unit.pages),
        direction=1,
        background_enabled=True,
    )

    current = select_layout_metadata_pages(
        units[48], plan, set(), maximum_pages=32, include_nearby=False
    )
    background = select_layout_metadata_pages(
        units[48], plan, set(), maximum_pages=32, include_nearby=True
    )

    assert [page.page_index for page in current] == [48]
    assert len(background) == 32
    assert [page.page_index for page in background[:5]] == [48, 49, 47, 50, 46]


def test_directional_header_corridor_has_constant_visit_and_distance_bound():
    from types import SimpleNamespace

    anchor = 50_000
    current = _unit(anchor)

    class CountingTopology:
        def __init__(self):
            self.visits = []

        def __len__(self):
            return 100_000

        def ordinal_for_identity(self, _identity):
            return anchor

        def ordinal_for_page(self, _page_index):
            return anchor

        def unit_at(self, ordinal):
            self.visits.append(ordinal)
            assert len(self.visits) <= 300, "header selection scanned the book"
            return ZipRasterDisplayUnit(
                ordinal,
                (ZipRasterPage(ordinal, f"page-{ordinal}.jpg", (100, 100)),),
                True,
            )

    topology = CountingTopology()
    plan = SimpleNamespace(topology=topology, direction=1)
    pages = select_layout_metadata_pages(
        current, plan, set(), maximum_pages=32,
        include_nearby=True, directional_bias=0.75,
    )
    assert [page.page_index for page in pages] == [anchor]
    assert len(topology.visits) <= 256
    assert max(abs(ordinal - anchor) for ordinal in topology.visits) <= 128
