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
    WAITING_FOR_COMMIT = "waiting_for_commit"
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

    def startup_runway_units(
        self,
        *,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> tuple[UnitT, ...]:
        """Return a bounded current-centered display-unit runway.

        This is a startup *minimum target*, not a cache ceiling.  Units are
        complete topology entries, so a two-page spread consumes one slot and
        a split/wide unit is never counted from a half-finished source page.
        The normal book-wide iterator remains independent and follows this
        runway without a scheduler gap, skipping artifacts already completed
        by it.
        """

        return tuple(
            self.topology.unit_at(ordinal)
            for ordinal in self._priority_band_ordinals(
                preferred_units=preferred_units,
                opposite_units=opposite_units,
            )
        )

    def priority_band_identities(
        self,
        *,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> tuple[IdentityT, ...]:
        """Return the complete display units in the urgent work-order band.

        The band affects scheduling only. Cache residency remains governed by
        the combined source/frame byte budget owned by the runtime.
        """

        return tuple(
            self.topology.identity_at(ordinal)
            for ordinal in self._priority_band_ordinals(
                preferred_units=preferred_units,
                opposite_units=opposite_units,
            )
        )

    def iter_continuous_units(
        self,
        *,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> Iterator[UnitT]:
        """Yield the priority band and remaining book as one work order."""

        if not self.background_enabled or not len(self.topology):
            return
        priority_ordinals = self._priority_band_ordinals(
            preferred_units=preferred_units,
            opposite_units=opposite_units,
        )
        emitted = {
            self.topology.identity_at(ordinal) for ordinal in priority_ordinals
        }
        current_pages = frozenset(self.current_page_indexes)
        for ordinal in priority_ordinals:
            yield self.topology.unit_at(ordinal)
        for ordinal in self._iter_background_ordinals():
            identity = self.topology.identity_at(ordinal)
            if identity in emitted or identity == self.current_identity:
                continue
            if current_pages.intersection(self.topology.page_indexes_at(ordinal)):
                continue
            emitted.add(identity)
            yield self.topology.unit_at(ordinal)

    def _priority_band_ordinals(
        self,
        *,
        preferred_units: int,
        opposite_units: int,
    ) -> tuple[int, ...]:
        preferred_remaining = max(0, int(preferred_units))
        opposite_remaining = max(0, int(opposite_units))
        if (
            not self.background_enabled
            or not len(self.topology)
            or preferred_remaining + opposite_remaining <= 0
        ):
            return ()
        current_pages = frozenset(self.current_page_indexes)
        anchor = max(
            0,
            min(self._anchor_ordinal, max(0, len(self.topology) - 1)),
        )
        preferred_step = self.direction or 1
        opposite_step = -preferred_step
        selected: list[int] = []

        def append_ordinal(ordinal: int) -> bool:
            if not 0 <= ordinal < len(self.topology):
                return False
            identity = self.topology.identity_at(ordinal)
            if identity == self.current_identity:
                return False
            if current_pages.intersection(self.topology.page_indexes_at(ordinal)):
                return False
            selected.append(ordinal)
            return True

        distance = 1
        while preferred_remaining > 0 or opposite_remaining > 0:
            added = False
            if preferred_remaining > 0:
                preferred_ordinal = anchor + preferred_step * distance
                if append_ordinal(preferred_ordinal):
                    preferred_remaining -= 1
                    added = True
                elif not 0 <= preferred_ordinal < len(self.topology):
                    preferred_remaining = 0
            if opposite_remaining > 0:
                opposite_ordinal = anchor + opposite_step * distance
                if append_ordinal(opposite_ordinal):
                    opposite_remaining -= 1
                    added = True
                elif not 0 <= opposite_ordinal < len(self.topology):
                    opposite_remaining = 0
            distance += 1
            if not added and (
                not 0 <= anchor + preferred_step * distance < len(self.topology)
                and not 0 <= anchor + opposite_step * distance < len(self.topology)
            ):
                break
        return tuple(selected)

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
    """Own one persistent, recenterable work order for a raster book."""

    __slots__ = (
        "_capacity_skips",
        "_deferred_by_target",
        "_iterator",
        "_last_candidate_identity",
        "_opposite_units",
        "_plan",
        "_preferred_units",
        "_released",
        "_stop_reason",
        "_visited_identities",
    )

    def __init__(self, plan: RasterWarmupPlan[UnitT, IdentityT]) -> None:
        self._plan = plan
        self._capacity_skips: set[IdentityT] = set()
        self._deferred_by_target = 0
        self._preferred_units = 4
        self._opposite_units = 1
        self._released = False
        self._stop_reason = WarmupStopReason.WAITING_FOR_COMMIT
        self._visited_identities: set[IdentityT] = set()
        self._last_candidate_identity: IdentityT | None = None
        self._mark_current_visited()
        self._iterator = self._new_iterator()

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
    def visited_identities(self) -> frozenset[IdentityT]:
        return frozenset(self._visited_identities)

    @property
    def background_released(self) -> bool:
        return self._released

    @property
    def startup_target_count(self) -> int:
        """Compatibility diagnostic for the active priority-band size."""

        return len(
            self._plan.priority_band_identities(
                preferred_units=self._preferred_units,
                opposite_units=self._opposite_units,
            )
        )

    @property
    def book_complete(self) -> bool:
        return self._stop_reason in {
            WarmupStopReason.COMPLETE,
            WarmupStopReason.COMPLETE_WITH_SKIPS,
        }

    @property
    def unprocessed_hint(self) -> int:
        visited = sum(
            self._plan.topology.ordinal_for_identity(identity) is not None
            for identity in self._visited_identities
        )
        return max(
            0,
            self._plan.unit_count - visited + self._deferred_by_target,
        )

    def replace(self, plan: RasterWarmupPlan[UnitT, IdentityT]) -> None:
        """Compatibility adapter for callers that used to replace the owner."""

        self.recenter(plan)

    def recenter(self, plan: RasterWarmupPlan[UnitT, IdentityT]) -> None:
        """Reprioritize unstarted work while preserving book-scoped state."""

        self._plan = plan
        self._capacity_skips = {
            identity
            for identity in self._capacity_skips
            if plan.contains_identity(identity)
        }
        self._deferred_by_target = 0
        self._last_candidate_identity = None
        self._mark_current_visited()
        self._iterator = self._new_iterator()
        self._stop_reason = (
            WarmupStopReason.RUNNING
            if self._released
            else WarmupStopReason.WAITING_FOR_COMMIT
        )

    def release_after_first_commit(
        self,
        *,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> bool:
        """Release background population once for this planner's lifetime."""

        if self._released:
            return False
        self._preferred_units = max(0, int(preferred_units))
        self._opposite_units = max(0, int(opposite_units))
        self._released = True
        self._iterator = self._new_iterator()
        self._stop_reason = WarmupStopReason.RUNNING
        return True

    def release_startup_runway(
        self,
        *,
        preferred_units: int = 4,
        opposite_units: int = 1,
    ) -> bool:
        """Compatibility adapter for the former request-scoped API."""

        return self.release_after_first_commit(
            preferred_units=preferred_units,
            opposite_units=opposite_units,
        )

    def release_after_paint(self) -> None:
        # Paint transfers displayed-frame ownership at the runtime boundary.
        # It is deliberately not a scheduler release or resume signal.
        return None

    def suspend(self) -> None:
        self._stop_reason = WarmupStopReason.SUSPENDED

    def stop_for_soft_target(self) -> None:
        # Admission has inspected a concrete unit after the iterator advanced.
        # Keep it represented as deferred until recenter/reset rebuilds order.
        self._deferred_by_target = 1
        self._stop_reason = WarmupStopReason.SOFT_TARGET

    def stop_for_hard_limit(self) -> None:
        self._deferred_by_target = 1
        self._stop_reason = WarmupStopReason.HARD_LIMIT

    def mark_capacity_skip(self, identity: IdentityT) -> None:
        self._capacity_skips.add(identity)
        self._visited_identities.add(identity)

    def discard_capacity_skip(self, identity: IdentityT) -> None:
        """Make one newly important unit eligible without rewinding the book."""

        if identity not in self._capacity_skips:
            return
        self._capacity_skips.discard(identity)
        self._deferred_by_target = 0
        self._iterator = self._new_iterator()
        self._stop_reason = (
            WarmupStopReason.RUNNING
            if self._released
            else WarmupStopReason.WAITING_FOR_COMMIT
        )

    def reset_capacity(self) -> None:
        self._capacity_skips.clear()
        self._deferred_by_target = 0
        self._last_candidate_identity = None
        self._iterator = self._new_iterator()
        self._stop_reason = (
            WarmupStopReason.RUNNING
            if self._released
            else WarmupStopReason.WAITING_FOR_COMMIT
        )

    def next_candidate(
        self,
        *,
        identity_of: Callable[[UnitT], IdentityT],
        is_ready: Callable[[UnitT], bool],
        is_terminal_failure: Callable[[UnitT], bool],
    ) -> UnitT | None:
        if not self._released:
            self._stop_reason = WarmupStopReason.WAITING_FOR_COMMIT
            return None
        if self._stop_reason in {
            WarmupStopReason.SUSPENDED,
            WarmupStopReason.SOFT_TARGET,
            WarmupStopReason.HARD_LIMIT,
        }:
            return None
        for unit in self._iterator:
            identity = identity_of(unit)
            self._visited_identities.add(identity)
            if identity in self._capacity_skips:
                continue
            if is_ready(unit) or is_terminal_failure(unit):
                continue
            self._last_candidate_identity = identity
            self._stop_reason = WarmupStopReason.RUNNING
            return unit
        self._last_candidate_identity = None
        self._stop_reason = (
            WarmupStopReason.COMPLETE_WITH_SKIPS
            if self._capacity_skips
            else WarmupStopReason.COMPLETE
        )
        return None

    def _new_iterator(self) -> Iterator[UnitT]:
        return self._plan.iter_continuous_units(
            preferred_units=self._preferred_units,
            opposite_units=self._opposite_units,
        )

    def _mark_current_visited(self) -> None:
        if (
            self._plan.topology.ordinal_for_identity(
                self._plan.current_identity
            )
            is not None
        ):
            self._visited_identities.add(self._plan.current_identity)


__all__ = [
    "RasterBookTopology",
    "RasterWarmupPlan",
    "RasterWarmupPlanner",
    "WarmupStopReason",
]
