"""Declarative Browser and Viewer shortcut definitions.

The catalog deliberately stores stable IDs and portable key text.  Labels are
translated only when the settings UI is built, so persisted bindings never
depend on the current language.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

from PySide6.QtGui import QKeySequence


@dataclass(frozen=True)
class ShortcutSpec:
    action_id: str
    scope: str
    label: str
    defaults: tuple[str, ...]


SHORTCUT_SPECS: tuple[ShortcutSpec, ...] = (
    # Browser navigation and item operations.
    ShortcutSpec("browser_focus_search", "browser", "検索欄へ移動", ("Ctrl+F",)),
    ShortcutSpec("browser_undo", "browser", "直前のファイル操作を元に戻す", ("Ctrl+Z",)),
    ShortcutSpec("browser_back", "browser", "戻る", ("Alt+Left",)),
    ShortcutSpec("browser_forward", "browser", "進む", ("Alt+Right",)),
    ShortcutSpec("browser_up", "browser", "上へ", ("Alt+Up",)),
    ShortcutSpec("browser_refresh", "browser", "更新", ("F5",)),
    ShortcutSpec("browser_focus_address", "browser", "アドレス欄へ移動", ("Ctrl+L",)),
    ShortcutSpec("browser_rename", "browser", "名前の変更", ("F2",)),
    ShortcutSpec("browser_delete", "browser", "削除", ("Delete",)),
    ShortcutSpec("browser_copy", "browser", "コピー", ("Ctrl+C",)),
    ShortcutSpec("browser_cut", "browser", "切り取り", ("Ctrl+X",)),
    ShortcutSpec("browser_paste", "browser", "貼り付け", ("Ctrl+V",)),
    ShortcutSpec("browser_new_folder", "browser", "新しいフォルダ", ("Ctrl+Shift+N",)),
    ShortcutSpec("browser_toggle_folder_bookmark", "browser", "現在のフォルダをお気に入りに登録／解除", ("Ctrl+B",)),
    ShortcutSpec("browser_backspace", "browser", "履歴を戻る", ("Backspace",)),
    ShortcutSpec("browser_cancel", "browser", "操作をキャンセル", ("Esc",)),
    ShortcutSpec("browser_clear_filters", "browser", "検索・評価・タグ絞り込みを解除", ()),
    ShortcutSpec("browser_open_selection", "browser", "選択項目を開く", ("Return", "Enter")),
    ShortcutSpec("browser_open_with", "browser", "関連付けで開く...", ("Ctrl+T",)),
    # Viewer navigation and commands.  Alias order is part of the persisted
    # contract; do not collapse the three zoom-in aliases to one value.
    ShortcutSpec("viewer_history_back", "viewer", "表示履歴を戻る", ("Alt+Left",)),
    ShortcutSpec("viewer_history_forward", "viewer", "表示履歴を進む", ("Alt+Right",)),
    ShortcutSpec("viewer_next_page", "viewer", "次ページ", ("Right",)),
    ShortcutSpec("viewer_previous_page", "viewer", "前ページ", ("Left",)),
    ShortcutSpec("viewer_next_page_or_scroll", "viewer", "下スクロール／次ページ", ("Space", "PgDown")),
    ShortcutSpec("viewer_previous_page_or_scroll", "viewer", "上スクロール／前ページ", ("Backspace", "PgUp")),
    ShortcutSpec("viewer_next_single_page", "viewer", "1ページ進む", ("Shift+Right",)),
    ShortcutSpec("viewer_previous_single_page", "viewer", "1ページ戻る", ("Shift+Left",)),
    ShortcutSpec("viewer_first_page", "viewer", "先頭ページ", ("Home",)),
    ShortcutSpec("viewer_last_page", "viewer", "最終ページ", ("End",)),
    ShortcutSpec("viewer_next_book", "viewer", "次の本", ("Ctrl+PgDown",)),
    ShortcutSpec("viewer_previous_book", "viewer", "前の本", ("Ctrl+PgUp",)),
    ShortcutSpec("viewer_page_dialog", "viewer", "ページ指定", ("G",)),
    ShortcutSpec("viewer_toggle_spread", "viewer", "単ページ／見開き切替", ("D",)),
    ShortcutSpec("viewer_toggle_reading_direction", "viewer", "読み方向切替", ("Shift+R",)),
    ShortcutSpec("viewer_toggle_fullscreen", "viewer", "全画面切替", ("F",)),
    ShortcutSpec("viewer_cancel_temporary", "viewer", "一時状態を解除", ("Esc",)),
    ShortcutSpec("viewer_zoom_in", "viewer", "拡大", ("+", "Shift++", "=")),
    ShortcutSpec("viewer_zoom_out", "viewer", "縮小", ("-",)),
    ShortcutSpec("viewer_fit_window", "viewer", "ウィンドウに合わせる", ("0",)),
    ShortcutSpec("viewer_toggle_magnifier", "viewer", "拡大鏡切替", ("Z",)),
    ShortcutSpec("viewer_toggle_bookmark", "viewer", "ブックマーク切替", ("B", "Ctrl+B")),
    ShortcutSpec("viewer_copy_path", "viewer", "現在画像のパスをコピー", ("Ctrl+Shift+C",)),
    ShortcutSpec("viewer_copy_image", "viewer", "現在画像をコピー", ("Ctrl+C",)),
    ShortcutSpec("viewer_copy_view", "viewer", "現在の表示をコピー", ("Ctrl+Alt+C",)),
    ShortcutSpec("viewer_page_info", "viewer", "ページ情報", ("Ctrl+I",)),
    ShortcutSpec("viewer_open", "viewer", "開く", ("Ctrl+O",)),
    ShortcutSpec("viewer_open_with", "viewer", "関連付けで開く...", ("Ctrl+T",)),
    ShortcutSpec("viewer_reload", "viewer", "再読み込み", ("F5",)),
    ShortcutSpec("viewer_rotate_left", "viewer", "左に回転", ("Ctrl+Left",)),
    ShortcutSpec("viewer_rotate_right", "viewer", "右に回転", ("Ctrl+Right",)),
    ShortcutSpec("viewer_reset_rotation", "viewer", "回転を解除", ("Ctrl+0",)),
    ShortcutSpec("viewer_slideshow_toggle", "viewer", "スライドショー開始／停止", ("S",)),
    ShortcutSpec("viewer_slideshow_interval", "viewer", "スライドショー間隔を選択", ("Shift+S",)),
    # Ctrl+Q is the historical menu binding; keep it as an alias of the same
    # per-window close operation instead of presenting a second quit action.
    ShortcutSpec("viewer_close", "viewer", "Viewerを閉じる", ("Ctrl+W", "Ctrl+Q")),
)

SPECS_BY_SCOPE: dict[str, tuple[ShortcutSpec, ...]] = {
    scope: tuple(spec for spec in SHORTCUT_SPECS if spec.scope == scope)
    for scope in ("browser", "viewer")
}


def canonical_key(value: object) -> str:
    if isinstance(value, QKeySequence):
        sequence = value
    elif isinstance(value, str):
        sequence = QKeySequence(value.strip())
    else:
        return ""
    if sequence.isEmpty() or sequence.count() != 1:
        return ""
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def normalize_binding_list(value: object, default: tuple[str, ...] = ()) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)):
        return list(default)
    result: list[str] = []
    for entry in value:
        key = canonical_key(entry)
        if key and key not in result:
            result.append(key)
    return result


def default_shortcut_bindings() -> dict[str, dict[str, list[str]]]:
    return {
        scope: {
            spec.action_id: [canonical_key(value) or value for value in spec.defaults]
            for spec in SPECS_BY_SCOPE[scope]
        }
        for scope in SPECS_BY_SCOPE
    }


def normalize_shortcut_bindings(value: object) -> dict[str, dict[str, list[str]]]:
    defaults = default_shortcut_bindings()
    if not isinstance(value, dict):
        return defaults
    result = deepcopy(defaults)
    for scope, specs in SPECS_BY_SCOPE.items():
        raw_scope = value.get(scope)
        if not isinstance(raw_scope, dict):
            continue
        for spec in specs:
            if spec.action_id in raw_scope:
                # An explicit [] is meaningful and must stay unassigned.
                result[scope][spec.action_id] = normalize_binding_list(
                    raw_scope[spec.action_id], ()
                )
        if scope == "browser":
            # Newly introduced defaults must not steal existing custom keys.
            for introduced in ("browser_focus_search", "browser_undo"):
                if introduced not in raw_scope:
                    occupied = {key for action, keys in raw_scope.items()
                                if action != introduced
                                for key in normalize_binding_list(keys)}
                    result[scope][introduced] = [key for key in result[scope][introduced] if key not in occupied]
        if scope == "viewer" and "viewer_quit" in raw_scope:
            # ``viewer_quit`` was the old label for the same per-window close
            # request. Preserve a saved binding while presenting one action.
            legacy_quit = normalize_binding_list(raw_scope["viewer_quit"], ())
            if "viewer_close" not in raw_scope:
                result[scope]["viewer_close"] = legacy_quit
            else:
                # Keep the union of the two independent legacy actions. An
                # explicit empty close list must not erase a saved quit key;
                # only two empty lists mean that the operation is disabled.
                for sequence in legacy_quit:
                    if sequence not in result[scope]["viewer_close"]:
                        result[scope]["viewer_close"].append(sequence)
    return result


def scope_bindings(value: object, scope: str) -> dict[str, list[str]]:
    normalized = normalize_shortcut_bindings(value)
    return normalized.get(scope, {})
