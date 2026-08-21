"""Memory-only admission decisions for raster background artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RasterAdmissionAction(str, Enum):
    ADMIT = "admit"
    ADMIT_WITH_RECLAIM = "admit_with_reclaim"
    PROBE_EXACT_COST = "probe_exact_cost"
    SOFT_TARGET_REACHED = "soft_target_reached"
    HARD_LIMIT = "hard_limit"
    SKIP_OVERSIZED = "skip_oversized"


@dataclass(frozen=True, slots=True)
class RasterAdmissionDecision:
    action: RasterAdmissionAction
    target_bytes: int
    estimated_bytes: int | None
    reclaim_bytes_needed: int
    worker_allowance_bytes: int

    @property
    def admitted(self) -> bool:
        return self.action in {
            RasterAdmissionAction.ADMIT,
            RasterAdmissionAction.ADMIT_WITH_RECLAIM,
            RasterAdmissionAction.PROBE_EXACT_COST,
        }


class RasterAdmissionPolicy:
    """Apply soft/hard targets without owning jobs or cached objects.

    The first two background ranks are the immediate forward/reverse display
    units.  They may replace lower-ranked artifacts up to the hard limit, but
    optional distant warm-up stops at the soft target.  Current rendering is
    deliberately outside this policy because it may soft-overflow to preserve
    the atomic display contract.
    """

    __slots__ = ("_hard_limit_bytes", "_minimum_rank", "_soft_target_bytes")

    def __init__(
        self,
        *,
        hard_limit_bytes: int,
        soft_target_bytes: int,
        minimum_protected_rank: int = 2,
    ) -> None:
        self._minimum_rank = max(0, int(minimum_protected_rank))
        self.set_limits(
            hard_limit_bytes=hard_limit_bytes,
            soft_target_bytes=soft_target_bytes,
        )

    @property
    def hard_limit_bytes(self) -> int:
        return self._hard_limit_bytes

    @property
    def soft_target_bytes(self) -> int:
        return self._soft_target_bytes

    @property
    def minimum_protected_rank(self) -> int:
        return self._minimum_rank

    def set_limits(
        self,
        *,
        hard_limit_bytes: int,
        soft_target_bytes: int,
    ) -> None:
        hard = max(1, int(hard_limit_bytes))
        self._hard_limit_bytes = hard
        self._soft_target_bytes = max(1, min(hard, int(soft_target_bytes)))

    def target_for_rank(self, rank: int) -> int:
        return (
            self._hard_limit_bytes
            if int(rank) <= self._minimum_rank
            else self._soft_target_bytes
        )

    def decide_background(
        self,
        *,
        retained_bytes: int,
        estimated_bytes: int | None,
        reclaimable_lower_rank_bytes: int,
        rank: int,
    ) -> RasterAdmissionDecision:
        retained = max(0, int(retained_bytes))
        reclaimable = max(0, int(reclaimable_lower_rank_bytes))
        target = self.target_for_rank(rank)
        free = max(0, target - retained)
        allowance = free + reclaimable
        if estimated_bytes is None:
            if allowance > 0:
                return RasterAdmissionDecision(
                    RasterAdmissionAction.PROBE_EXACT_COST,
                    target,
                    None,
                    0,
                    allowance,
                )
            return RasterAdmissionDecision(
                (
                    RasterAdmissionAction.SOFT_TARGET_REACHED
                    if target == self._soft_target_bytes
                    else RasterAdmissionAction.HARD_LIMIT
                ),
                target,
                None,
                0,
                0,
            )

        estimated = max(0, int(estimated_bytes))
        if estimated > self._hard_limit_bytes:
            return RasterAdmissionDecision(
                RasterAdmissionAction.SKIP_OVERSIZED,
                target,
                estimated,
                max(0, estimated - free),
                allowance,
            )
        if estimated <= free:
            return RasterAdmissionDecision(
                RasterAdmissionAction.ADMIT,
                target,
                estimated,
                0,
                allowance,
            )
        reclaim_needed = estimated - free
        if reclaim_needed <= reclaimable:
            return RasterAdmissionDecision(
                RasterAdmissionAction.ADMIT_WITH_RECLAIM,
                target,
                estimated,
                reclaim_needed,
                allowance,
            )
        if target == self._soft_target_bytes:
            action = RasterAdmissionAction.SOFT_TARGET_REACHED
        else:
            action = RasterAdmissionAction.HARD_LIMIT
        return RasterAdmissionDecision(
            action,
            target,
            estimated,
            reclaim_needed,
            allowance,
        )


__all__ = [
    "RasterAdmissionAction",
    "RasterAdmissionDecision",
    "RasterAdmissionPolicy",
]
