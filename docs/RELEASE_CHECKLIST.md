# Release checklist

## Publication approval gates

Current project license: **AGPL-3.0-or-later**. NivisViewer has not been publicly
released. See
`PROJECT_LICENSE.md` and `docs/RELEASE_LICENSE_AUDIT.md`.

This checklist is not permission to publish, build, commit or push. Work in a
separately approved isolated source/output tree; preserve the current worktree.

- [ ] Owner explicitly approves the exact candidate for publication.
- [ ] Reconcile release pins with the tested environment and freeze complete
      dependency/tool/source hashes (current pins do not match the installed build).
- [ ] Inventory actual Qt modules/plugins, especially GPLv3 Qt Virtual Keyboard
      and Qt PDF's separate PDFium tree; complete applicable copyright/notices.
- [ ] Complete exact Corresponding Source, build/rebuild instructions and any
      required installation/relinking/replacement information; test that route.
- [ ] Provide and verify the chosen license-compliant, version-matched source
      delivery mechanism alongside binaries; a LICENSE file alone is insufficient.
- [ ] Review Python/OpenSSL/VC runtime/other native notices and redistribution terms.
- [ ] Confirm About, root/internal package license notices and source links agree.
- [ ] Separately complete secrets/privacy/Git-history, asset/contribution-rights
      and source-package allowlist review; exclude tracked portable backups.
- [ ] Reviewer signs off the manifest's unresolved warnings; collection success
      or `--strict` success is not a compliance certificate.

## Functional / packaging review

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
