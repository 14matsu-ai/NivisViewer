# 関連付けで開くショートカット

BrowserとViewerへ初期割り当てCtrl+Tの「関連付けで開く...」を追加。
browser_open_with / viewer_open_withとして通常のショートカット設定・保存を使用する。
Windowsの関連付け済みアプリを直接起動する操作。関連付けがない場合だけ
SystemFileOpenerの既存処理でアプリ選択画面を開く。Browser右クリックも同じ挙動。
Browserは現在項目が対象。Viewerは描画済み領域からマウス下のページを選び、
マウスが画像上にない場合は表示中の主ページを選ぶ。LTR/RTLは画面の左右と
ページ番号を推測せず、実際に描いたレイアウトで判定する。
フォルダ画像は元ファイル、書庫画像は選んだ1枚を元形式のままprofileの
data/external-open配下へworkerで取り出す。PDFは元PDFファイルが対象。
外部アプリで使う書庫画像コピーは、開いた直後に削除しない。編集しても元書庫は変わらない。
Viewerは準備と非同期パス確認後も要求ID・本の世代・表示ページを照合し、
別の対象に切り替わっていれば開かない。Viewer終了時には取り出し要求をキャンセル。

設定・Browser・Viewer binding検査は78件成功、新規Browserのfocus修正後は
両画面の新操作2件が成功。外部アプリ起動は禁止し、picker呼び出しを差し替えて検査。
構文チェック成功。大画像ZIP評価はout/open-with-shortcut-navigation.json。
広いViewer監査では画像コピー幅の既存検査1件が失敗（期待800に対し361）。
HEADの変更前Viewerをメモリ上に読み込んだ単独検査でも同じ失敗を確認した。
今回のショートカット変更では画像コピー処理を変更していない。

## Ctrl+Tと見開きへの修正

旧メニューQActionにCtrl+Tを割り当て、メニューバーを隠すと発火しないことを再現。
独立したWindowShortcutへ変更し、長押しのリピート起動を無効化した。
通常の.venv311実行ファイルとapp/viewer_window.pyの実パスを確認し、
メインのDocuments/Codex/NivisViewerが参照されていることを確認。
ユーザー設定にもviewer_open_withのCtrl+Tが保存されていた。

関連84件成功。実Folder/ZIP、LTR/RTL、メニュー非表示、描画済み見開き左右の
それぞれへCtrl+Tを送信し、外部起動境界を差し替えて検査した。
ZIPは取り出し結果が選択したエントリーの原データと一致することを確認。
失効した要求の完了通知からは起動しないことを確認。
構文チェック成功、大画像ZIP操作評価out/open-with-spread-navigation.jsonは完了。
実アプリ・外部アプリは起動していない。RAR/7z実行ファイルによる実抽出は未検証。

## 関連付け済みアプリへの直接起動

Browser/Viewerともopen_with_default_applicationを呼び、関連付けがない場合だけ
既存のpicker fallbackを使用するよう変更。既定アプリの変更は行わない。
Browserの既存検査は非同期パス確認の完了を待つよう更新。
関連89件成功（直接起動、未関連付けfallback、左右ページ、ショートカット設定）。
構文チェック・差分チェック成功。合成大画像ZIPの順送り・逆方向・往復・高速入力は
out/open-associated-navigation.jsonで完了。実外部アプリ起動は未検証。
test_critical_followup全体を含む広い実行では複数の失敗が出て中断したため、
全体成功とはしていない。今回対象外の失敗について原因確認は未完了。
