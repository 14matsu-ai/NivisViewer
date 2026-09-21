from __future__ import annotations

from html import escape

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from .shortcut_catalog import SPECS_BY_SCOPE, normalize_shortcut_bindings


def show_shortcuts_help(parent: QWidget) -> None:
    """Show the complete, language-independent shortcut inventory."""

    config = getattr(parent, "config", None)
    settings = (
        config.data
        if config is not None and hasattr(config, "data")
        else getattr(parent, "settings", {})
    )
    bindings = normalize_shortcut_bindings(settings.get("shortcut_bindings"))

    def binding_text(scope: str, action_id: str) -> str:
        if (
            scope == "browser"
            and action_id == "browser_clear_filters"
            and bool(settings.get("browser_cancel_clears_filters", True))
        ):
            return tr("無効（操作をキャンセルに含む）")
        values = bindings.get(scope, {}).get(action_id, [])
        return ", ".join(values) if values else tr("未設定")

    def html_text(value: object) -> str:
        return escape(str(value), quote=True)

    def scope_lines(scope: str) -> list[str]:
        return [
            f"{html_text(tr(spec.label))}: "
            f"{html_text(binding_text(scope, spec.action_id))}"
            for spec in SPECS_BY_SCOPE[scope]
        ]

    chord_state = (
        tr("数字キー＋Sの間隔指定: 有効")
        if bool(settings.get("viewer_slideshow_chord_enabled", True))
        else tr("数字キー＋Sの間隔指定: 無効")
    )
    browser_body = [
        *scope_lines("browser"),
        "",
        html_text(tr("Browser の入力コンテキスト")),
        html_text(tr("選択中の項目やジェスチャー操作の途中では、キャンセルキーが先に操作を解除します。")),
        html_text(tr("絞り込み解除はコピー／切り取り候補解除より先に行います。")),
        html_text(tr("アドレス欄や文字入力中は、Escapeを編集操作に使います。")),
        html_text(tr("お気に入り切替は、右クリックした項目ではなく現在開いているフォルダを対象にします。")),
    ]
    viewer_body = [
        *scope_lines("viewer"),
        "",
        html_text(tr("Viewer の入力コンテキスト")),
        html_text(tr("拡大・原寸表示では矢印キーで画像を移動し、Shiftで移動量を増やします。")),
        html_text(tr("数字キー＋Sの間隔指定は、キーを押す順序を問いません。")),
        html_text(chord_state),
        html_text(tr("一時状態の解除は拡大鏡・ジェスチャーを優先し、なければ全画面を解除します。")),
    ]
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("ショートカット一覧"))
    dialog.setModal(True)
    dialog.setSizeGripEnabled(True)
    layout = QVBoxLayout(dialog)
    text_edit = QTextBrowser(dialog)
    text_edit.setObjectName("shortcut_help_text")
    text_edit.setReadOnly(True)
    text_edit.setOpenLinks(False)
    text_edit.setOpenExternalLinks(False)
    text_edit.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
    browser_heading = html_text(f"[Browser] {tr('Browser ショートカット')}")
    viewer_heading = html_text(f"[Viewer] {tr('Viewer ショートカット')}")
    body_css = "font-size: 10pt;"
    heading_css = "font-size: 14pt; font-weight: 700; margin: 8px 0 4px 0;"
    body_lines = "<br>".join(
        f"<div style=\"{body_css}\">{line or '&nbsp;'}</div>"
        for line in browser_body
    )
    viewer_lines = "<br>".join(
        f"<div style=\"{body_css}\">{line or '&nbsp;'}</div>"
        for line in viewer_body
    )
    text_edit.setHtml(
        "<html><body>"
        f"<h2 style=\"{heading_css}\"><b>{browser_heading}</b></h2>"
        f"{body_lines}"
        f"<h2 style=\"{heading_css}\"><b>{viewer_heading}</b></h2>"
        f"{viewer_lines}"
        "</body></html>"
    )
    layout.addWidget(text_edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    screen = parent.screen() if parent is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(680, max(240, available.width() - 80))
        height = min(720, max(220, available.height() - 100))
        width = min(width, available.width())
        height = min(height, available.height())
        dialog.resize(width, height)
    else:
        dialog.resize(620, 600)
    dialog.exec()

