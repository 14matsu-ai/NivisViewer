from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from app.config_manager import ConfigManager
from app.diagnostics_dialog import DiagnosticsDialog, diagnostic_text
from app.i18n import install_ui_language, tr
from app.pdfium_service import PdfiumService
from app.settings_dialog import SettingsDialog
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
    messages = []
    diagnostics = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args))

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
        assert messages[0][0] is dialog
        assert messages[0][1:] == messages[1][1:]
        assert messages[0][1] == tr('ショートカット一覧')
        assert len(messages[0][2].splitlines()) == 27
        assert tr('Right: 次ページ') in messages[0][2]
        assert tr('Ctrl+I: ページ情報') in messages[0][2]
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
