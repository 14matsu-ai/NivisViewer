from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QTextBrowser, QWidget

from app.config_manager import ConfigManager
from app.diagnostics_dialog import DiagnosticsDialog, diagnostic_text
from app.i18n import install_ui_language, tr
from app.pdfium_service import PdfiumService
from app.settings_dialog import SettingsDialog
from app.shortcut_catalog import SPECS_BY_SCOPE
from app.shortcuts_help import show_shortcuts_help
from app.viewer_window import ViewerWindow


@pytest.mark.parametrize('language', ['ja', 'en'])
@pytest.mark.parametrize('apply_draft', [False, True])
def test_settings_help_reuses_content_without_applying_draft(
    tmp_path: Path, qapp, monkeypatch, language, apply_draft,
):
    install_ui_language(language)
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    original_gap = config.get('gap')
    service = PdfiumService()
    dialog = SettingsDialog(config, pdfium_service=service)
    viewer_parent = QWidget()
    help_dialogs = []
    diagnostics = []

    def capture_help(dialog):
        help_dialogs.append(dialog)
        dialog.reject()
        return 0

    monkeypatch.setattr(QDialog, 'exec', capture_help)

    class CapturedDiagnostics(DiagnosticsDialog):
        def exec(self):
            diagnostics.append((self.parent(), self.profile_dir,
                                self.pdfium_service, self.text_edit.toPlainText(),
                                self.windowTitle()))
            self.reject()
            return 0

    monkeypatch.setattr('app.diagnostics_dialog.DiagnosticsDialog', CapturedDiagnostics)
    try:
        dialog.gap_spin.setValue(99)
        dialog.ui_language_combo.setCurrentIndex(
            dialog.ui_language_combo.findData('en' if language == 'ja' else 'ja')
        )
        assert dialog.shortcuts_help_button.text() == tr('ショートカット一覧')
        assert dialog.diagnostics_button.text() == tr('NivisViewerについて／診断情報')
        dialog.shortcuts_help_button.click()
        ViewerWindow.show_shortcuts_help(viewer_parent)
        assert help_dialogs[0].parent() is dialog
        assert help_dialogs[1].parent() is viewer_parent
        assert help_dialogs[0].windowTitle() == tr('ショートカット一覧')
        assert help_dialogs[0].windowTitle() == help_dialogs[1].windowTitle()
        text_edit = help_dialogs[0].findChild(QTextBrowser)
        content = text_edit.toPlainText()
        assert text_edit.isReadOnly()
        html = text_edit.toHtml()
        assert "font-size:14pt" in html.replace(" ", "")
        assert "font-size:10pt" in html.replace(" ", "")
        assert "[Browser]" in html and "[Viewer]" in html
        available = help_dialogs[0].screen().availableGeometry()
        assert help_dialogs[0].width() <= available.width()
        assert help_dialogs[0].height() <= available.height()
        assert '[Browser]' in content
        assert '[Viewer]' in content
        for scope_specs in SPECS_BY_SCOPE.values():
            for spec in scope_specs:
                assert tr(spec.label) in content
                assert spec.action_id not in content
        assert 'Return, Enter' in content
        assert 'Ctrl+Left' in content
        assert tr('Viewerを終了') not in content
        assert f'{tr("Viewerを閉じる")}: Ctrl+W, Ctrl+Q' in content
        assert tr('数字キー＋Sの間隔指定: 有効') in content
        dialog.diagnostics_button.click()
        parent, profile, passed_service, content, title = diagnostics[0]
        assert parent is dialog
        assert profile == config.base_dir
        assert passed_service is service
        assert title == tr('NivisViewerについて／診断情報')
        assert 'NivisViewer ' in content
        assert f'Profile: {config.base_dir}' in content
        assert content == diagnostic_text(
            config.base_dir, pdf_snapshot=service.availability_snapshot,
        )
        assert dialog.gap_spin.value() == 99
        assert config.get('gap') == original_gap
        assert not config.path.exists()
        if apply_draft:
            dialog.apply_settings()
            assert config.get('gap') == 99
        dialog.reject()
        assert config.get('gap') == (99 if apply_draft else original_gap)
    finally:
        dialog.reject()
        service.shutdown()
        viewer_parent.close()
        install_ui_language('ja')


def test_settings_help_reflects_current_bindings_and_unassigned_actions(
    tmp_path: Path, qapp, monkeypatch,
):
    install_ui_language('ja')
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({
        'shortcut_bindings': {
            'browser': {
                'browser_back': ['T'],
                'browser_delete': [],
            },
            'viewer': {
                'viewer_rotate_left': ['Y'],
            },
        },
        'viewer_slideshow_chord_enabled': False,
    })
    parent = QWidget()
    parent.config = config
    help_dialogs = []

    def capture_help(dialog):
        help_dialogs.append(dialog)
        dialog.reject()
        return 0

    monkeypatch.setattr(QDialog, 'exec', capture_help)
    try:
        show_shortcuts_help(parent)
        content = help_dialogs[0].findChild(QTextBrowser).toPlainText()
        assert '戻る: T' in content
        assert '削除: 未設定' in content
        assert '検索・評価・タグ絞り込みを解除: 無効（操作をキャンセルに含む）' in content
        assert '左に回転: Y' in content
        assert tr('数字キー＋Sの間隔指定: 無効') in content
        for scope_specs in SPECS_BY_SCOPE.values():
            for spec in scope_specs:
                assert tr(spec.label) in content
                assert spec.action_id not in content
    finally:
        parent.close()
        install_ui_language('ja')
