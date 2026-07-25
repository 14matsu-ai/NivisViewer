# Roadmap

1. Sprint 0：基盤整理と回帰テスト（完了）
2. Sprint 1：ApplicationController導入と責務分離（完了）
3. Sprint 2：ViewerWindowの独立（完了）
4. Sprint 3：BrowserWindowとフォルダ／サムネイル表示（完了）
5. Sprint 4：設定画面、見開き密着表示、永続サムネイルキャッシュ（完了）
6. Sprint 5：マウスジェスチャー、XButton書庫移動（完了）
7. Sprint 6：メタデータDB、ブックマーク、閲覧履歴（完了）
8. Sprint 7：BrowserWindowの基本ナビゲーション強化（完了）
9. Sprint 8：表示密度設定と並び替え（完了）
10. Sprint 9：大量項目の非同期・増分列挙（完了）
    - 可視範囲優先のサムネイル要求
    - 先読み範囲
    - 高速スクロール中の生成抑制
11. Sprint 10：安全なファイル操作（完了）
    - 名前変更、コピー、切り取り、貼り付け、指定先コピー／移動
    - Windowsごみ箱、新規フォルダ、複数選択
    - 直列非同期worker、進捗、キャンセル、部分失敗
    - MetadataStore、BrowserNavigationHistory、Viewer使用中確認
12. Sprint 11：RAR／7z／CBR／CB7対応（完了）
    - Windows関連付けからのWinRAR／7-Zip自動検出と明示指定
    - 拡張子ごとのbackend選択、認識済みCLIへの安全な変換
    - WinRAR公式コンソールCLIとSevenZipBackendの共存
    - 非同期の書庫一覧取得と単一ページstdout抽出
    - Browser、サムネイル、Viewer、前後の本、履歴への統合
    - 通常RARと`.part1.rar`を初期範囲とし、7z分割は正式対応外
13. Sprint 12：PDF対応（完了）
    - pypdfium2 v5の遅延読み込みとアプリ全体で直列化したPdfiumService
    - Browser、表紙サムネイル、Viewer、履歴、前後の本への統合
    - 96 logical DPI、DPR、64px bucket、リサイズ／ズーム後の再レンダー
14. Sprint 13：Windowsポータブル配布、起動統合、任意の関連付け、安定性試験（完了）
    - PyInstaller one-folder、portable.flag、ZIP／SHA-256
    - AppPaths、起動引数、QLocalServer単一インスタンス
    - opt-inのOpen With／Capabilities登録とローカル診断ログ
15. Sprint 14：Browser一覧の高速化と固定セルグリッド（完了）
    - Compact／Standard／Comfortable／Largeの表示プリセット
    - 固定サムネイル領域、固定タイトル領域、左下の種別バッジ
    - 可視範囲優先、2画面先読み、高速スクロール抑制、サイズbucket再利用
    - 大量項目のパス検索を定数時間化
16. Sprint 14追補：Viewer最優先とサムネイル表示統合（完了）
    - Viewer current専用実行枠とfirst-frame gate
    - QImageReader優先のWebP decode、Pillow fallback、lazy page metadata
    - 9種類の固定frame ratioとletterbox／center crop／smart crop
    - 2次元thumbnail bucketとsmart crop rect cache
    - Explorer関連付けアイコンとサムネイルを隠さない選択枠
17. 後続候補：実機不具合修正、ポータブル版UI磨き込み
    - ファイルドラッグ＆ドロップ、PDFパスワード入力、PDF目次
    - 画像中央クロップ／余白除去、Viewerメモリ予算調整
18. 後続候補：レート／タグ編集UIと検索
    - 基本ナビゲーションとファイル操作の安定後に着手
    - ZipPla `{zpi$...}` の明示的な読み取り互換
    - タグ・レート検索と絞り込み
    - サムネイル上のレート／タグ表示
19. 公開前ライセンス監査、コード署名の将来検討
