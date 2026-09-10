from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import random
import os

import pytest
from PySide6.QtCore import QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleFactory, QStyleOptionViewItem

from app.adjacent_book_search import (
    AdjacentBookFileSystem, AdjacentBookSearchRequest, SIBLING_FOLDERS,
    _FileSystemEntry, _SearchWorker,
)
from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_scanner import BrowserScanRequest, scan_directory, scan_entry_from_dir_entry
from app.browser_sort import (
    BROWSER_SORT_CHOICES, MAX_BROWSER_RANDOM_SEED, BrowserSortKey as Key,
    BrowserSortOrder as Order, BrowserSortPolicy, browser_sort_choice_index,
    creation_time_ns,
)
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.zippla_filename_metadata import ZipPlaFilenameMetadata


def item(root, name, **kwargs):
    kind = kwargs.pop("kind", BrowserItemKind.IMAGE)
    return BrowserItem(ZipPlaFilenameMetadata.parse(name).display_name,
                       root / name, kind, None, extension=Path(name).suffix, **kwargs)


@pytest.mark.parametrize("key", list(Key))
@pytest.mark.parametrize("order", list(Order))
def test_all_keys_orders_and_seed_persist(tmp_path, key, order):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"browser_sort_key": key.value, "browser_sort_order": order.value,
                  "browser_random_seed": MAX_BROWSER_RANDOM_SEED}, save=True)
    loaded = ConfigManager(config.path).load()
    assert loaded["browser_sort_key"] == key.value
    assert loaded["browser_sort_order"] == order.value
    assert loaded["browser_random_seed"] == MAX_BROWSER_RANDOM_SEED


@pytest.mark.parametrize("seed", [None, True, -1, 2**64, "12", 1.5])
def test_legacy_and_invalid_settings_have_stable_seed(tmp_path, seed):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    assert config.get("browser_random_seed") == 0
    config.apply({"browser_sort_key": "nonsense", "browser_sort_order": "nonsense",
                  "browser_random_seed": seed}, save=True)
    assert config.get("browser_sort_key") == "name"
    assert config.get("browser_sort_order") == "ascending"
    assert ConfigManager(config.path).load()["browser_random_seed"] == 0


@pytest.mark.parametrize("key,attribute", [
    (Key.CREATED_TIME, "created_time_ns"), (Key.ACCESSED_TIME, "accessed_time_ns"),
    (Key.MODIFIED_TIME, "modified_time_ns"), (Key.FILE_SIZE, "file_size"),
])
@pytest.mark.parametrize("order", list(Order))
def test_dates_sizes_unknowns_and_natural_ties(tmp_path, key, attribute, order):
    values = [item(tmp_path, name, **{attribute: value}) for name, value in
              [("本10.jpg", 20), ("本2.jpg", 20), ("古い.jpg", 10), ("不明.jpg", None)]]
    expected = ["不明.jpg", "古い.jpg", "本2.jpg", "本10.jpg"]
    if order is Order.DESCENDING:
        expected = ["本2.jpg", "本10.jpg", "古い.jpg", "不明.jpg"]
    assert [i.display_name for i in BrowserSortPolicy(key, order, False).sorted_items(values)] == expected


@pytest.mark.parametrize("order", list(Order))
def test_type_extensions_unrated_and_folder_partition(tmp_path, order):
    values = [item(tmp_path, name, rating=rating) for name, rating in
              [("a.zip", 3), ("b.png", None), ("c.jpg", 1), ("d.xyz", None)]]
    folder = item(tmp_path, "Folder.Name", kind=BrowserItemKind.FOLDER)
    for key in Key:
        assert BrowserSortPolicy(key, order, True, 77).sorted_items([*values, folder])[0] == folder
    typed = BrowserSortPolicy(Key.ITEM_TYPE, order, False).sorted_items(values)
    assert [i.path.suffix for i in typed] == sorted([".zip", ".png", ".jpg", ".xyz"], reverse=order is Order.DESCENDING)
    rated = BrowserSortPolicy(Key.RATING, order, False).sorted_items(values)
    assert [i.rating for i in rated] == ([1, 3, None, None] if order is Order.ASCENDING else [3, 1, None, None])


def test_random_is_stable_for_batches_filters_metadata_and_rating_rename(tmp_path, monkeypatch):
    values = [item(tmp_path, f"本{i}.jpg", rating=i % 5 + 1) for i in range(90)]
    policy = BrowserSortPolicy(Key.RANDOM, random_seed=12345)
    expected = policy.sorted_items(values)
    shuffled = list(values)
    random.Random(17).shuffle(shuffled)
    # Sort keys may not stat, decode, or consult rendered text.
    monkeypatch.setattr(Path, "stat", lambda *a, **k: pytest.fail("sort performed I/O"))
    assert policy.sorted_items(shuffled) == expected
    assert policy.sorted_items(values[::3]) == [i for i in expected if i in values[::3]]
    for end in (10, 35, 90):
        assert policy.sorted_items(shuffled[:end]) == [i for i in expected if i in shuffled[:end]]
    mutated = [replace(i, page_count=42, accessed_time_ns=987, display_name="untrusted rendered text") for i in values]
    assert [i.path for i in policy.sorted_items(mutated)] == [i.path for i in expected]
    renamed = [replace(i, path=ZipPlaFilenameMetadata.parse(i.path).with_rating(4).serialized_path()) for i in values]
    assert [ZipPlaFilenameMetadata.parse(i.path).display_name for i in policy.sorted_items(renamed)] == [i.display_name for i in expected]
    assert any(BrowserSortPolicy(Key.RANDOM, random_seed=seed).sorted_items(values) != expected for seed in range(5))
    assert BrowserSortPolicy(Key.RANDOM, Order.DESCENDING, random_seed=12345).sorted_items(values) == expected


def test_random_hash_collision_and_duplicate_stripped_names_are_deterministic(tmp_path, monkeypatch):
    import app.browser_sort as module
    monkeypatch.setattr(module.hashlib, "sha256", lambda *_: SimpleNamespace(digest=lambda: b"same"))
    values = [item(tmp_path, n) for n in ["B.jpg", "A {zpi$r=4}.jpg", "A {zpi$r=2}.jpg", "Folder.Name"]]
    policy = BrowserSortPolicy(Key.RANDOM, random_seed=34)
    assert policy.sorted_items(values) == policy.sorted_items(reversed(values))
    assert policy.sorted_items(values)[-1].path.name == "Folder.Name"


@pytest.mark.parametrize("platform,attrs,expected", [
    ("nt", {"st_ctime_ns": 31}, 31),
    ("nt", {"st_birthtime_ns": 32, "st_ctime_ns": 31}, 32),
    ("posix", {"st_ctime_ns": 31}, None),
    ("posix", {"st_birthtime_ns": 32, "st_ctime_ns": 31}, 32),
    ("nt", {}, None),
])
def test_creation_time_python_versions_and_platforms(platform, attrs, expected):
    assert creation_time_ns(SimpleNamespace(**attrs), platform=platform) == expected


class Entry:
    def __init__(self, root, name, birth, access):
        self.name, self.path = name, str(root / name)
        self.calls = 0
        self.value = SimpleNamespace(st_birthtime_ns=birth, st_atime_ns=access, st_mtime_ns=10, st_size=20)

    def stat(self, *, follow_symlinks):
        assert follow_symlinks is False
        self.calls += 1
        return self.value

    def is_dir(self, **_):
        return False

    def is_file(self, **_):
        return True


def test_scan_prepared_metadata_and_timestamp_only_reconcile(tmp_path, monkeypatch, qapp):
    import app.browser_scanner as scanner
    entries = [Entry(tmp_path, "a.jpg", 40, 10), Entry(tmp_path, "b.jpg", 20, 30)]
    monkeypatch.setattr(scanner.os, "scandir", lambda _: nullcontext(entries))
    policy = BrowserSortPolicy(Key.CREATED_TIME, random_seed=87)
    batches = []
    result = scan_directory(BrowserScanRequest(str(tmp_path), 1, sort_policy=policy), Event(), batches.append)
    assert result.sort_policy == policy
    assert [i.created_time_ns for i in result.prepared_items] == [20, 40]
    assert [i.accessed_time_ns for i in result.prepared_items] == [30, 10]
    assert all(e.calls == 1 for e in entries)
    model = BrowserItemModel()
    model.configure_sort(Key.CREATED_TIME, Order.ASCENDING, True, 87)
    model.set_sorted_items(result.prepared_items)
    previous = model.source_items
    entries[0].value.st_birthtime_ns = 5
    updated = scan_directory(BrowserScanRequest(str(tmp_path), 2, sort_policy=policy), Event(), lambda _: None)
    # Browser's refresh gate compares dataclass tuples, so timestamp-only changes are visible.
    assert updated.prepared_items != previous
    model.set_sorted_items(updated.prepared_items, preserve_thumbnails=True)
    assert model.source_items[0].path.name == "a.jpg"
    assert all(e.calls == 2 for e in entries)


@pytest.fixture
def browser(tmp_path, qapp, monkeypatch):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    window.current_path = tmp_path
    try:
        with monkeypatch.context() as owned_patches:
            owned_patches.setattr(window, "_schedule_thumbnail_requests", lambda *_a, **_k: None)
            window.resize(900, 600)
            window.show()
            yield window
        # Restore bound QObject methods before WA_DeleteOnClose destroys them.
    finally:
        window.close()
        qapp.processEvents()


def activate(combo, key, order=Order.ASCENDING):
    index = browser_sort_choice_index(key, order)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)  # Real signal, fake user invocation; no native input.


@pytest.mark.parametrize("style", ["Fusion", "windows11"])
def test_sort_popup_painted_regions_have_no_inter_row_gaps(browser, qapp, style):
    if style.lower() not in [name.lower() for name in QStyleFactory.keys()]:
        pytest.skip(f"{style} style unavailable")
    previous_style = qapp.style().objectName()
    combo = browser.browser_sort_key_combo
    try:
        qapp.setStyle(style)
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        height, width = view.sizeHintForRow(0), view.viewport().width()
        dpr = view.devicePixelRatioF()
        canvas = QImage(round(width * dpr), round(3 * height * dpr),
                        QImage.Format.Format_ARGB32_Premultiplied)
        canvas.setDevicePixelRatio(dpr)
        base = view.palette().color(QPalette.ColorRole.Base)
        highlight = view.palette().color(QPalette.ColorRole.Highlight)
        canvas.fill(base)
        painter = QPainter(canvas)
        try:
            for row, state in enumerate((QStyle.StateFlag.State_Selected,
                                         QStyle.StateFlag.State_MouseOver,
                                         QStyle.StateFlag.State_None)):
                option = QStyleOptionViewItem()
                option.initFrom(view)
                option.widget = view
                option.rect = QRect(0, row * height, width, height)
                option.state = (QStyle.StateFlag.State_Enabled
                                | QStyle.StateFlag.State_Active | state)
                combo.itemDelegate().paint(painter, option, combo.model().index(row, 0))
        finally:
            painter.end()
        # Sample painted pixels away from text, including the outer edges:
        # adjacent visualRects alone missed Windows 11's inset rounded fills.
        for x in (canvas.width() - 1, canvas.width() - round(10 * dpr)):
            for y in range(round(2 * height * dpr)):
                assert canvas.pixelColor(x, y) == highlight, (style, x, y)
            for y in range(round(2 * height * dpr), canvas.height()):
                assert canvas.pixelColor(x, y) == base, (style, x, y)
        # The real selected popup row also paints right up to both boundaries.
        index = combo.model().index(combo.currentIndex(), 0)
        rect = view.visualRect(index)
        actual = view.viewport().grab().toImage()
        x = actual.width() - round(10 * dpr)
        for y in (round(rect.top() * dpr), round((rect.bottom() + 1) * dpr) - 1):
            assert actual.pixelColor(x, y) == highlight
    finally:
        combo.hidePopup()
        qapp.setStyle(previous_style)
        qapp.processEvents()


@pytest.mark.parametrize("style", ["Fusion", "windows11"])
@pytest.mark.parametrize("points", [9, 12])
def test_sort_popup_zero_padding_preserves_glyphs(browser, qapp, style, points):
    if style.lower() not in [name.lower() for name in QStyleFactory.keys()]:
        pytest.skip(f"{style} style unavailable")
    previous_style = qapp.style().objectName()
    combo = browser.browser_sort_key_combo
    original_font = combo.font()
    try:
        qapp.setStyle(style)
        font = combo.font()
        font.setPointSize(points)
        combo.setFont(font)
        qapp.processEvents()  # Settle the font-dependent closed-control width.
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        height, width = view.sizeHintForRow(0), view.viewport().width()
        dpr = view.devicePixelRatioF()
        assert height == combo.fontMetrics().height()
        assert view.verticalScrollBar().maximum() == 0
        assert combo.height() == 24

        def text_pixels(row, row_height, state):
            canvas = QImage(round(width * dpr), round(row_height * dpr),
                            QImage.Format.Format_ARGB32_Premultiplied)
            canvas.setDevicePixelRatio(dpr)
            role = QPalette.ColorRole.Highlight if state else QPalette.ColorRole.Base
            background = view.palette().color(role)
            canvas.fill(background)
            painter = QPainter(canvas)
            try:
                option = QStyleOptionViewItem()
                option.initFrom(view)
                option.widget = view
                option.rect = QRect(0, 0, width, row_height)
                option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active | state
                combo.itemDelegate().paint(painter, option, combo.model().index(row, 0))
            finally:
                painter.end()
            pixels = [(x, y, canvas.pixel(x, y))
                      for y in range(canvas.height()) for x in range(canvas.width())
                      if canvas.pixelColor(x, y) != background]
            assert pixels
            top = min(y for x, y, color in pixels)
            return [(x, y - top, color) for x, y, color in pixels]

        for row in range(combo.count()):
            assert view.viewport().rect().contains(view.visualRect(combo.model().index(row, 0)))
            for state in (QStyle.StateFlag.State_Selected,
                          QStyle.StateFlag.State_MouseOver, QStyle.StateFlag.State_None):
                # Normalize baseline position, not glyph size. Extra reference
                # height exposes any strokes clipped by the compact row.
                assert text_pixels(row, height, state) == text_pixels(row, height + 8, state)
        print(f"Glyphs {style} {points}pt DPR={dpr}: row={height} "
              f"popup={view.window().height()} visible=15 scroll_max=0; 45 text masks intact")
    finally:
        combo.hidePopup()
        combo.setFont(original_font)
        qapp.setStyle(previous_style)
        qapp.processEvents()


@pytest.mark.parametrize("style", ["Fusion", "windows11"])
@pytest.mark.parametrize("larger_font", [False, True])
def test_sort_popup_compact_rows_at_process_dpi(browser, qapp, style, larger_font):
    if style.lower() not in [name.lower() for name in QStyleFactory.keys()]:
        pytest.skip(f"{style} style unavailable")
    previous_style = qapp.style().objectName()
    combo = browser.browser_sort_key_combo
    if larger_font:
        font = combo.font()
        font.setPointSize(12)
        combo.setFont(font)
    original_font = combo.font()
    try:
        qapp.setStyle(style)
        qapp.processEvents()
        assert browser.devicePixelRatioF() == pytest.approx(
            float(os.environ.get("QT_SCALE_FACTOR", "1"))
        )
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        expected_height = combo.fontMetrics().height()
        assert combo.maxVisibleItems() == combo.count() == 15
        assert combo.height() == 24
        assert combo.font() == original_font
        assert view.spacing() == 0
        previous_rect = None
        for row in range(combo.count()):
            index = combo.model().index(row, 0)
            rect = view.visualRect(index)
            assert rect.height() == expected_height
            assert view.viewport().rect().contains(rect)
            if previous_rect is not None:
                assert rect.top() == previous_rect.bottom() + 1
            previous_rect = rect
            option = QStyleOptionViewItem()
            option.initFrom(view)
            combo.itemDelegate().initStyleOption(option, index)
            option.rect = rect
            text_rect = view.style().subElementRect(
                QStyle.SubElement.SE_ItemViewItemText, option, view
            )
            assert text_rect.height() >= option.fontMetrics.height()
        assert view.verticalScrollBar().maximum() == 0
        assert combo.itemText(combo.count() - 1) == "ランダム"
        # Exercise the actual popup's mouse selection with synthetic Qt input.
        target = combo.model().index(1, 0)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         pos=view.visualRect(target).center())
        assert combo.currentIndex() == 1
        assert browser.config.get("browser_sort_order") == "descending"
        combo.hidePopup()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() == 2
        assert browser.config.get("browser_sort_key") == BROWSER_SORT_CHOICES[2][1]
        assert ConfigManager(browser.config.path).load()["browser_sort_key"] == BROWSER_SORT_CHOICES[2][1]
        # Render the real control/popup offscreen as well as checking geometry.
        combo.showPopup()
        qapp.processEvents()
        assert not combo.grab().isNull()
        assert not view.window().grab().isNull()
        # Reopening on Random must still expose the first and last choices.
        combo.hidePopup()
        activate(combo, Key.RANDOM)
        combo.showPopup()
        qapp.processEvents()
        assert view.verticalScrollBar().maximum() == 0
        assert all(view.viewport().rect().contains(
            view.visualRect(combo.model().index(row, 0))
        ) for row in range(combo.count()))
        print(f"{style}: DPR={browser.devicePixelRatioF()} closed={combo.height()} "
              f"font={combo.fontMetrics().height()} row={expected_height} "
              f"popup={view.window().height()} visible={combo.count()} "
              f"scroll_max={view.verticalScrollBar().maximum()}")
    finally:
        combo.hidePopup()
        qapp.setStyle(previous_style)
        qapp.processEvents()


@pytest.mark.parametrize("style", ["Fusion", "windows11"])
def test_sort_popup_retains_native_screen_bounding(browser, qapp, style):
    if style.lower() not in [name.lower() for name in QStyleFactory.keys()]:
        pytest.skip(f"{style} style unavailable")
    previous_style = qapp.style().objectName()
    combo = browser.browser_sort_key_combo
    try:
        qapp.setStyle(style)
        # Force content taller than the available screen, without touching
        # native display settings or changing the closed Browser chrome.
        font = combo.view().font()
        font.setPixelSize(combo.screen().availableGeometry().height() // 8)
        combo.view().setFont(font)
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        assert view.sizeHintForRow(0) * combo.count() > combo.screen().availableGeometry().height()
        popup_rect = view.window().geometry()
        if style == "Fusion" and qapp.platformName() == "offscreen":
            # Fusion's menu-style popup retains a desktop-edge offset even
            # when its height reaches the offscreen screen limit. Test its
            # height cap/reachable rows; Windows-style placement is strict.
            assert popup_rect.height() <= combo.screen().availableGeometry().height()
        else:
            assert combo.screen().availableGeometry().contains(popup_rect)
        assert view.verticalScrollBar().maximum() > 0
        assert combo.height() == 24
        # Both endpoints remain reachable through Qt's existing scrolling.
        for row in (combo.count() - 1, 0):
            index = combo.model().index(row, 0)
            view.setCurrentIndex(index)
            view.scrollTo(index)
            qapp.processEvents()
            assert view.viewport().rect().contains(view.visualRect(index))
        print(f"bounded {style}: DPR={browser.devicePixelRatioF()} "
              f"screen={combo.screen().availableGeometry().height()} "
              f"row={view.sizeHintForRow(0)} popup={view.window().height()} "
              f"scroll_max={view.verticalScrollBar().maximum()}")
    finally:
        combo.hidePopup()
        qapp.setStyle(previous_style)
        qapp.processEvents()


def test_one_selector_atomic_activation_settings_and_seed_lifetime(browser, qapp):
    combo = browser.browser_sort_key_combo
    assert combo.count() == 15 and combo.itemText(14) == "ランダム"
    assert [combo.itemText(i) for i in range(15)] == [c[0] for c in BROWSER_SORT_CHOICES]
    assert not hasattr(browser, "browser_sort_order_combo")
    changes, resets = [], []
    browser.config.settings_changed.connect(changes.append)
    browser.item_model.modelReset.connect(lambda: resets.append(True))
    activate(combo, Key.CREATED_TIME, Order.DESCENDING)
    assert len(changes) == len(resets) == 1
    assert changes[0]["browser_sort_key"] == "created_time"
    assert changes[0]["browser_sort_order"] == "descending"
    for _ in range(2):
        seed = browser.browser_random_seed
        count = len(resets)
        activate(combo, Key.RANDOM)
        assert browser.browser_random_seed != seed
        assert len(resets) == count + 1
    seed = browser.browser_random_seed
    count = len(resets)
    browser._sync_browser_controls()
    combo.setCurrentIndex(0)
    browser._sync_browser_controls()
    combo.showPopup()
    combo.hidePopup()
    assert browser.browser_random_seed == seed and len(resets) == count
    assert ConfigManager(browser.config.path).load()["browser_random_seed"] == seed
    dialog = SettingsDialog(browser.config)
    try:
        dialog.show()
        assert [dialog.browser_sort_key_combo.itemText(i) for i in range(15)] == [c[0] for c in BROWSER_SORT_CHOICES]
        assert dialog.browser_sort_key_combo.currentIndex() == 14
        assert not hasattr(dialog, "browser_sort_order_combo")
        dialog.apply_settings()
        assert browser.browser_random_seed == seed and len(resets) == count
        activate(dialog.browser_sort_key_combo, Key.RANDOM)
        assert browser.browser_random_seed == seed  # Draft respects Cancel/Apply.
        dialog.apply_settings()
        assert browser.browser_random_seed != seed and len(resets) == count + 1
    finally:
        dialog.reject()
        qapp.processEvents()


@pytest.mark.parametrize("retained_by_filter", [True, False], ids=["retained", "excluded"])
def test_random_selection_filter_rename_and_open_time_snapshot(
    browser, qapp, monkeypatch, retained_by_filter,
):
    values = [item(browser.current_path, f"本{i}.jpg", rating=4) for i in range(150)]
    browser.item_model.set_items(values)
    activate(browser.browser_sort_key_combo, Key.RANDOM)
    qapp.processEvents()
    # Select a known identity, not a random row whose membership in 本1 varies
    # with the seed/temp parent path. Exercise both filter-selection contracts.
    selected = values[110 if retained_by_filter else 80]
    index = browser.item_model.index(browser.item_model.row_for_path(selected.path), 0)
    browser.list_view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    browser.list_view.scrollTo(index)
    qapp.processEvents()
    captured = browser._capture_list_view_state()
    old = browser.adjacent_book_snapshot(browser.current_path)
    activate(browser.browser_sort_key_combo, Key.RANDOM)
    qapp.processEvents()
    assert browser._capture_list_view_state().selected_paths == (str(selected.path),)
    new = browser.adjacent_book_snapshot(browser.current_path)
    assert old.viewer_paths != new.viewer_paths
    assert old.entries != new.entries
    assert old.image_paths == tuple(e.absolute_path for e in old.entries)
    assert old.adjacent_viewer_path(old.viewer_paths[10], 1)[1] == old.viewer_paths[11]
    current = browser.list_view.currentIndex()
    assert browser.list_view.visualRect(current).intersects(browser.list_view.viewport().rect())
    assert new.sort_identity != old.sort_identity
    stable = tuple(i.path for i in browser.items)
    seed = browser.browser_random_seed

    with monkeypatch.context() as restore_patches:
        restored_states = []
        restore = browser._restore_list_view_state

        def observe_restore(state):
            restore(state)
            restored_states.append(state)

        restore_patches.setattr(browser, "_restore_list_view_state", observe_restore)

        def filter_and_settle(state):
            before = len(restored_states)
            assert browser._set_browser_filter(state)
            # The production authority restores now and once after layout. Observe
            # that queued completion before issuing another user/test operation.
            assert len(restored_states) == before + 1
            immediate = restored_states[-1]
            qapp.processEvents()
            assert len(restored_states) == before + 2
            assert restored_states[-1] is immediate

        filter_and_settle(BrowserFilterState.normalized(search_text="本1"))
        assert tuple(i.path for i in browser.items) == tuple(p for p in stable if "本1" in p.name)
        expected_selection = (str(selected.path),) if retained_by_filter else ()
        assert browser._capture_list_view_state().selected_paths == expected_selection
        filter_and_settle(BrowserFilterState())
        assert tuple(i.path for i in browser.items) == stable
        assert browser._capture_list_view_state().selected_paths == expected_selection
        browser.item_model.set_items(reversed(values), preserve_thumbnails=True)
        qapp.processEvents()
        assert tuple(i.path for i in browser.items) == stable
        row = browser.item_model.row_for_path(selected.path)
        renamed = ZipPlaFilenameMetadata.parse(selected.path).with_rating(2).serialized_path()
        # Exercise the existing Browser completion/relocation authority, not a new
        # selection path; file-operation tests own physical rename safety.
        index = browser.item_model.index(row, 0)
        browser.list_view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        browser.list_view.scrollTo(index)
        qapp.processEvents()
        state = browser._capture_list_view_state()
        assert state.selected_paths == (str(selected.path),)
        assert state.current_path == str(selected.path)
        browser._finalize_rating_batch(SimpleNamespace(
            replacements=((selected.path, str(renamed), 2),), failures=[], view_state=state,
        ))
        qapp.processEvents()
        assert browser.item_model.row_for_path(renamed) == row
        restored = browser._capture_list_view_state()
        assert restored.selected_paths == (str(renamed),)
        assert abs(restored.vertical_scroll - state.vertical_scroll) <= 1
        assert browser.browser_random_seed == seed
        assert captured.current_path == str(selected.path)


def test_sibling_folder_consumers_accept_dates_ratings_and_seed(tmp_path):
    entries = tuple(_FileSystemEntry(str(tmp_path / name), name, True, False, "",
                                     created_time_ns=birth, accessed_time_ns=access)
                    for name, birth, access in [("Folder.Name", 30, 10), ("本 {zpi$r=4}", 10, 30), ("本2", 20, 20)])
    for key in Key:
        request = AdjacentBookSearchRequest(1, entries[0].path, 1, False, None, 1,
                                            SIBLING_FOLDERS, key.value, "ascending", True, 98)
        worker = _SearchWorker(request, AdjacentBookFileSystem(), None, Event())
        expected = tuple(e.path for e in BrowserSortPolicy(key, random_seed=98).sorted_items(entries))
        assert worker._collect_candidates(entries) == expected


def test_random_refresh_back_forward_and_new_items_keep_seed(browser, tmp_path, qapp):
    parent = tmp_path / "parent"
    parent.mkdir()
    for i in range(50):
        (parent / f"Folder{i}.Name").mkdir()
    browser.config.apply({"browser_sort_key": "random", "browser_random_seed": 90210}, save=True)

    def finish():
        assert browser.wait_for_scan()
        qapp.processEvents()

    assert browser.set_current_folder(parent)
    finish()
    original = tuple(i.path for i in browser.items)
    selected = original[35]
    browser.list_view.setCurrentIndex(browser.item_model.index(35, 0))
    browser.list_view.scrollTo(browser.list_view.currentIndex())
    qapp.processEvents()
    state = browser._capture_list_view_state()
    assert browser.set_current_folder(selected)
    finish()
    assert browser.go_back()
    finish()
    assert tuple(i.path for i in browser.items) == original
    assert browser._capture_list_view_state().selected_paths == state.selected_paths
    assert browser.go_forward()
    finish()
    assert browser.current_path == selected
    assert browser.go_back()
    finish()
    added = parent / "New.Folder"
    added.mkdir()
    assert browser.refresh_current_folder()
    finish()
    assert tuple(i.path for i in browser.items if i.path != added) == original
    assert browser.browser_random_seed == 90210
    assert ConfigManager(browser.config.path).load()["browser_random_seed"] == 90210


def test_settings_cancel_does_not_commit_draft_random(browser):
    before = browser.config.get("browser_random_seed")
    dialog = SettingsDialog(browser.config)
    activate(dialog.browser_sort_key_combo, Key.RANDOM)
    assert dialog.values()["browser_random_seed"] != before
    dialog.reject()
    assert browser.config.get("browser_random_seed") == before


def test_legacy_random_direction_unchanged_settings_do_not_reorder(browser):
    browser.config.apply({"browser_sort_key": "random", "browser_sort_order": "descending",
                          "browser_random_seed": 75})
    resets = []
    browser.item_model.modelReset.connect(lambda: resets.append(True))
    dialog = SettingsDialog(browser.config)
    dialog.apply_settings()
    assert not resets
    assert browser.browser_random_seed == 75
    assert browser.config.get("browser_sort_order") == "descending"
    dialog.reject()


@pytest.mark.parametrize("birth_attribute", ["st_ctime_ns", "st_birthtime_ns"])
def test_scanner_reuses_windows_stat_version_shape(tmp_path, birth_attribute):
    entry = Entry(tmp_path, "本.jpg", 30, 40)
    del entry.value.st_birthtime_ns
    setattr(entry.value, birth_attribute, 31)
    scanned = scan_entry_from_dir_entry(entry)
    assert scanned.created_time_ns == 31
    assert scanned.accessed_time_ns == 40
    assert entry.calls == 1


def test_model_incremental_random_order_matches_full_snapshot(tmp_path, qapp):
    values = [item(tmp_path, f"本{i}.jpg") for i in range(70)]
    random.Random(987).shuffle(values)
    model = BrowserItemModel()
    model.configure_sort(Key.RANDOM, Order.ASCENDING, True, 17)
    model.begin_directory_scan(generation=7)
    expected = BrowserSortPolicy(Key.RANDOM, random_seed=17).sorted_items(values)
    for start in range(0, 70, 10):
        model.append_scan_batch(values[start:start + 10], generation=7)
        assert list(model.items) == [i for i in expected if i in values[:start + 10]]
    assert model.finish_directory_scan(generation=7)
    assert list(model.items) == expected
