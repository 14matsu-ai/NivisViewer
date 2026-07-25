# Release checklist

- clean checkout相当のソースとバージョンを確認
- 全テスト、compileall、diff check
- clean one-folder buildとfrozen smoke
- JPEG、PNG、WebP、GIF、TIFF、ICO、ZIP、CBZ、PDFを手動確認
- 利用可能ならRAR／CBR、7z／CB7を外部backendで確認
- 日本語・空白パス、別working directory、Cドライブ以外で確認
- portable.flagとdata保存先、読み取り専用時の制限を確認
- 二重起動、Explorer連続open、Viewer前面表示を確認
- 関連付け登録、Default Apps、解除、他アプリ値保持を確認
- licenses manifestとすべての本文を人手監査
- ZIP内容にユーザーデータと外部archive binaryがないことを確認
- ZIPのSHA-256、ウイルススキャン、SmartScreen案内を確認
- orphan process、PDF handle解放、終了時間を確認
- Git tag候補とGitHub Release候補を別途レビュー
