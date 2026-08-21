from __future__ import annotations

from app.viewer_navigation_policy import (
    NavigationAdmissionDecision,
    NavigationAdmissionPolicy,
    NavigationInputKind,
)


def test_discrete_and_low_speed_wheel_dispatch_immediately() -> None:
    policy = NavigationAdmissionPolicy()

    assert policy.decide(
        NavigationInputKind.DISCRETE,
    ) is NavigationAdmissionDecision.IMMEDIATE
    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=1,
        now_ns=1_000_000,
    ) is NavigationAdmissionDecision.IMMEDIATE
    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=1,
        now_ns=51_000_001,
    ) is NavigationAdmissionDecision.IMMEDIATE


def test_rapid_wheel_stages_but_reversal_and_finish_dispatch() -> None:
    policy = NavigationAdmissionPolicy()

    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=1,
        now_ns=1_000_000,
    ) is NavigationAdmissionDecision.IMMEDIATE
    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=1,
        now_ns=9_000_000,
    ) is NavigationAdmissionDecision.STAGE
    assert policy.wheel_flush_delay_ms == 10
    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=-1,
        now_ns=17_000_000,
    ) is NavigationAdmissionDecision.IMMEDIATE

    policy.finish_wheel()
    assert policy.wheel_flush_delay_ms == 0
    assert policy.decide(
        NavigationInputKind.WHEEL,
        direction=-1,
        now_ns=18_000_000,
    ) is NavigationAdmissionDecision.IMMEDIATE


def test_key_initial_repeat_and_release_have_independent_state() -> None:
    policy = NavigationAdmissionPolicy()

    assert policy.decide(
        NavigationInputKind.KEY_INITIAL,
        repeat_key="PageDown",
    ) is NavigationAdmissionDecision.IMMEDIATE
    assert policy.decide(
        NavigationInputKind.KEY_REPEAT,
        repeat_key="PageDown",
    ) is NavigationAdmissionDecision.STAGE
    assert policy.decide(
        NavigationInputKind.KEY_REPEAT,
        repeat_key="PageDown",
    ) is NavigationAdmissionDecision.STAGE

    # A different repeat key is a new leading action, not part of the old run.
    assert policy.decide(
        NavigationInputKind.KEY_REPEAT,
        repeat_key="PageUp",
    ) is NavigationAdmissionDecision.IMMEDIATE
    policy.finish_key_repeat("PageUp")
    assert policy.decide(
        NavigationInputKind.KEY_REPEAT,
        repeat_key="PageUp",
    ) is NavigationAdmissionDecision.IMMEDIATE


def test_slider_scrub_stages_only_while_dragging() -> None:
    policy = NavigationAdmissionPolicy()

    assert policy.decide(
        NavigationInputKind.SLIDER_SCRUB,
        slider_drag_active=True,
    ) is NavigationAdmissionDecision.STAGE
    assert policy.decide(
        NavigationInputKind.SLIDER_SCRUB,
        slider_drag_active=False,
    ) is NavigationAdmissionDecision.IMMEDIATE
    assert policy.decide(
        NavigationInputKind.REFRESH,
    ) is NavigationAdmissionDecision.IMMEDIATE
