from __future__ import annotations

from dataclasses import dataclass

from app.raster_warmup_planner import (
    RasterBookTopology,
    RasterWarmupPlan,
    RasterWarmupPlanner,
    WarmupStopReason,
)


@dataclass(frozen=True)
class Unit:
    pages: tuple[int, ...]

    @property
    def identity(self) -> tuple[int, ...]:
        return self.pages


def topology(page_count: int) -> RasterBookTopology[Unit, tuple[int, ...]]:
    return RasterBookTopology(
        (Unit((index,)) for index in range(page_count)),
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: unit.pages,
        page_count=page_count,
    )


def plan(
    page_count: int,
    current: int,
    direction: int,
) -> RasterWarmupPlan[Unit, tuple[int, ...]]:
    return RasterWarmupPlan(
        topology(page_count),
        current=Unit((current,)),
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: unit.pages,
        direction=direction,
        background_enabled=True,
    )


def test_plan_is_book_wide_and_direction_centered_without_sorting() -> None:
    forward = plan(8, 3, 1)
    assert [unit.pages for unit in forward.iter_background_units()] == [
        (4,),
        (2,),
        (5,),
        (1,),
        (6,),
        (0,),
        (7,),
    ]
    reverse = plan(8, 3, -1)
    assert [unit.pages for unit in reverse.iter_background_units()] == [
        (2,),
        (4,),
        (1,),
        (5,),
        (0,),
        (6,),
        (7,),
    ]
    assert forward.rank_for_identity((4,)) < forward.rank_for_identity((2,))
    assert reverse.rank_for_identity((2,)) < reverse.rank_for_identity((4,))


def test_sliding_current_skips_overlapping_topology_units() -> None:
    spread_topology = RasterBookTopology(
        (Unit((0, 1)), Unit((2, 3)), Unit((4, 5))),
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: unit.pages,
        page_count=6,
    )
    sliding = RasterWarmupPlan(
        spread_topology,
        current=Unit((1, 2)),
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: unit.pages,
        direction=1,
        background_enabled=True,
    )
    assert [unit.pages for unit in sliding.iter_background_units()] == [(4, 5)]
    assert sliding.rank_for_page(1) == 0
    assert sliding.rank_for_page(2) == 0


def test_planner_waits_for_paint_skips_capacity_and_finishes_later_work() -> None:
    warmup = RasterWarmupPlanner(plan(5, 0, 1))
    assert warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    ) is None
    assert warmup.stop_reason is WarmupStopReason.WAITING_FOR_PAINT

    warmup.release_after_paint()
    first = warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    )
    assert first is not None and first.pages == (1,)
    warmup.mark_capacity_skip(first.identity)
    remaining: list[tuple[int, ...]] = []
    while True:
        candidate = warmup.next_candidate(
            identity_of=lambda unit: unit.identity,
            is_ready=lambda _unit: False,
            is_terminal_failure=lambda _unit: False,
        )
        if candidate is None:
            break
        remaining.append(candidate.pages)
    assert remaining == [(2,), (3,), (4,)]
    assert warmup.stop_reason is WarmupStopReason.COMPLETE_WITH_SKIPS
    assert warmup.unprocessed_hint == 0


def test_capacity_reset_reconsiders_skipped_units_without_rebuilding_topology() -> None:
    work_plan = plan(4, 1, 1)
    warmup = RasterWarmupPlanner(work_plan)
    warmup.release_after_paint()
    first = warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    )
    assert first is not None
    warmup.mark_capacity_skip(first.identity)
    warmup.reset_capacity()
    retried = warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    )
    assert retried is first
    assert warmup.stop_reason is WarmupStopReason.RUNNING


def test_soft_target_pauses_without_losing_the_deferred_unit() -> None:
    warmup = RasterWarmupPlanner(plan(4, 0, 1))
    warmup.release_after_paint()
    deferred = warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    )
    assert deferred is not None and deferred.pages == (1,)
    warmup.stop_for_soft_target()
    assert warmup.unprocessed_hint == 3
    assert warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    ) is None

    warmup.reset_capacity()
    retried = warmup.next_candidate(
        identity_of=lambda unit: unit.identity,
        is_ready=lambda _unit: False,
        is_terminal_failure=lambda _unit: False,
    )
    assert retried is not None and retried.identity == deferred.identity
