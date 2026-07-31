# ZipPlaFork compatible raster path: final adoption record

> **Historical A/B record (superseded).** The feature-eligible compatible
> path described below is no longer the production ZIP Viewer. The current
> Viewer-wide decision and the book-scoped `ZipRasterBookRuntime` cutover are
> recorded in `ZIPPLAFORK_COMPARISON.md`, section 11. Keep the measurements
> below as provenance for the discarded narrow-path experiment; do not use
> its eligibility matrix or three-pixmap ring as the current design contract.

This document is the final decision table for the 2026-07-31 Viewer performance
experiment that preceded the structural cutover. It supplements the historical
end-to-end traces in `ZIPPLAFORK_COMPARISON.md`; section 11 of that document
supersedes the adoption recommendation here.

## Scope and terminology

- Upstream repository: <https://github.com/himamon/ZipPlaFork>
- Fixed upstream revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
- Upstream license origin: `AGPL-3.0-or-later`
- **旧A**: 従来production経路。`_ImageLoadTask`、
  `ViewerRenderTask`、source/prepared/render cacheを使用する。
- **新B**: compatible rasterを自動選択するproduction経路。対象JPEGでは
  archive entryからdisplay-ready rasterまでを直接処理する。

統合後A/Bは、`QT_QPA_PLATFORM=offscreen`、fake/mock、temp directoryだけを
使って実行した。A/Bは4096 x 6500の高詳細JPEG 21 entryを持つ
682,441,432-byte ZIPを別々のfresh processで生成したが、固定seedと固定ZIP
timestampにより、両方のSHA-256は
`7bf798a4eee4a895c56d6e2a3415aa1a6ab47e4228d41d0118d24a39124e35f1`
で一致した。実アプリ、native入力、外部GUIは起動していない。したがって、
以下の時間値はoffscreenの補助証拠であり、実機体感の改善を断定しない。

## Final comparison and adoption decision

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針 |
|---|---|---|---|---|---|
| decode pipeline | book中はarchive readerを保持し、通常JPEGはentry streamをnative decoderへ渡し、同じpage jobでdisplay bitmapまで作る。 | **旧A:** entryを`BytesIO`へ展開し、Pillow header parse、`QImageReader`、別`ViewerRenderTask`を通る。 **新B:** `ZipImageSource.open_compatible_jpeg_at_most()`がentryを1 MiB chunkで予約済み`QByteArray`へ一度だけmaterializeし、seekable `QBuffer`から`QImageReader`へ渡す。decode、EXIF、decoder scale、display-ready `QImage`まで一jobで完結する。 | 新B | Python製sequential `QIODevice`は高詳細JPEGでworker decode 270.44 ms、旧buffered Aは241.86 msとなり、約1,985回の`readData` callbackが支配した。`QByteArray`版はPython decoder callbackをなくし、二段workerも除く。 | 新Bへ構造移植。native streamを忠実再現できないQt/Python差は、1 payload materializationを許容して実機体感を優先。 |
| scheduler / queue | 一つのpage workerと一つのUI完了処理を中心にした浅い直列構造。実行中jobの強制preemptはせず、完了後に最新状態から次jobを選ぶ。 | **旧A:** load、render、preparedが別job。 **新B:** decode、orientation、縮小、display-ready artifact生成を一jobで完結し、一度だけGUIへ渡す。 | ZipPlaFork型の新B | job境界とcallback数を減らし、currentに必要な全処理を一つの優先単位にできる。 | 新Bへ構造移植。旧schedulerはfallback専用まで縮小。 |
| priority制御 | currentを最優先にし、完成後にnext、previousの浅い近傍を処理する。 | **旧A:** current、prepared、prefetchが複数層に分かれ、古い近傍jobが競合し得る。 **新B:** current完成前には近傍jobを開始せず、完了後に指定されたnext、previousだけを処理する。 | ZipPlaFork型の新B | currentのI/O、decode、公開を他の表示準備より先に完結させられる。 | `current → next → previous`を維持。遠方jobを作らない。 |
| direction reversal handling | 実行中の一件はpreemptしないが、完了後は最新位置からorderを組み直し、旧方向の深いqueueを残さない。 | **旧A:** generationは古い表示混入を防ぐが、旧jobのtask/callbackが残り得る。 **新B:** request IDとcancelをentry chunk readへ伝播し、未開始jobを`tryTake`して最新current周辺だけを再構築する。decoder内部で停止できない区間の結果もQPixmap化前に棄却する。 | 新B | 反転coldのcurrent criticalは旧A 3 task / 3 GUI callback、新B 2 / 2で、最終QImage/QPixmapは各1。stale commitは0。 | 構造移植。強制停止へ依存せず、古いjobを追加しない。 |
| cache structure | original source bitmapとresize済みbitmapを同じpage lifecycleで保持し、両方をbyte accountingへ含める。 | **旧A:** source、prepared display、render/QPixmapが重なり得る。 **新B:** 対象JPEGはoriginal-size rasterとentry bytesを常設せず、最大3件のdisplay-ready QPixmap ringだけを主cacheにする。 | 対象JPEGは新B | 表示に不要なfull-size rasterと多層cacheを通常経路から外せる。 | 対象JPEGは新Bへ全置換。旧cacheはfallbackだけに残す。 |
| eviction policy | sourceとresizedの双方を計上し、page単位でdisposeする。current周辺を保護する。 | **旧A:** cache層ごとにkey、容量、寿命が異なる。 **新B:** current/next/previousに対応する最大3 artifact ringとし、source/layout/request identityで古いartifactを拒否する。 | 新B | 128 MiB設定の高詳細runで、旧Aは7 page / 121.46 MiB、新Bは3 page / 30.36 MiBを保持した。 | 最大3件ringを採用。表示中近傍を優先し、別byte上限は現時点で追加しない。 |
| prefetch policy | current完成後に一workerで浅い近傍を処理し、遠方を大量に並列処理しない。 | **旧A:** prepared schedulerが複数段に分かれる。 **新B:** matching `framePainted`後だけ移動方向側、反対側の最大2件を一件ずつ処理する。高速入力停止前は6 ms idle graceで最終currentへcoalesceする。 | ZipPlaFork型の新B | rapid-finalで通過page 11–17のcommit前decodeは0。paint後のpage 19、17だけを正当な近傍prefetchとして分離計測した。 | 全置換。current paint前のprefetchは禁止し、post-paint近傍workは別指標にする。 |
| display apply timing | decodeとresizeが完成したbitmapだけを一つのUI完了処理で差し替える。 | **旧A:** load完了、render完了、cache登録、UI反映が別callback。 **新B:** GUI callbackで一度だけ`QPixmap.fromImage()`を実行し、`commit_display_ready_single()`で完成frameを原子的にcommitする。 | ZipPlaFork型の新B | 未完成状態をViewerへ公開せず、worker→GUI境界とQPixmap生成を一度に限定する。 | 構造移植。`framePainted`までを表示完了として追跡。 |
| dark-frame avoidance | 前の完成bitmapを保持したまま次を準備し、完成bitmapがない段階で表示を空にしない。 | **旧A:** 多段状態とclear/placeholder可能点がある。 **新B:** cold currentの準備中も旧frameを保持し、完成後に一回で交換する。 | ZipPlaFork型の新B | clear-before-loadを経路から除き、paint可能なframeを常に残す。 | 構造移植。旧frame保持→完成artifact commit→paintの順序を固定。 |
| cold miss behavior | persistent archiveからentryを一度取得し、一page jobでdecode/resize/display-ready化する。 | **旧A:** 1 entry、1 QImage、1 QPixmapだが2 task / 2 callback、3 whole-payload API handoff。 **新B:** 1 entry、1 `QByteArray` materialization、1 QImage、1 QPixmap、1 task / 1 callback / 1 paint。 | 新B | fresh-process高詳細runは初回266.759 ms、順送り255.481 ms、反転268.689 ms、往復中央値262.848 ms、rapid-final270.425 msで、旧Aより1.33–8.21%短い。 | 対象JPEGは新B。旧Aは非対応条件・一方向failure fallbackに限定。 |
| memory use | originalとresizedを同時保持するが、双方を明示的に計上する。 | **旧A:** source raster、prepared raster、QPixmapが重複し得る。 **新B:** page job中だけcompressed `QByteArray`とdisplay-size `QImage`を持ち、常設は最大3 QPixmap。 | 新B | memory-pressure viewer working-set deltaは旧A 195.63 MiB、新B 65.02 MiB（-66.76%）。logical cacheは121.46→30.36 MiB（-75.00%）。 | 新Bを採用。Qt plugin内部一時bufferはcache budget外である点を維持記録。 |
| large ZIP behavior | archive stream/readerとentry indexをbook中保持し、ページごとのarchive再openや名前全走査をしない。 | 両経路ともpersistent `ZipFile`とO(1) entry lookupを使う。新Bは対象entryだけを32回の1 MiB readで一つの`QByteArray`へ読み、同じjobでdecodeする。 | lookupは同等、pipelineは新B | 682.44 MB、日本語entry名21件の同一bytes ZIPで完走し、archiveのページごとの再openや全一覧走査はない。 | archive/index保持を維持し、対象JPEGを新Bへ通す。 |

## Directly translated/adopted scope

新BはC#／GDI+コードの逐語コピーではなく、次のZipPlaFork由来の
処理構造をPySide6／Qtへ直接翻訳・移植したものである。

- persistent archive readerとindexed entry lookup
- archive entryをpage jobが一度だけ取得しdecoderへ渡すownership
- currentを最優先にする一体型page job
- current完成後だけnext、previousを処理する浅いqueue
- 方向反転時のqueue再構築
- display-ready image完成後だけUIへ公開する更新順
- current周辺だけを保持する限定的cache
- 前の完成frameを残すdark-frame回避

固定revisionで対応を確認した主な移植元は次のとおり。

- `source/ZipPla/ImageLoader.cs`
  - `ImageLoader.GetJpegOrientation(Stream)`
  - `ImageLoader.LoadRotateBitmap(Stream)`
  - private
    `ImageLoader.GetFullBitmap(Stream, bool, out ImageInfo, string, bool)`
  - public `ImageLoader.GetFullBitmap(Stream, string, bool)`
  - `VirtualBitmapEx`、`VirtualBitmapEx.DataSizeInBytes`、
    `BitmapEx.GetDataSizeInBytes()`
  - `SeekableStream.Seekablize(...)`、`Exif.GetAll(...)`、
    `StopBuffering()`、`new Bitmap(seekable)`、
    `ViewerFormImageFilter.Rotate(...)`から成るJPEG stream decode範囲
- `source/ZipPla/PackedImageLoader.cs`
  - `PackedImageLoader`、`ReadOnMemoryMode`
  - book中のarchive reader/entry index保持とphysical-entry stream公開範囲
- `source/ZipPla/ViewerForm.cs`
  - 一件ずつのpage worker、current/近傍選択、UI完了callback、
    完成bitmap差し替え、loading maskの処理範囲
- `source/ZipPla/ViewerFormImageFilter.cs`
  - `ViewerFormImageFilter.GetOrientation(int)`
  - `ViewerFormImageFilter.Rotate(...)`
- `source/ZipPla/Exif.cs`
  - `Exif.GetAll(Stream)`

NivisViewer側の対応箇所:

- `app/image_source.py`
  - `StreamedJpegDecode`
  - `ZipImageSource._read_entry_qbytearray()`
  - `ZipImageSource.open_compatible_jpeg_at_most()`
  - reserved `QByteArray` → seekable `QBuffer` → `QImageReader`
  - 診断用に比較した`_ZipEntrySequentialDevice`と
    `open_streamed_jpeg_at_most()`（production新Bでは非選択）
  - request終了時のdeferred archive close
- `app/zippla_compatible_raster_path.py`
  - `ZipPlaCompatibleRasterRequest`
  - `ZipPlaCompatibleRasterPath`
  - one-active-job scheduler、世代/cancel、current-first prefetch、
    display-ready artifact、最大3件のartifact ring
- `app/viewer_widget.py`
  - `set_direct_display_mode()`
  - `commit_display_ready_single()`
  - `framePainted`
  - 完成済み旧frameを保持するdirect paint
- `app/viewer_window.py`
  - `_compatible_raster_request()`
  - compatible pathのactivate/deactivate、失敗時fallback
  - `frameReady`からatomic commit、`framePainted`後の競合lane解放
  - page-list、magnifier、非対応layout/formatでの旧A fallback
- `app/image_cache.py`
  - `suspend_raster_work()`による旧A job/cacheの競合停止

ZipPlaFork固有のGDI+ `Image.FromStream`、WinForms UI callback、UI全般、
書庫形式全般、original bitmapとresized bitmapの二重cacheは移植していない。

## Final offscreen A/B

条件は21 page、4096 x 6500、各entry 32,497,082 bytes、ZIP_STORED、
Viewer 3840 x 2106である。時間とworking setは実行順のheap/cache汚染を避ける
ためA/Bを別fresh processで取得し、上記SHA-256一致で入力bytesの同一性を
確認した。

| Scenario | 旧A | 新B | Bの差 |
|---|---:|---:|---:|
| 完全cold初回 request→paint | 270.349 ms | 266.759 ms | -1.33% |
| ready順送り | 6.216 ms | 7.629 ms | +22.73%（+1.413 ms） |
| ready逆方向 | 5.107 ms | 6.939 ms | +35.87%（+1.832 ms） |
| ready往復 median | 5.394 ms | 6.312 ms | +17.02%（+0.918 ms） |
| 順送りcold | 278.333 ms | 255.481 ms | -8.21% |
| 反転直後cold | 275.505 ms | 268.689 ms | -2.47% |
| 往復cold median / max（6 legs） | 277.500 / 279.248 ms | 262.848 / 278.292 ms | median -5.28% |
| rapid-final cold（8 requests） | 287.891 ms | 270.425 ms | -6.07% |
| viewer working-set baseline | 349.07 MiB | 175.90 MiB | 参考値 |
| viewer sampled max | 544.70 MiB | 240.92 MiB | 参考値 |
| viewer working-set delta | 195.63 MiB | 65.02 MiB | -66.76% |
| logical cache / retained pages | 121.46 MiB / 7 | 30.36 MiB / 3 | -75.00% / -4 pages |

単発ready hitでは両経路ともworker、QImage、QPixmap、queued worker callbackが
0で、1 atomic commit / 1 paintである。ready往復中の新Bでは、直前paint後に
開始した近傍prefetchのcancel完了callbackが3回だけcurrent intervalへ重なったが、
ZIP read、payload materialization、QImage、QPixmap、worker taskは0だった。
新Bは同期的なrequest/frame検証、direct-frame acknowledgement、このprefetch
cancel bookkeepingにより0.918–1.832 ms余分だったが、最大7.629 msである。
cold表示クリティカルパスの1 pageは次のとおり。

| Count / volume | 旧A | 新B |
|---|---:|---:|
| ZIP entry open / bytes | 1 / 32,497,082 | 1 / 32,497,082 |
| 明示的full-payload materialization | `BytesIO` 1 | `QByteArray` 1 |
| whole-payload API handoff | 3 | 0（`QByteArray`を`QBuffer`が共有） |
| QImage / QPixmap.fromImage | 1 / 1 | 1 / 1 |
| worker task / queued GUI callback | 2 / 2 | 1 / 1 |
| atomic commit / content paint | 1 / 1 | 1 / 1 |
| decode終了→paint（順送り） | 8.462 ms | 5.823 ms |

「copy回数」はPython/Qtから観測できるfull-payload objectとAPI境界を指す。
Qt implicit sharing、JPEG plugin内部、native raster uploadの物理memcpyは
観測不能であり、推定回数を実測値として扱わない。新Bのpaint後prefetchは
別phaseであり、順送りでは追加1 entry、rapid-finalではpage 19と17の
追加2 entryを読む。rapid-finalの通過page 11–17は最終commit前decode 0、
commit 0で、旧frameは最終frame完成まで保持された。

### Rejected sequential-stream backend

最初の新Bは`ZipExtFile`をPython製sequential `QIODevice`へ直結した。
同じ高詳細JPEGの旧A buffered worker decodeは241.86 ms、sequential版は
270.44 msで、GUI側のdecode終了→paintは8.76 / 8.55 msと同等だった。
またsequential版は1 pageあたり約1,985回のPython `readData` callbackを
発生させた。別component probeでも、32,497,082-byte entryに対して
`BytesIO` read 20.378 ms、`QByteArray` read 24.601 ms、legacy decode-only
188.384 ms、seekable QBuffer decode-only 186.577 ms、Python sequential
total 225.663 msだった。したがって支配点はQPixmap uploadやGUI callbackでは
なくPython `QIODevice` callbackを含むdecoder inputであり、production新Bは
seekable `QByteArray` / `QBuffer`へ置換した。

## Validation status

統合後の構文/import、compatible単体、eligibility/fallback、
archive lifetime/shutdown、serial/stale rejection、atomic commit/old-frame、
page-list/magnifier/rotation/DPI/corrupt JPEG/book switch、関連Viewer test、
分割全回帰、同一bytes A/Bを実行した。全82 test fileの重複なし分割回帰は
1,436件合格した。詳細は最終作業報告と
`ZIPPLA_COMPATIBLE_CHECKLIST.md`に記録する。

offscreenでは新Bのcold時間、task/callback数、working-set deltaが改善した。
一方、ready hitは最大1.832 ms遅く、Qt offscreenにはWindows compositor、
実screen DPR、native input cadence、実ZIP storage/cache条件がない。
ZipPlaFork並み以上の実機体感を達成したという結論は、同一実ZIPによる
ユーザー確認が終わるまで保留する。
