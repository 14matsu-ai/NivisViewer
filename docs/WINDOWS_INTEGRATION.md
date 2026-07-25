# Windows起動統合

起動引数は日本語、空白、UNC、相対パス、複数パスを受け取ります。同一要求内の重複を除き、1件目は設定または`--new-window`／`--reuse`、2件目以降は新しいViewerで処理します。`--browser-only`ではViewerを開きません。

通常起動では同じユーザー・同じportable folderごとに1つのQLocalServerを使用します。IPCは長さprefix付きUTF-8 JSONで、pickleやshell実行は使いません。セカンダリはDBとPDF serviceを開く前に転送し、ACK後に終了します。

設定画面の「Windows連携」から、画像、漫画書庫、PDFを個別にOpen With候補へ登録できます。HKCU配下のNivisViewer ProgID、Applications、Capabilities、RegisteredApplications、OpenWithProgidsだけを所有します。任意で「NivisViewerで開く」context verbも追加します。HKLM、拡張子の既定値、UserChoiceは変更しません。

既定アプリはWindows設定画面で利用者が選択します。portableフォルダを移動した場合は不一致状態を表示するため再登録してください。フォルダ削除前には登録解除を推奨します。解除はNivisViewer所有値だけを削除し、他アプリのOpenWith値は保持します。
