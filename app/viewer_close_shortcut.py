from __future__ import annotations

from PySide6.QtGui import QKeySequence


VIEWER_CLOSE_SHORTCUT_DEFAULT = "Ctrl+W"

# Keep this list beside the Viewer key registration so settings can reject a
# close key before it reaches an already-owned Viewer command.
VIEWER_FIXED_SHORTCUT_TEXTS = (
    "Space",
    "Backspace",
    "PgDown",
    "PgUp",
    "+",
    "Shift++",
    "=",
    "-",
    "0",
    "Esc",
    "D",
    "Shift+R",
    "B",
    # S and Shift+S are consumed by SlideshowKeys (toggle / choose interval).
    # Bare 1-9 are held-key chord constituents; modified combinations remain
    # available because conflict matching is exact and canonical.
    "S",
    "Shift+S",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "Ctrl+B",
    "Ctrl+O",
    "F5",
    "Ctrl+Shift+C",
    "Ctrl+C",
    "Ctrl+Alt+C",
    "Ctrl+I",
    "Ctrl+Q",
    "F",
    "Ctrl+Left",
    "Ctrl+Right",
    "Ctrl+0",
    "Z",
    "Alt+Left",
    "Alt+Right",
    "Right",
    "Left",
    "Shift+Right",
    "Shift+Left",
    "Ctrl+PgDown",
    "Ctrl+PgUp",
    "G",
    "Home",
    "End",
)


def _canonical_sequence(value: object) -> str:
    if isinstance(value, QKeySequence):
        sequence = value
    elif isinstance(value, str):
        sequence = QKeySequence(value.strip())
    else:
        return ""
    if sequence.isEmpty() or sequence.count() != 1:
        return ""
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


VIEWER_FIXED_SHORTCUTS = frozenset(
    _canonical_sequence(value) for value in VIEWER_FIXED_SHORTCUT_TEXTS
)


def normalize_viewer_close_shortcut(
    value: object,
    *,
    default: str = VIEWER_CLOSE_SHORTCUT_DEFAULT,
) -> str:
    if value is None:
        return default
    if isinstance(value, QKeySequence) and value.isEmpty():
        return ""
    if isinstance(value, str) and not value.strip():
        return ""
    canonical = _canonical_sequence(value)
    return canonical or default


def viewer_close_shortcut_conflict(value: object) -> str | None:
    canonical = _canonical_sequence(value)
    return canonical if canonical in VIEWER_FIXED_SHORTCUTS else None


def key_event_combined(event) -> int:
    return int(event.key()) | int(event.modifiers().value)


def sequence_combined(sequence: QKeySequence) -> int:
    return int(sequence[0].toCombined()) if not sequence.isEmpty() else 0
