# Viewer refinements 統合・検証記録（2026-09-23）

## 基準と統合

現在の HEAD は `6185230`（Browser thumbnail RAM cache）。開始時に tracked 差分はなく、既存の未追跡7ファイル（2 probe、2文書、3 scripts）は変更していない。packet の `78af593e8e7757a36201d352d698d18e3bfbcfed` には戻していない。

`CODEX_REQUEST.txt`、README、検証記録、manifest、plan、patch、live code を照合した。付属 `PREPARE_PATCH.py --repo … --output …` を一時ディレクトリで実行し、6ファイル／19 text edits／既適用0件、構文検査と `git apply --check` が成功。preflight 自身は repository を変更していない。生成差分の各hunkを適用し、古い全文ファイルの上書きはしていない。Browser実装との競合hunkはなく、Browser側に変更を加えていない。

候補に対する追加修正は、不正な memory byte sample（負値、available > total、非整数／NaN／無限値等）で成長観測をリセットして最後の有効値を保つ検証。既存ZIP統合テストの「actual-sizeは常にcurrent-only」という旧期待値だけは、新契約へ変更した。他の容量・pixel fidelity・source lifetime assertion は維持。

## 実装

| 箇所 | 動作 |
| --- | --- |
| `ResolvedViewerMemoryPolicy.observe_memory_pressure` / `_maybe_expand_auto_limit` | Autoのみ、active・live需要・実cache使用率75%以上・pressure ceiling87.5%以上で継続10秒を確認。5秒以上ごと最大256 MiB、上限32 GiB。欠測／不正sample／時間の不連続／需要消失で観測をリセット。固定modeは増えない。 |
| `set_active` / `target_bytes` | active→inactiveの10秒だけ保持猶予。繰り返し通知で延長しない。実pressureは猶予中でも即時に優先。既存5秒sampleで期限反映。 |
| `RasterBookRuntime.auto_cache_growth_requested` | 現在の未完・容量制約workを見る。累積失敗counterだけでは増加要求しない。 |
| `ViewerWindow._sample_viewer_memory_pressure` | active raster Windowだけ成長を要求。既存 `_apply_raster_memory_policy` → BookSession 経由で現在／将来runtimeの予算を反映。 |
| `_raster_background_allowed` / `_zip_runtime_request` | actual-size／manual zoom／nearestのfull-source fidelityとbackground可否を分離。magnifier中はcurrent-only。exact admissionと予約を維持。 |
| `_on_zoom_changed` / `_release_settled_raster_warmup` | current要求は直ちに更新。150 ms single-shotはbackgroundだけを制限し、最新の同一book/sourceのwork orderを更新。request ID、履歴、current再publish、保留入力ゲートを変更しない。deactivate/shutdownでtimer/contextを破棄。 |
| `_refresh_raster_decode_bounds` / `matches_render_spec` | complete render specが同一ならclearを省略。A→B→Aも同じ。異なるspecは既存invalidateへ。current refreshは常に維持。 |

新scheduler、worker増設、依存ライブラリ、共通メモリ管理者は追加していない。既存ZipPlaFork由来runtimeの固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、AGPL-3.0-or-later表示、`ZIPPLAFORK_COMPARISON.md` の由来記録とlicense通知は維持。本作業で新たなZipPlaコード移植はしていない。

## 実行済みテスト

Python 3.11.9、PySide6/Qt 6.11.2、Pillow 12.3.0。全Qt実行で `QT_QPA_PLATFORM=offscreen`、一時生成画像、fake入力／制御clockのみ。通常の実アプリは起動していない。

以下27モジュール、重複再実行を除き **522 passed**。各モジュールを原則別プロセスで実行。標準コマンドは `.venv311\Scripts\python.exe -B -m pytest -q -x -p no:cacheprovider <module>`。

| tests/ 内のモジュール | passed |
| --- | ---: |
| test_viewer_memory_policy.py | 20 |
| test_viewer_memory_refinements.py | 42 |
| test_viewer_refinements_qt.py（packet未実行の7ケース） | 7 |
| test_viewer_refinement_integration_qt.py | 5 |
| test_viewer_full_source_matrix_qt.py | 72 |
| test_viewer_window.py | 24 |
| test_zip_raster_viewer_integration.py | 18 |
| test_zip_raster_book_runtime.py | 26 |
| test_folder_raster_book_runtime.py | 36 |
| test_viewer_resampling_magnifier.py | 50 |
| test_viewer_widget.py | 41 |
| test_raster_confirmed_cost_regression.py | 7 |
| test_raster_scaled_fallback_safety.py | 1 |
| test_raster_reachable_neighbors.py | 12 |
| test_raster_layout_metadata.py | 1 |
| test_browser_thumbnail_memory_policy.py | 39 |
| test_browser_thumbnail_memory_integration_qt.py | 4 |
| test_browser_thumbnail_memory_qt.py | 5 |
| test_viewer_navigation_policy.py | 8 |
| test_viewer_navigation_feedback.py | 13 |
| test_viewer_presentation_state.py | 7 |
| test_viewer_xbutton_navigation.py | 9 |
| test_navigation_benchmark_observation.py | 7 |
| test_magnifier_margins.py | 30 |
| test_pdf_magnifier_integration.py | 27 |
| test_raster_admission_policy.py | 4 |
| test_raster_warmup_planner.py | 7 |

追加統合テストでは実ViewerWindow/QTimer、最新zoomへの集約、保留sliderゲートの維持、同一spec clear回避／実変更invalidateを確認。24枚の1000×1400 JPEGを実際にcacheへ保持し、128 MiB予算の75%以上を使用して容量制約停止したruntimeが、余裕回復後に384 MiBへ成長、24枚すべてを準備できることを確認。BookSessionの将来予算も更新される。Qt WindowDeactivateイベントと制御clockで、短い猶予と10秒後のtarget縮小、current page保持を確認した。

72ケースは ZIP 1 lane／Folder 2 lane × actual-size／manual zoom／nearest × portrait／landscape／mixed × single／spread × known／unknown dimensions。各ケースで同一JPEGに対し旧current-only相当のwork orderと新background-enabledを対照比較した（旧production全体の速度測定とは別）。旧条件は隣接移動時にsource decode、render、QPixmapを追加する。新条件は usable final artifactへのcache hitとなり、追加decode・render・resize・QPixmapがすべて0。cached indexの個数だけで判定していない。巨大隣接画像のheader段階拒否と後続小画像の準備はpacketの7ケースにも含まれる。

変更Python 9ファイルのAST構文検査、`git diff --check` 成功。packet作者のmethod-double／preflight 43件は今回の522件に含めない。

### 失敗・未完了の補助テスト（隠さない）

追加で試した `test_sprint18_navigation_followup.py` 一括実行は32 dot後にQtプロセス終了 `-1073740791`。個別プロセスで切り分けると、以下3件のassertion失敗を検出した。

- `test_hidden_bottom_overlay_does_not_claim_qwindow_then_viewer_wheel`: display_move_calls が期待2に対し1。
- `test_hidden_bottom_overlay_uses_same_region_for_reveal_and_hover`: bottom座標が期待483に対し800。
- `test_canvas_click_moves_immediately_on_release_without_pending_timer`: page indexesが期待[2,3]に対し[0]。

3件とも対象production 3モジュールを `git show HEAD:…` からメモリ内で読み込み、作業ツリーを復元／変更せずに同じassertion失敗を確認した。今回の統合前からの再現であり、対象外の入力／fullscreen仕様変更で緑にしていない。失敗後のQt teardownが終了しない診断プロセスは停止した。一括プロセス異常終了の厳密な原因は未確定。この補助スイートは途中までの個別成功を522件に加算せず、最後のcanvas失敗より後のケースは未実行として残す。全repositoryテスト成功とは報告しない。

## 同一大画像offscreen比較

既存 `scripts/benchmark_viewer_navigation.py` を使用。合成high-detail JPEG 9枚、各2400×3600、viewport1280×800、256 MiB、deflated ZIP。payload SHA256 `eb1ade19ee3aa110f986e9a3bdee7d8a58681ab53d5fcdc071250376ad313582`。

最初の既定 `--immediate-target 5` は30秒timeout。連続wheelが現在のhold契約でpage1までしか受理されていないのに、観測がpage5を待つため。入力契約もbenchmark実装も変更していない。`--immediate-target 1` で受理される初期targetを観測し、以降の順・逆・反転・往復・高速入力を実行した。

HEAD比較は対象3モジュールをメモリ内読込し、同じ現行benchmarkを実行。両条件ともcompleted。各1回の補助測定で、Windows compositor遅延や統計的な性能差を主張しない。

| request→offscreen paint (ms) | HEAD比較 | 統合後 |
| --- | ---: | ---: |
| 初回open→paint | 63.408 | 66.599 |
| warm idle | 1.227 | 1.271 |
| 順送り | 1.153 | 1.177 |
| 逆送り | 1.114 | 1.155 |
| 即時方向反転 | 1.556 | 1.565 |
| 往復 median | 1.085 | 1.095 |
| 高速連続最終page | 2.042 | 2.266 |

両条件でjobs11、QPixmap10、cache hit16、terminal error0、最終cache32,665,896 bytes。同じ通常fit/Auto scalingのwork量を維持。原寸／zoomの改善根拠は上記72ケースの追加work削減であり、この通常fitの微小時間差ではない。

## Browserとの共存と限界

現在の `BrowserThumbnailMemoryBroker` は全Browserの予算のみを管理し、Viewer/PDF/native allocationsの共通grant ownerではない。ViewerへBrowser grantを誤流用せず、新たな二重authorityも作っていない。Browserは自身のmanaged cacheだけを足し戻す。Viewer等の使用量はOS sample内に含まれる。

実Browser broker／providerとactive・inactiveの2 Viewer policy、共通の制御sampleで同時回復を検証。Browserのaggregate capacityと段階回復を守り、inactive Viewer policyは成長せず、active側だけ成長した。さらに2個の実ViewerWindowで小bookを開き、Browserと同時sampleしても完了済みbookが追加成長を要求しないこと、両runtimeへの予算伝播を確認した。別テストの実ViewerWindowでは実resident充填と成長を検証している。ただし、2個の実ViewerWindowを同時に高負荷で埋めた実機試験ではない。

**未使用の将来予算はBrowser・Viewer間で原子的に予約されない。** 複数window、複数process、PDF scratch、Qt native allocationsまで含めた総量安全性／RSS上限は証明していない。既存の `auto_growth_cap_bytes` は共通ownerが今後存在する場合の入口として維持し、今回Windowからは渡さない。

## 変更ファイルと残る実機確認

- production: `app/viewer_memory_policy.py`, `app/viewer_window.py`, `app/zip_raster_book_runtime.py`
- tests: `test_viewer_memory_policy.py`, `test_zip_raster_viewer_integration.py`、新規 `test_viewer_memory_refinements.py`, `test_viewer_refinements_qt.py`, `test_viewer_refinement_integration_qt.py`, `test_viewer_full_source_matrix_qt.py`
- docs: `docs/ARCHITECTURE.md`, 本記録

残る実機確認は、大容量実ZIPのcold/逆転/往復/連続入力の体感、実Alt-Tab、ズーム／magnifier／book切替の重なり、複数Viewer＋Browser同時回復時のRSS／native memory、実DPI／compositor。offscreenではtimerと主要lifecycleを確認したが、全組合せを網羅したとはしない。上記旧補助テスト3件と一括Qt teardown問題は別途調査が必要。

実アプリ起動、native入力、外部GUI、commit、push、reset/restore/checkout/stash/cleanはいずれも実施していない。
