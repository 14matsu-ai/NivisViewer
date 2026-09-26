# Creative Formats Phase 2 改訂版の統合検証（2026-09-26）

対象は `NivisViewer_CreativeFormats_Phase2_REVISED.zip`。
現在のworktreeの未コミット変更を基準に、23ファイルのパッチ内容を文脈一致で統合した。
通常の `git apply --check --recount` は一部の文脈で不一致となったため使用していない。
PSD、Browser操作、HDD応答性、1.0.17のZIPヘッダー読み取り対策を保持した。
実アプリ・実GIMP・native入力は起動していない。commit/pushも行っていない。

## 実装

- KRA/ORA: 保存済み合成PNG、Browserは十分なサイズの埋め込みサムネイルを優先。
- CLIP: サイズ制限付きの埋め込みSQLite/CanvasPreview読み取り。原寸レイヤー描画ではない。
- XCF: レビュー後、単純な旧形式RGB・1レイヤーだけを内蔵デコードし、それ以外は外部GIMP 3.xの非対話変換。
  GIMPの探索・引数・エラー処理は模擬テストのみ。GIMPは自動導入・同梱しない。
- Folder/ZIP/外部書庫の画像・サムネイル経路とBrowser分類へ接続。
- XCFの投機的なREAD_AHEAD/PREFETCH/BACKGROUNDサムネイル生成を抑制。

添付候補に対して以下を補正した。

1. gimpformats 2025はBytesIOにも`.name`を要求するため、書庫内XCFの読み取りが失敗する。
   メモリ入力は`GimpDocument.decode(bytes)`を使用するよう変更。
2. KRA/ORAの任意サムネイルが破損していても、正常な合成PNGを表示できるようフォールバック。
3. GIMP出力のPNGはピクセル展開前にも64 Mpixel上限を確認。
4. Windows依存のcolorama/win32-setctimeをライセンス収集対象に追加。
   wheel/sdistに本文のないbrackettreeとloguruは、公式出典・バージョン・SHA-256付きの本文で補完。

## 確認済み

環境: Windows 11、Python 3.12.14、PySide6/Qt 6.11.2、Pillow 12.3.0、
gimpformats 2025、NumPy 2.5.3。Qtは`offscreen`。
依存はworktreeの`build/creative-test-deps`と`build/psd-test-deps`を使用した。

- 添付元デコーダーで追加再現テスト: **5 failed / 1 passed**。
  破損プレビュー4条件とメモリXCF1条件が失敗。worktreeを戻さず参照モジュールを読み込んで比較。
- 最終コードの重点回帰: **201 passed**、正常終了。
  新形式、GIMP模擬、PSD、Folder/ZIP/外部書庫、サムネイル、Browser分類、
  HDD応答性、ZIPネイティブバッファ、ライセンス収集を含む。
- 既存Viewer回帰: **100 passed**、正常終了。
  BookSession、ZIP runtime/Viewer統合、read-ahead、cold-wheel、並列設定を含む。
- 合成した旧XCFは実際のgimpformatsでピクセル値まで確認。GIMPへのフォールバックは禁止したテスト。
- 追加依存7件のライセンス収集: strict成功、警告なし。
- app/testsとライセンス収集スクリプトの構文チェック成功。

ログは`build/creative-focused-final.txt`、`build/creative-viewer-regressions.txt`、
`build/creative-candidate-before-tests.txt`。

## 全体回帰の制約と変更前比較

全件成功ではない。最初の全体実行は約71%の時点で集計なしに終了した。
出力上は2,629件成功・38件失敗・1件skipまで確認できるが、Pythonの終了コードは
その実行では保存できていない。残り1,044件を別実行し、**1,011 passed / 33 failed**
の集計まで確認した。失敗を含む一部の比較実行は集計後も終了待機が残っている。

現在の変更を戻さず、適用前のworktree内容を`build/creative-baseline/`へ再現して比較した。

- 前半の38失敗: **31件は適用前でも失敗**。残る7件は適用前と現行の単独再実行で成功。
- 後半: 適用前は**1,010 passed / 33 failed**。失敗33件のテスト名は適用後と完全一致。
  適用後だけの成功1件は追加のKRAサムネイルテスト。
- 従って観測された失敗71件のうち64件を適用前でも再現し、残る7件は
  現行コードの単独再実行で成功。実行順や時間に依存する可能性があり、原因解消とは判定しない。
- Browser再描画/外部操作、既存Viewer入力、サムネイル保存、翻訳などの既存テスト失敗は
  今回の形式追加へ便乗して変更していない。

比較ログ: `build/creative-baseline-verified.txt`、`build/creative-baseline-second.txt`、
`build/creative-remaining-result.txt`、`build/creative-remaining-baseline.txt`。
機械比較結果は`build/creative-comparison.json`。

最初の比較コピーはTEMP配下のアクセス権差で収集に失敗したため、worktree内コピーへ切り替えた。
その比較用Python（PID 18908・25836）の停止は自動承認レビューで拒否された。
処理の強制終了禁止との抵触と対象誤判定のリスクが理由だった。
その後、利用者から検証用Pythonの停止許可を得たため、PIDと起動時刻を再照合して
権限エラーの2件と、集計後の終了待機4件（2816・31468・26004・26852）を停止した。
後半比較の2件はスタック確認で、PDFiumサービスが空キューを待ち、Pythonが
`threading._shutdown`で待機していた。実アプリや以前からの別プロセスは停止していない。

## 大画像ZIPのoffscreen評価

既存ベンチマークを使用。4096×6500、JPEG品質88、高密度パターン8ページ、
deflate ZIP約113.5 MB、3840×2106 viewport。順送り・逆方向・往復・高速連続入力が完了。

| 測定 | ミリ秒 |
|---|---:|
| 初回表示 | 157.857 |
| 初回表示直後の未キャッシュ次ページ | 347.923 |
| 順送り | 7.960 |
| 逆方向 | 6.827 |
| 往復の中央値 | 8.043 |
| 高速入力の最終ページ | 8.772 |

結果: `build/creative_navigation.json`。通常JPEG経路の回帰確認であり、
新形式の速度やWindows実機の体感改善を示す比較ではない。
高速入力は同一ループ内のburstであり、20/50/100 ms間隔の実負荷比較ではない。

## 残る確認

- 実制作ファイル（KRA/ORA/CLIP/XCF）の表示・色・透過・レイヤー効果。
- 実GIMP 3.xの探索、非対話PNG出力、元ファイル内容/更新時刻の不変性。
  模擬テストでは内容不変・一時ファイル削除・timeoutを確認済み。
- Python 3.11とrelease固定依存、portableビルド/起動。今回は実行していない。
- 高DPI、実ディスク、Windows画面描画での操作感。

GIMP呼び出しは公式の[file_save API](https://developer.gimp.org/api/3.0/libgimp/func.file_save.html)
と[CLI仕様](https://www.gimp.org/man/gimp.html)を参照した。実行による検証とは区別する。

追加runtime依存gimpformatsはLGPL-3.0-only。blendmodes/loguru/win32-setctimeはMIT、
aenumはBSD、brackettreeはLGPL-3.0、coloramaはBSD系。
本文は`licenses/`へ保存し、出典は`THIRD_PARTY_NOTICES.md`と各SOURCES.jsonに記録。

## レビュー差し戻し2件への対応（2026-09-26）

対象を無効XCFマスクとGIMPキャンセルに限定。全件テストは再実行していない。

- **P1:** 実gimpformats 2025で、1×1 RGB(220,30,40)、無効の黒マスクを含む
  XCF v0の`document.image`が透明(0,0,0,0)になることをテスト内で再現。
  本体はこの合成APIを使わず、機能判定後の単純なレイヤー画素を直接読む。
  補正後はRGBA(220,30,40,255)。無効マスクの画素は読まない。
  内蔵経路はv0–3/RGB/単一可視全画面レイヤー/完全不透明設定/legacy normalに限定。
  有効マスク・グループ・その他blend・text/parasites等の構造とv4以降はGIMP経路。
  GIMPが無ければエラーにし、黙示的な近似結果をキャッシュしない。
- **P2:** ViewerとBrowserのworkerトークンをスコープ付きContextVarでGIMP backendへ接続。
  lock待ち・実行ファイルのバージョン照会・変換process監視でキャンセルを検査。
  待機中の不要要求は起動せず、自分の子processだけを停止・回収してから処理枠を返す。
  コンテキストは終了時に復元する。Viewerの既存要求ID・世代・メモリ予約管理を維持。
- 実FolderRasterBookRuntime/1 workerで`0.xcf`→`1.png`を検証。
  接続を無効にした対照では150 ms Qtイベントを処理してもframeReadyは0件で、
  fake変換を手動解放すると次ページへ進む。接続ありでは手動解放不要でPNGだけが表示され、
  旧結果が混入しない。これはキャンセル制御の検査であり速度改善量の測定ではない。
- Browserの実サムネイルloaderから所有processの停止・回収まで確認。
  lock待ち・事前キャンセル・timeout・GIMP不在のエラーも検証。

最終の対象回帰は **175 passed / 正常終了**（32.42秒）。
対象: creative review/decoder、GIMP backend、thumbnail provider、Folder/ZIP runtime。
ログ: `build/creative-review-targeted.txt`。
実GIMP（子プロセスを含む終了動作）、制作ファイル全般の忠実性、実機操作は未確認。
実アプリ・実GIMP起動、native入力、commit/pushは実施していない。

## メイン起動環境への反映（2026-09-26）

レビュー修正済みPhase 2を `C:/Users/yurig/Documents/Codex/NivisViewer` へ反映。
`.venv311` と `.venv-release` にgimpformats 2025と必須依存を追加した。
Python 3.11.9 / Qt 6.11.2の重点185件は終了コード0で成功。
構文チェック、両環境のpip check、大画像4096×6500・8ページの順送り・
逆送り・反転・往復・高速連続入力のoffscreen評価も成功。
計測結果は `out/creative-main-py311-navigation.json`。
実GIMP・実制作ファイル・Portableビルドの検証は未実施。
コミット・Pushはしていない。

## ユーザー専用GIMP検出の修正

GIMP 3.2.4が `%LOCALAPPDATA%/Programs/GIMP 3/bin` にある環境で、
従来の探索候補が空になることを確認した。ユーザー専用インストール先を探索へ追加。
修正後は実環境のconsole実行ファイルが候補に含まれることを読み取り専用で確認。
関連49テストと構文チェック成功。実GIMPの起動・変換確認は行っていない。

## GIMP手動指定設定

書庫タブへGIMP実行ファイルの参照・自動検出復帰・非同期の利用可否確認を追加。
設定はgimp_executableとして保存し、ApplicationControllerが起動時と設定変更時に
デコーダーへ反映する。手動指定が無効な場合は別のGIMPへ黙って切り替えない。
設定/画像/キャンセル/中国語等の対象164件が成功。確認ボタンによる検出キャッシュ
更新を追加後、GIMP設定/バックエンド13件を再確認。
大画像ZIPの操作評価はout/gimp-settings-navigation.json、構文チェックも成功。
英語UIの既存全体検証では、今回のGIMP欄以外の未翻訳文言に関する2件が失敗。
実GIMPの起動は未実施。

## XCF直接読み込みとBrowser処理枠の分離（2026-09-26）

`app/xcf_raster_reader.py` を追加し、対応するRGB8画像をタイルから直接復元。
通常の単一レイヤーではフルサイズの複製・空キャンバスとの合成も省略する。
XCFサムネイル専用の1 workerを追加し、ZIPの表紙でXCFが見つかった場合も
通常workerを解放してから専用workerへ引き継ぐ。Viewer契約は変更していない。

- 関連回帰230件成功（33.04秒）。画像ソース・Folder/ZIP runtimeを含む。
- 単一レイヤーのコピー削減とキャンセル検査を追加後、対象102件成功（2.76秒）。
- 構文チェック、git diff --check成功。
- 合成したXCF v0/3/10/11/19/23/26、非圧縮/RLE/zlib、タイル端、
  日本語パス・bytes入力で画素を照合。通常合成と未対応構造への拒否を検査。
- 重い変換をEventで保持し、XCF単体/ZIP内のXCFのどちらでもPNGサムネイルが
  先に完了。coordinator有無の両方を確認。世代変更後の旧XCF結果は不採用。
- 生成画像2048×3072 RGB8/RLEの同一画素で、従来gimpformatsレイヤー復元
  1198.2 ms、今回の直接読み込み21.0 ms。画素は一致。
  `out/xcf-direct-timing.json`。単一合成fixtureの1回測定であり、import・file IO・
  GIMP起動を含まない。実制作XCF全般の速度を示す数字ではない。
- 前回と同じ4096×6500、8ページ、高密度JPEG/deflate ZIP約113.5 MBの
  offscreen評価完了。初回153.2 ms、直後の未キャッシュ次ページ343.1 ms、
  往復中央値6.8 ms、高速入力の最終ページ7.5 ms。
  `out/xcf-direct-navigation-target1.json`。

最初のZIP評価はimmediate-targetの既定値5で実行しtimeoutになった。
記録ではホイール入力の最終受理先は1で、観測側が5の描画を待っていた。
この実行では後続シナリオ未実施（`out/xcf-direct-navigation.json`）。
前回の記録と同じimmediate-target=1に揃えた再実行では、順送り・逆方向・
往復・高速入力まで完了。冷状態の5連続入力が改善したという証拠にはしない。

実GIMP・ユーザーの制作画像・実機操作は未実施。依存追加、commit/pushなし。
多層の線形合成、マスク、グループ、効果などは引き続きGIMP変換が必要。
実ファイルの体感がなお遅ければ、次段階でXCFを既定OFFのオプションにする。

## XCF既定OFF設定（利用者の体感確認後）

利用者から速度が変わらないと報告されたため、xcf_loading_enabledを既定Falseで追加。
書庫タブのGIMP欄で変更し、次回起動時に反映。起動時だけ形式集合を更新するため、
実行中のページ一覧や画像workerの途中で拡張子集合を変更しない。
通常起動に使う.venv311のsys.executableと各モジュールの__file__を確認し、
メインのDocuments/Codex/NivisViewer/appが参照されることを確認した。
ユーザーが実行中のプロセス自体は未確認。制作XCFの構成も未確認のため、
そのファイルがGIMP経路へ回ったとは断定しない。直接読み込みからのfallback理由は
DEBUGログに記録するようにした。

対象回帰192件成功後、新設定テストの期待値を実APIに合わせ修正し2件成功。
新設定テストでは実ApplicationController初期化から、既定OFF、Folder/ZIP列挙、
Browser分類と要求、decoder手前での拒否、設定保存と次回起動時の有効化を確認。
外部起動はテストで禁止。既存XCF対応テストは明示的にenable_xcf fixtureでopt-inする。
構文チェックとgit diff --check成功。
同じ4096×6500・8ページ・高密度JPEG ZIPのoffscreen操作評価完了:
out/xcf-default-off-navigation.json（順送り・逆方向・往復・高速入力）。
実アプリ起動、GIMP起動、build、commit、pushは行っていない。
