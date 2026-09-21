from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QRect, QEvent, QObject, QItemSelectionModel
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QStyleOptionViewItem, QMessageBox
from PySide6.QtTest import QTest

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_tag_dialogs import TagManagerDialog, ItemTagsDialog, TagFilterDialog
from app.browser_tags import edited_tags, filename_tags, normalize_tag_registry, valid_tag_name, visible_tags
from app.i18n import install_ui_language
from app.rating_rename_service import RatingRenameService
from app.zippla_filename_metadata import ZipPlaFilenameMetadata
from tests.test_application_controller import make_controller, write_image, write_archive, close_controller, wait_until, finish_viewer_open


REGISTRY = [{'name': '旧', 'color': '#ff8080'}, {'name': '新', 'color': '#80ff80'}]


def test_case_only_service_rename_changes_stored_tag_case(tmp_path):
    source = tmp_path / 'sample {zpi$c=2.123456;r=4;t=Other,Read}.zip'
    source.write_bytes(b'unchanged case rename')
    stamp = source.stat().st_mtime_ns
    metadata = ZipPlaFilenameMetadata.parse(source).with_tag_changes({'Read': False, 'read': True})
    result = RatingRenameService().set_metadata(metadata)
    assert result.success and result.changed
    actual = next(tmp_path.iterdir())
    assert actual.name == metadata.serialized_path().name
    assert filename_tags(str(actual)) == ('Other', 'read')
    assert actual.stat().st_mtime_ns == stamp
    assert actual.read_bytes() == b'unchanged case rename'
    assert 'c=2.123456;r=4' in actual.name


def test_tag_metadata_edit_preserves_other_fields_and_collisions(tmp_path):
    source = tmp_path / '本 {zpi$c=2.123456;b=r;r=4;t=旧,未知;d=l}.cbz'
    source.write_bytes(b'unchanged')
    stamp = source.stat().st_mtime_ns
    metadata = ZipPlaFilenameMetadata.parse(source).with_tag_changes({'旧': False, '新': True})
    target = metadata.serialized_path()
    assert 'c=2.123456;b=r;r=4;d=l' in target.name
    assert metadata.tags == ('未知', '新')
    target.write_bytes(b'collision')
    service = RatingRenameService()
    assert not service.set_metadata(metadata).success
    assert source.read_bytes() == b'unchanged' and target.read_bytes() == b'collision'
    # Reuse another explicitly named destination, without deleting the collision.
    metadata = metadata.with_tag_changes({'別': True})
    result = service.set_metadata(metadata)
    assert result.success
    assert result.destination_path.read_bytes() == b'unchanged'
    assert result.destination_path.stat().st_mtime_ns == stamp
    parsed = ZipPlaFilenameMetadata.parse(result.destination_path)
    assert (parsed.cover, parsed.binding, parsed.rating, parsed.legacy_direction) == ((2, .123456), 'r', 4, 'l')


@pytest.mark.parametrize('name', ['', ' ', 'a,b', 'a;b', 'a/b', 'a{b', 'a\nb', ' a', 'a '])
def test_invalid_names(name):
    assert not valid_tag_name(name)


def test_tristate_draft_and_unknown_tags(qapp):
    paths = ['a {zpi$t=旧,未登録}.zip', 'b {zpi$t=未登録}.zip']
    dialog = ItemTagsDialog(paths, REGISTRY)
    assert dialog.checks['旧'].checkState() == Qt.CheckState.PartiallyChecked
    assert '未登録' in dialog.checks['未登録'].text()
    assert edited_tags(filename_tags(paths[0]), dialog.changes()) == ('旧', '未登録')
    assert edited_tags(filename_tags(paths[1]), dialog.changes()) == ('未登録',)
    dialog.checks['旧'].setCheckState(Qt.CheckState.Unchecked)
    dialog.checks['新'].setCheckState(Qt.CheckState.Checked)
    assert edited_tags(filename_tags(paths[0]), dialog.changes()) == ('未登録', '新')
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_item_tags_apply_first_default_enter_and_escape_cancel(qapp):
    path = 'a {zpi$t=旧}.zip'
    dialog = ItemTagsDialog((path,), REGISTRY)
    dialog.show()
    qapp.processEvents()
    apply_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Apply)
    cancel_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    layout = dialog.buttons.layout()
    try:
        assert layout.indexOf(apply_button) < layout.indexOf(cancel_button)
        assert apply_button.isDefault() and not cancel_button.isDefault()
        assert QApplication.focusWidget() is apply_button
        assert dialog.checks['旧'].checkState() == Qt.CheckState.Checked
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()

    cancel_dialog = ItemTagsDialog((path,), REGISTRY)
    cancel_dialog.show()
    qapp.processEvents()
    try:
        QTest.keyClick(cancel_dialog, Qt.Key.Key_Escape)
        assert cancel_dialog.result() == QDialog.DialogCode.Rejected
    finally:
        cancel_dialog.close()


def test_registry_rename_delete_reregister_has_no_history(qapp):
    path = 'a {zpi$t=旧}.zip'
    dialog = TagManagerDialog(REGISTRY)
    dialog.table.item(0, 0).setText('変更')
    assert visible_tags(path, dialog.registry()) == []
    dialog.table.setCurrentCell(0, 0)
    dialog.remove_row()
    dialog.add_row('旧', '#123456')
    dialog.move_row(-1)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert visible_tags(path, dialog.registry()) == [{'name': '旧', 'color': '#123456'}]
    assert filename_tags(path) == ('旧',)
    duplicate = TagManagerDialog([{'name': 'Name', 'color': '#ffffff'}])
    duplicate.add_row('name')
    duplicate.accept()
    assert duplicate.result() != QDialog.DialogCode.Accepted
    assert duplicate.error_label.text()
    assert len(normalize_tag_registry(duplicate.registry())) == 1


def test_strict_filter_and_or_exclude_composes_with_search_and_rating():
    def item(name, rating=4):
        p = Path(name)
        return BrowserItem(display_name=ZipPlaFilenameMetadata.parse(p).display_name, path=p,
                           kind=BrowserItemKind.ARCHIVE, rating=rating, modified_at=0)
    both = item('本 {zpi$t=旧,新}.zip')
    old = item('本 {zpi$t=旧}.zip')
    text_only = item('本旧.zip')
    state = BrowserFilterState.normalized(search_text='本', rating_mode='at_least', rating_reference=3,
                                         include_tags=('旧', '新'))
    assert state.matches(both) and not state.matches(old) and not state.matches(text_only)
    state = BrowserFilterState.normalized(include_tags=('旧', '新'), tag_match='any', exclude_tags=('新',))
    assert state.matches(old) and not state.matches(both)
    assert not BrowserFilterState.normalized(include_tags=('旧',), search_text='別').matches(old)
    assert not BrowserFilterState.normalized(include_tags=('旧',), rating_mode='equal', rating_reference=5).matches(old)


@pytest.fixture
def library(tmp_path, qapp):
    root = tmp_path / 'shelf'
    root.mkdir()
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    controller.config.apply({'browser_tag_registry': REGISTRY})
    try:
        yield controller, browser, root
    finally:
        close_controller(controller, qapp)


def load(browser, root):
    browser.set_current_folder(root)
    assert browser.wait_for_scan()


def test_single_zip_tag_badge_paints_before_metadata_relocation(library, qapp, monkeypatch):
    _, browser, root = library
    source = root / '本.cbz'
    write_archive(source)
    load(browser, root)
    browser.metadata_store.add_browser_bookmark(str(source))
    browser.show()
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    assert wait_until(qapp, lambda: browser.thumbnail_provider.pending_count == 0, timeout=5)
    target = ZipPlaFilenameMetadata.parse(source).with_tag_changes({'新': True}).serialized_path()

    class PaintWatch(QObject):
        def __init__(self):
            super().__init__()
            self.count = 0
            self.painted_target = False

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.count += 1
                row = browser.item_model.row_for_path(target)
                if row >= 0:
                    rect = browser.list_view.visualRect(browser.item_model.index(row, 0))
                    self.painted_target |= event.region().intersects(rect)
            return False

    watch = PaintWatch()
    browser.list_view.viewport().installEventFilter(watch)
    original_relocate = browser.metadata_store.relocate_item

    def relocate_after_paint(old_path, new_path):
        assert watch.count > 0
        assert watch.painted_target
        return original_relocate(old_path, new_path)

    # Drain show/selection paints so only the tag operation can satisfy the
    # assertion while set_tags_for_paths blocks the event loop.
    for _ in range(3):
        qapp.processEvents()
    watch.count = 0
    watch.painted_target = False
    with monkeypatch.context() as edits:
        edits.setattr(browser.image_detail_probe, 'close',
                      lambda: pytest.fail('ZIP rename must not wait for image detail probe'))
        edits.setattr(browser.metadata_store, 'relocate_item', relocate_after_paint)
        assert browser.set_tags_for_paths((str(source),), {'新': True})
    assert target.exists() and not source.exists()
    assert browser.metadata_store.is_browser_bookmarked(str(target))


@pytest.mark.parametrize('kind', ['image', 'archive', 'folder'])
def test_browser_case_only_tag_replacement_preserves_cache_selection_and_filters(library, qapp, kind):
    controller, browser, root = library
    path = root / ('sample {zpi$r=4;t=Other,Read}' + {'image': '.png', 'archive': '.cbz', 'folder': ''}[kind])
    if kind == 'archive':
        write_archive(path)
    else:
        write_image(path / '1.png' if kind == 'folder' else path)
    payload = (path / '1.png' if kind == 'folder' else path).read_bytes()
    stamp = path.stat().st_mtime_ns
    load(browser, root)
    controller.config.apply({'browser_tag_registry': [{'name': 'read', 'color': '#123456'}]})
    browser._set_browser_filter(BrowserFilterState.normalized(include_tags=('Read', 'read'), tag_match='any'))
    qapp.processEvents()
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    qapp.processEvents()
    assert browser.list_view.selectionModel().selectedIndexes()
    thumbnail = QImage(20, 20, QImage.Format.Format_ARGB32)
    thumbnail.fill(QColor('#123456'))
    browser.item_model.set_thumbnail_image(path, thumbnail)
    browser.item_model.set_image_dimensions(path, (800, 1200))
    key = browser.item_model.data(browser.item_model.index(0, 0), browser.item_model.ThumbnailImageRole).cacheKey()
    assert browser.set_tags_for_paths((str(path),), {'Read': False, 'read': True})
    assert wait_until(qapp, lambda: browser._rating_batch is None and not browser.file_operation_coordinator.busy, timeout=5)
    qapp.processEvents()
    actual = next(root.iterdir())
    assert 't=Other,read}' in actual.name
    assert actual.stat().st_mtime_ns == stamp
    assert (actual / '1.png' if kind == 'folder' else actual).read_bytes() == payload
    model = browser.item_model
    assert str(model.item_at(0).path) == str(actual)
    assert model.data(model.index(0, 0), model.ThumbnailImageRole).cacheKey() == key
    assert model.image_dimensions(actual) == (800, 1200)
    assert [str(model.item_at(i).path) for i in browser.list_view.selectionModel().selectedIndexes()] == [str(actual)]
    assert [entry['name'] for entry in visible_tags(str(actual), browser.item_delegate.tag_registry)] == ['read']
    browser._set_browser_filter(BrowserFilterState.normalized(include_tags=('Read',)))
    assert model.rowCount() == 0
    browser._set_browser_filter(BrowserFilterState.normalized(include_tags=('read',)))
    assert model.rowCount() == 1


def test_case_folded_different_entry_is_not_ignored(tmp_path, monkeypatch):
    # Windows normally aliases casing; inject a case-sensitive directory listing
    # to verify the safety boundary without changing filesystem flags.
    from types import SimpleNamespace
    from app.file_operation_service import FileOperationService
    import app.file_operation_service as service_module
    source = tmp_path / 'Read'
    target = tmp_path / 'read'
    source.write_bytes(b'keep source')
    class Entries:
        def __enter__(self):
            return iter([SimpleNamespace(name='Read', path=str(source)),
                         SimpleNamespace(name='read', path=str(target))])
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(service_module.os, 'scandir', lambda _: Entries())
    assert FileOperationService._name_exists(str(target), ignore_path=str(source))
    rename = Mock()
    monkeypatch.setattr(service_module.os, 'rename', rename)
    result = FileOperationService().rename(source, target.name)
    assert not result.effective_items[0].success
    rename.assert_not_called()
    assert source.read_bytes() == b'keep source'


@pytest.mark.parametrize('kind', ['image', 'archive', 'folder'])
def test_apply_rename_preserves_selection_rating_and_other_tags(library, qapp, kind):
    controller, browser, root = library
    path = root / ('本 {zpi$r=4;t=旧,未知}' + {'image': '.png', 'archive': '.cbz', 'folder': ''}[kind])
    if kind == 'archive':
        write_archive(path)
    else:
        write_image(path / '1.png' if kind == 'folder' else path)
    load(browser, root)
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    qapp.processEvents()
    browser._set_browser_filter(BrowserFilterState.normalized(include_tags=('未知',), rating_mode='equal', rating_reference=4))
    qapp.processEvents()
    assert browser.set_rating_for_paths((str(path),), None, tag_changes={'旧': False, '新': True})
    assert wait_until(qapp, lambda: browser._rating_batch is None and not browser.file_operation_coordinator.busy, timeout=5)
    qapp.processEvents()
    target = ZipPlaFilenameMetadata.parse(path).with_tag_changes({'旧': False, '新': True}).serialized_path(is_directory=kind == 'folder')
    assert target.exists() and not path.exists()
    assert browser.item_model.rowCount() == 1
    assert browser.item_model.item_at(0).rating == 4
    assert {browser.item_model.item_at(i).path for i in browser.list_view.selectionModel().selectedIndexes()} == {target}
    assert browser.current_path == root
    # Registry edits repaint only and never modify the newly written filename.
    controller.config.apply({'browser_tag_registry': [{'name': '別', 'color': '#ffffff'}]})
    assert visible_tags(str(target), browser.item_delegate.tag_registry) == []
    controller.config.apply({'browser_tag_registry': REGISTRY})
    assert [x['name'] for x in visible_tags(str(target), browser.item_delegate.tag_registry)] == ['新']


def test_search_rating_changes_preserve_tag_filter_and_escape_clears_it(library, qapp):
    _, browser, root = library
    write_image(root / 'a {zpi$t=旧}.png')
    write_image(root / 'a旧.png')
    load(browser, root)
    browser._set_browser_filter(BrowserFilterState.normalized(include_tags=('旧',)))
    browser.browser_search_edit.setText('a')
    browser._apply_pending_browser_search()
    browser._on_rating_quick_filter_changed('unrated', 0)
    assert browser.item_model.rowCount() == 1
    assert browser.browser_filter_state.include_tags == ('旧',)
    browser.clear_active_browser_search()
    assert browser.browser_filter_state.include_tags == ('旧',)
    browser.clear_browser_filters()
    assert browser.item_model.rowCount() == 2


def test_multiselect_one_apply_preserves_mixed_and_unknown(library, qapp, monkeypatch):
    _, browser, root = library
    paths = [root / 'a {zpi$t=旧,未知}.png', root / 'b {zpi$t=未知}.png']
    for path in paths:
        write_image(path)
    load(browser, root)
    for row in range(2):
        browser.list_view.selectionModel().select(browser.item_model.index(row, 0), QItemSelectionModel.SelectionFlag.Select)
    qapp.processEvents()
    def apply_dialog(dialog):
        assert all(path.exists() for path in paths)
        dialog.checks['旧'].setCheckState(Qt.CheckState.Unchecked)
        dialog.checks['新'].setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ItemTagsDialog, 'exec', apply_dialog)
    apply = Mock(wraps=browser.set_rating_for_paths)
    monkeypatch.setattr(browser, 'set_rating_for_paths', apply)
    browser.edit_selected_tags()
    qapp.processEvents()
    apply.assert_called_once()
    selected = [browser.item_model.item_at(i).path for i in browser.list_view.selectionModel().selectedIndexes()]
    assert len(selected) == 2
    assert all(filename_tags(str(p)) == ('未知', '新') for p in selected)


@pytest.mark.parametrize('kind', ['image', 'archive', 'folder'])
def test_open_viewer_rename_obeys_existing_close_contract(library, qapp, monkeypatch, kind):
    controller, browser, root = library
    path = root / ('本' + {'image': '.png', 'archive': '.cbz', 'folder': ''}[kind])
    if kind == 'archive':
        write_archive(path)
    else:
        write_image(path / '1.png' if kind == 'folder' else path)
    load(browser, root)
    viewer = controller.open_path(path)
    finish_viewer_open(qapp, viewer)
    question = Mock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, 'question', question)
    assert not browser.set_rating_for_paths((str(path),), None, tag_changes={'新': None})
    question.assert_not_called()
    assert not browser.set_rating_for_paths((str(path),), None, tag_changes={'新': True})
    assert path.exists() and not viewer._shutdown_prepared
    question.return_value = QMessageBox.StandardButton.Yes
    assert browser.set_rating_for_paths((str(path),), None, tag_changes={'新': True})
    assert wait_until(qapp, lambda: browser._rating_batch is None and not browser.file_operation_coordinator.busy, timeout=5)
    assert viewer._shutdown_prepared
    target = ZipPlaFilenameMetadata.parse(path).with_tag_changes({'新': True}).serialized_path(is_directory=kind == 'folder')
    assert target.exists()
    reopened = controller.open_path(target)
    finish_viewer_open(qapp, reopened)
    assert reopened.book_session.current_path == target


def test_folder_tag_collision_preserves_both_trees(library, qapp):
    _, browser, root = library
    path, collision = root / '本', root / '本 {zpi$t=新}'
    write_image(path / '1.png')
    write_image(collision / '2.png')
    load(browser, root)
    browser.set_rating_for_paths((str(path),), None, tag_changes={'新': True})
    assert wait_until(qapp, lambda: browser._rating_batch is None and not browser.file_operation_coordinator.busy, timeout=5)
    assert (path / '1.png').exists() and (collision / '2.png').exists()


def test_registered_labels_render_and_disappear_without_touching_filename(library, qapp):
    _, browser, root = library
    path = root / 'a {zpi$t=旧}.png'
    write_image(path)
    load(browser, root)
    delegate = browser.item_delegate
    option = QStyleOptionViewItem()
    rect = QRect(0, 0, 180, 200)
    item = browser.item_model.item_at(0)
    def paint():
        image = QImage(180, 200, QImage.Format.Format_ARGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        delegate._paint_tags(painter, option, rect, item)
        painter.end()
        return image
    shown = paint()
    assert any(shown.pixelColor(x, y).name() == '#ff8080' for x in range(100, 180) for y in range(150, 200))
    delegate.tag_registry = []
    hidden = paint()
    assert all(hidden.pixelColor(x, y) == QColor('white') for x in range(180) for y in range(200))
    assert path.exists()


@pytest.mark.parametrize('language', ['ja', 'en'])
def test_dialogs_translate_and_registry_drafts_do_not_touch_files(library, qapp, language):
    _, browser, root = library
    path = root / 'a {zpi$t=旧}.png'
    write_image(path)
    install_ui_language(language)
    try:
        for dialog in [TagManagerDialog(REGISTRY), ItemTagsDialog((str(path),), REGISTRY),
                       TagFilterDialog(BrowserFilterState(), REGISTRY)]:
            dialog.show()
            qapp.processEvents()
            assert dialog.windowTitle()
            if language == 'en':
                assert not any('\u3040' <= char <= '\u30ff' for char in dialog.windowTitle())
            dialog.reject()
        assert path.exists()
    finally:
        install_ui_language('ja')
