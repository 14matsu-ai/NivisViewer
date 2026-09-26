# PSD / PSB Phase 1

PSD/PSBを読み取り専用画像として扱う。Viewerは保存済み合成画像を表示する。
Browserは十分な大きさの埋め込みサムネイルを優先し、小さい場合や欠落時は
保存済み合成画像を使用する。レイヤー編集・表示切替・再合成は対象外。
合成画像が保存されていない文書はViewerで読み込み失敗になる。

フォルダ、ZIP/CBZ、外部書庫の既存ワーカー経路へ統合した。
外部書庫は既存バックエンドが展開したデータを使用する。
Folder/ZIPの画像寸法とBrowser詳細欄は26バイトのヘッダーを読み取る。
外部書庫の寸法取得ではバックエンドによるエントリー展開が必要。
PSDライブラリの初期化はPSDのデコード時まで遅延する。

## 依存と上限

`requirements.txt`にpsd-toolsを追加し、リリース依存は1.19.0へ固定した。
実行するPython環境へ更新済みrequirementsをインストールする必要がある。
psd-toolsはMITライセンスで、原文を`licenses/psd-tools/LICENSE`へ保持する。
必須依存のattrs、NumPy、typing-extensionsもライセンス収集対象へ追加した。
`psd-tools[composite]`は導入しない。

保存済み合成画像のデコード時にpsd-toolsの割当推定上限を指定する。
Viewerは2 GiB、Browserは512 MiB。この上限は文書の解析、元データの保持、
後段のコピーを含むプロセス全体のメモリ使用量を保証するものではない。
ZIPサムネイルでもPSDエントリーの既存最大サイズを検査する。

## 検証

2026-09-26、Windows / Python 3.12 / Qt offscreen / psd-tools 1.19.0で実施。
生成したPSD/PSBでFolder/ZIP/外部書庫、画素値、ヘッダー寸法、埋め込み
サムネイル優先と合成画像へのフォールバック、合成画像なし、割当制限、
不正データを検証した。外部書庫バックエンドはfakeを使用した。

画像・サムネイル・一覧・Viewer cold-start・ZIP先読み・既存Browser変更を
含む205件は全件成功表示。ただしこのまとめ実行はサマリー表示後の
プロセス終了を確認できていない。構文チェックと差分チェックは成功。
PSD専用11件は単独実行でも成功し、終了コード0を確認した。

未確認: ユーザーの実PSD/PSB、Photoshopの多様なカラーモードや保存設定、
実外部書庫ツール、大容量文書の実機体感、Python 3.11、Portableビルド。
実アプリ・外部GUIは起動していない。

## 1.0.17への統合再検証

メインの`f934520`（1.0.17）との差分を3者比較で取り込んだ。ZIPの非PSD
寸法取得はZIPロック外のnative QBuffer経路を保持し、PSD専用の26バイト
ヘッダー取得をその前段へ追加している。BookSessionの分割キャッシュ解放、
Viewer/runtimeの診断境界、freeze_diagnostics、main.pyは1.0.17と一致する。
既存のBrowser・HDD wake・overlay・video変更と追加テストを維持した。

再検証はPython 3.12.14 / Qt 6.11.2 offscreen。画像・PSD・Browser等151件、
BookSession・Viewer runtime/integration・cold-start・先読み100件がともに
終了コード0で成功。追加回帰ではJPEG/PNG寸法取得がnative QBufferを使用し、
QImageReader作成時にZIPロックを保持しないことを検証した。

高詳細JPEG 4096×6500、8ページ、表示領域3840×2106、キャッシュ512 MiBで
順送り・逆送り・方向反転・往復・高速連続入力のoffscreen評価を完了。
初期表示149.362 ms、初回直後のcold遷移338.246 ms、往復の中央値5.843 ms。
これは統合後の補助計測で、修正前後の性能比較や実機フリーズ解消の保証ではない。
結果JSONは`build/psd_1017_navigation.json`。構文・差分チェックも成功。
Python 3.11、Portable、ユーザー実データの実機確認は引き続き未検証。

## メイン起動環境への反映

2026-09-26、レビュー済みPSD変更を
`C:/Users/yurig/Documents/Codex/NivisViewer`へ反映し、ユーザーが起動に使う
`.venv311`へpsd-tools 1.19.0と必須依存を導入した。
Python 3.11.9 / Qt 6.11.2で関連217件が終了コード0で成功。
生成PSD/PSBのFolder/ZIP計4経路でViewer runtimeのframeReadyも確認した。
構文チェック、pip check、大画像4096×6500・8ページの順送り・逆送り・
反転・往復・高速連続入力のoffscreen評価も成功。
計測結果は`out/psd-main-py311-navigation.json`。
Portableビルドとユーザー実PSD/PSBでの実機確認は未実施。
