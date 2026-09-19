from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMenu, QPushButton

from app.browser_filter import BrowserFilterState
from app.browser_tag_quick_filters import BrowserTagQuickFilterStrip
from app.browser_tag_dialogs import ItemTagsDialog, TagSelectionMenu, TagManagerDialog, TagFilterDialog
from app.browser_tags import filename_tags
from app.i18n import install_ui_language
from tests.test_browser_tags import library, load, REGISTRY
from tests.test_application_controller import write_image, wait_until


def edit_with_clicks(browser, clicks, cancel=False):
    errors = []
    def operate():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ItemTagsDialog)
            for name, repetitions in clicks:
                check = dialog.checks[name]
                for _ in range(repetitions):
                    QTest.mouseClick(check, Qt.MouseButton.LeftButton, pos=QPoint(8, check.height() // 2))
            button = QDialogButtonBox.StandardButton.Cancel if cancel else QDialogButtonBox.StandardButton.Apply
            QTest.mouseClick(dialog.buttons.button(button), Qt.MouseButton.LeftButton)
        except Exception as exc:
            errors.append(exc)
            dialog.reject()
    QTimer.singleShot(0, operate)
    browser.edit_selected_tags()
    if errors:
        raise errors[0]
    assert wait_until(QApplication.instance(), lambda: browser._rating_batch is None, timeout=5)


def select_all(browser):
    browser.list_view.selectAll()


def test_single_click_apply_add_remove_and_replace_unknown(library, qapp):
    _, browser, root = library
    write_image(root / '本 {zpi$t=未知}.png')
    load(browser, root)
    select_all(browser)
    edit_with_clicks(browser, [('旧', 1)])
    assert set(filename_tags(str(next(root.iterdir())))) == {'未知', '旧'}
    select_all(browser)
    edit_with_clicks(browser, [('旧', 1), ('新', 1)])
    assert set(filename_tags(str(next(root.iterdir())))) == {'未知', '新'}
    select_all(browser)
    edit_with_clicks(browser, [('新', 1)])
    assert filename_tags(str(next(root.iterdir()))) == ('未知',)
    select_all(browser)
    edit_with_clicks(browser, [('旧', 1)], cancel=True)
    assert filename_tags(str(next(root.iterdir()))) == ('未知',)


@pytest.mark.parametrize('clicks, expected', [(0, [True, False]), (1, [False, False]), (2, [True, True]), (3, [True, False])])
def test_mixed_actual_click_cycle_apply(library, qapp, clicks, expected):
    _, browser, root = library
    write_image(root / 'a {zpi$t=旧,未知}.png')
    write_image(root / 'b {zpi$t=未知}.png')
    load(browser, root)
    select_all(browser)
    edit_with_clicks(browser, [('旧', clicks)])
    files = sorted(root.iterdir())
    assert [('旧' in filename_tags(str(path))) for path in files] == expected
    assert all('未知' in filename_tags(str(path)) for path in files)


@pytest.mark.parametrize('mode', ['outside', 'escape', 'left'])
def test_context_right_click_batch_and_escape(library, qapp, mode):
    _, browser, root = library
    write_image(root / 'a {zpi$t=旧,未知}.png')
    load(browser, root)
    browser.show()
    select_all(browser)
    qapp.processEvents()
    errors = []
    def operate():
        menu = QApplication.activePopupWidget()
        try:
            assert isinstance(menu, QMenu)
            tags = next(child for child in menu.findChildren(TagSelectionMenu))
            tags.popup(menu.mapToGlobal(QPoint(20, 20)))
            qapp.processEvents()
            for name in ('旧', '新'):
                action = next(a for a,n in tags.tag_actions.items() if n == name)
                left = mode == 'left' and name == '新'
                QTest.mouseClick(tags, Qt.MouseButton.LeftButton if left else Qt.MouseButton.RightButton,
                                 pos=tags.actionGeometry(action).center())
                assert tags.isVisible() != left
            assert filename_tags(str(next(root.iterdir()))) == ('旧', '未知')
            if mode == 'escape':
                QTest.keyClick(tags, Qt.Key.Key_Escape)
            elif mode == 'outside':
                menu.close()
        except Exception as exc:
            errors.append(exc)
            menu.close()
    QTimer.singleShot(0, operate)
    browser._show_context_menu(browser.list_view.visualRect(browser.item_model.index(0,0)).center())
    if errors:
        raise errors[0]
    assert wait_until(qapp, lambda: browser._rating_batch is None, timeout=5)
    assert set(filename_tags(str(next(root.iterdir())))) == ({'旧','未知'} if mode == 'escape' else {'新','未知'})


def test_tag_control_is_immediately_left_of_rating(library, qapp):
    _, browser, _ = library
    browser.show()
    qapp.processEvents()
    layout = browser.rating_filter_container.layout()
    tag_index = layout.indexOf(browser.tag_button)
    rating_index = layout.indexOf(browser.rating_filter_widget)
    quick_index = layout.indexOf(browser.tag_quick_filter_strip)
    assert tag_index >= 0 and rating_index == tag_index + 1
    assert quick_index == tag_index - 1
    assert layout.itemAt(quick_index).widget() is browser.tag_quick_filter_strip
    assert browser.tag_button.mapToGlobal(QPoint()).x() < browser.rating_filter_widget.mapToGlobal(QPoint()).x()


def test_quick_tag_startup_keeps_registry_order_and_long_labels_reachable(library, qapp):
    controller, browser, _ = library
    registry = [
        {'name': 'asd', 'color': '#80bfff'},
        {'name': 'bcd', 'color': '#80ff80'},
        {'name': 'aaaaaaaaaaaaaaaaaaaaaaaa', 'color': '#ffb080'},
    ]
    controller.config.apply({'browser_tag_registry': registry})
    browser.resize(700, 520)
    browser.show()
    qapp.processEvents()
    strip = browser.tag_quick_filter_strip
    visible = [
        entry['name'] for entry in registry
        if strip._buttons[entry['name']].isVisible()
    ]
    assert visible == [entry['name'] for entry in registry[:len(visible)]]
    layout_names = [
        strip._layout.itemAt(index).widget().text()
        for index in range(strip._layout.count())
        if strip._layout.itemAt(index).widget() is not strip._overflow
    ]
    assert layout_names == visible
    assert strip._registry_names_not_visible()
    assert strip._registry_names_not_visible() == tuple(
        entry['name'] for entry in registry[len(visible):]
    )
    strip_rect = strip.geometry()
    tag_rect = browser.tag_button.geometry()
    rating_rect = browser.rating_filter_widget.geometry()
    assert strip_rect.right() < tag_rect.left() < rating_rect.left()
    assert strip._overflow.isVisible() == bool(strip._registry_names_not_visible())


def test_grouped_tag_menu_clears_only_tag_filters_and_updates_immediately(library, qapp):
    controller, browser, _ = library
    registry = [
        {'name': 'asd', 'color': '#80bfff'},
        {'name': 'bcd', 'color': '#80ff80'},
    ]
    controller.config.apply(
        {'browser_tag_registry': registry, 'browser_tag_grouped': True}
    )
    browser.show()
    qapp.processEvents()
    strip = browser.tag_quick_filter_strip
    layout = browser.rating_filter_container.layout()
    assert layout.indexOf(strip) < 0
    assert not strip.isVisible()
    assert [action.text() for action in browser.tag_menu.actions() if not action.isSeparator()][:2] == ['asd', 'bcd']
    browser._set_browser_filter(
        BrowserFilterState.normalized(
            search_text='keep',
            rating_mode='at_least',
            rating_reference=3,
            include_tags=('asd',),
            exclude_tags=('bcd', '未登録'),
            tag_match='any',
        )
    )
    browser.show()
    qapp.processEvents()
    clear_action = browser._tag_clear_action
    assert clear_action.isEnabled()
    assert browser._grouped_tag_actions['asd'].isChecked()
    assert browser._grouped_tag_actions['bcd'].text() == '− bcd'
    clear_action.trigger()
    qapp.processEvents()
    state = browser.browser_filter_state
    assert state.include_tags == ()
    assert state.exclude_tags == ()
    assert state.search_text == 'keep'
    assert state.rating_reference == 3
    assert state.tag_match == 'all'
    assert not clear_action.isEnabled()
    controller.config.apply(
        {
            'browser_tag_registry': [
                {'name': 'bcd', 'color': '#80ff80'},
                {'name': 'asd', 'color': '#80bfff'},
            ]
        }
    )
    qapp.processEvents()
    assert [
        action.text() for action in browser.tag_menu.actions()
        if not action.isSeparator()
    ][:2] == ['bcd', 'asd']
    controller.config.apply({'browser_tag_grouped': False})
    qapp.processEvents()
    assert layout.indexOf(strip) >= 0
    assert [
        strip._layout.itemAt(index).widget().text()
        for index in range(strip._layout.count())
        if strip._layout.itemAt(index).widget() is not strip._overflow
    ] == ['bcd', 'asd']


def test_tag_manager_add_starts_editing_new_name(library, qapp):
    dialog = TagManagerDialog([{'name': '既存', 'color': '#80bfff'}])
    dialog.show()
    qapp.processEvents()
    add = next(button for button in dialog.findChildren(QPushButton) if button.text() == '追加')
    QTest.mouseClick(add, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    editor = dialog.table.findChild(QLineEdit)
    assert editor is not None
    assert dialog.table.currentRow() == 1
    assert editor.hasFocus()
    dialog.close()


def test_grouped_toggle_reanchors_corner_after_real_settings_apply(library, qapp):
    controller, browser, _ = library
    registry = [
        {'name': 'asd', 'color': '#80bfff'},
        {'name': 'bcd', 'color': '#80ff80'},
        {'name': 'aaaaaaaaaaaaaaaaaaaaaaaa', 'color': '#ffb080'},
    ]
    controller.config.apply({'browser_tag_registry': registry})
    browser.show()
    qapp.processEvents()

    def rect(widget):
        return widget.rect().translated(widget.mapToGlobal(QPoint()))

    def settings_rect():
        action_rect = browser.menuBar().actionGeometry(browser.settings_action)
        return action_rect.translated(browser.menuBar().mapToGlobal(QPoint()))

    def snapshot():
        container = rect(browser.rating_filter_container)
        tag = rect(browser.tag_button)
        rating = rect(browser.rating_filter_widget)
        strip = rect(browser.tag_quick_filter_strip)
        assert settings_rect().right() < container.left()
        assert tag.right() < rating.left()
        if browser.tag_quick_filter_strip.isVisible():
            assert strip.right() < tag.left()
        return container.right(), tag, rating, strip

    initial_right, initial_tag, _, _ = snapshot()
    for count in (1, 9, 10, 99):
        browser._set_browser_filter(
            BrowserFilterState.normalized(
                include_tags=tuple(f'tag-{index}' for index in range(count))
            )
        )
        qapp.processEvents()
        counted_right, counted_tag, _, _ = snapshot()
        assert abs(counted_right - initial_right) <= 1
        if count <= 99:
            assert counted_tag.width() == initial_tag.width()

    def apply_grouped(value):
        errors = []

        def interact():
            dialog = QApplication.activeModalWidget()
            try:
                dialog.browser_tag_grouped_checkbox.setChecked(value)
                QTest.mouseClick(
                    dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
                    Qt.MouseButton.LeftButton,
                )
                QTest.mouseClick(
                    dialog.button_box.button(QDialogButtonBox.StandardButton.Ok),
                    Qt.MouseButton.LeftButton,
                )
            except Exception as exc:
                errors.append(exc)
                dialog.reject()

        QTimer.singleShot(0, interact)
        browser.open_settings_dialog()
        assert not errors, errors
        qapp.processEvents()

    apply_grouped(True)
    grouped_right, grouped_tag, grouped_rating, _ = snapshot()
    assert abs(grouped_right - initial_right) <= 1
    assert not browser.tag_quick_filter_strip.isVisible()
    assert grouped_tag.right() < grouped_rating.left()

    apply_grouped(False)
    restored_right, restored_tag, restored_rating, restored_strip = snapshot()
    assert abs(restored_right - initial_right) <= 1
    assert browser.tag_quick_filter_strip.isVisible()
    assert restored_strip.right() < restored_tag.left() < restored_rating.left()

    apply_grouped(True)
    repeated_right, repeated_tag, repeated_rating, _ = snapshot()
    assert abs(repeated_right - initial_right) <= 1
    assert repeated_tag.right() < repeated_rating.left()


def test_tag_count_reservation_keeps_constrained_strip_allocation(library, qapp):
    controller, browser, _ = library
    registry = [
        {'name': 'first-long-tag', 'color': '#80bfff'},
        {'name': 'second-long-tag', 'color': '#80ff80'},
        {'name': 'third-long-tag', 'color': '#ffb080'},
    ]
    controller.config.apply({'browser_tag_registry': registry})
    browser.resize(760, 520)
    browser.show()
    qapp.processEvents()

    def rect(widget):
        return widget.rect().translated(widget.mapToGlobal(QPoint()))

    def snapshot():
        tag = rect(browser.tag_button)
        rating = rect(browser.rating_filter_widget)
        strip = rect(browser.tag_quick_filter_strip)
        visible = tuple(
            entry['name'] for entry in registry
            if browser.tag_quick_filter_strip._buttons[entry['name']].isVisible()
        )
        assert strip.right() < tag.left() < rating.left()
        return tag.left(), rating.left(), strip.width(), visible

    baseline = snapshot()
    for count in (1, 9, 10, 99):
        browser._set_browser_filter(
            BrowserFilterState.normalized(
                include_tags=tuple(f'count-{index}' for index in range(count))
            )
        )
        qapp.processEvents()
        assert snapshot() == baseline
    browser.clear_browser_filters()
    qapp.processEvents()
    assert snapshot() == baseline


def test_quick_tag_buttons_toggle_and_preserve_other_filters(library, qapp):
    controller, browser, root = library
    write_image(root / 'a {zpi$r=4;t=旧}.png')
    write_image(root / 'b {zpi$r=5;t=新}.png')
    write_image(root / 'c {zpi$r=5;t=旧,新}.png')
    load(browser, root)
    browser._set_browser_filter(
        BrowserFilterState.normalized(
            search_text='a',
            rating_mode='at_least',
            rating_reference=3,
            exclude_tags=('他',),
        )
    )
    browser.show()
    qapp.processEvents()
    old_button = browser.tag_quick_filter_strip._buttons['旧']
    QTest.mouseClick(old_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    state = browser.browser_filter_state
    assert state.include_tags == ('旧',)
    assert state.exclude_tags == ('他',)
    assert state.search_text == 'a'
    assert state.rating_reference == 3
    tag_rect = browser.tag_button.geometry()
    strip_rect = browser.tag_quick_filter_strip.geometry()
    assert browser.tag_button.text() == 'タグ (2)'
    assert strip_rect.right() < tag_rect.left()
    QTest.mousePress(old_button, Qt.MouseButton.LeftButton)
    QTest.mouseRelease(
        old_button,
        Qt.MouseButton.LeftButton,
        pos=QPoint(-4, -4),
    )
    qapp.processEvents()
    assert old_button.isDown() is False
    assert browser.browser_filter_state.include_tags == ('旧',)
    QTest.mouseClick(old_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert browser.browser_filter_state.include_tags == ()
    assert browser.browser_filter_state.exclude_tags == ('他',)
    new_button = browser.tag_quick_filter_strip._buttons['新']
    QTest.keyClick(new_button, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert browser.browser_filter_state.include_tags == ('新',)
    assert browser.browser_filter_state.tag_match == 'any'
    assert browser.browser_filter_state.exclude_tags == ('他',)
    QTest.keyClick(new_button, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
    qapp.processEvents()
    assert browser.browser_filter_state.include_tags == ()
    assert browser.browser_filter_state.exclude_tags == ('他', '新')
    assert browser.browser_filter_state.tag_match == 'any'
    browser.browser_search_edit.setFocus()
    browser.browser_search_edit.selectAll()
    QTest.keyClicks(browser.browser_search_edit, 'pending')
    assert browser._browser_search_timer.isActive()
    QTest.mouseClick(old_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert browser.browser_filter_state.search_text == 'pending'
    assert browser.browser_filter_state.include_tags == ('旧',)
    assert not browser._browser_search_timer.isActive()
    controller.config.apply({'browser_tag_registry': [{'name': '新', 'color': '#80ff80'}]})
    qapp.processEvents()
    assert list(browser.tag_quick_filter_strip.registry) == [
        {'name': '新', 'color': '#80ff80'}
    ]
    browser.clear_browser_filters()
    qapp.processEvents()
    assert browser.tag_button.text() == 'タグ'
    assert browser.tag_quick_filter_strip.geometry().right() < browser.tag_button.geometry().left()


def test_quick_tag_overflow_keeps_all_registered_tags_reachable(library, qapp):
    controller, browser, _ = library
    registry = [
        {'name': f'タグ{i}長い名前', 'color': '#80bfff'}
        for i in range(8)
    ]
    controller.config.apply({'browser_tag_registry': registry})
    browser.resize(1200, 420)
    browser.show()
    qapp.processEvents()

    strip = browser.tag_quick_filter_strip
    assert strip.width() <= strip._available_width
    hidden = strip._registry_names_not_visible()
    assert hidden
    assert strip._overflow.isVisible()
    assert [action.text() for action in strip._overflow_menu.actions()] == list(hidden)
    target = hidden[-1]
    strip._overflow_menu.actions()[-1].trigger()
    qapp.processEvents()
    assert target in browser.browser_filter_state.include_tags
    modifier_target = hidden[0]
    strip._overflow_menu.popup(
        strip._overflow.mapToGlobal(QPoint(0, strip._overflow.height()))
    )
    qapp.processEvents()
    modifier_action = next(
        action
        for action in strip._overflow_menu.actions()
        if action.text() == modifier_target
    )
    QTest.mouseClick(
        strip._overflow_menu,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        pos=strip._overflow_menu.actionGeometry(modifier_action).center(),
    )
    qapp.processEvents()
    assert modifier_target in browser.browser_filter_state.exclude_tags
    assert browser.browser_filter_state.tag_match == 'any'

    browser.resize(900, 600)
    qapp.processEvents()
    narrow_width = strip.width()
    browser.resize(1600, 600)
    qapp.processEvents()
    first_wide_width = strip.width()
    browser.resize(900, 600)
    qapp.processEvents()
    browser.resize(1600, 600)
    qapp.processEvents()
    assert first_wide_width > narrow_width
    assert strip.width() >= first_wide_width
    tag_rect = browser.tag_button.geometry()
    rating_rect = browser.rating_filter_widget.geometry()
    assert tag_rect.right() < rating_rect.left()
    assert strip.geometry().right() < tag_rect.left()
    assert strip.width() > 0
    assert [action.text() for action in strip._overflow_menu.actions()] == list(
        strip._registry_names_not_visible()
    )
    controller.config.apply(
        {'browser_tag_registry': [{'name': registry[0]['name'], 'color': '#80bfff'}]}
    )
    qapp.processEvents()
    assert strip._registry_names_not_visible() == ()
    controller.config.apply({'browser_tag_registry': registry})
    qapp.processEvents()
    assert [action.text() for action in strip._overflow_menu.actions()] == list(
        strip._registry_names_not_visible()
    )
    assert strip._overflow.isVisible() == bool(strip._registry_names_not_visible())


def test_quick_tag_strip_translates_fixed_labels_in_english(qapp):
    install_ui_language('en')
    strip = BrowserTagQuickFilterStrip()
    try:
        assert strip._overflow.toolTip() == 'Show all registered tags'
        strip.set_registry([{'name': 'Read', 'color': '#80bfff'}])
        assert strip._buttons['Read'].toolTip() == 'Click to filter by tag: Read'
    finally:
        install_ui_language('ja')
        strip.deleteLater()


def test_filter_and_registry_lifecycle_real_events(library, qapp):
    controller, browser, root = library
    path = root / 'a {zpi$t=旧}.png'
    write_image(path)
    write_image(root / 'b.png')
    load(browser, root)
    errors = []
    def filter_clicks():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, TagFilterDialog)
            combo = dialog.controls['旧']
            combo.setFocus()
            QTest.keyClick(combo, Qt.Key.Key_Down)
            QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Apply), Qt.MouseButton.LeftButton)
        except Exception as exc:
            errors.append(exc)
            dialog.reject()
    QTimer.singleShot(0, filter_clicks)
    browser.edit_tag_filter()
    assert not errors, errors
    assert browser.item_model.rowCount() == 1
    from PySide6.QtWidgets import QPushButton
    def registry_clicks():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, TagManagerDialog)
            # Delete the first registration, then add the same authoritative name.
            cell = dialog.table.visualItemRect(dialog.table.item(0, 0)).center()
            QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=cell)
            delete = next(b for b in dialog.findChildren(QPushButton) if b.text() == '削除')
            QTest.mouseClick(delete, Qt.MouseButton.LeftButton)
            add = next(b for b in dialog.findChildren(QPushButton) if b.text() == '追加')
            QTest.mouseClick(add, Qt.MouseButton.LeftButton)
            dialog.table.editItem(dialog.table.item(dialog.table.rowCount()-1, 0))
            qapp.processEvents()
            from PySide6.QtWidgets import QLineEdit
            editor = dialog.table.findChild(QLineEdit)
            assert editor is not None
            # Unicode key input is delivered through Qt's input method event.
            from PySide6.QtGui import QInputMethodEvent
            event = QInputMethodEvent()
            event.setCommitString('旧')
            QApplication.sendEvent(editor, event)
            QTest.keyClick(editor, Qt.Key.Key_Return)
            QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        except Exception as exc:
            errors.append(exc)
            dialog.reject()
    QTimer.singleShot(0, registry_clicks)
    browser.manage_tags()
    assert not errors, errors
    assert [entry['name'] for entry in controller.config.get('browser_tag_registry')] == ['新', '旧']
    assert path.exists() and filename_tags(str(path)) == ('旧',)


@pytest.mark.parametrize('dpi', [1, 1.25, 1.5, 2])
def test_compact_tag_pixels_order_bounds_and_font_height(library, qapp, dpi):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem
    from app.browser_model import BrowserItem, BrowserItemKind
    _, browser, _ = library
    delegate = browser.item_delegate
    # Registry order deliberately differs from serialized filename order.
    delegate.tag_registry = [{'name':'日本語', 'color':'#00ff00'}, {'name':'gjpq', 'color':'#ff0000'}]
    entry = BrowserItem('test', Path('test {zpi$t=gjpq,日本語}.png'), BrowserItemKind.IMAGE, None)
    option = QStyleOptionViewItem()
    # Windows offscreen exposes no system font database by default. Load an
    # already installed font into this test process only; do not test tofu boxes.
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/meiryo.ttc')
    assert font_id >= 0
    option.font = QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 9)
    image = QImage(round(220*dpi), round(150*dpi), QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(dpi)
    image.fill(QColor('white'))
    painter = QPainter(image)
    rect = QRect(10,10,200,130)
    delegate._paint_tags(painter, option, rect, entry)
    painter.end()
    pixels = {color: [] for color in ('#00ff00','#ff0000')}
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x,y).name()
            if color in pixels:
                pixels[color].append((x/dpi,y/dpi))
    red, green = pixels['#ff0000'], pixels['#00ff00']
    assert red and green
    assert max(x for x,y in red) < min(x for x,y in green)
    font = QFont(option.font)
    font.setPointSize(max(7,min(10,delegate.profile.font_size)))
    height = QFontMetrics(font).tightBoundingRect('日本語gjpq').height() + 4
    assert height < QFontMetrics(font).height() + 4
    for background, points in pixels.items():
        assert abs((max(y for x,y in points)-min(y for x,y in points)+1/dpi)-height) <= 1/dpi
        assert all(35 <= x < rect.right() and rect.top()+25 <= y < rect.bottom() for x,y in points)
        left, right = min(x for x,y in points), max(x for x,y in points)
        top, bottom = min(y for x,y in points), max(y for x,y in points)
        glyph_rows = [y/dpi for y in range(image.height()) for x in range(image.width())
                      if left <= x/dpi <= right and top <= y/dpi <= bottom
                      and image.pixelColor(x,y).name() != background]
        assert glyph_rows
        assert min(glyph_rows) > top and max(glyph_rows) < bottom
