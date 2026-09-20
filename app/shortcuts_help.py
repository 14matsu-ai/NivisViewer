from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from .i18n import tr


def show_shortcuts_help(parent: QWidget) -> None:
    config = getattr(parent, "config", None)
    settings = (
        config.data
        if config is not None and hasattr(config, "data")
        else getattr(parent, "settings", {})
    )
    close_key = str(settings.get("viewer_close_shortcut", "") or "")
    close_key = close_key or tr('未設定')
    QMessageBox.information(
        parent,
        tr('ショートカット一覧'),
        "\n".join(
            [
                tr('Right: 次ページ'),
                tr('Left: 前ページ'),
                tr('Alt+Left / Alt+Right: 表示履歴を戻る / 進む'),
                tr('Space / PageDown: 下スクロールまたは次ページ'),
                tr('Backspace / PageUp: 上スクロールまたは前ページ'),
                tr('Shift+Right: 1ページ進む'),
                tr('Shift+Left: 1ページ戻る'),
                tr('矢印キー: 拡大・原寸表示では画像の移動を優先（Shiftで大きく移動）'),
                tr('Home / End: 先頭 / 最後'),
                tr('G: ページ指定'),
                tr('D: 単ページ / 見開き切替'),
                tr('Shift+R: 左綴じ / 右綴じ切替'),
                tr('F: 全画面切替'),
                tr('Esc: 拡大鏡・ジェスチャー解除（なければ全画面解除）'),
                tr('Z: 拡大鏡の切り替え'),
                tr('+ / - / Ctrl+Wheel: ズーム'),
                tr('0: ウィンドウに合わせる'),
                tr('S: スライドショー開始/停止（キーを離したとき）'),
                tr('1〜9を押しながらS（逆順も可）: 指定秒数でスライドショー開始'),
                tr('Shift+S: 間隔を選択し、Enterでスライドショー開始'),
                tr('B / Ctrl+B: ブックマーク切替'),
                tr('Viewerを閉じるキー: {p0}', p0=close_key),
                tr('Ctrl+PageDown / Ctrl+PageUp: 次 / 前の本'),
                tr('Ctrl+C: 現在画像をコピー'),
                tr('Ctrl+Shift+C: 現在画像のパスをコピー'),
                tr('Ctrl+Alt+C: 現在の表示をコピー'),
                tr('Ctrl+I: ページ情報'),
                tr('Double Click: 全画面切替'),
            ]
        ),
    )

