"""Lazy, book-wide work ordering for raster Viewer warm-up.

The topology is built once for a PageModel layout revision.  Navigation then
creates only a tiny plan/cursor around the new current unit; it never sorts or
materializes the whole book on the input path.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Iterator
from enum import Enum
from typing import Generic, TypeVar


UnitT = TypeVar("UnitT")
IdentityT = TypeVar("IdentityT", bound=Hashable)


class WarmupStopReason(str, Enum):
    WAITING_FOR_PAINT = "waiting_for_paint"
    RUNNING = "running"
    SOFT_TARGET = "soft_target"
    HARD_LIMIT = "hard_limit"
    COMPLETE = "complete"
    COMPLETE_WITH_SKIPS = "complete_with_skips"
    SUSPENDED = "suspended"


class RasterBookTopology(Generic[UnitT, IdentityT]):
    """Immutable display-unit topology shared by many navigation requests."""

    __slots__ = (
        "_identities",
        "_ordinal_by_identity",
        "_ordinal_by_page",
        "_page_indexes",
        "_units",
    )

    def __init__(
        self,
        units: Iterable[UnitT],
        *,
        identity_of: Callable[[UnitT], IdentityT],
        page_indexes_of: Callable[[UnitT], Iterable[int]],
        page_count: int,
    ) -> None:
        normalized_units = tuple(units)
        identities = tuple(identity_of(unit) for unit in normalized_units)
        if len(set(identities)) != len(identities):
            raise ValueError("raster topology unit identities must be unique")
        ordinal_by_identity = {
            identity: ordinal for ordinal, identity in enumerate(identities)
        }
        ordinal_by_page = [-1] * max(0, int(page_count))
        page_indexes: list[tuple[int, ...]] = []
        for ordinal, unit in enumerate(normalized_units):
            indexes = tuple(dict.fromkeys(int(index) for index in page_indexes_of(unit)))
            page_indexes.append(indexes)
            for page_index in indexes:
                if 0 <= page_index < len(ordinal_by_page):
                    ordinal_by_page[page_index] = ordinal
        self._units = normalized_units
        self._identities = identities
        self._ordinal_by_identity = ordinal_by_identity
        self._ordinal_by_page = tuple(ordinal_by_page)
        self._page_indexes = tuple(page_indexes)

    def __len__(self) -> int:
        return len(self._units)

    @property
    def units(self) -> tuple[UnitT, ...]:
        return self._units

    def unit_at(self, ordinal: int) -> UnitT:
        return self._units[int(ordinal)]

    def identity_at(self, ordinal: int) -> IdentityT:
        return self._identities[int(ordinal)]

    def page_indexes_at(self, ordinal: int) -> tuple[int, ...]:
        return self._page_indexes[int(ordinal)]

    def ordinal_for_identity(self, identity: IdentityT) -> int | None:
        return self._ordinal_by_identity.get(identity)

    def ordinal_for_page(self, page_index: int) -> int | None:
        index = int(page_index)
        if not 0 <= index < len(self._ordinal_by_page):
            return None
        ordinal = self._ordinal_by_page[index]
        return ordinal if ordinal >= 0 else None


class RasterWarmupPlan(Generic[UnitT, IdentityT]):
    """One current-centered view over an immutable book topology."""

    __slots__ = (
        "background_enabled",
        "current",
        "current_identity",
        "current_page_indexes",
        "direction",
        "topology",
        "_anchor_ordinal",
    )

    def __init__(
        self,
        topology: RasterBookTopology[UnitT, IdentityT],
        *,
        current: UnitT,
        identity_of: Callable[[UnitT], IdentityT],
        page_indexes_of: Callable[[UnitT], Iterable[int]],
        direction: int,
        background_enabled: bool,
    ) -> None:
        self.topology = topology
        self.current = current
        self.current_identity = identity_of(current)
        self.current_page_indexes = tuple(
            dict.fromkeys(int(index) for index in page_indexes_of(current))
        )
        normalized_direction = int(direction)
        self.direction = (
            -1 if normalized_direction < 0 else 1 if normalized_direction > 0 else 0
        )
        self.background_enabled = bool(background_enabled)
        anchor = topology.ordinal_for_identity(self.current_identity)
        if anchor is None:
            for page_index in self.current_page_indexes:
                anchor = topology.ordinal_for_page(page_index)
                if anchor is not None:
                    break
        self._anchor_ordinal = 0 if anchor is None else anchor

    @property
    def anchor_ordinal(self) -> int:
        return self._anchor_ordinal

    @property
    def unit_count(self) -> int:
        return len(self.topology)

    def contains_identity(self, identity: IdentityT) -> bool:
        return identity == self.current_identity or (
            self.topology.ordinal_for_identity(identity) is not None
        )

    def unit_for_identity(self, identity: IdentityT) -> UnitT | None:
        if identity == self.current_identity:
            return self.current
        ordinal = self.topology.ordinal_for_identity(identity)
        return None if ordinal is None else self.topology.unit_at(ordinal)

    def unit_for_page(self, page_index: int) -> UnitT | None:
        if int(page_index) in self.current_page_indexes:
            return self.current
        ordinal = self.topology.ordinal_for_page(page_index)
        return None if ordinal is None else self.topology.unit_at(ordinal)

    def rank_for_identity(self, identity: IdentityT) -> int | None:
        if identity == self.current_identity:
            return 0
        ordinal = self.topology.ordinal_for_identity(identity)
        if ordinal is None:
            return None
        return self._rank_for_ordinal(ordinal)

    def rank_for_page(self, page_index: int) -> int | None:
        if int(page_index) in self.current_page_indexes:
            return 0
        ordinal = self.topology.ordinal_for_page(page_index)
        if ordinal is None:
            return None
        return self._rank_for_ordinal(ordinal)

    def iter_background_units(self) -> Iterator[UnitT]:
        if not self.background_enabled or not len(self.topology):
            return
        current_pages = frozenset(self.current_page_indexes)
        for ordinal in self._iter_background_ordinals():
            identity = self.topology.identity_at(ordinal)
            if identity == self.current_identity:
                continue
            if current_pages.intersection(self.topology.page_indexes_at(ordinal)):
                continue
            yield self.topology.unit_at(ordinal)

    def _iter_background_ordinals(self) -> Iterator[int]:
        total = len(self.topology)
        anchor = max(0, min(self._anchor_ordinal, max(0, total - 1)))
        preferred_step = self.direction or 1
        opposite_step = -preferred_step
        max_distance = max(anchor, total - anchor - 1)
        for distance in range(1, max_distance + 1):
            preferred = anchor + preferred_step * distance
            if 0 <= preferred < total:
                yield preferred
            opposite = anchor + opposite_step * distance
            if 0 <= opposite < total:
                yield opposite

    def _rank_for_ordinal(self, ordinal: int) -> int:
        delta = int(ordinal) - self._anchor_ordinal
        if delta == 0:
            # A topology unit overlapped by an ephemeral/sliding current is
            # skipped by the iterator but remains the nearest retention band.
            return 1
        preferred_step = self.direction or 1
        preferred = (1 if delta > 0 else -1) == preferred_step
        distance = abs(delta)
        return distance * 2 - (1 if preferred else 0)


class RasterWarmupPlanner(Generic[UnitT, IdentityT]):
    """Own the lazy cursor, capacity skips, and warm-up stop state."""

    __slots__ = (
        "_capacity_skips",
        "_commit_release_remaining",
        "_deferred_by_target",
        "_fully_released",
        "_iterator",
        "_plan",
        "_released",
        "_stop_reason",
        "_visited_background_units",
    )

    def __init__(self, plan: RasterWarmupPlan[UnitT, IdentityT]) -> None:
        self._plan = plan
        self._capacity_skips: set[IdentityT] = set()
        self._commit_release_remaining = 0
        self._deferred_by_target = 0
        self._fully_released = False
        self._released = False
        self._stop_reason = WarmupStopReason.WAITING_FOR_PAINT
        self._visited_background_units = 0
        self._iterator = plan.iter_background_units()

    @property
    def plan(self) -> RasterWarmupPlan[UnitT, IdentityT]:
        return self._plan

    @property
    def stop_reason(self) -> WarmupStopReason:
        return self._stop_reason

    @property
    def capacity_skips(self) -> frozenset[IdentityT]:
        return frozenset(self._capacity_skips)

    @property
    def background_released(self) -> bool:
        return self._released

    @property
    def book_complete(self) -> bool:
        return self._stop_reason in {
            WarmupStopReason.COMPLETE,
            WarmupStopReason.COMPLETE_WITH_SKIPS,
        }

    @property
    def unprocessed_hint(self) -> int:
        return max(
            0,
            self._plan.unit_count
            - 1
            - self._visited_background_units
            + self._deferred_by_target,
        )

    def replace(self, plan: RasterWarmupPlan[UnitT, IdentityT]) -> None:
        self.__init__(plan)

    def release_after_commit(self, *, unit_limit: int = 1) -> None:
        """Release only the nearest missing units before physical paint.

        The first accepted frame no longer leaves the Viewer worker idle while
        Qt services the posted paint.  This bounded phase deliberately cannot
        walk the book: after ``unit_limit`` useful candidates it returns to the
        paint gate, where normal memory-driven population is released.
        """

        if self._fully_released:
            return
        self._released = True
        self._commit_release_remaining = max(
            self._commit_release_remaining,
            max(0, int(unit_limit)),
        )
        self._stop_reason = (
            WarmupStopReason.RUNNING
            if self._commit_release_remaining
            else WarmupStopReason.WAITING_FOR_PAINT
        )

    def release_after_paint(self) -> None:
        self._released = True
        self._fully_released = True
        self._stop_reason = WarmupStopReason.RUNNING

    def suspend(self) -> None:
        self._stop_reason = WarmupStopReason.SUSPENDED

    def stop_for_soft_target(self) -> None:
        # Admission has to inspect a concrete unit, so ``next_candidate`` has
        # already advanced the lazy iterator by one.  Keep that unit counted as
        # unprocessed until a target change resets the cursor.
        self._deferred_by_target = 1
        self._stop_reason = WarmupStopReason.SOFT_TARGET

    def stop_for_hard_limit(self) -> None:
        self._deferred_by_target = 1
        self._stop_reason = WarmupStopReason.HARD_LIMIT

    def mark_capacity_skip(self, identity: IdentityT) -> None:
        self._capacity_skips.add(identity)

    def reset_capacity(self) -> None:
        self._capacity_skips.clear()
        self._deferred_by_target = 0
        self._visited_background_units = 0
        self._iterator = self._plan.iter_background_units()
        self._stop_reason = (
            WarmupStopReason.RUNNING
            if self._fully_released or self._commit_release_remaining
            else WarmupStopReason.WAITING_FOR_PAINT
        )

    def next_candidate(
        self,
        *,
        identity_of: Callable[[UnitT], IdentityT],
        is_ready: Callable[[UnitT], bool],
        is_terminal_failure: Callable[[UnitT], bool],
    ) -> UnitT | None:
        if not self._released:
            self._stop_reason = WarmupStopReason.WAITING_FOR_PAINT
            return None
        if not self._fully_released and self._commit_release_remaining <= 0:
            self._stop_reason = WarmupStopReason.WAITING_FOR_PAINT
            return None
        if self._stop_reason in {
            WarmupStopReason.SUSPENDED,
            WarmupStopReason.SOFT_TARGET,
            WarmupStopReason.HARD_LIMIT,
        }:
            return None
        for unit in self._iterator:
            self._visited_background_units += 1
            identity = identity_of(unit)
            if identity in self._capacity_skips:
                continue
            if is_ready(unit) or is_terminal_failure(unit):
                continue
            if not self._fully_released:
                self._commit_release_remaining -= 1
            self._stop_reason = WarmupStopReason.RUNNING
            return unit
        self._stop_reason = (
            WarmupStopReason.COMPLETE_WITH_SKIPS
            if self._capacity_skips
            else WarmupStopReason.COMPLETE
        )
        return None


__all__ = [
    "RasterBookTopology",
    "RasterWarmupPlan",
    "RasterWarmupPlanner",
    "WarmupStopReason",
]
