# AI / SVG 実装・検証記録

作業先: `C:/Users/yurig/Documents/Codex/NivisViewer`。基準: `b6b092880fe85c9ef13dca9038ab57c9970e7dd0` (1.0.20)。開始時の未追跡設計書 `AI_SVG_IMPLEMENTATION_PLAN.md` を保持。古い7723 worktreeからの転送、既存変更の破棄、build、commit、pushは行っていない。

## 実装

- `vector_image_decoder.py`: SVGをQtSvgで静止画描画。透過、viewBox（負の原点を含む）、px/pt/pc/in/cm/mm、サイズ指定に対応。入力はSVG 8 MiB / AI 64 MiB、描画は16,777,216 pixels / 一辺16,384 pixels以内。SVGのXMLは深さ128、要素50,000まで。Qtへ渡す前にXMLを解析し、DOCTYPE/entity/processing instruction、外部href・xml:base・CSS url/@import、スクリプト・アニメーション等を拒否する。内部#idと4 MiB以内のPNG/JPEG data URIを許可。SVGネイティブ同時描画は最大2。
- AIは先頭がPDFヘッダーであるPDF互換保存の先頭ページのみ。bytesを保持したまま既存PdfiumServiceの所有スレッドで開く・描く・閉じる。元のAI/PDFを一時保存しない。透過を維持し、PDF固有のページ回転を二重適用しない。96 dpiの論理寸法と最終ラスター寸法を分離し、native端数は宣言した出力寸法に合わせる。文書・ページ・bitmap・Pillow像は終了時に解放する。
- Folder / ZIP / RAR・7z共通bytes経路、Browserサムネイルと寸法表示、Viewerページ一覧を接続。元ファイル・エントリーの識別子を維持し、Ctrl+Tは元形式のデータを渡す。
- RasterBookRuntimeの目標寸法描画分岐、論理寸法、サイズ別source cache、renderer版キー、既存世代照合・予約枠寿命へ接続。ズーム・DPI・回転・見開き・拡大鏡は不足するサイズを再描画し、通常表示を保持する。キャンセル中のnative処理は強制終了しない。AIはサービス停止でconsumer Futureが先に完了してもnative所有が終わるまで呼出元の予約を保持する。
- `require_local`を入力読み込み・寸法取得の前に実行。書庫は外側のパスも検査する。オンライン項目除外・保存済みプレビューの既存方針を維持。
- AI/SVGを個別に読み込みON/OFF（初期ON）。設定→書庫→AI・SVG画像、再起動後に反映。ツールチップと「？」、設定保存、日/英/簡体/繁体の説明を追加。XCFの初期OFFは維持。
- 新規依存なし。既存PySide6/QtSvg・pypdfium2を利用し、specにPython QtSvg hidden importを追加。README非同梱方針は変更していない。

主な変更: `supported_formats.py`, `application_controller.py`, `config_manager.py`, `settings_dialog.py`, `image_source.py`, `pdfium_backend.py`, `pdfium_service.py`, `thumbnail_provider.py`, `browser_image_detail.py`, `browser_window.py`, `zip_raster_book_runtime.py`, `viewer_page_list_runtime.py`, `viewer_window.py`, `viewer_widget.py`, 翻訳カタログ、4言語README、spec。既存ZipPlaFork通知は維持。

## 確認済み

Windows / Python 3.11.9 (`.venv311/Scripts/python.exe`) / PySide6 6.11.2 / `QT_QPA_PLATFORM=offscreen`。ユーザー画像を使用せず、すべて合成SVG、合成の複数ページPDFを.aiとして保存したfixture、合成JPEG。

- 構文: `python -m compileall -q app tests/test_vector_images.py scripts/benchmark_vector_jpeg_regression.py` 成功。`git diff --check` 成功。
- 最終関連回帰: **467 passed / 1 failed、60.36秒**。
- 失敗は `test_command_line.py::test_version_text_uses_central_version` の固定期待値1.0.17と現行1.0.20の不整合。基準コミットにも期待値1.0.17、`app/version.py` に1.0.20があることをgit showで確認。今回このテスト・バージョンは変更していない。
- 最後にAI寸法上限/SVG深さ上限を追加し、**新規29ケースは29 passed、5.97秒**。構文・差分チェックも再確認。
- 新規ケース: SVGの透過・負原点・pt寸法・目標サイズ・内部gradient/埋込PNG、不正寸法、外部参照がQtに到達しないこと、DTD拒否、キャンセル、実PDFiumの先頭ページ/固有回転/不正AI/所有スレッド、Folder/ZIPとサムネイル、ページ一覧、ズーム/DPI/拡大鏡サイズ、実ViewerのLTR/RTL・回転見開き・拡大鏡・本切替、Ctrl+T経路の元データ保持、設定保存、オンライン入口、無効設定。
- 実Viewer統合テストはoffscreenのViewerWindow/ViewerWidgetを使用。外部アプリ起動はテスト共通ガードで禁止し、Ctrl+Tは外部起動直前のパス検査をfake化して、エクスポートされた合成ファイルの内容を照合した。
- 翻訳の固定語数テストは追加7文言分（1069→1076）と技術名SVG/Illustratorを更新。全言語のキー・書式一致検証は通過。

関連回帰の実行対象:

```text
test_vector_images.py test_cloud_files.py test_creative_image_decoder.py
test_creative_review_regressions.py test_folder_raster_book_runtime.py
test_zip_raster_book_runtime.py test_pdf_support.py
test_viewer_resampling_magnifier.py test_pdf_magnifier_integration.py
test_thumbnail_provider.py test_sprint16.py test_ui_language_chinese.py
test_viewer_page_list_runtime.py test_config_manager.py test_command_line.py
test_system_file_opener_explorer.py
```

## 通常JPEGの修正前後比較

`benchmark_vector_jpeg_regression.py`。基準コミットのappをgit archiveでtempへ展開し、同一Python環境・同一ZIP・同一入力列で実行。現行作業ツリーを切り替えていない。4096×6500 / 8ページ / quality 90 / ZIP_STOREDの合成JPEG、1600×1000ウィンドウ、single、各条件2回。coldは最初のページ表示直後、warmは全処理完了後に同じ入力列。高速入力は実Qtループを進めて20 ms間隔でホイールを投入。その他は各表示コミットを待つ。

以下は全入力列の完了までの平均ms。初回オープンは含めない。

|条件|修正前 cold|修正後 cold|修正前 warm|修正後 warm|
|---|---:|---:|---:|---:|
|順送り|804.39|809.04|3.23|3.16|
|末尾→逆方向|816.58|806.00|5.97|6.62|
|往復×2|815.31|811.47|13.50|12.95|
|高速連続入力|810.28|806.96|143.71|143.47|

cold計上ピークは前後とも74,316,944 bytes、warmは71,153,536 bytes。cold高速入力は前後とも35イベント中7回受付、8ジョブ/8 source miss、キャンセル0、古い結果0、エラー0。warm高速の約140 msには7回×20 msの入力間隔を含む。これはnative処理速度ではない。warmの通常送りは同期コミットまでを測り、画面表示/モニターのpaint待ちを保証しない。OSファイルキャッシュは排除しておらず、HDD起動待ちの測定ではない。実行ごとの数値とruntime metricsは `AI_SVG_JPEG_BENCHMARK.json` に保存。

この比較では大きな通常JPEGの退行は見られないが、サンプル2回のoffscreen補助証拠であり、実機体感改善や小さな差の有意性を主張しない。

## 未確認・非対応

- 実Illustratorが保存した様々なAI、フォント依存・複雑なSVG効果の表示一致、実機高DPIでの体感、Windowsの実関連付けダイアログ、実クラウド/HDDの状態遷移。
- RAR/7zには共通デコーダ入口を接続したが、今回AI/SVG入り実RAR/7zと外部展開プログラムの実行は未確認。
- QtSvgのPythonモジュールは実行環境でimport確認、spec追加済み。ビルドを指示されていないため、生成EXEへの同梱は未検証。
- SVGの外部参照・animation・script・foreignObject、一部効果/フォント、非PDF互換AI、EPS、AIT、SVGZ、多アートボードの全ページ展開、編集データ再現は対象外。PDF互換部が再保存案内ページだけなら、その案内ページを表示する。
- native処理の即時中断やRSSの厳密な上限は保証しない。入力/出力上限、既存worker数、予約枠の寿命、結果採用のキャンセルで制御する。

実機確認時の起動コマンド（実在するPython 3.11.9環境を確認済み、本作業では起動していない）:

```powershell
cd "C:\Users\yurig\Documents\Codex\NivisViewer"
.venv311\Scripts\python.exe main.py
```


## 独立レビュー追補: SVGメタデータ互換性 (P2)

RDF名前空間の非描画メタデータだけで自己完結SVGが拒否される問題を修正。
修正前に追加回帰1件の失敗を再現した。SVG metadataサブツリーとInkscape/Sodipodi名前空間の編集用要素・属性を除去し、残った描画XMLを検査する。Qtへはこの検査済みツリーを再シリアライズしたbytesのみ渡す。削除要素に続くmixed-content textは維持する。未知の描画名前空間、外部image href、CSS外部url等の拒否とDTD/entity/構造上限は維持。メタデータ内のRDF resource URLはQtへ渡らず、名前空間URI自体を取得指示と解釈しない。source renderer版は2へ更新。

合成回帰では、RDF+編集用メタデータ付きSVGとplain SVGのQImage画素一致、Qtが受け取ったXMLに削除メタデータがないこと、未知の描画要素/外部参照がQtへ到達しないことを確認。新規テスト全30件通過、クラウド回帰込み45 passed / 6.11秒。compileallとdiff --check成功。

同一4096×6500・8ページJPEG、2回ずつのoffscreen評価も再実行成功。cold平均ms（順送り/逆方向/往復/高速入力）: 811.72, 804.39, 812.62, 804.43. 生データはJSONのmetadata_followupに保存。実アプリ・外部アプリ起動、build、commit、pushなし。前述の実機未確認事項は継続。

## 1.0.21 リリース確認

リリース環境（Python 3.11.9 / PySide6 6.11.1）で関連テスト490件が通過。構文チェック、diff --check、4096×6500・8ページのoffscreenナビゲーション評価を完了した（`out/release-1.0.21-navigation.json`）。

PyInstallerビルドとportable検証を完了。EXEの版番号1.0.21、組み込みPythonアーカイブのvectorデコーダ、QtSvg.pydとQt6Svg.dllの同梱を静的に確認した。プロジェクトREADMEはルートの日本語版1つのみで、他言語版や`_internal`への重複はない。実アプリ起動と実ファイルでの表示確認は行っていない。
