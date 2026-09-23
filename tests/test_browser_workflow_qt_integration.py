"""Requires the real project + PySide6; never uses native input or main.py."""
from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QMimeData, QUrl, QObject, QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from app.internal_clipboard import ClipboardPasteReceipt, InternalClipboardState
from app.browser_workflow_policy import paste_is_move
from app.browser_workflow_settings import BrowserWorkflowSettings
from app.file_operation_service import FileOperationService, FileOperationRequest, FileOperationKind
from app.file_operation_service import FileOperationItemResult, FileOperationResult
from app.shortcut_catalog import normalize_shortcut_bindings
from app.browser_workflow_controller import BrowserWorkflowController
from app.browser_window import BrowserWindow


class _ClipboardCleanupHarness:
    def __init__(self):
        self._internal_clipboard_state = InternalClipboardState()

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.abspath(os.path.normpath(path))).casefold()

    _finish_paste_clipboard = BrowserWindow._finish_paste_clipboard


def test_external_cut_mime_is_move_without_private_marker(tmp_path):
    path=tmp_path/'page.jpg'; path.write_bytes(b'test')
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    state=InternalClipboardState()
    assert paste_is_move(internal_matches=state.matches_mime(mime),internal_cut=state.is_cut,
                         preferred_effect=state.preferred_drop_effect(mime))


def test_old_paste_does_not_clear_replaced_clipboard_with_same_urls(tmp_path, qapp):
    source=tmp_path/'page.jpg'
    clipboard=qapp.clipboard()
    first=QMimeData(); first.setUrls([QUrl.fromLocalFile(str(source))])
    first.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(first)
    state=InternalClipboardState()
    receipt=ClipboardPasteReceipt.capture(state,clipboard.mimeData())

    replacement=QMimeData(); replacement.setUrls([QUrl.fromLocalFile(str(source))])
    replacement.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(replacement)
    assert not receipt.matches(state,clipboard.mimeData())

    result=FileOperationResult(FileOperationKind.MOVE,(
        FileOperationItemResult(str(source),str(tmp_path/'dest'/'page.jpg'),True,
                                source_removed=True,source_exists_after=False),))
    _ClipboardCleanupHarness()._finish_paste_clipboard(result,receipt)
    assert [Path(url.toLocalFile()) for url in clipboard.mimeData().urls()]==[source]
    clipboard.clear()


def test_current_external_cut_is_cleared_after_successful_move(tmp_path, qapp):
    source=tmp_path/'page.jpg'
    clipboard=qapp.clipboard()
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(source))])
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',b'\x02\0\0\0')
    clipboard.setMimeData(mime)
    state=InternalClipboardState()
    receipt=ClipboardPasteReceipt.capture(state,clipboard.mimeData())
    result=FileOperationResult(FileOperationKind.MOVE,(
        FileOperationItemResult(str(source),str(tmp_path/'dest'/'page.jpg'),True,
                                source_removed=True,source_exists_after=False),))

    _ClipboardCleanupHarness()._finish_paste_clipboard(result,receipt)
    current=clipboard.mimeData()
    assert current is None or not current.hasUrls()


def test_browser_search_shortcuts_yield_during_ime_composition(qapp):
    from PySide6.QtWidgets import QLineEdit
    edit=QLineEdit()
    controller=BrowserWorkflowController.__new__(BrowserWorkflowController)
    QObject.__init__(controller)
    controller.window=SimpleNamespace(browser_search_edit=edit)
    controller._ime_composing=False

    assert not controller.handle_key(edit,QInputMethodEvent('にほんご',[]))
    assert controller._ime_composing
    shortcut=QKeyEvent(QEvent.Type.KeyPress,ord('F'),Qt.KeyboardModifier.ControlModifier)
    assert not controller.handle_key(edit,shortcut)
    assert not controller.handle_key(edit,QInputMethodEvent('',[]))
    assert not controller._ime_composing
    edit.deleteLater(); controller.deleteLater(); qapp.processEvents()


def test_workflow_settings_roundtrip(qapp):
    widget=BrowserWorkflowSettings()
    values={'browser_thumbnail_background_screens':-1,'browser_selection_filename_opacity':0,
            'browser_selection_border_width':9,'browser_selection_color':'#aabbcc'}
    widget.load(values)
    assert widget.values()==values
    widget.load({})
    assert widget.values()['browser_thumbnail_background_screens']==3
    widget.deleteLater(); qapp.processEvents()


def test_real_move_undo_stays_on_file_operation_service(tmp_path):
    source=tmp_path/'a'/'file.txt'; source.parent.mkdir()
    dest=tmp_path/'b'; dest.mkdir(); source.write_text('data',encoding='utf-8')
    service=FileOperationService()
    result=service.execute(FileOperationRequest(1,FileOperationKind.MOVE,(str(source),),str(dest)))
    assert result.successes and result.undo_entries
    undone=service.execute(FileOperationRequest(2,FileOperationKind.UNDO,
                            undo_entries=result.undo_entries))
    assert undone.successes and source.read_text(encoding='utf-8')=='data'
    assert not (dest/source.name).exists()
    assert not undone.undo_entries


def test_shortcut_migration_preserves_explicit_unassigned(qapp):
    migrated=normalize_shortcut_bindings({'browser':{'browser_copy':['Alt+C']}})['browser']
    assert migrated['browser_focus_search']==['Ctrl+F'] and migrated['browser_undo']==['Ctrl+Z']
    explicit=normalize_shortcut_bindings({'browser':{'browser_undo':[]}})['browser']
    assert explicit['browser_undo']==[]
    custom=normalize_shortcut_bindings({'browser':{'browser_copy':['Ctrl+F'],
                                                  'browser_cut':['Ctrl+Z']}})['browser']
    assert custom['browser_focus_search']==[] and custom['browser_undo']==[]
    assert custom['browser_copy']==['Ctrl+F'] and custom['browser_cut']==['Ctrl+Z']
