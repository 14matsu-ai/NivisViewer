from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from app.browser_sort import BrowserSortKey, BrowserSortOrder, BrowserSortPolicy
from app.config_manager import ConfigManager
from app.folder_sort_dialog import FolderSortDialog, FolderSortRuleDialog
from app.folder_sort_rules import (
    SORT_RULES_KEY, folder_sort_policy, matching_folder_sort_rule, normalize_folder_sort_rules,
)


def rule(path, scope="subtree", order="descending", mode="specified"):
    return {"path": str(path), "scope": scope, "mode": mode,
            "sort_key": "name", "sort_order": order}


@pytest.mark.parametrize("scope,expected", [
    ("folder", [True, False, False]),
    ("subtree", [True, True, True]),
    ("descendants", [False, True, True]),
])
def test_three_scopes_and_sibling_boundaries(tmp_path, scope, expected):
    root = tmp_path / "漫画"
    rules = normalize_folder_sort_rules([rule(root, scope)])
    for path, applies in zip((root, root / "作品A", root / "作品A" / "外伝"), expected):
        assert (matching_folder_sort_rule(path, rules) is not None) == applies
    assert matching_folder_sort_rule(tmp_path / "漫画別", rules) is None
    assert matching_folder_sort_rule(tmp_path, rules) is None


def test_closest_applicable_rule_and_default_escape(tmp_path):
    root, child = tmp_path / "漫画", tmp_path / "漫画" / "作品A"
    rules = normalize_folder_sort_rules([
        rule(root), rule(child, "descendants", "ascending"),
        rule(child / "外伝", mode="default"),
    ])
    default = BrowserSortPolicy(BrowserSortKey.MODIFIED_TIME, BrowserSortOrder.ASCENDING, False, 12)
    assert folder_sort_policy(child, rules, default).sort_order is BrowserSortOrder.DESCENDING
    policy = folder_sort_policy(child / "本編", rules, default)
    assert policy.sort_order is BrowserSortOrder.ASCENDING
    assert policy.folders_first is False
    assert policy.random_seed == 12
    assert folder_sort_policy(child / "外伝" / "続編", rules, default) == default
    assert folder_sort_policy(root, [], default) == default


def test_normalization_deduplicates_windows_paths_and_preserves_unc():
    rules = normalize_folder_sort_rules([
        rule("C:/Library/漫画"), rule("c:\\library\\漫画\\", order="ascending"),
        rule(r"\\server\share\漫画", "descendants"),
    ])
    assert len(rules) == 2
    assert rules[0]["sort_order"] == "ascending"
    assert matching_folder_sort_rule(r"\\SERVER\share\漫画\作品", rules) == rules[1]
    assert matching_folder_sort_rule(r"\\server\other\漫画\作品", rules) is None
    drive = normalize_folder_sort_rules([rule("C:/", "descendants")])
    assert matching_folder_sort_rule("C:/Library", drive) is not None
    assert matching_folder_sort_rule("C:/", drive) is None


@pytest.mark.parametrize("invalid", [None, "text", {}, [None], [rule("relative")],
    [{**rule("C:/Library"), "scope": []}], [{**rule("C:/Library"), "mode": {}}],
    [{**rule("C:/Library"), "sort_key": []}], [{**rule("C:/Library"), "sort_order": {}}]])
def test_invalid_saved_rules_are_ignored(invalid):
    assert normalize_folder_sort_rules(invalid) == []


def test_persistence_and_missing_folders_do_not_require_filesystem(tmp_path, monkeypatch):
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    saved = normalize_folder_sort_rules([rule(tmp_path / "未接続" / "シリーズ")])
    config.apply({SORT_RULES_KEY: saved}, save=True)
    restored = ConfigManager(config.path)
    restored.load()
    assert restored.get(SORT_RULES_KEY) == saved
    monkeypatch.setattr(os, "stat", lambda *_a, **_k: pytest.fail("Rule resolution accessed filesystem"))
    assert matching_folder_sort_rule(tmp_path / "未接続" / "シリーズ" / "巻", saved)


def test_dialog_add_edit_duplicate_remove_and_apply(qapp, tmp_path, monkeypatch):
    root = tmp_path / "漫画"
    dialog = FolderSortDialog([], root)
    try:
        def edit(detail):
            detail.scope_combo.setCurrentIndex(detail.scope_combo.findData("descendants"))
            detail.sort_combo.setCurrentIndex(detail.sort_combo.findData("name:descending"))
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(FolderSortRuleDialog, "exec", edit)
        dialog.add_current_button.click()
        dialog.add_current_button.click()
        assert dialog.list.topLevelItemCount() == 1
        assert dialog.rules() == normalize_folder_sort_rules([rule(root, "descendants")])
        applied = []
        dialog.apply_requested.connect(applied.append)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
        assert applied == [dialog.rules()]
        dialog.remove_button.click()
        assert dialog.rules() == []
        dialog.accept()
        assert applied[-1] == []
    finally:
        dialog.close()


def test_dialog_cancel_and_edit_path_collision(qapp, tmp_path, monkeypatch):
    first, second = tmp_path / "A", tmp_path / "B"
    dialog = FolderSortDialog([rule(first), rule(second)], first)
    try:
        original = dialog.rules()
        monkeypatch.setattr(FolderSortRuleDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
        dialog.add_path(first)
        assert dialog.rules() == original
        item = dialog.list.topLevelItem(0)
        dialog.list.setCurrentItem(item)
        def edit(detail):
            detail.path_edit.setText(str(second))
            detail.sort_combo.setCurrentIndex(0)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(FolderSortRuleDialog, "exec", edit)
        dialog.edit_selected()
        assert len(dialog.rules()) == 1
        assert dialog.rules()[0]["mode"] == "default"
        applied = []
        dialog.apply_requested.connect(applied.append)
        dialog.reject()
        assert applied == []
    finally:
        dialog.close()


def test_browser_rules_manual_override_return_cache_and_delete(tmp_path, qapp):
    from tests.test_browser_window import make_config, write_image, finish_scan
    from app.browser_window import BrowserWindow

    root, child = tmp_path / "漫画", tmp_path / "漫画" / "作品A"
    for folder in (root, child):
        for name in ("1.jpg", "2.jpg", "10.jpg"):
            write_image(folder / name)
    config = make_config(tmp_path, root)
    config.apply({SORT_RULES_KEY: [rule(root, "descendants")]})
    window = BrowserWindow(config_manager=config)
    def names():
        return [item.display_name for item in window.items if item.kind.value == "image"]
    try:
        finish_scan(window, qapp)
        assert names() == ["1.jpg", "2.jpg", "10.jpg"]
        assert window.navigate_to(child)
        finish_scan(window, qapp)
        assert names() == ["10.jpg", "2.jpg", "1.jpg"]
        assert "<指定>" in window.browser_sort_key_combo.currentText()
        combo = window.browser_sort_key_combo
        combo.setCurrentIndex(combo.findData("name:ascending"))
        combo.activated.emit(combo.currentIndex())
        assert names() == ["1.jpg", "2.jpg", "10.jpg"]
        assert "一時変更" in combo.currentText()
        assert config.get("browser_sort_order") == "ascending"
        assert config.get(SORT_RULES_KEY)[0]["sort_order"] == "descending"
        window._save_folder_sort_rules(config.get(SORT_RULES_KEY))
        assert names() == ["10.jpg", "2.jpg", "1.jpg"]
        combo.setCurrentIndex(combo.findData("name:ascending"))
        combo.activated.emit(combo.currentIndex())
        window.refresh_current_folder()
        finish_scan(window, qapp)
        assert names() == ["1.jpg", "2.jpg", "10.jpg"]
        window.navigate_to(root)
        finish_scan(window, qapp)
        window.navigate_to(child)
        finish_scan(window, qapp)
        assert names() == ["10.jpg", "2.jpg", "1.jpg"]
        config.apply({SORT_RULES_KEY: []})
        finish_scan(window, qapp)
        assert names() == ["1.jpg", "2.jpg", "10.jpg"]
        assert "<指定>" not in combo.currentText()
        menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "お気に入り")
        assert [a.text() for a in menu.actions()] == ["現在のフォルダを追加", "並び順変更指定…"]
    finally:
        window.close()
        qapp.processEvents()


def test_rule_change_restarts_pending_scan_and_rejects_stale_result(tmp_path, qapp, monkeypatch):
    from tests.test_browser_async_navigation import make_committed_window, entry
    from app.browser_scanner import BrowserScanCompleted
    from app.browser_model import browser_item_from_scan_entry

    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "シリーズ"
    target.mkdir()
    try:
        window.config.apply({SORT_RULES_KEY: [rule(target)]})
        assert window.navigate_to(target)
        old = scanner.requests[-1]
        assert old.sort_policy.sort_order is BrowserSortOrder.DESCENDING
        window.config.apply({SORT_RULES_KEY: [rule(target, order="ascending")]})
        new = scanner.requests[-1]
        assert new.generation != old.generation
        items = tuple(browser_item_from_scan_entry(entry(target / name)) for name in ("2.jpg", "10.jpg"))
        window._on_scan_completed(BrowserScanCompleted(
            old.path, old.generation, 2, prepared_items=tuple(reversed(items)), sort_policy=old.sort_policy,
        ))
        assert window.current_path != target
        monkeypatch.setattr(BrowserSortPolicy, "sorted_items", lambda *_a, **_k: pytest.fail("Prepared listing was sorted on GUI thread"))
        window._on_scan_completed(BrowserScanCompleted(
            new.path, new.generation, 2, prepared_items=items, sort_policy=new.sort_policy,
        ))
        assert window.current_path == target
        assert [item.display_name for item in window.items] == ["2.jpg", "10.jpg"]
    finally:
        window.close()


def test_viewer_adjacent_books_follow_folder_rule(tmp_path, qapp):
    from tests.test_application_controller import (
        make_controller, write_archive, finish_viewer_open, close_controller,
    )
    root = tmp_path / "シリーズ"
    for name in ("1.cbz", "2.cbz", "10.cbz"):
        write_archive(root / name)
    controller = make_controller(tmp_path, qapp)
    try:
        controller.config.apply({SORT_RULES_KEY: [rule(root)]})
        browser = controller.create_browser_window()
        browser.navigate_to(root)
        assert browser.wait_for_scan()
        viewer = controller.open_path(root / "10.cbz", browser_snapshot=browser.adjacent_book_snapshot(root))
        finish_viewer_open(qapp, viewer)
        assert controller.open_adjacent_book(viewer, 1) == "opened"
        finish_viewer_open(qapp, viewer)
        assert viewer.book_session.current_path == root / "2.cbz"
        assert controller.open_adjacent_book(viewer, -1) == "opened"
        finish_viewer_open(qapp, viewer)
        assert viewer.book_session.current_path == root / "10.cbz"
    finally:
        close_controller(controller, qapp)


def test_menu_editor_save_cancel_and_reload(tmp_path, qapp, monkeypatch):
    from tests.test_browser_window import make_config, write_image, finish_scan
    from app.browser_window import BrowserWindow

    root = tmp_path / "作品"
    write_image(root / "1.jpg")
    config = make_config(tmp_path, root)
    window = BrowserWindow(config_manager=config)
    try:
        finish_scan(window, qapp)
        def save(dialog):
            assert dialog.current_path == str(root)
            dialog.apply_requested.emit([rule(root, "descendants")])
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(FolderSortDialog, "exec", save)
        window.folder_sort_rules_action.trigger()
        saved = config.get(SORT_RULES_KEY)
        assert saved == normalize_folder_sort_rules([rule(root, "descendants")])
        restored = ConfigManager(config.path)
        restored.load()
        assert restored.get(SORT_RULES_KEY) == saved
        monkeypatch.setattr(FolderSortDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
        window.folder_sort_rules_action.trigger()
        assert config.get(SORT_RULES_KEY) == saved
        assert list(root.iterdir()) == [root / "1.jpg"]
    finally:
        window.close()
        qapp.processEvents()


def test_browser_sibling_navigation_uses_parent_rule(tmp_path, qapp, monkeypatch):
    from unittest.mock import Mock
    from tests.test_application_controller import make_controller, write_image, close_controller

    parent, current = tmp_path / "漫画", tmp_path / "漫画" / "作品A"
    write_image(current / "1.jpg")
    controller = make_controller(tmp_path, qapp)
    try:
        controller.config.apply({SORT_RULES_KEY: [
            rule(parent, order="ascending"), rule(current, order="descending"),
        ]})
        browser = controller.create_browser_window()
        browser.navigate_to(current)
        assert browser.wait_for_scan()
        assert browser.browser_sort_order is BrowserSortOrder.DESCENDING
        search = Mock(return_value=True)
        monkeypatch.setattr(controller.adjacent_book_search, "search", search)
        assert controller.handle_browser_folder_navigation(browser, 1) == "searching"
        assert search.call_args.args[0].sort_order == "ascending"
    finally:
        close_controller(controller, qapp)
