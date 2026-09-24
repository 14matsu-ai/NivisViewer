# Honeyviewの高速ページ送り: 調査記録

2026-09-22。ユーザーから同じ用途でHoneyviewの高速スクロールが滑らかとの報告を受け、相談側でインストール済み資料・設定と公開一次情報を確認した。Honeyviewは起動しておらず、画像内容・履歴・画像パスは取得していない。ソースコードや実行トレースは確認できていないため、内部のスレッド数や処理順を断定しない。

## 確認した事実

- インストール先`C:\Program Files\Honeyview`。実行ファイルのProductVersion/FileVersionは5.53.0.0、VersionNo.iniのBuildNoは6273。
- 同梱`langs/Japanese.ini`412–420行、`langs/English.ini`に、次画像キャッシュ、メモリ上限、低画質で先に表示するプレビュー、ICC処理、縮小フィルターの設定文言がある。
- 実ユーザーの`HKCU\Software\Honeyview`から性能関連の値だけを読んだところ、`bCacheImage=1`、`nCacheMemorySizeMB=300`、`bUsePreviewMode=1`、`bApplyICC=1`、`bApplyMonitorICC=0`、`nPageTurnEffect=0`。キャッシュと高速プレビューは有効、ページ切替効果は0。`nInterpolateMode=0`と`nFilterMode=0`も確認したが、内部enum定義は未確認なので名称に変換して推測しない。
- `dll/OpenSourceLicense.txt`にlibjpeg-turbo、libpng、libWebP等の記載あり。通知の記載だけから現行バイナリの全処理や速度を推定しない。
- 公式更新履歴 https://www.bandisoft.com/honeyview/history/ に、v5.04のキャッシュ最小値30MBへの変更、v5.03のキャッシュ無効設定の不具合修正等がある。公開情報からワーカー数・キャンセル戦略は確認できなかった。
- BandiViewは別製品の後継であり、そのGPU/並列処理仕様をHoneyview5.53の仕様として扱わない。

## NivisViewerへの示唆

Honeyviewでは「先読み」と「まず低画質で早く表示」が別の有効な設定として存在する。このユーザーの速さが低画質先行だけによる、と因果関係を断定することはできない。しかし、並列数だけに注目せず、最初の可視画像を出すまでの処理量を比較する根拠になる。

Nivisの`source_is_preview`は縮小デコードされたsourceを意味する場合があり、それだけでHoneyviewの低画質先行と同じとはいえない。確認時の`_ZipRasterUnitJob._render_unit`はdecode/layout/設定された縮小処理を終えたdisplay_qimageを返し、その後GUI側でQPixmap化して公開する。`viewer_render.render_qimage`の明示フィルター経路にはQImage→Pillow→QImage変換がある。変換・縮小の負荷と初回表示への寄与は別途計測する必要がある。

候補は、完成済みの表示用キャッシュを最短で描画する経路の維持、未キャッシュ時に軽い表示を先に出して安定後に設定品質へ更新する方式、不要な画像表現変換の削減。高速プレビューを採用するなら現在の品質設定は最終出力へ適用し、余分なdecodeや古い画像の遅延表示を増やさない設計が必要。

2ワーカー試作は別途進行中。途中で追加指示や同じソースへの実装修正はせず、完了後のレビューで本調査を参照する。今回Honeyviewからコード・バイナリをコピーしたり、設定を変更したりしていない。
