from __future__ import annotations


BROWSER_ICON_SIZE_PRESETS = ("small", "medium", "large", "custom")
BROWSER_ICON_SIZE_PRESET_PERCENT = {
    "small": 75,
    "medium": 100,
    "large": 150,
}
BROWSER_ICON_SIZE_DEFAULT_PRESET = "medium"
BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT = 100
BROWSER_ICON_SIZE_CUSTOM_MIN_PERCENT = 25
BROWSER_ICON_SIZE_CUSTOM_MAX_PERCENT = 300
ICON_POSITION_SETTING_KEYS = (
    "browser_badge_icon_left_margin",
    "browser_badge_icon_bottom_margin",
)


def normalize_browser_icon_margin(value: object) -> int:
    try:
        return max(-1, min(128, int(value)))
    except (ValueError, TypeError):
        return -1


ICON_SIZE_SETTING_SPECS = (
    (
        "browser_center_folder_icon_size",
        "browser_center_folder_icon_custom_percent",
        "中央・フォルダ",
    ),
    (
        "browser_center_file_icon_size",
        "browser_center_file_icon_custom_percent",
        "中央・それ以外",
    ),
    (
        "browser_badge_folder_icon_size",
        "browser_badge_folder_icon_custom_percent",
        "左下・フォルダ",
    ),
    (
        "browser_badge_file_icon_size",
        "browser_badge_file_icon_custom_percent",
        "左下・それ以外",
    ),
)


def normalize_browser_icon_size_preset(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in BROWSER_ICON_SIZE_PRESETS else BROWSER_ICON_SIZE_DEFAULT_PRESET


def normalize_browser_icon_size_custom_percent(value: object) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError):
        percent = BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT
    return max(
        BROWSER_ICON_SIZE_CUSTOM_MIN_PERCENT,
        min(BROWSER_ICON_SIZE_CUSTOM_MAX_PERCENT, percent),
    )


def browser_icon_size_percent(preset: object, custom_percent: object) -> int:
    normalized = normalize_browser_icon_size_preset(preset)
    if normalized == "custom":
        return normalize_browser_icon_size_custom_percent(custom_percent)
    return BROWSER_ICON_SIZE_PRESET_PERCENT[normalized]


def browser_icon_size_scale(preset: object, custom_percent: object) -> float:
    return browser_icon_size_percent(preset, custom_percent) / 100.0
