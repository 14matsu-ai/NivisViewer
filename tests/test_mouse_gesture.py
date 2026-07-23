from __future__ import annotations

from app.mouse_gesture import MouseGestureRecognizer


def test_movement_below_threshold_is_not_a_gesture() -> None:
    recognizer = MouseGestureRecognizer(36)
    recognizer.begin((10, 10))

    assert recognizer.update((30, 20)) == ""
    assert recognizer.finish((30, 20)) == ""


def test_primary_axis_is_quantized_to_udlr() -> None:
    recognizer = MouseGestureRecognizer(20)
    recognizer.begin((100, 100))

    assert recognizer.update((105, 70)) == "U"
    assert recognizer.update((70, 65)) == "UL"
    assert recognizer.update((75, 100)) == "ULD"
    assert recognizer.finish((110, 105)) == "ULDR"


def test_diagonal_uses_larger_axis_and_tie_prefers_horizontal() -> None:
    recognizer = MouseGestureRecognizer(10)
    recognizer.begin((0, 0))

    assert recognizer.update((12, 12)) == "R"
    assert recognizer.finish((14, 28)) == "RD"


def test_repeated_direction_is_compressed() -> None:
    recognizer = MouseGestureRecognizer(10)
    recognizer.begin((0, 0))

    recognizer.update((15, 0))
    recognizer.update((30, 0))
    recognizer.update((45, 0))

    assert recognizer.finish() == "R"


def test_finish_executes_sequence_only_once() -> None:
    recognizer = MouseGestureRecognizer(10)
    recognizer.begin((0, 0))

    assert recognizer.finish((0, 20)) == "D"
    assert recognizer.finish((0, 40)) == ""
    assert not recognizer.active


def test_cancel_discards_current_sequence() -> None:
    recognizer = MouseGestureRecognizer(10)
    recognizer.begin((0, 0))
    recognizer.update((0, -20))

    recognizer.cancel()

    assert recognizer.pattern == ""
    assert recognizer.finish() == ""


def test_sequence_never_exceeds_maximum_length() -> None:
    recognizer = MouseGestureRecognizer(5, max_directions=3)
    recognizer.begin((0, 0))
    for point in ((10, 0), (10, 10), (0, 10), (0, 0), (20, 0)):
        recognizer.update(point)

    assert recognizer.finish() == "RDL"
