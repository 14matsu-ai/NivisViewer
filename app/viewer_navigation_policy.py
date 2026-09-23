from __future__ import annotations

from enum import Enum
from time import perf_counter_ns
from typing import Callable, Hashable


class NavigationInputKind(str, Enum):
    """User-input families with distinct cold-frame admission behavior."""

    DISCRETE = "discrete"
    WHEEL = "wheel"
    KEY_INITIAL = "key_initial"
    KEY_REPEAT = "key_repeat"
    SLIDER_SCRUB = "slider_scrub"
    REFRESH = "refresh"


class NavigationAdmissionDecision(str, Enum):
    """Whether a cold request starts now or only updates the work order."""

    IMMEDIATE = "immediate"
    STAGE = "stage"


class NavigationAdmissionPolicy:
    """Small state machine for cold raster-navigation admission.

    The caller observes every navigation input. Cold wheel requests are
    admitted immediately when the Window's readiness guard permits a turn;
    ready wheel frames commit synchronously. Key repeat and
    slider scrub may return ``STAGE`` to replace their pending work order.
    The policy never owns a timer.
    """

    DEFAULT_RAPID_WHEEL_NS = 40_000_000
    # A real pointer drag normally produces packets faster than this.  Keep
    # those packets replaceable until release, while a paced drag gets a
    # chance to admit each target and show the page before the thumb stops.
    DEFAULT_RAPID_SLIDER_NS = 50_000_000
    _MIN_WHEEL_FLUSH_NS = 6_000_000
    _MAX_WHEEL_FLUSH_NS = 28_000_000
    _WHEEL_FLUSH_PADDING_NS = 2_000_000

    def __init__(
        self,
        *,
        rapid_wheel_ns: int = DEFAULT_RAPID_WHEEL_NS,
        rapid_slider_ns: int = DEFAULT_RAPID_SLIDER_NS,
        clock: Callable[[], int] = perf_counter_ns,
    ) -> None:
        normalized_window = int(rapid_wheel_ns)
        if normalized_window < 0:
            raise ValueError("rapid_wheel_ns must be non-negative")
        normalized_slider_window = int(rapid_slider_ns)
        if normalized_slider_window < 0:
            raise ValueError("rapid_slider_ns must be non-negative")
        self._rapid_wheel_ns = normalized_window
        self._rapid_slider_ns = normalized_slider_window
        self._clock = clock
        self._last_wheel_ns: int | None = None
        self._wheel_direction = 0
        self._wheel_flush_delay_ns = 0
        self._last_slider_admitted_ns: int | None = None
        self._repeat_key: Hashable | None = None

    @property
    def wheel_flush_delay_ms(self) -> int:
        """Trailing delay derived from the observed rapid-wheel cadence."""

        if self._wheel_flush_delay_ns <= 0:
            return 0
        return max(1, (self._wheel_flush_delay_ns + 999_999) // 1_000_000)

    def decide(
        self,
        kind: NavigationInputKind,
        *,
        direction: int = 0,
        repeat_key: Hashable | None = None,
        slider_drag_active: bool = False,
        now_ns: int | None = None,
    ) -> NavigationAdmissionDecision:
        """Observe one input and return its cold-request admission decision."""

        normalized_kind = NavigationInputKind(kind)
        if normalized_kind is NavigationInputKind.WHEEL:
            self.finish_key_repeat()
            return self._decide_wheel(
                direction=direction,
                now_ns=now_ns,
            )
        if normalized_kind is NavigationInputKind.KEY_INITIAL:
            self.finish_wheel()
            self._repeat_key = self._validated_repeat_key(repeat_key)
            return NavigationAdmissionDecision.IMMEDIATE
        if normalized_kind is NavigationInputKind.KEY_REPEAT:
            self.finish_wheel()
            normalized_key = self._validated_repeat_key(repeat_key)
            if normalized_key != self._repeat_key:
                self._repeat_key = normalized_key
                return NavigationAdmissionDecision.IMMEDIATE
            return NavigationAdmissionDecision.STAGE
        if normalized_kind is NavigationInputKind.SLIDER_SCRUB:
            self.finish_wheel()
            self.finish_key_repeat()
            if not slider_drag_active:
                self.finish_slider()
                return NavigationAdmissionDecision.IMMEDIATE
            observed_ns = int(self._clock() if now_ns is None else now_ns)
            if observed_ns < 0:
                raise ValueError("now_ns must be non-negative")
            previous_ns = self._last_slider_admitted_ns
            if previous_ns is None:
                # Admit the leading target so a cold drag can start painting
                # while the handle continues moving.
                self._last_slider_admitted_ns = observed_ns
                return NavigationAdmissionDecision.IMMEDIATE
            elapsed_ns = observed_ns - previous_ns
            if 0 <= elapsed_ns <= self._rapid_slider_ns:
                return NavigationAdmissionDecision.STAGE
            # Measure the cadence from the last admitted target rather than
            # the last observed packet. Continuous pointer packets can be
            # frequent even when the user is dragging at a readable pace.
            self._last_slider_admitted_ns = observed_ns
            return NavigationAdmissionDecision.IMMEDIATE
        self.reset()
        return NavigationAdmissionDecision.IMMEDIATE

    def finish_wheel(self) -> None:
        """End a wheel gesture so the next wheel request dispatches now."""

        self._last_wheel_ns = None
        self._wheel_direction = 0
        self._wheel_flush_delay_ns = 0

    def finish_key_repeat(self, repeat_key: Hashable | None = None) -> None:
        """End the active repeat sequence, optionally only for one key."""

        if repeat_key is None or repeat_key == self._repeat_key:
            self._repeat_key = None

    def finish_slider(self) -> None:
        """End a slider gesture before the next drag gets a leading admit."""

        self._last_slider_admitted_ns = None

    def reset(self) -> None:
        """Forget every gesture when a book or input context changes."""

        self.finish_wheel()
        self.finish_key_repeat()
        self.finish_slider()

    def _decide_wheel(
        self,
        *,
        direction: int,
        now_ns: int | None,
    ) -> NavigationAdmissionDecision:
        normalized_direction = self._normalized_direction(direction)
        observed_ns = int(self._clock() if now_ns is None else now_ns)
        if observed_ns < 0:
            raise ValueError("now_ns must be non-negative")

        previous_ns = self._last_wheel_ns
        previous_direction = self._wheel_direction
        self._last_wheel_ns = observed_ns
        self._wheel_direction = normalized_direction

        if previous_ns is None or previous_direction != normalized_direction:
            self._wheel_flush_delay_ns = 0
            return NavigationAdmissionDecision.IMMEDIATE
        elapsed_ns = observed_ns - previous_ns
        rapid = 0 <= elapsed_ns <= self._rapid_wheel_ns
        if rapid:
            # The cadence is retained only for legacy staged-request cleanup.
            # Wheel admission itself remains immediate; the Window holds a
            # repeated direction at an unready display unit, while the
            # runtime reorders unstarted work around its sole running job.
            self._wheel_flush_delay_ns = min(
                self._MAX_WHEEL_FLUSH_NS,
                max(
                    self._MIN_WHEEL_FLUSH_NS,
                    elapsed_ns + self._WHEEL_FLUSH_PADDING_NS,
                ),
            )
            return NavigationAdmissionDecision.IMMEDIATE
        self._wheel_flush_delay_ns = 0
        return NavigationAdmissionDecision.IMMEDIATE

    @staticmethod
    def _normalized_direction(direction: int) -> int:
        normalized = int(direction)
        if normalized == 0:
            raise ValueError("wheel direction must not be zero")
        return 1 if normalized > 0 else -1

    @staticmethod
    def _validated_repeat_key(repeat_key: Hashable | None) -> Hashable:
        if repeat_key is None:
            raise ValueError("repeat_key is required for key navigation")
        try:
            hash(repeat_key)
        except TypeError as exc:
            raise TypeError("repeat_key must be hashable") from exc
        return repeat_key
