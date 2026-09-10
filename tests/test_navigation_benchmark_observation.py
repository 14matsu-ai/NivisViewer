import hashlib

import pytest

from scripts.benchmark_viewer_navigation import _build_zip, _jpeg_payload, _paint_progress_summary


def test_high_detail_fixture_is_repeatable_and_not_flat(tmp_path):
    size = (160, 240)
    flat = _jpeg_payload(size, 0)
    detail = _jpeg_payload(size, 0, fixture_mode="high-detail")
    assert flat == _jpeg_payload(size, 0, fixture_mode="flat")
    assert detail == _jpeg_payload(size, 0, fixture_mode="high-detail")
    assert detail != flat
    assert len(detail) > len(flat)
    assert detail != _jpeg_payload(size, 1, fixture_mode="high-detail")
    first = _build_zip(tmp_path, pages=2, image_size=size, fixture_mode="high-detail")
    archive_digest = hashlib.sha256(first[0].read_bytes()).hexdigest()
    second = _build_zip(tmp_path, pages=2, image_size=size, fixture_mode="high-detail")
    assert first == second
    assert archive_digest == hashlib.sha256(second[0].read_bytes()).hexdigest()


def test_paint_intervals_include_first_last_gaps_and_ignore_duplicates():
    report = _paint_progress_summary(
        started_at=10, observed_until=20, initial_unit=(0,), final_unit=(3,),
        paints=[(10.5, (0,)), (12, (1, 2)), (13, (1, 2)), (16, (3,)), (17, (3,))],
    )
    assert report["unchanged_intervals_ms"] == [2000, 4000, 4000]
    assert report["maximum_unchanged_page_interval_ms"] == 4000
    assert report["first_final_paint_ms"] == 6000
    assert report["same_unit_paints"] == 3
    assert report["status"] == "completed"
    assert [event["pages"] for event in report["distinct_painted_transitions"]] == [[1, 2], [3]]


@pytest.mark.parametrize("timed_out", [False, True])
def test_no_transition_is_not_zero_duration_success(timed_out):
    report = _paint_progress_summary(
        started_at=1, observed_until=4, initial_unit=(0,), final_unit=(5,),
        paints=[(2, (0,)), (3, (0,))], timed_out=timed_out,
    )
    assert report["maximum_unchanged_page_interval_ms"] == 3000
    assert report["first_final_paint_ms"] is None
    assert report["status"] == ("timeout" if timed_out else "incomplete")


def test_already_at_target_and_no_paints_still_account_for_observation():
    report = _paint_progress_summary(
        started_at=1, observed_until=3, initial_unit=(5,), final_unit=(5,), paints=[],
    )
    assert report["status"] == "already_at_target"
    assert report["first_final_paint_ms"] == 0
    assert report["maximum_unchanged_page_interval_ms"] == 2000


def test_paint_interval_rounding_happens_after_aggregation():
    report = _paint_progress_summary(
        started_at=0.0000004, observed_until=0.0020008, initial_unit=(0,), final_unit=(1,),
        paints=[(-1, (1,)), (0.0010008, (1,)), (5, (2,))],
    )
    assert report["maximum_unchanged_page_interval_ms"] == 1.0
    assert report["first_final_paint_ms"] == 1.0
    assert report["observation_duration_ms"] == 2.0


def test_immediate_timeout_is_returned_as_failed_report(qapp, monkeypatch):
    from scripts import benchmark_viewer_navigation as benchmark

    pump = benchmark._pump_until

    def fail_observation(application, predicate, *, timeout):
        if predicate.__name__ == "observe_final_paint":
            raise TimeoutError("deterministic observation timeout")
        return pump(application, predicate, timeout=timeout)

    monkeypatch.setattr(benchmark, "_pump_until", fail_observation)
    report = benchmark.run_benchmark(
        pages=8, image_size=(64, 96), viewport_size=(320, 240),
        cache_mib=256, timeout=3.0,
    )
    assert report["status"] == "timeout"
    assert report["remaining_scenarios"].startswith("not run")
    progress = report["immediate_after_first_visible"]["paint_progress"]
    assert progress["status"] == "timeout"
    assert progress["observation_duration_ms"] > 0
    assert progress["first_final_paint_ms"] is None
