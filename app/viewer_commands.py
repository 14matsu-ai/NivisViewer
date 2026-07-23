from __future__ import annotations

PREVIOUS_PAGE = "previous_page"
NEXT_PAGE = "next_page"
FIRST_PAGE = "first_page"
LAST_PAGE = "last_page"
PREVIOUS_BOOK = "previous_book"
NEXT_BOOK = "next_book"
TOGGLE_FULLSCREEN = "toggle_fullscreen"
CLOSE_VIEWER = "close_viewer"
TOGGLE_SPREAD = "toggle_spread"
TOGGLE_READING_DIRECTION = "toggle_reading_direction"
FIT_WINDOW = "fit_window"
ZOOM_IN = "zoom_in"
ZOOM_OUT = "zoom_out"

VIEWER_COMMANDS = frozenset(
    {
        PREVIOUS_PAGE,
        NEXT_PAGE,
        FIRST_PAGE,
        LAST_PAGE,
        PREVIOUS_BOOK,
        NEXT_BOOK,
        TOGGLE_FULLSCREEN,
        CLOSE_VIEWER,
        TOGGLE_SPREAD,
        TOGGLE_READING_DIRECTION,
        FIT_WINDOW,
        ZOOM_IN,
        ZOOM_OUT,
    }
)

COMMAND_CHOICES: tuple[tuple[str, str], ...] = (
    ("何もしない", ""),
    ("前のページ", PREVIOUS_PAGE),
    ("次のページ", NEXT_PAGE),
    ("先頭ページ", FIRST_PAGE),
    ("最終ページ", LAST_PAGE),
    ("前の本", PREVIOUS_BOOK),
    ("次の本", NEXT_BOOK),
    ("全画面表示を切り替え", TOGGLE_FULLSCREEN),
    ("Viewerを閉じる", CLOSE_VIEWER),
    ("単ページ／見開きを切り替え", TOGGLE_SPREAD),
    ("読み方向を切り替え", TOGGLE_READING_DIRECTION),
    ("ウィンドウに合わせる", FIT_WINDOW),
    ("拡大", ZOOM_IN),
    ("縮小", ZOOM_OUT),
)


def normalize_viewer_command(value: object) -> str:
    if isinstance(value, str) and value in VIEWER_COMMANDS:
        return value
    return ""
