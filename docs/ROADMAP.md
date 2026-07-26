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
17. Sprint 15：日常操作の表示・同期改善（完了）
    - selection ghost修正とold／new currentの再描画
    - 全画面の上下端UI overlay表示
    - Extra Compact、サムネイル間隔、セル内余白
    - お気に入りフォルダと5種のサイドバーレイアウト
    - current folderへのgeneration付きツリー同期
    - お気に入り／ツリーをコピー・移動先として利用
18. Sprint 15追補：高DPIサムネイル鮮明化（完了）
    - DPR-aware high-resolution thumbnail cache
    - display size／cache physical sizeの分離
    - multi-resolution cache selectionとprogressive replacement
    - WebP／PNG encoding quality review
    - fractional-DPIでのphysical pixel snapping
19. Sprint 16：Explorer型D&Dとキャッシュ保持制御（完了）
    - BrowserGridMetricsによる高密度filename配置
    - 通常file dragとShift rubber bandを分離したExplorer型選択
    - Viewer、folder item、tree、favoriteへのlocal path drop
    - 同一volume move／異なるvolume copyと修飾キーoverride
    - 全画面chromeの0ms非表示と状態変更後の再評価
    - thumbnail cacheのvariant最大2、source最大4、age cleanup
20. Sprint 17：全画面・一覧・設定・dropの信頼性改善（完了）
    - fullscreen chromeの単一状態調停とcursor idle hide
    - hidden／system／unsupported項目の表示policy
    - visible-only高品質thumbnail永続化とsession統計
    - folder treeの中央表示、ancestor-depth rebase、全体表示復帰
    - お気に入りfolderのsingle-click navigation
    - Viewer child widget／fullscreen overlayのnative drop転送
    - scroll可能なSettingsDialogとconfig round-trip監査
    - 追補：Browser pointer state machineとpath基準drag
    - 追補：wide-imageのexact open identityと遅延寸法後の再配置
    - 追補：folder treeのclick-confirmed navigation
    - 追補：FavoriteRowMetricsによる高密度お気に入り行
21. Sprint 18：汎用ファイルプレビューとBrowser中央ドロップ（完了）
    - `PreviewResultKind`による静かな未生成と実失敗の分離
    - 高DPIのWindows関連付けアイコンとShellサムネイル
    - UTF-8／UTF-16／UTF-32／CP932の安全なテキストプレビュー
    - Windows Shell優先と任意FFmpeg fallbackによる動画サムネイル
    - Viewer open可否とBrowser preview可否の能力分離
    - 外部drop項目の親フォルダ移動、path基準複数選択、中央表示
    - `focus_only`／`focus_and_open`設定とscan generation連携
    - Viewer左クリックの論理1ページ送りと従来の表示単位操作の共存
22. Sprint 18追補：Viewer入力と全画面下部UIの分離（完了）
    - fullscreen page-slider wheel isolation
    - larger bottom reveal target
    - configurable one-page canvas click
    - sliding spread navigation
    - pan／double-click／overlay／dropとの入力排他
23. Sprint 19：Explorer型ファイル操作の完成（完了）
    - Qt非依存FileOperationPlanと非同期preflight
    - file／folder衝突の一括解決、skip／keep both／replace／merge
    - 4 MiB chunk copy、byte進捗、速度、ETA、安全な一時出力
    - ApplicationController所有の直列FileOperationQueue
    - 非モーダル進捗パネルとBrowser終了後のfallback
    - 最近使ったコピー／移動先とお気に入り／指定先メニュー
    - Viewer使用中のsource／replace destination解放
24. Sprint 19追補：Explorer経路の完全性・動画・中央D&D安定化（完了）
    - MOVEのdestination存在＋source不存在の物理事後条件
    - source削除失敗／部分成功とcut／MetadataStore整合
    - smart／one-third動画frame、SAR／DAR／rotation、共通ratio／crop
    - Shell placeholderからFFmpeg finalへの置換
    - Browser viewport drop routingと外部／内部MIME分離
25. Sprint 20優先修正：お気に入りフォルダ移動の応答性改善（完了）
    - release確定時の即時navigateと同一path dedupe
    - GUI threadの同期path検証・DB再queryなし
    - interactive scanner priorityとlatest generation wins
    - first list paint後のtree sync／thumbnail開始
    - DEBUG／テスト限定の区間performance trace
26. 後続候補：ファイル操作と項目表示の拡張
    - ファイル操作履歴と安全に可能なrename／moveのUndo
    - pause／resume、失敗項目の再試行
    - 右ドラッグ後のcopy／moveメニュー
    - ネットワーク転送再開
    - フォルダ／画像／書庫・PDF等の種類フィルタ
    - 再帰的なサブフォルダ表示
    - PDFパスワード入力、PDF目次
    - 画像中央クロップ／余白除去、Viewerメモリ予算調整
27. 後続候補：レート／タグ編集UIと検索
    - 基本ナビゲーションとファイル操作の安定後に着手
    - ZipPla `{zpi$...}` の明示的な読み取り互換
    - タグ・レート検索と絞り込み
    - サムネイル上のレート／タグ表示
28. 公開前ライセンス監査、コード署名の将来検討
