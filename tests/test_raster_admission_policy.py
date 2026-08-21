from app.raster_admission_policy import (
    RasterAdmissionAction,
    RasterAdmissionPolicy,
)


def policy() -> RasterAdmissionPolicy:
    return RasterAdmissionPolicy(
        hard_limit_bytes=1000,
        soft_target_bytes=850,
    )


def test_optional_background_stops_at_soft_target() -> None:
    decision = policy().decide_background(
        retained_bytes=850,
        estimated_bytes=100,
        reclaimable_lower_rank_bytes=0,
        rank=3,
    )
    assert decision.action is RasterAdmissionAction.SOFT_TARGET_REACHED
    assert not decision.admitted


def test_near_neighbor_can_replace_lower_rank_artifact_up_to_hard_limit() -> None:
    decision = policy().decide_background(
        retained_bytes=900,
        estimated_bytes=180,
        reclaimable_lower_rank_bytes=200,
        rank=1,
    )
    assert decision.action is RasterAdmissionAction.ADMIT_WITH_RECLAIM
    assert decision.reclaim_bytes_needed == 80
    assert decision.worker_allowance_bytes == 300


def test_oversized_unit_is_skipped_instead_of_blocking_the_book() -> None:
    decision = policy().decide_background(
        retained_bytes=100,
        estimated_bytes=1200,
        reclaimable_lower_rank_bytes=400,
        rank=1,
    )
    assert decision.action is RasterAdmissionAction.SKIP_OVERSIZED


def test_unknown_cost_gets_only_free_plus_lower_rank_probe_allowance() -> None:
    decision = policy().decide_background(
        retained_bytes=800,
        estimated_bytes=None,
        reclaimable_lower_rank_bytes=120,
        rank=4,
    )
    assert decision.action is RasterAdmissionAction.PROBE_EXACT_COST
    assert decision.worker_allowance_bytes == 170
