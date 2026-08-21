# ZipPlaFork comparison record

## 1. Scope, fixed revision, and license provenance

この比較と構造移植は、次の固定snapshotだけを移植元として監査した。

- Repository: https://github.com/himamon/ZipPlaFork
- Fixed revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
- License: AGPL-3.0-or-later
- `license/About.txt`:
  `Copyright ©  2016 Rio's Toolbox`
- `source/ZipPla/Properties/AssemblyInfo.cs`:
  `Copyright © 2016-2017 Rio's Toolbox`
- `README.md:16-17`: original ZipPlaと同じくAGPLである旨

固定revisionはViewer実装を比較するためのsnapshotである。このrevisionの
commit自体がViewerの単一workerを導入したわけではない。commitの変更対象は
`CatalogForm`のthumbnail同時読み込み制限であり、Viewerの
`bmwLoadEachPage.ThreadCount = 1`はsnapshot内に既に存在する。

2026-07-31に`refs/heads/master`を再照合し、現在HEADも固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`と同一Git objectであることを
確認した。このため、固定snapshot以後に取りこぼしたViewer性能変更はない。

NivisViewerは個人利用専用で、ZipPlaForkのコード、アルゴリズム、処理構造の
コピー、翻訳、移植を許可している。今回のPython差分にC#の式、pixel loop、
GDI操作、`BackgroundWorker`実装の逐語的なコピーまたは翻訳はない。一方、
単一lane、queue再構築、display-unit単位のpaced dispatch、cache lifecycle
などは、repository規則に従いAGPL-3.0-or-later由来の構造移植として記録する。

ZipPlaForkはruntime/build dependencyではなく、binaryや実装source fileを
NivisViewerへ組み込んでいない。固定revisionの`license/AGPL.txt`は
`licenses/ZipPlaFork/AGPL.txt`、`license/About.txt`は
`licenses/ZipPlaFork/About.txt`としてbyte-for-byte保持している。

監査対象は次のとおり。

- `source/ZipPla/ViewerForm.cs`
- `source/ZipPla/ViewerForm.Designer.cs`
- `source/ZipPla/SettingForm.cs`
- `source/ZipPla/GenerarClasses.cs`
- `source/ZipPla/PackedImageLoader.cs`
- `source/ZipPla/ImageLoader.cs`
- `source/ZipPla/BitmapResizer.cs`
- `source/ZipPla/QuickGraphic.cs`
- `source/ZipPla/LongVectorImageResizer.cs`
- `source/ZipPla/CatalogForm.cs`
- base import/core commit:
  `5e942ef0006decf01c21d9dbe037dba50e763420`
- visible-thumbnail commit:
  `8ea492821efa95ac66483246c5b17fa71b400f0e`
- fixed snapshot / one-thumbnail-worker commit:
  `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`

## 2. ZipPlaFork Viewer path: end-to-end inventory

| 段階 | File / class / method | 固定revisionで確認した処理 |
| --- | --- | --- |
| 入力受付 | `ViewerForm.cs:1066-1085`, `ViewerForm` constructor; `:15852-15855`, default shortcut table | `PreviewKeyDown`、`KeyboardShortcut`、mouse gestureを接続し、既定のWheelDown/UpをNext/Previousへ割り当てる。 |
| command dispatch | `ViewerForm.cs:13042`, `getMouseGestureSettingTemplate` | `Command.NextPage` / `PreviousPage`を`moveToNextPage` / `MoveToPreviousPage`へ結び付ける。 |
| ページ移動要求 | `ViewerForm.cs:12403`, `moveToNextPage`; `:12424`, `MoveToPreviousPage`; `:9279`, `movePageNatural` | natural `NextPage` / `PreviousPage`は**現在の数値band**がreadyなら1回だけ次のbandへ進める。移動先は未完成でもよく、そこで次のnatural入力を拒否する。`MoveForwardOnePage` / `movePageToMinimulForward`は別経路で、ready frontierを越えてlogical currentを更新できる。 |
| current / 未完成判定 | `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage` | singleでは現在pageがreadyならcold destinationへ最大1 step進み、そのpageが完成するまで後続WheelDownを捨てる。spreadでは原則として現在の数値2-page bandが揃ってから次bandへ最大1 step進む（wide/cover分岐は1-page動作を取り得る）。natural入力にpending final targetやburst coalescerはない。 |
| 方向管理 | `ViewerForm.cs:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | 直前の移動方向を状態として保持しない。常に新しい`currentPage`を中心にpriorityを再計算する。 |
| current / next / previous priority | `ViewerForm.cs:5586`, `priorityLevel` | `M = MaxPageCountInWindow`の固定数値bandを使う。`M=1`はcurrent / `c+1` / `c-1`、`M=2`は`c,c+1` / `c+2,c+3` / `c-2,c-1`をlevel 0/1/2とし、残りをlevel 3へ置く。これはwide/coverを含む実際のdisplay-unit topologyではなく、逆方向移動中も数値上のnextが先である。 |
| queue / scheduler | `ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage`; `GenerarClasses.cs:247`, `SetWorksOrder`; `:266`, `privateRunWorkerCompletedEventHandler` | Viewer page workerは1 thread。priority permutationを差し替え、現在実行中の1 jobはpreemptせず、完了境界で次の未開始jobを新orderから選ぶ。 |
| queue再構築 | `ViewerForm.cs:5371`, `bmwLoadEachPage_EachRunWorkerCompleted`; `:5558`, `SetBackgroundMode` | current変更を検出するとworker完了時に全page orderを再構築する。pause中は`SetBackgroundModeIfPausing`から反映する。 |
| ZIP entry一覧 | `PackedImageLoader.cs:283`, constructor; `:1197`, `getZipArchiveEntries` | `ZipArchive`を開きentry tableを作る。archive全体をmemoryへ置くoptional pathも持つ。 |
| ZIP entry read / 展開 | `PackedImageLoader.cs:1781`, `OpenImageStream`; `:1795`, private `OpenImageStream`; `:1856`, `OpenInnerImageStream` | 選択entryのstreamを開き、同じpage job内で展開と画像openを行い、entry streamを処理後にdisposeする。 |
| decode / filter | `ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork`; `ImageLoader.cs`, `BitmapEx.ConvertToBitmapEx` | entry open、full image decode、orientation/filter、`PreFilteredImageArray`生成を1 page job内で行う。 |
| resize / scale | `ViewerForm.cs:5273`, `GetResizedSize`; `:3405-3449`, scaling selection; `BitmapResizer.cs`; `QuickGraphic.cs`; `LongVectorImageResizer.cs` | viewportとbindingからtarget sizeを決め、同じjobでdisplay sizeへresizeする。page schedulerは1 threadだが、pixel resizer内部はparallel pathを使用できる。 |
| display-ready生成 | `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | decodeからresize/post-filter、`VirtualBitmapEx`生成までをliteralな1 background page jobで完結する。 |
| publish | `ViewerForm.cs:5451`, `SetNewResizedImage` | 完成した`VirtualBitmapEx`だけを`ResizedImageArray[idx]`へ代入する。admission失敗時はdisposeして`ReworkOrder`する。 |
| 表示差し替え | `ViewerForm.cs:5371`, `bmwLoadEachPage_EachRunWorkerCompleted`; `:6438`, `showCurrentPage` | singleのcold targetではlogical currentとtrackbarを先に進め、入力直後とworker完成後にinvalidateする。完成後は`ResizedImageArray`へ公開済みのartifactへ差し替える。 |
| paint / loading frame | `ViewerForm.cs:6748-6756`, `:6768`, `pbView_Paint`; `:6990`, `pbView_PaintToCanvas` | cold target未完成時は空clearせず、再利用canvas上の旧frameをin-placeでhalf-gray化してloading frameとして描く。通常coldはloading paint 1回＋完成paint 1回で、NivisViewerの「旧frameを無加工保持」とは異なる。 |
| prefetch | `ViewerForm.cs:5558`, `SetBackgroundMode`; `GenerarClasses.BackgroundMultiWorker` | page配列全体をwork setとし、current近傍から遠方へ1 jobずつ進む。memory admission不能時は再work化または必要近傍完成後にpauseする。 |
| source / display cache | `ViewerForm.cs` fields `PreFilteredImageArray`, `OriginalImageInfoArray`, `ResizedSizeArray`, `ResizedImageArray` | decoded/filter済みsourceとdisplay-ready artifactを別配列で保持するが、同じpage indexとeviction lifecycleに結び付ける。 |
| memory accounting / eviction | `ViewerForm.cs:3522-3528`, `VirtualBitmapEx`生成; `ImageLoader.cs:1660-1673`, constructor; `ViewerForm.cs:5471-5524`, `ReduceUsingMemory` | `VirtualBitmapEx.DataSizeInBytes`へ原寸`PreFilteredImageArray`相当bytesとresize済みdisplay bytesの双方を加算する。evictionでは同pageのresized bitmap、原寸source、size/infoを同じlifecycleでdisposeする。 |
| 方向反転 | `ViewerForm.cs:5377-5384`, `SetBackgroundMode` call | travel direction専用のqueue反転はない。新current基準のorder再構築により、旧方向の未開始jobを後方へ送る。実行中の最大1 jobは完了する。 |
| stale work / source replacement | `ViewerForm.cs:2757-2771`, open path; `:2882-2915`, `Reload` / `clearResizedImageArray`; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 新loader/arrayへ切り替える前に旧resized配列をclearし、`waitCancel=true`で旧worker終了を待つ。意味のあるgeneration IDではなく、wait-cancelとobject replacementで隔離する。 |
| spread protection | `ViewerForm.cs:1903-2002`, `NextPage` / `PreviousPage`; `:5273`, `GetResizedSize`; `priorityLevel` level 0 | navigationのsize/ready判定はbinding/wide/cover分岐を持つ一方、worker priorityは`M=2`の固定数値2-page bandをlevel 0として扱う。通常partnerは同時優先されるが、実display unitとの完全な同一性は保証されない。 |

### 2.1 通常color JPEG 1ページの具体的処理列

対象はsingle page、standard fit、identity EXIF、filterなし、
`ReadOnMemoryMode.None`である。

1. book open時に`File.OpenRead`とarchive readerを各1回だけ作り、
   全entry metadataを配列化する
   (`PackedImageLoader.cs:283-330`, `:1197-1244`, `:1341-1445`)。
2. page jobは`EntryArray[page]`に記録済みのphysical indexから
   `ZipArchiveEntry.Open()`または`OpenEntryStream()`を1回呼ぶ
   (`ViewerForm.cs:3192-3203`, `PackedImageLoader.cs:1781-1917`)。
3. non-seek streamだけ`SeekableStream`とprefix用`MemoryStream`を各1個作る。
   EXIFを読んだprefixだけbufferし、`StopBuffering`後は元entry streamを
   decoderが直接読む (`PackedImageLoader.cs:2731-2898`)。
4. `new System.Drawing.Bitmap(stream)`でJPEGを原寸decodeする。managed層で
   entry全体の`byte[]`、`MemoryStream`、`ToArray`は作らない
   (`ImageLoader.cs:2065-2068`)。
5. orientationが90/270度の時だけ追加Bitmapを1個作る。通常24bpp color
   JPEGはformat変換せず、grayscale判定を1回行う
   (`ViewerFormImageFilter.cs:177-220`, `ViewerForm.cs:4321-4485`)。
6. 原寸sourceを`PreFilteredImageArray[page]`へ保存し、同じworker job内で
   `GetResizedSize`、resize 1回、任意post-filter、`VirtualBitmapEx`生成まで
   完了する (`ViewerForm.cs:3279-3528`, `BitmapResizer.cs:42-188`)。
7. worker completionをUI synchronization contextへ1回marshalし、
   `ResizedImageArray[page]`へ1回insert、currentなら
   `showCurrentPage(false)`からinvalidateする
   (`GenerarClasses.cs:266-287`, `ViewerForm.cs:5371-5468`)。
8. paintは再利用canvasへfit済みbitmapをscanline copyし、最後に
   `DrawImageUnscaled`でwindowへ転送する
   (`ViewerForm.cs:6768-6858`, `:6990-7250`, `:7386-7414`)。

| 処理 | ZipPlaFork full cold | display hit |
| --- | ---: | ---: |
| archive再open / reader再生成 / entry全走査 | 0 / 0 / 0 | 0 / 0 / 0 |
| physical entry参照 / entry stream open / 展開 | 1 / 1 / 1 | 0 / 0 / 0 |
| entry全体byte copy / byte[] / full MemoryStream | 0 / 0 / 0 | 0 / 0 / 0 |
| EXIF prefix MemoryStream | 通常1 | 0 |
| JPEG decoder / decode | 1 / 原寸1 | 0 / 0 |
| orientation追加Bitmap | identity 0、90/270度時1 | 0 |
| display resize / resized Bitmap / wrapper | 1 / 1 / 1 | 0 / 既存1 / 既存1 |
| page worker job / worker→UI callback | 1 / 1 | 0 / 0 |
| source cache lookup / insert | 1 / 1 | 0 / 0 |
| display cache insert | UIで1 | 0 |
| 入力直後 / 完成後invalidate | 1 / 1 | 1 / 0 |
| 通常paint | loading 1＋完成1 | 1 |

方向反転時は実行中の旧方向jobをpreemptしないため最大1件残り、その結果も
通常どおりcacheへ入る。worker完了境界で新current中心に全orderをsortし直す。
個別のqueued worker objectはなく、次に選ばれるjobだけが変わる。

## 3. NivisViewer Viewer path: end-to-end inventory

| 段階 | File / class / method | 現在の未commit実装 |
| --- | --- | --- |
| 入力受付 | `app/viewer_widget.py`, `ViewerWidget.wheelEvent` | Ctrl+wheelはzoom、それ以外は`nextRequested` / `previousRequested`をemitする。native inputは検証で使用しない。 |
| ページ移動要求 | `app/viewer_window.py`, `ViewerWindow.next_page` / `previous_page`; `app/viewer_page_navigation.py`, `ViewerPageNavigationController.next_display_unit` / `previous_display_unit` | navigation controllerが`PageModel.next` / `previous`を呼び、`_on_page_navigation_changed`から`_refresh_view`へ進む。 |
| current決定 | `app/page_model.py`, `PageModel.next` / `previous` / `go_to_index`; `app/viewer_window.py`, `_refresh_view` | logical current、slider、historyは表示artifact完成前にtargetへ進む。visual frameだけは完成まで旧frameを維持する。この点はZipPlaForkのnormal navigationと同一ではない。 |
| request isolation | `app/viewer_window.py`, `_active_request_id`, `ViewerDisplayUnit`; `app/image_cache.py`, `generation`; `app/viewer_widget.py`, `_display_request_generation`, `_render_generation` | request ID、source generation、source identity、render generationを各境界で照合し、古いdecode/render結果をcurrentへcommitしない。 |
| 方向管理 | `app/viewer_window.py`, `_navigation_prefetch_direction`, `_configured_prefetch_units` | 直前centerとの差から進行方向を明示的に追跡する。反転時は両側の直近unitを先に保護し、その後を新方向優先に並べる。これはZipPlaForkより強い独自拡張。 |
| 共通priority | `app/image_work_coordinator.py`, `ImageWorkPriority`; `app/image_cache.py`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_prepare_display`, `_request_magnifier_render` | decodeとrenderを同一priority domainへ統一する。current=800、spread partner / 近傍preparedは700以下、interactive rerender / magnifier=600、next=500、previous=400。 |
| production execution lane | `app/image_work_coordinator.py`, `ImageWorkCoordinator`; `app/image_cache.py`, `_start_task`; `app/viewer_widget.py`, `_start_render_task`; `app/viewer_window.py`, `_build_ui` | productionではdecode taskとdisplay render taskを同じ1-worker Viewer laneへ送る。Browserは別laneで、current cold処理中はpauseする。coordinator未指定時だけ各classの1-worker fallback poolを使う。 |
| paced scheduler | `app/viewer_window.py`, `_start_raster_prefetch_pipeline`, `_advance_raster_prefetch_pipeline`, `_clear_raster_prefetch_pipeline` | raster prefetchは1 display unitだけをadmitし、そのunitがsource decodeとprepared renderを終えてterminalになるまで次unitをqueueへ入れない。budget拒否、cancel、render failureではfrontierで停止する。 |
| literal job境界 | `app/image_cache.py`, `_ImageLoadTask.run`; `app/viewer_render.py`, `ViewerRenderTask.run`; Qt queued signal boundary | ZipPlaForkのliteralな1 jobとは異なる。decode taskが`QImage`をGUI-thread signalで公開し、別`ViewerRenderTask`がscaleし、GUI threadが`QPixmap`化する。paced single laneにより実行順だけを「1 unit完了後に次unit」と同等化する。 |
| queue再計画 / 反転 | `app/viewer_window.py`, `_refresh_view`, `_schedule_prepared_display_prefetch`; `app/image_cache.py`, `preload_around`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_queue_render` | 新currentごとにdecode/prepared planを再構築する。`tryTake`可能な未開始jobは昇格だけでなく降格も行い、旧方向のqueued jobを残さない。実行中の1 jobはpreemptしない。 |
| input coalescing | `app/viewer_window.py`, `_queue_decode_demand`, `_apply_pending_decode_demand`, `_apply_pending_display_demand` | 16 ms input-idle境界で高速wheelの通過targetを置換し、request ID/generation確認後に最終targetだけを登録する。これはZipPlaForkの`SetWorksOrder`そのものではなくNivis独自改善。 |
| ZIP entry read / 展開 | `app/image_source.py`, `ZipImageSource._read_entry_stream` | `zipfile.ZipFile` entryを1 MiB chunkでrequest-local cancellation確認しながら、size preallocated `BytesIO`へ展開する。ZipPlaForkのdirect entry-stream decodeとは異なり、entry全体を一度buffer化する。 |
| decode | `app/image_cache.py`, `_ImageLoadTask._run_load`; `app/image_source.py`, `ZipImageSource.open_qimage_at_most`, `_read_jpeg_qimage_at_most` | standard fit JPEGは`QImageReader.setScaledSize()`でphysical Viewer size以下へdecoder-scaleする。actual/manual/non-standard、調整あり、unsupported formatはfull decode pathを維持する。 |
| resize / display-ready生成 | `app/viewer_render.py`, `ViewerRenderTask.run`, `render_qimage`; `app/viewer_widget.py`, `_queue_render` | worker-sideでtarget-size `QImage`を生成する。standardはQt、non-standardは既存Pillow resamplingを使う。 |
| publish / QPixmap | `app/viewer_widget.py`, `_on_render_completed`, `_commit_pending_display`, `_commit_display` | accepted generationの完成`QImage`だけをGUI threadで一度`QPixmap.fromImage`し、single/spread全slot terminal後にatomic commitする。 |
| paint / 暗転回避 | `app/viewer_widget.py`, `paintEvent`; `app/viewer_window.py`, `_on_viewer_content_painted`, `_release_raster_prefetch_without_paint` | old complete frameを新target完成まで維持する。commit後のpaintを確認してからprefetchを解放する。hidden/minimized/error-only frameには250 ms fallbackがあり、Browser pauseの永久残留を防ぐ。 |
| current-first cold path | `app/viewer_window.py`, `_apply_pending_decode_demand`, `_preload_image_source(immediate_only=True)` | target visualが未表示ならcurrent visible unitだけをdecode/render/publish/paintまで進め、遠方prefetchを開始しない。 |
| prefetch | `app/viewer_window.py`, `_configured_prefetch_units`, `_start_raster_prefetch_pipeline`; `app/viewer_widget.py`, `prepared_display_is_ready`, `renderWorkFinished` | current後は直近forward、直近backward、残りの新方向、反対方向の順にdisplay unitを作る。各unitのprepared terminal signalで次へ進む。 |
| source cache | `app/image_cache.py`, `ImageCache`, `_enforce_limit`, `_oldest_evictable_index` | decoded/decoder-sized `QImage`をbyte計上する。currentとvisible spreadを保護し、wanted rankの遠いpageからevictする。 |
| display cache | `app/viewer_widget.py`, `_render_cache`, `_prepared_units`, `_enforce_render_cache_limit` | target-size `QPixmap`とdisplay-unit metadataを保持し、current、pending、直近forward/backward、active paced unitを保護する。 |
| combined memory | `app/viewer_window.py`, `_enforce_combined_cache_budget`; `ImageCache.cache_bytes`; `ViewerWidget.render_cache_bytes` | 設定値をsource cacheとpixmap cacheへ別々に満額付与せず、両者のresident byte合計へ1つの上限を適用する。巨大なcurrent spreadの保護によりbest-effortで超過し得る。 |
| spread protection | `PageModel.spread_at`; `ViewerWidget.prepare_display_units`, `_protected_prepared_units`; `ImageCache._protected_indexes` | current spread partnerをcurrent相当でdecodeし、prepared cacheはcurrent、直近両側、active paced unitをdisplay-unit単位で保護する。partial spreadをatomic表示しない。 |

### 3.1 `d19554a` production cold pathの実数

前回のpaced scheduler移植後も、通常JPEG cold missのcritical pathには次の
二段構造が残っていた。

```text
16 ms decode-demand timer
  -> _ImageLoadTask
     -> ZipExtFile read -> preallocated BytesIO
     -> BytesIO.getvalue
     -> Pillow header/EXIF parser
     -> QByteArray -> QBuffer -> QImageReader target decode
  -> queued ImageCache._on_loaded
     -> source cache insert -> pageLoaded
  -> ViewerWindow._on_cache_page_loaded
     -> combined budget -> _render_spread
  -> ViewerRenderTask（通常target一致なので実resize 0回）
  -> queued ViewerWidget._on_render_completed
     -> QPixmap.fromImage -> render cache insert
     -> atomic commit -> update -> paint
```

single通常経路の`focused_index`参照はinput handler内6回、cold完了時に
さらに3回である。ただし`PageModel.focused_index`のcurrent identity一致
fast pathが成立するため、この条件では`index_for_identity`、
`list_images`、entry全走査はすべて0回であり、大ZIPの線形要因ではない。

| 項目 | prepared hit | source hit / prepared miss | full cold (`d19554a`) |
| --- | ---: | ---: | ---: |
| 固定idle待ち | 0 ms | 約16 ms | 約16 ms |
| Viewer worker task | 0 | render 1 | decode 1＋render 1 |
| cross-thread queued callback | 0 | 1 | 2 |
| ZIP entry open / 展開 | 0 / 0 | 0 / 0 | 1 / 1 |
| entry payload materialization | 0 | 0 | entry `BytesIO`＋Pillow `BytesIO`＋Qt `QByteArray` |
| JPEG parser / pixel decoder | 0 / 0 | 0 / 0 | Pillow 1 / QImageReader 1 |
| post-decode scale | 0 | 通常0 | 通常0 |
| `QPixmap.fromImage` | 0 | 1 | 1 |
| source cache lookup / insert | 2 / 0 | 2 / 0 | 3 / 1 |
| render cache lookup / insert | 2 / 0 | 3 / 1 | 3 / 1 |
| combined budget enforcement | 0 | 1 | 2 |
| slider / status更新 | 1 / 1 | 1 / 1 | 2 / 2 |
| `_sync_actions` / QAction setter | 2 / 約80 | 2 / 約80 | 3 / 約120 |
| nominal fit layout / DPR参照 | 3 / 2 | 6 / 6 | 7 / 7 |
| 実windowでのDWM attribute設定 | 2 | 2 | 2 |

`BytesIO.getvalue()`やQt implicit sharingがcopyを省略する場合はあるが、
source code上はchunk群からentry buffer、Pillow header buffer、Qt byte arrayの
最大3 payload相当の境界がある。加えて、通常portraitではresizeしない
`ViewerRenderTask`がworker切替と2つ目のqueued callbackだけを発生させていた。

### 3.2 production-only bottleneck

既存offscreen benchmarkはWindowをshowせず、BrowserWindow /
BrowserThumbnailProviderも接続しない。そのため次の実機処理を測っていなかった。

- `ViewerWindow._refresh_view`が毎ページ
  `FullscreenChromeController.reevaluate_visibility`を同期呼出しし、visible
  Windows windowでは`winId()`と`DwmSetWindowAttribute`を2回実行していた。
  fullscreen state、native handle、paletteが変わらないページ移動には不要である。
- cold currentで`begin_viewer_interactive`がBrowser thumbnail pending全件を
  lock内走査・cancelし、paint内の`contentPainted`から即resumeして30 ms後に
  Browser decodeを再開していた。次のpage decodeとCPU／memory bandwidthが
  競合し得る。
- benchmarkは`window.next_page()`を直接呼ぶため、wheel event、
  `nextRequested`、`next_page_or_scroll`、旧frame fit layout 1回を含まない。
- offscreen raster pixmapにはvisible backing store、Windows compositor、
  real screen DPR、native QPixmap upload、vsync presentationがない。

この差により、前回のscheduler変更はprefetch orderを改善しても、実機の
DWM call、Browser pause/resume cascade、16 ms gate、payload materialization、
二段worker、QPixmap uploadを残したままであり、体感差が小さかったと説明できる。

### 3.3 `ZipPlaCompatibleRasterPath`

2026-07-31の変更では、次の限定条件を満たす通常経路を新しいproduction
dispatcherとして採用した。

- `ZipImageSource`のZIP/CBZ
- JPEG entry
- single page
- `fit_window`または`fit_no_upscale`
- standard resampling、rotation 0、画像調整なし
- wide splitなし、magnifier機能OFF、page list設定OFF

呼び出し列は次のとおり。

```text
ViewerWindow._refresh_view
  -> strict eligibility / request ID / book generation
  -> ImageCache.suspend_raster_work（A→B切替時の1回だけ）
  -> ZipPlaCompatibleRasterPath.request
  -> _ZipPlaCompatibleRasterJob（同時最大1件）
     -> ZipImageSource.open_compatible_jpeg_at_most
        -> ZipFile.NameToInfo O(1)
        -> ZipExtFile 1回
        -> 1 MiB chunkをreserved QByteArrayへ格納
        -> seekable QBuffer
        -> QImageReader.size/transformation（C++側）
        -> setAutoTransform + setScaledSize + read
  -> queued GUI callback 1回
     -> stale source/epoch/request/layout照合
     -> QPixmap.fromImage 1回
     -> path-local QPixmap ring insert
     -> ViewerWidget.commit_display_ready_single
        -> QPixmap-only atomic swap -> update 1回
  -> paintEvent -> frame serial acknowledgement
```

この経路は`ImageCache` source cache、`CachedImage`、`ViewerRenderTask`、
render cache、prepared-unit cache、combined-cache enforcementを通らない。
QImageはworker resultから唯一のGUI callbackへownershipを渡し、
QPixmap化後に保持しない。current／numeric next／numeric previousの最大3枚だけを
QPixmap ringへ保持する。cache hitはworker 0、queued callback 0、
QPixmap生成0で同じartifactをcommitする。

direction reversalでは、新currentと同じactive jobだけrequest IDをadoptする。
それ以外のactive jobはcancelし、未開始なら`tryTake`する。controller自身は
先行queued jobを作らないため、旧方向queued jobは0件である。running
ZipExtFile readは1 MiB chunk境界でcancelされ、decoder内部から戻った古い結果も
QPixmap化前にrejectする。

直接移植・対応箇所:

| ZipPlaFork由来の処理 | 移植元 | NivisViewer側 |
| --- | --- | --- |
| persistent archive/index、entryをpage jobが一度だけ取得してdecoderへ渡す | `PackedImageLoader.cs:283-330`, `:1781-1917` | `app/image_source.py`, `_read_entry_qbytearray`, `ZipImageSource.open_compatible_jpeg_at_most` |
| entry→decode→display-readyを1 page jobで完結 | `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run` |
| current→next→previousを1件ずつdispatch | `ViewerForm.cs:5558-5605`, `SetBackgroundMode` / `priorityLevel` | `ZipPlaCompatibleRasterPath.request`, `_drive` |
| completion 1回でdisplay artifact公開 | `GenerarClasses.cs:266-287`, `ViewerForm.cs:5371-5468` | `ZipPlaCompatibleRasterPath._on_job_completed`, `ViewerWidget.commit_display_ready_single` |
| page単位の近傍artifact保持 | `PreFilteredImageArray`, `ResizedImageArray` | path-local最大3枚QPixmap ring。対応外機能は従来Aへfallback |

Qt/Python差として、ZipPlaForkのC#/native streamに最も近いPython製sequential
`QIODevice`はQt JPEG pluginから約1,985回/pageの`readData` callbackを発生
させ、高詳細JPEGのworker decodeが旧buffered 241.86 msに対して270.44 msに
なった。そのためproduction新Bはentryを一つのreserved `QByteArray`へ
materializeし、seekable `QBuffer`から`QImageReader`へ渡す。`BytesIO`、
`getvalue()`、Pillow header parserは通らず、Python decoder callbackもない。
ZipPlaForkは原寸source＋resized artifactを保持するのに対し、この限定pathは
display QPixmapだけを保持し、magnifier、非standard、page list等は従来
pipelineへ戻す。

付随するproduction変更:

- page refreshから不変なDWM attribute再適用を撤去した。
- compatible cache hitではBrowser laneをpauseしない。
- cold frame paint後のBrowser resumeに500 ms graceを設け、連続入力中の
  thumbnail全queue pause/resumeを避ける。
- compatible commitでslider/statusを各1回だけ更新し、page navigation時は
  history action 2件だけ更新する。full QAction同期とpage-list scanは通らない。
- active ZIP decode中の`ZipImageSource.close()`はUI threadでlock待ちせず、
  cancelを設定してreturnし、最後のrequest終了時にpersistent ZipFileを閉じる。

## 4. Historical legacy-pipeline comparison

次表は`ZipPlaCompatibleRasterPath`採用前の旧Aに対する判断であり、対象条件を
満たすZIP/JPEG production経路の最終判断ではない。旧Aがfallbackとして残る
理由と、前回paced dispatcherが実機体感を十分変えなかった差分を保存する。

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針 |
| --- | --- | --- | --- | --- | --- |
| decode pipeline | entry readからdecode、filter、resize、display-ready生成までliteralな1 page job | decode taskとrender taskは別だが、同じ1-worker laneをpaced dispatcherで直列化 | miss時の単純さはZipPlaFork、standard JPEGのpixel量はNivisViewer | ZipPlaForkはstage間queueがない。Nivisはdecoder-scaled JPEGでfull rasterを避け、generation境界が強い | 構造移植 |
| scheduler / queue | `BackgroundMultiWorker` 1 thread、全page permutation、完了境界で再order | shared Viewer lane、1 display unitずつadmit、terminal後に次unit | NivisViewer（移植後） | literal jobは2つ残るが、遠方workをqueueへ先行登録しないため、current missを阻害しにくい | 構造移植 |
| priority制御 | currentの固定数値`M`-page band → 数値上のnext band → previous band → rest | current 800、spread/prepared 700以下、interactive 600、next 500、previous 400。decode/render共通domain | NivisViewer | currentとactual spread topologyを保護しつつ、renderがdecode priorityに負けない | 構造移植 |
| direction reversal handling | directionを追跡せず、新current基準で全order再計算 | directionを追跡し、queued decode/renderを昇格・降格し、paced planを作り直す | NivisViewer | 反転直後の直近両側を保護し、旧方向の未開始workを残しにくい | 部分移植（独自拡張を維持） |
| cache structure | source/displayを別配列で持つがpage indexとeviction lifecycleを共有 | source `QImage` cache、render `QPixmap` cache、prepared unit metadataの二層 | ZipPlaForkは単純、NivisViewerはQt機能要件に適合 | Nivisはmagnifier、resize、resampling再生成のためsource/display分離が必要だが複雑 | 維持 |
| eviction policy | display artifact bytesを基準に遠方からevictし、同page sourceも同時破棄 | sourceとpixmapを双方byte計上し、それぞれpriority-aware eviction | NivisViewerの計上精度、ZipPlaForkのlifecycle単純性 | Nivisは実resident artifactをより正確に上限へ反映する | 部分移植 |
| prefetch policy | 全page work setをcurrent近傍から1 jobずつ消化 | current paint後、1 display unitずつdecodeからprepared terminalまでpaced実行 | NivisViewer | 通過pageや複数unitの同時未完成を抑止し、設定した近傍だけに限定できる | 構造移植 |
| display apply timing | natural moveは現在bandがreadyならcold destinationへ最大1 step logical currentを進め、forced one-page/trackbarはさらに先へ進める。pixel完成とはtransaction化されない | requested targetは先へ進むがdisplayed/slider/statusはold complete frameを維持し、完成unitだけatomic commit | NivisViewer | ZipPlaForkはhalf-gray loading canvasとpaint時statusを使う。Nivisはrequested/displayedを分離し、stale completionを表示へ混入させない | Hybrid（atomic commit維持） |
| dark-frame avoidance | cold destinationへ1 step進んだ場合もreusable canvasをclearせずhalf-gray化する | old complete `QPixmap`を新unit完成まで無加工で保持 | NivisViewer | Qtで安全なatomic swapを行い、error terminalもpaint完了として扱う | 維持 |
| cold miss behavior | current pageのliteral 1 jobが完了してから次job | current visibleのみをpaced single laneでdecode→signal→render→commit→paintし、その後prefetch | 構造要件は同等、cancel/stale拒否はNivisViewer | 4096 x 6500 JPEGのforced coldでcurrent以外を先行decodeせず、53.336–60.200 msでpaintした | 構造移植 |
| memory use | full decoded sourceとresized artifact。`usedMemory`はdisplayのみ計上 | standard JPEGはdecoder-size source、source+pixmapをcombined byte計上 | NivisViewer | 大JPEGでfull source rasterを常駐させず、二重満額budgetも廃止 | 部分移植 |
| large ZIP behavior | entry streamを同じpage jobのdecoderへ渡す。optional archive-memory modeあり | entryをpreallocated `BytesIO`へ展開し、decoder-scale後に別render task | 既知の実機体感はZipPlaFork、offscreen構造検証はNivisViewerも合格 | Nivisはbuffer copyが残る一方、decoder-scaled JPEG、current-only cold start、通過job抑止が有効。実機の同一ZIP再確認は未実施 | 構造移植、entry stream直結は検討継続 |
| stale result rejection | wait-cancelとloader/array replacement | request ID、source/render generation、source identity | NivisViewer | 高速入力、resize、book replacementの古い結果を明示的に拒否できる | 維持 |
| spread protection | 通常は固定数値`M=2` bandをlevel 0に置くが、navigationのwide/cover分岐とworker bandは完全には同じtopologyでない | decode protection、prepared-unit protection、atomic multi-slot commit | NivisViewer | actual complete display-unit topologyをsource/render両層で共有し、partial spreadをcommitしない | 維持 |
| QPixmap / paint | GDI `VirtualBitmapEx`とreusable bitmap canvas | worker `QImage`、GUI-thread one-time `QPixmap.fromImage`、cached paint | NivisViewer | QtのGUI resource境界を守りつつnavigation時変換を避ける | 維持 |
| Browser contention | Viewer比較対象内にNivis相当のapplication-wide lane制御なし | current cold中はBrowser laneをpause、paint/fallbackでrelease | NivisViewer | Viewerを他画面のdecode競合から保護する | 維持 |

### 4.1 Final comparison for eligible ZIP/JPEG

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針（維持 / 部分移植 / 構造移植 / 全置換） |
| --- | --- | --- | --- | --- | --- |
| decode pipeline | persistent entry streamからnative decoderへ渡し、同じpage jobでorientation、resize、display bitmapまで作る | 新Bはentryを一つのreserved `QByteArray`へ読み、seekable `QBuffer` / `QImageReader`でdecoder scaleし、同じjobでdisplay-ready `QImage`まで作る | native streamはZipPlaFork、Qt/Python上の実測は新B構成 | Python sequential streamは約1,985 callback/pageで旧Aより遅かった。QByteArray版は1 materializationを許容してcallbackをC++内へ戻す | 構造移植 |
| scheduler / queue | page worker 1、completion 1、完了境界でpriority orderを再評価 | `_ZipPlaCompatibleRasterJob`最大1、completion 1、先行queueを作らない | 同等、新Bはstale境界が強い | cold currentは旧A 2 task / 2 callback、新B 1 / 1 | 全置換 |
| priority制御 | currentの固定数値band → numeric next band → previous band | current完成・matching paint後に移動方向側 → 反対側 | 新B | upstreamの固定数値bandよりactual display-unit topologyを正確に保護する | 構造移植 |
| direction reversal handling | 実行中1件の終了後、新current中心にwork orderを再構築 | 未開始jobを`tryTake`、running jobへcancel、request/source/layout serialで結果棄却 | 新B | 反転coldは旧A 3 task / 3 callback、新B 2 / 2。stale commit 0 | 構造移植 |
| cache structure | original bitmap配列とresized bitmap配列をpage lifecycleで管理 | eligible pathは最大3 display-ready QPixmap ringだけ。source/prepared/render cacheを通らない | 新B | 常設logical cache 121.46→30.36 MiB、7→3 page | 全置換 |
| eviction policy | current周辺を保護し、遠方pageのsource/displayをdispose | current、next、previous以外をpruneし、source/epoch/layout/DPR key違いを残さない | 新B | 古い仕様entryと多層cache間の寿命差を除去 | 全置換 |
| prefetch policy | current完成後、浅い近傍をsingle workerで処理 | matching `framePainted`後だけ最大2近傍。rapid inputは6 ms idle graceで最終currentへcoalesce | 新B | rapid-finalのcommit前通過page decode 0。paint後page 19 / 17は別phase | 構造移植 |
| display apply timing | 完成bitmapだけをUI completionで差し替える | GUI callbackでQPixmapを1回作り、`commit_display_ready_single`でatomic swap | 同等、新Bはserial確認が強い | 未完成QImageやplaceholderをViewerへ公開しない | 構造移植 |
| dark-frame avoidance | reusable canvasの旧frameを残しloading maskを描く | 完成済み旧QPixmapを無加工保持し、新frame完成後だけswap | 新B（Qt上） | blocking probe中`clear()` 0、commit 0、旧frame保持 | 構造移植 |
| cold miss behavior | entry open→decode→resize→displayを1 jobで完了 | 1 entry、1 materialization、1 QImage、1 QPixmap、1 task、1 callback、1 paint | 新B | fresh-process順送り255.481 ms、反転268.689 ms、往復中央値262.848 ms、rapid270.425 msで旧Aより2.47–8.21%短い | 全置換 |
| memory use | originalとresizedを保持する | task-local compressed QByteArray/QImage、常設最大3 QPixmap | 新B | viewer working-set delta 195.63→65.02 MiB（-66.76%） | 全置換 |
| large ZIP behavior | archive readerとentry indexをbook中保持、entryごとにstreamを開く | persistent `ZipFile`とO(1) indexを保持し、対象entryだけ32 x 1 MiB read | 同等のindex、pipelineは新B | 682,441,432-byte、日本語名21 entryの同一SHA-256 ZIPで完走 | 構造移植 |

## 5. Fixed-revision port map

以下はすべてrevision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、
AGPL-3.0-or-later由来である。

| Upstream file / class / method | 移植した処理 | NivisViewer destination | 移植境界 |
| --- | --- | --- | --- |
| `ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage.ThreadCount = 1`; `ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork` | memory-bandwidth-heavyなViewer page処理を1 execution laneに限定 | `app/image_work_coordinator.py`, `ImageWorkCoordinator`; `app/image_cache.py`, `_start_task`; `app/viewer_widget.py`, `_start_render_task`; `app/viewer_window.py`, `_build_ui` | 構造移植。C# worker実装は未コピー。 |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork`; `:5371`, completion; `:5451`, `SetNewResizedImage` | 数値priority bandから選んだ1 pageをdecodeからdisplay-ready publishまで完了し、最新orderの次pageへ進む | `app/viewer_window.py`, `_apply_pending_decode_demand`, `_start_raster_prefetch_pipeline`, `_advance_raster_prefetch_pipeline`, `_on_viewer_content_painted`; `app/viewer_widget.py`, `prepared_display_is_ready`, `renderWorkFinished` | 構造移植。Nivisはliteral 1 QRunnableではなく、decode task → GUI signal → render taskのpaced single lane。 |
| `ViewerForm.cs:5558`, `SetBackgroundMode`; `GenerarClasses.cs:247`, `SetWorksOrder`; `:266`, completion reorder | 新currentを中心に未開始workを再優先付け | `app/viewer_window.py`, `_refresh_view`, `_configured_prefetch_units`, `_schedule_prepared_display_prefetch`; `app/image_cache.py`, `preload_around`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_queue_render` | 構造移植。Nivisのtravel-direction追跡、両側直近保護、16 ms coalescingは独自拡張。 |
| `ViewerForm.cs:5451`, `SetNewResizedImage`; `:5471`, `ReduceUsingMemory` | 完成artifactだけをpublishし、遠方pageをmemory policyで破棄 | `app/viewer_widget.py`, `_on_render_completed`, `_commit_pending_display`, `_commit_display`; `app/image_cache.py`, `_enforce_limit`, `retain_visible_only`; `app/viewer_window.py`, `_clear_raster_prefetch_pipeline`, `_enforce_combined_cache_budget` | algorithm/structure移植。upstreamはdisplay bytesだけを計上しsourceを同時evict、Nivisはsource+pixmap双方を計上し、active unitの一時source保護をterminal時に解除する。 |
| `ViewerForm.cs:6438`, `showCurrentPage`; `:6768`, `pbView_Paint`; `:6990`, `pbView_PaintToCanvas` | 完成frameのatomicな表示とblank frame回避 | `app/viewer_widget.py`, `_commit_display`, `paintEvent`; `app/viewer_window.py`, `_on_viewer_content_painted` | 処理構造移植。GDI canvas、`LockBits`、`CopyMemory`は未コピー。 |
| `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage`; `:9279`, `movePageNatural` | 未完成表示単位へnormal currentを進めない | 現在は完全移植なし。`ViewerWidget`がold visual frameを維持する一方、`PageModel`はtargetへ進む | 差分として意図的に記録。stateとvisualを同時commitする場合は追加の構造移植候補。 |
| `PackedImageLoader.cs:1781`, `OpenImageStream`; `:1856`, `OpenInnerImageStream` | entry単位の読み出しownershipと同じpage jobのdecoderへの受け渡し | `app/image_source.py`, `ZipImageSource._read_entry_qbytearray`, `open_compatible_jpeg_at_most`; `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run` | 構造移植。C# native streamはPython callback costのため直訳せず、一つのQByteArray/QBufferへ適応した。 |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork`; `:5371-5468`, completion | eligible JPEGでentry取得からdisplay-ready artifactまでをliteralな1 jobと1 UI completionで処理 | `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run`, `_on_job_completed`; `app/viewer_widget.py`, `commit_display_ready_single` | class / processing structure移植。Qt GUI resource規則によりQPixmap化だけGUI threadで行う。 |
| `ViewerForm.cs:5558-5605`, `SetBackgroundMode`; `GenerarClasses.cs:247-287` | current完成後に近傍を一件ずつ選び、完了境界で最新orderへ戻る | `ZipPlaCompatibleRasterPath.request`, `release_prefetch`, `_drive`; `ViewerWindow._on_compatible_raster_frame_painted` | 構造移植。新Bは方向側優先、6 ms rapid coalescing、request/source/layout serialを追加。 |
| `ViewerForm.cs:2757-2771`, open; `:2882-2915`, reload; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 古いworkを新sourceへ混入させない | `app/image_cache.py`, `generation`, `_on_loaded`; `app/viewer_window.py`, request ID / `ViewerDisplayUnit`; `app/viewer_widget.py`, render generations | 目的を独立実装。Nivisのgeneration方式を維持。 |

## 6. Removed, replaced, and deliberately retained structures

今回、eligible ZIP/JPEG production pathでは次をさらに置換した。以下の
旧paced pipelineは非対応条件とfailure fallbackには残るが、新Bのcritical
pathには入らない。

- `_ImageLoadTask` → GUI load callback → `ViewerRenderTask` → GUI render
  callbackを、`_ZipPlaCompatibleRasterJob` → GUI frame callbackへ置換した。
- `BytesIO` → Pillow header parse → `QByteArray` handoffを、reserved
  `QByteArray` → seekable `QBuffer`へ置換した。
- source cache、prepared-unit cache、render cache、combined budget lookupを
  eligible current pathから撤去し、最大3件artifact ringへ置換した。
- current result callback直後にprefetchを開始する方式を、matching
  `framePainted`後に解放する方式へ置換した。
- 高速入力中の各cold requestを即時worker投入する方式を、古いworkをcancel後、
  6 ms idle graceで最新requestだけをsubmitする方式へ置換した。
- decode用poolとrender用poolを別々に同時進行させる経路を、共通の
  1-worker Viewer laneへ置換した。
- 全prefetch unitを先にqueueへ積む経路を、1 display unitだけadmitし、
  prepared terminal後に次unitを投入するpaced dispatcherへ置換した。
- priority上昇だけを許すqueue更新を、方向反転時のpriority降格にも対応する
  queue再構築へ置換した。
- source cacheとprepared pixmap cacheへ設定値を別々に満額付与する方式を、
  resident byte合計に対するcombined budgetへ置換した。
- cold currentと遠方prefetchを同時に開始する方式を、current commit/paint後に
  prefetchを解放する方式へ置換した。
- 最後にadmitした遠方sourceがpipeline完了後もprotectedに残る方式を、
  terminal時にcurrent visibleだけへsource保護を戻す方式へ置換した。

次はfallbackと非対応機能のため撤去していない。

- `ImageCache`のdecoded/decoder-sized `QImage` cache
- `ViewerWidget`のdisplay-ready `QPixmap` cacheとprepared-unit metadata
- decode taskとrender taskの型およびGUI signal境界
- request ID、source generation、render generation、source identity確認
- coordinatorを注入しないtest/fake用のlocal one-worker fallback pools
- Pillow/PySide6/PDFiumに適合した既存decode/render実装

eligible ZIP/JPEG新Bはliteralな1 page QRunnableと1 GUI completionへ
置換した。ただしC#／GDI+コードの逐語コピーではなく、archive/worker ownership、
work order、completion/publish順をPySide6へ構造移植したものである。
旧Aの二段taskはpage list、magnifier、rotation、non-standard、非JPEG、
decoder failureのfallbackとして残る。

## 7. Historical legacy-A ready/cold measurement status

次表はcompatible新B採用前のpaced single-lane、共通priority、combined
cache実装を対象とする旧A baselineである。
`QT_QPA_PLATFORM=offscreen`で、21 entry、4096 x 6500 JPEG、3840 x 2106
Viewer、日本語名を含む15,313,338 byteの一時ZIPを使った。JPEGは生成時間を
抑えるため最大8種類の画素内容をentry名を変えて再利用した。実アプリ、
native入力、外部アプリは起動していない。値は2026-07-31の
`scripts/benchmark_viewer_navigation.py` target-decode runである。

| Scenario | Handler median / max | Request-to-paint median / max | Worker / stale-work evidence | Memory / retained pages |
| --- | --- | --- | --- | --- |
| ready順送り | 0.292 / 0.505 ms | 5.082 / 6.147 ms | prepared hit、decodeなし | warm window |
| ready逆方向 | 0.261 / 0.329 ms | 5.044 / 6.025 ms | prepared hit、decodeなし | warm window |
| ready往復 | 0.232 / 0.259 ms | 5.100 / 5.441 ms | prepared hit、decodeなし | warm window |
| 順送りcold miss | 0.301 / 0.301 ms | 59.775 / 59.775 ms | page 11だけをdecode、1 atomic commit、旧frame保持 | forced eviction |
| 逆方向反転直後cold miss | 0.309 / 0.309 ms | 53.336 / 53.336 ms | 旧running page 11をcancelしtarget page 9へ移行、far page 12は未開始 | forced eviction |
| 往復cold miss（6 legs） | 0.391 / 0.411 ms | 53.787 / 54.416 ms | page 11 / 10だけを交互に6 decode、各leg 1 commit | targetを各legでevict |
| 高速連続入力停止後の最終page cold miss（8 inputs） | 0.088 / 0.299 ms、合計0.933 ms | final paint 60.200 ms | page 18だけをdecode、通過page 11–17は未開始、1 commit | final targetをforced cold |
| current周辺保護 / 通過page抑止 | N/A | N/A | page 11、12、13を保護。高速入力の通過pageはdecodeなし | 保護3 pageを保持 |
| old-direction queued/running job | N/A | N/A | running page 11はcancel観測点まで進むが、queued far page 12は開始せずpage 9を優先 | stale commitなし |
| 暗転 / empty frame | N/A | blocked中も旧frame | requested/slider 11でもdisplayed 10を保持、`clear()` 0回、blocked中commit 0回、完了時1回 | empty frameなし |
| process working set / peak | N/A | N/A | baseline 351.07 MiB、sampled max 497.11 MiB、delta 146.04 MiB | 終了時487.32 MiB、process peak 517.36 MiB。ZIP/JPEG生成も含む |
| source+pixmap combined bytes / 保持page数 | N/A | N/A | source 70.85 MiB + render 50.62 MiB = 121.47 MiB / 128 MiB | source 7 entries、render 12 entries、prepared 5 units、current周辺を保持 |

historical paced-dispatcher前のready値と比べ、ready paintは順送り
5.294→5.082 ms、逆方向5.510→5.044 ms、往復6.055→5.100 msへ短縮した。
warm windowから8入力でprepared frontierを越える`rapid_frontier`のfinal
paintは70.297→53.015 msへ17.282 ms、24.6%短縮した。
forced-cold 4種には同じ計測方法の変更前値がないため、上表を新しい回帰baseline
とし、異なる旧cold計測と直接比較しない。

offscreen結果は構造回帰の証拠であり、最終判断は同一実ZIPを用いた実機体感とする。

### 7.1 Final fresh-process A/B after compatible adoption

2026-07-31の最終runはA/B別fresh process、21 entry、4096 x 6500高詳細JPEG、
3840 x 2106 Viewer、682,441,432-byte ZIPで実行した。A/BのZIP SHA-256は
`7bf798a4eee4a895c56d6e2a3415aa1a6ab47e4228d41d0118d24a39124e35f1`
で一致する。

| Scenario | 旧A request→paint | 新B request→paint | B差 |
| --- | ---: | ---: | ---: |
| 完全cold初回 | 270.349 ms | 266.759 ms | -1.33% |
| ready順送り | 6.216 ms | 7.629 ms | +1.413 ms |
| ready逆方向 | 5.107 ms | 6.939 ms | +1.832 ms |
| ready往復 median | 5.394 ms | 6.312 ms | +0.918 ms |
| 順送りcold | 278.333 ms | 255.481 ms | -8.21% |
| 反転直後cold | 275.505 ms | 268.689 ms | -2.47% |
| 往復cold median / max | 277.500 / 279.248 ms | 262.848 / 278.292 ms | median -5.28% |
| rapid-final cold | 287.891 ms | 270.425 ms | -6.07% |

1 cold pageのcurrent-critical処理は、旧Aが1 entry / 1 `BytesIO` /
3 whole-payload API handoff / 1 QImage / 1 QPixmap / 2 task /
2 GUI callback / 1 paint、新Bが1 entry / 1 `QByteArray` /
0追加whole-payload handoff / 1 QImage / 1 QPixmap / 1 task /
1 GUI callback / 1 paintだった。順送りdecode終了→paintは8.462→5.823 ms。
新Bはpaint後に近傍を読むため、順送りの総計には別phaseの1 entry、
rapid-finalにはpage 19 / 17の2 entryが加わる。通過page 11–17の
最終commit前decodeは0である。

memory-pressureでは旧Aがbaseline 349.07 MiB、sampled max 544.70 MiB、
delta 195.63 MiB、logical cache 121.46 MiB / 7 page、新Bが
175.90 MiB、240.92 MiB、65.02 MiB、30.36 MiB / 3 pageだった。
process peakにはZIP/JPEG生成やQt allocator high-waterも含まれるため、
viewer baselineからのsampled deltaをA/B判断に使った。

単発ready hitの新Bはworker/QImage/QPixmap生成0、atomic commit 1、paint 1で
ある。ready往復では直前paint後prefetchのcancel完了callbackが3回だけcurrent
intervalへ重なったが、ZIP read、materialization、QImage/QPixmap生成は0だった。
0.918–1.832 msの差はdirect-frame request/serial検証、paint acknowledgement、
prefetch cancel bookkeepingの固定費で、coldの支配点ではない。offscreen
timingだけで実機体感を断定しない。

同じ行列でdecoder-sized JPEGを意図的に無効化した`--full-decode`診断も
完走した。これは標準JPEG表示経路ではなく、full rasterを必要とする形式・条件の
残存costを見る比較である。forced coldは順送り207.049 ms、反転208.696 ms、
往復中央値209.128 ms、高速入力後221.845 msだった。通過page抑止、旧方向cancel、
暗転回避はtarget-decodeと同じく合格した。128 MiB圧迫時は、完了した遠方unitの
一時source保護が残る不具合を修正した後、source 76.17 MiB + render 40.49 MiB =
116.66 MiB、decoded source 1件となった。一方、複数のfresh windowと生成ZIPを
同一processで反復する診断全体のworking-set deltaは1,712.74 MiB、peakは
5,084.01 MiBであり、full-raster pathの大きな残存riskを示す。

## 8. Historical offscreen measurements before the paced dispatcher

The measurements below were recorded during earlier prepared-cache and
decoder-sized-JPEG work. They predate at least part of the current shared
priority domain, one-display-unit paced raster dispatcher, and combined cache
budget. They are retained as historical baselines and must not be reported as
current after-change results. Section 7 contains the reproducible current run.

These measurements used generated in-memory data and offscreen Qt. They are
regression evidence, not a substitute for testing the packaged application on
the user's 4K display and storage device.

### Decoder-sized large-JPEG ZIP comparison

The earlier run used the production `ViewerWindow` through a predecessor of
`scripts/benchmark_viewer_navigation.py`: a temporary ZIP with 21 entries,
Japanese entry/path names, 4096 x 6500 JPEG pages, a 3840 x 2106 Viewer,
single-page mode, standard fit-window rendering, a 6-forward/4-backward plan,
and a 512 MiB setting then applied to the decoded-source cache. It did not
launch the real application or generate native input. The current script and
cache semantics have since changed.

Direct decode comparison for the same 4096 x 6500 JPEG payload:

- Pillow full decode: 48.726 ms median.
- Qt full decode: 76.699 ms median.
- Qt JPEG decoder-scaled decode to 1361 x 2160: 26.101 ms median.

Production-window results with decoder-sized JPEG enabled:

- forward: 0.329 ms median handler, 5.294 ms median request-to-paint;
- reverse: 0.282 ms median handler, 5.510 ms median request-to-paint;
- repeated forward/backward: 0.292 ms median handler, 6.055 ms median
  request-to-paint;
- eight uninterrupted requests crossing the prepared frontier: 1.466 ms total
  handler time and 70.297 ms from first request to final-page paint;
- each decoded preview source is about 10.12 MiB; ten were resident
  (101.21 MiB) at the final instantaneous cache snapshot.

The same run with decoder-sized JPEG disabled:

- forward sequence: 0.347 ms median handler, 59.983 ms median
  request-to-paint, 169.851 ms maximum;
- reverse sequence: 0.311 ms median handler, 5.467 ms median
  request-to-paint, 210.486 ms maximum;
- repeated forward/backward: 0.385 ms median handler, 21.128 ms median
  request-to-paint;
- eight uninterrupted frontier requests: 1.296 ms total handler time and
  230.075 ms to final-page paint;
- final decoded cache: six full sources totaling 457.03 MiB.

The 512 MiB full-decode run can retain only about six 4096 x 6500 RGB sources,
so it cannot keep the configured 11-page working set resident. This is itself
the observed working-set difference; the sequence timings must not be read as
an all-ready cache microbenchmark. Decoder-sized mode can retain the complete
planned window and avoids that raw-raster pressure. Before decode-demand
coalescing, the decoder-sized rapid-frontier result was 148.747 ms; starting
only the latest idle target reduced it to 70.297 ms.

- Source raster: 4096 x 6500 RGB888 (76.17 MiB)
- 4K-fit target: 1361 x 2160 (8.41 MiB output)
- Previous unconditional RGBA boundary:
  - 101.56 MiB intermediate raster
  - 319.404 ms high-quality render in the same comparison run
- Format-preserving RGB boundary:
  - 76.17 MiB intermediate raster
  - 202.855 ms high-quality render in the same comparison run
- Exact-target fast path: 0.039 ms, no Pillow conversion
- Current cold renders:
  - moire reduction: 136.059 ms
  - high quality: 205.385 ms
  - smooth: 123.854 ms
  - pixel: 64.886 ms
- GUI `QPixmap.fromImage` for the full source: 33.825 ms
- Repeated smooth 4K paint: 5.832 ms median in that run
- Preparing 11 configured high-quality display units:
  - 11 worker requests and 11 Pillow conversions
  - 1920.280 ms total
  - 123.36 MiB retained display-ready pixmaps
- Applying 17 already-ready forward/reverse targets:
  - 0.035 ms median, 0.055 ms p95
  - zero new workers and zero new Pillow conversions
  - one atomic commit per navigation
- A cold non-standard miss: 173.143 ms to atomic commit, one worker and one
  Pillow conversion
- Process working-set peak in the render benchmark: 426.95 MiB. The 20 logical
  pages shared one injected source raster, so this is intentionally not an
  estimate of a real 20-page decoded-source cache.

### Standard ready/cold comparison

An additional temporary benchmark created a ZIP containing 21 actual
4096 x 6500 JPEG entries (832,263 bytes each; 17,479,603-byte ZIP). Temporary
scripts, archives, and timing output were removed after the run.

- JPEG `Image.load()` decode, 20 samples:
  - 69.03 ms median
  - 74.44 ms p95
- Before the fix, with every source already decoded:
  - wheel handler: 15.89 ms median, 16.63 ms maximum
  - request to paint: 21.71 ms median, 23.11 ms maximum
  - full-source `QImage` to `QPixmap`: 21 conversions for 21 requests,
    approximately 15.78 ms each
- After the fix, with the configured 11-page working set ready:
  - `standard` wheel handler: 0.055 ms median, 0.218 ms maximum
  - `standard` request to paint: 0.961 ms median, 1.364 ms maximum
  - 32 prepared hits, zero misses, zero navigation-time workers, zero
    navigation-time `QImage` to `QPixmap` conversions
  - ten uninterrupted wheel handlers: 0.837 ms total, 3.214 ms to the
    coalesced final paint, ten state commits and one paint
  - `high_quality` wheel handler: 0.058 ms median, 0.192 ms maximum
  - `high_quality` request to paint: 0.910 ms median, 1.153 ms maximum
- Cold `standard` display-size miss with the source raster already decoded:
  - wheel handler: 0.140 ms median, 0.177 ms maximum
  - request to paint: 19.66 ms median, 74.08 ms maximum
  - zero navigation-time `QPixmap` conversions; one display worker was active
    while the previous frame remained visible
- Working set in the final ready-standard run:
  - 125.04 MiB after source conversion
  - 283.11 MiB with 11 display-ready pixmaps
  - each 1361 x 2160 32-bit pixmap accounts for about 11.21 MiB; the 11
    retained artifacts account for about 123.36 MiB before Qt allocator and
    widget overhead
- Wide-page split preparation at 4096 x 6500:
  - two GUI-thread half-image deep copies: 11.492 ms median
  - two shared `QImage` handles with worker-side crop: 0.0028 ms median
  - both split identities retain the source cache key; crop range remains part
    of the render key

A final rerun after the cache-eviction, priority, DPR, and lifecycle boundary
fixes used the same 21-page, 4096 x 6500 generated JPEG ZIP and an 11-page
prepared working set:

- `standard`:
  - wheel handler: 0.258 ms median / 0.569 ms maximum
  - request to paint: 5.162 ms median / 8.428 ms maximum
  - 32 ready hits, zero misses, zero navigation-time workers, and zero
    navigation-time QImage-to-QPixmap conversions
  - ten uninterrupted handlers: 0.953 ms total; 2.206 ms to the coalesced
    paint
- `high_quality`:
  - wheel handler: 0.170 ms median / 0.721 ms maximum
  - request to paint: 1.848 ms median / 7.124 ms maximum
  - 32 ready hits and zero navigation-time workers/conversions
  - ten uninterrupted handlers: 1.708 ms total; 8.146 ms to the coalesced
    paint
- process working set was approximately 125 MiB after one decoded source and
  284 MiB after the 11 display-ready artifacts.

The navigation portion intentionally reused one decoded `QImage` for all
logical page identities so it could isolate display-artifact costs. Its working
set is not an estimate for retaining 21 independently decoded source rasters.

These results isolate display preparation from archive decode. A true cold ZIP
request also includes the measured JPEG decode time and storage/archive
latency, but neither runs in the wheel handler or paint event.

### Earlier production-window navigation

This earlier offscreen measurement used the production `ViewerWindow`,
`ViewerWidget`, and `ViewerRenderTask` path at a 3840 x 2106 viewport with 21
raw-ready 4096 x 6500 RGB888 pages. Decode was intentionally excluded here so
input-handler, display preparation, and paint could be counted independently.

- One cold `standard` page:
  - 1.908 ms input handler in the latest confirmation
  - 56.893 ms request to paint, including the 16 ms input-idle boundary
  - one demand resize, one target-size `QPixmap`, one commit, and one paint
- Ten ready pages forward:
  - 1.380 ms median / 1.948 ms maximum input handler
  - 7.463 ms median / 8.868 ms maximum request to paint
  - zero demand workers, resizes, and `QImage` to `QPixmap` conversions
- Four ready pages in the reverse direction:
  - 1.287 ms median / 2.238 ms maximum input handler
  - 5.675 ms median request to paint
  - zero demand workers and conversions
- Six already-viewed forward/backward operations:
  - 1.414 ms median / 3.060 ms maximum input handler
  - 9.210 ms median request to paint
  - zero workers and conversions
- Six uninterrupted requests crossing the ready frontier:
  - 0.391 ms median / 0.923 ms maximum input handler in the latest
    confirmation
  - 2.753 ms total handler time
  - one demand worker and one target-size `QPixmap`
  - intermediate cold pages 10 and 11 produced no worker, commit, or paint;
    only the latest page 12 was materialized
  - 55.354 ms from the first request to the final paint

The rapid test was repeated with 21 distinct physical QImage buffers
(approximately 1599.61 MiB of raw raster data): handler median was 0.554 ms,
maximum was 1.161 ms, total was 3.888 ms, and only the latest cold target
created a worker and pixmap. Rolling prefetch began only after that target had
committed and painted. Shutdown left zero active render threads, tasks, pending
demands, or active demand/prepared timers.

## 9. Additional whole-application improvements

- Browser thumbnail memory hits now pass separate implicitly shared `QImage`
  handles through the provider and model. Qt copy-on-write still isolates
  later mutation, while removing two GUI-thread deep pixel copies per hit.
  A 1024 x 1024 RGBA comparison measured 0.5210 ms per deep copy versus
  0.0045 ms per shared-handle construction.
- Direct, non-recursive folder books use `os.scandir()` metadata and filter
  extensions before asking whether an entry is a file. Recursive listing,
  symbolic-link behavior, natural sorting, and disappearing-entry handling
  keep their previous contracts.
- ZIP entry reads remain seekable for Pillow but now write directly into one
  size-preallocated `BytesIO`, checking request-local cancellation between
  1 MiB reads. For a 48 MiB incompressible payload, six-run medians were
  31.611 ms / 50.002 MiB peak Python allocation for stored ZIP and
  66.984 ms / 52.033 MiB for deflate. The previous chunk-list plus final join
  measured 32.635 ms / 96.008 MiB and 62.458 ms / 96.008 MiB respectively.
  `tracemalloc` does not include native zlib or OS allocations.
- In a deterministic fake-reader cancellation comparison, the new ZIP path
  reported `process_cancelled` in 4.235 ms median (4.327 ms p95), versus
  144.261 ms completion wait for an equivalent loop without cancellation
  observation points.

## 10. Confirmed follow-up work

The following issues were measured or statically confirmed but deliberately
left outside this page-navigation change:

- Non-`standard` manual zoom still asks Pillow for the full zoomed output.
  A 4096 x 6500 page at 8x is about 6.35 GiB at 4 bytes per pixel before DPR;
  DPR 2 can make the requested area about 25.4 GiB. A safe solution needs
  viewport cropping/tiling or a documented quality-preserving cap.
- The combined cache budget now counts source `QImage` and prepared `QPixmap`
  under one configured total. Current/visible spread protection is intentionally
  stronger than the byte bound, so one unusually large current display unit can
  exceed the setting.
- NivisViewer still changes logical page state before a cold visual unit is
  ready. The old complete frame prevents darkness, but slider/status and image
  can temporarily refer to different pages. Matching ZipPlaFork exactly would
  require pending navigation state and an atomic model+visual commit.
- Eligible ZIP/JPEG新Bはcomplete entryを一つのreserved `QByteArray`へ
  materializeする。Python sequential `QIODevice`直結はfull payloadを
  避けられるが、約1,985 callback/pageにより高詳細decodeが遅くなったため
  production採用しなかった。将来native extension、Qt private archive device、
  または別native decoderを検討する場合はライセンスと実測が必要である。
- Eligible新Bはliteral 1 runnableになったが、Qt規則上QPixmap生成とatomic
  commitには1 queued GUI callbackが必要である。順送りのdecode終了→paintは
  5.823 msで、ここにはQPixmap、commit、offscreen paintが含まれる。
- ready hitは旧Aより0.918–1.832 ms遅い。worker/QImage/QPixmap生成は0である。
  単発hitではqueued worker callbackも0だが、ready往復では直前paint後prefetchの
  cancel完了callbackが3回だけ重なった。差はdirect-frame request/serial
  validation、paint acknowledgement、prefetch cancel bookkeepingの固定費で
  ある。cold改善と引き換えに許容したが、実機入力で悪化が見える場合はhit専用の
  synchronous commitとready往復時のprefetch抑制をさらに短縮する候補が残る。
- If a source-sized standard render is still running when zoom changes again,
  the stale generation is rejected correctly but the latest generation may
  repeat that one preparation. Completed source-sized artifacts are reused.
- When the page-list dock is visible, its legacy thumbnail path can still
  perform a large-image Smooth scale on the GUI thread. It is independent of
  the normal hidden page-list navigation path and should be moved to a worker
  in a separate change.
- 新Bのlogical artifactは30.36 MiB / 3 pageだが、fresh-process
  working-set deltaは65.02 MiBだった。task-local compressed QByteArray、
  QImage、Qt allocations、decoder bufferは常設artifact accounting外なので、
  設定値はhard process-memory limitではない。

## 11. Viewer-wide subsystem comparison and replacement decision

This section supersedes the earlier assumption that the narrow
`ZipPlaCompatibleRasterPath` could become the final Viewer architecture by
being extended one option at a time.  The comparison was performed before the
next implementation step and covers the complete path from book ownership to
paint and shutdown.

The ZipPlaFork reference remains commit
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` under AGPL-3.0-or-later.  The main
reference points are `PackedImageLoader.cs` (`PackedImageLoader` construction,
entry table, `OpenImageStream`, `Dispose`), `GenerarClasses.cs`
(`BackgroundMultiWorker.SetWorksOrder`, completion and interrupt handling), and
`ViewerForm.cs` (`bmwLoadEachPage_DoWork`, `priorityLevel`,
`SetNewResizedImage`, `ReduceUsingMemory`, `showCurrentPage`, and
`pbView_Paint`).  The attribution and license notice in sections 1 and 5 and
`THIRD_PARTY_NOTICES.md` apply to the structural port selected below.

### 11.1 Ten-subsystem comparison

| Subsystem | ZipPlaFork design | Current NivisViewer design | NivisViewer problem | Benefit of the ZipPlaFork structure | Port difficulty | Effect on NivisViewer-specific features | Decision |
|---|---|---|---|---|---|---|---|
| 1. Archive management | `PackedImageLoader` opens the book once, retains one archive/entry table, opens only the requested entry stream, and disposes the archive after the viewer worker has stopped. | `BookSession` owns an `ImageSource`; `ZipImageSource` already retains one `ZipFile`, cached entry names, request-local cancellation, deferred close, and retired-source lifetime.  It also exposes legacy `BytesIO`, compatible `QByteArray`, and experimental sequential-device decode entry points. | The persistent ZIP lifetime is sound, but ownership and cancellation knowledge are split between `BookSession`, `ImageCache`, `ZipPlaCompatibleRasterPath`, and the source.  Three decode-facing APIs permit three page lifecycles. | One book owner and one entry-open contract make close, cancellation, and reuse explicit and prevent two viewer consumers from decoding the same entry. | Medium-high | Folder, external RAR/7z, PDF, asynchronous open, Windows sharing, and Japanese names require adapters. | **Structural port.** Retain the persistent-handle/deferred-close behavior, but put it behind the new book runtime/page-source contract. |
| 2. Page state | `currentPage` advances only through navigation rules that consult the ready/known-size frontier; the accepted current page, trackbar, and shown image are updated by one Viewer form. | Logical current is `PageModel`; requested state is `ViewerWindow._active_request_id` plus `ViewerDisplayUnit`; displayed state is `ViewerWidget._spread/_images`; the compatible path has another current request/frame serial. | Logical page and related UI can advance while the old visual frame remains.  Legacy and compatible paths commit slider/status at different times, and rapid input has no single authoritative requested/displayed state. | A single presentation transaction prevents logical/visual disagreement and gives reversal and stale-result rejection one owner. | High | Spread, lazy wide-page discovery, history, bookmarks, slideshow, slider and reading progress must keep their semantics. | **Full replacement** by an explicit requested/displayed presentation state after the page runtime cutover. |
| 3. Image-load pipeline | One `bmwLoadEachPage_DoWork` page job performs entry open, decode, EXIF/filter work, target resize, and creation of the display artifact before one completion is published. | Legacy path is `_ImageLoadTask` to source `QImage`, then a second `ViewerRenderTask` to resize/rotate/filter, then prepared/pixmap publication.  The compatible path is a second end-to-end pipeline limited to ZIP/JPEG/single/standard/fit/no rotation/no adjustments/no page list/no magnifier. | Feature options switch the whole pipeline.  Source and display work have separate callbacks, schedulers, caches, failure rules, and generations; the narrow fast path is a bolt-on rather than the Viewer. | A page/display-slot job has one critical path and one result.  Decoder fallback stays inside the job instead of returning to another Viewer architecture. | High | All raster formats, spread slots, split/crop, rotation, resampling, image adjustments, manual zoom, DPI and magnifier must be expressible in a render specification. | **Full replacement** for ZIP books by one runtime-owned entry-to-display-ready job. |
| 4. Asynchronous processing | `BackgroundMultiWorker` uses one Viewer worker. `SetWorksOrder` replaces the page permutation; after a completion the worker chooses the first unstarted item in the latest order.  Book replacement uses interrupt/wait cancellation. | The production coordinator normally supplies one Viewer lane, but `ViewerWindow`, `ImageCache`, `ViewerWidget`, and the compatible path each own queue or pending-job state.  Without the coordinator, decode and scale can use separate one-thread pools. | One physical lane does not make one scheduler: obsolete running render work cannot be preempted, generations are duplicated, and shutdown must drain several owners. | One active page job and a replaceable work order bound obsolete work to the currently running decoder and make current/next/previous ordering observable. | High | Browser must retain a separate lane; PDF priorities and injected test pools need adapters. | **Structural port / Viewer scheduler full replacement.** Keep Nivis generation validation and Qt thread-affinity rules. |
| 5. Prefetch | `priorityLevel` orders the current fixed numerical `M`-page band first, numeric next band second, previous band third, then remaining pages; a changed current recomputes the unstarted order. Memory admission can stop/rework the frontier. | Window raster plan, `ImageCache` wanted/protected/ranks, Widget prepared-unit requests, compatible-path neighbors, and multiple idle/paint timers all plan related work. | Four planners must agree. Direction reversal can leave a running legacy decode or render, and cache admission/prefetch release are owned by different objects. | A replaceable work order provides one actual current unit -> forward neighbor -> reverse neighbor policy and stops remote work when the page-artifact budget is full. | High | Spread partner comes from actual topology; configurable direction and PDF rolling behavior remain policy inputs. | **Full replacement** inside the new runtime. |
| 6. Cache and memory | Page-indexed source information and `VirtualBitmapEx` display artifacts share a lifecycle and one memory reduction order. `ReduceUsingMemory` evicts farthest pages using the current work order. | `ImageCache` stores source `QImage`; `ViewerWidget` stores render `QPixmap`, last-rendered aliases, and prepared-unit state; the compatible path stores a separate three-pixmap ring. A Window loop tries to rebalance independent budgets. | The same page can have several unrelated lifetimes and ledgers. Entering/leaving the compatible mode discards one cache and warms the other. The compatible three-page cap is not a byte budget. | A page record with source/display components, one byte ledger, and current/spread/next/previous protection makes eviction and reversal reuse coherent. | High | Magnifier may require a full source artifact; one unusually large visible spread may intentionally exceed the budget. | **Full replacement** by a runtime-owned page artifact cache; preserve explicit visible-unit protection. |
| 7. Display update | `SetNewResizedImage` publishes a completed result and `showCurrentPage` paints it. During a cold load ZipPlaFork grays the preceding canvas rather than clearing it. | Legacy `ViewerWidget` waits for all spread slots to become terminal and atomically swaps them; direct mode also commits one completed pixmap while the old frame remains. Paint acknowledgement releases prefetch/browser work. | Atomic old-frame retention is good, but direct-versus-legacy guards permeate the Widget, while slider/status/page-list selection are committed separately. | One `commit_frame` boundary can keep NivisViewer's superior old-frame retention while removing path-specific Widget modes and synchronizing UI state. | Medium | Pan, zoom, magnifier interaction, gestures, fullscreen chrome and error placeholders remain surface behavior. | **Structural port**, not a literal visual copy. Retain Nivis atomic old-frame behavior and replace the two publication modes with one frame contract. |
| 8. Page list and thumbnails | The Viewer launches catalog viewing separately rather than sharing its in-window loader/cache. `CatalogForm` owns a distinct loader and a globally one-at-a-time thumbnail task; `ThumbViewer.PaintPart` requests only visible rows plus a small margin and clears images outside it. | The Viewer eagerly builds all `QListWidgetItem` rows. It consumes Viewer `ImageCache` results, linearly finds the row, then smooth-scales a large `QImage` and creates `QIcon` on the GUI thread. Merely showing the dock disables the compatible path. Browser thumbnails have a better dedicated lane/cache. | Thumbnail work competes with or changes the Viewer pipeline and can block the GUI. There is no visible-row scheduler for the Viewer page list. | A separate visible-range thumbnail session cannot invalidate or select the main Viewer runtime and can be paused while a cold current page is outstanding. | Medium | Filtering, current selection, bookmark decoration, thumbnail size/DPI, and click navigation remain. | **Full replacement** of the Viewer page-list thumbnail subsystem; retain Browser's independent visible-range/disk-cache design. |
| 9. Display features | Single/spread, binding direction, divided pages, filters, magnifier, rotation and fullscreen are integrated around the same loader/work-order/page arrays rather than choosing a different fast pipeline. | NivisViewer has richer explicit `PageModel`, layout, fit/manual zoom, split-range, resampling, EXIF, magnifier and DPI handling, but almost every feature makes the compatible pipeline ineligible. | Feature support and performance architecture are coupled: turning on a feature destroys the direct cache and returns to the legacy two-stage path. | Treating the feature set as request/render specifications keeps one execution structure while preserving Nivis behavior. | High | This is almost the complete visible feature surface and needs contract-level regression coverage. | **Keep NivisViewer feature policy; structurally replace its execution path.** |
| 10. Errors and shutdown | Worker exceptions become terminal error images/results; book replacement interrupts and waits for the current worker, clears page arrays, and disposes the old loader. | Legacy errors become terminal `CachedImage` records and can atomically join a spread; compatible failures maintain a Window failed-key set and retry the legacy pipeline. BookSession retired sources, direct-path wait, Widget render wait, cache wait, and coordinator shutdown are separate. | Failure can silently change architecture, and source retirement cannot ask one owner whether all consumers have stopped. Shutdown is special-cased for every pipeline. | One result type (`ready`, `error`, `cancelled`, `stale`) and one runtime shutdown make error behavior and archive lifetime deterministic. | Medium-high | Broken/unsupported entries, book switching, old-frame retention, asynchronous open and application shutdown must remain robust. | **Structural port.** Retain deferred close, terminal error frames and generation rejection; remove failure-triggered Viewer fallback. |

### 11.2 Main NivisViewer design problems

The dominant issue is ownership fragmentation, not a missing decoder tweak:

1. A normal raster page can be represented simultaneously by an
   `ImageCache` source record, a `ViewerWidget` render/prepared record, and a
   compatible-path artifact.  Each has a separate eviction and generation.
2. `ViewerWindow` is the effective scheduler but does not own a page job.  It
   coordinates several timers and queues implemented by other objects.
3. A UI feature is currently allowed to choose between two Viewer engines.
   Page-list visibility, spread, rotation, magnifier, adjustment, resampling,
   or fit mode can discard the compatible cache and restart the legacy path.
4. Logical, requested, completed, displayed, and painted page identities do
   not belong to one state machine.
5. Page-list thumbnail creation uses Viewer decode results and GUI-thread
   large-image scaling instead of a separate visible-range pipeline.

The current diff confirms that the compatible path was added beside the old
architecture: the substantial additions to `viewer_window.py`,
`viewer_widget.py`, `image_source.py`, and `image_cache.py`, plus the new
`zippla_compatible_raster_path.py`, did not remove the legacy raster loader,
render scheduler, prepared cache, or their prefetch planners.  Extending its
eligibility one condition at a time would preserve this root problem.

### 11.3 Structures to retain

The replacement must preserve these NivisViewer strengths rather than copying
ZipPlaFork literally:

- Qt GUI-thread-only `QPixmap` creation and request/source generation checks;
- `PageModel` spread, LTR/RTL, cover, wide-page, and split policy, initially as
  a pure topology/layout input;
- the completed-old-frame retention and all-slots-terminal atomic frame swap;
- fit/manual zoom, resampling, rotation, EXIF, image adjustments, magnifier,
  PDF render specification, and device-pixel-ratio behavior;
- asynchronous book open, retired-source close, Windows path/share behavior,
  external archive adapters, and Japanese filenames;
- Browser's dedicated thumbnail provider, memory/disk cache, and visible-row
  scheduling, while making Viewer cold work able to suppress active Browser
  work at a clear boundary.

### 11.4 Replacement roadmap

The work is grouped into four subsystem-sized changes, in performance-impact
order.  These are replacement boundaries, not a list of new helpers to attach
to the existing paths.

1. **`ZipRasterBookRuntime` full cutover.** One runtime owns a ZIP book's page
   request, one-active-job work order, entry-to-display-ready pipeline,
   current/next/previous page artifact cache, eviction, cancellation and
   shutdown. Every ZIP display request uses it; page-list visibility, spread,
   rotation, filter, fit or magnifier must not select the legacy pipeline.
2. **`ViewerPresentationState` full replacement.** Make requested and displayed
   display units explicit and commit the accepted frame, slider, status,
   page-list selection, history/progress policy and painted acknowledgement as
   one transaction.
3. **`ViewerPageListRuntime` full separation.** Replace eager Viewer-cache
   thumbnails with a visible-row worker/cache that never changes the main
   Viewer runtime and never scales large source images on the GUI thread.
4. **`BookPageSource` unification and legacy raster removal.** Split source
   ownership/entry access from decode/render, migrate folder and external
   archives to the raster runtime, adapt PDF to the common presentation
   contract, then delete the legacy `ImageCache`/prepared pipeline.

### 11.5 First structural port selected

The first implementation target is **`ZipRasterBookRuntime`**, not an
archive-only or cache-only change. The existing `ZipImageSource` already gets
the most important persistent archive/index behavior right; replacing only it
would leave the two-stage pipeline and mode-dependent engine switch intact.

The cutover contract is:

```text
one ZIP book
  -> one runtime
  -> latest display-unit request + replaceable work order
  -> at most one active page job
       entry read -> decode -> EXIF/filter -> split/rotate/resize
       -> display-ready QImage result
  -> GUI-thread QPixmap creation
  -> one complete frame commit; preceding frame remains visible until then
  -> paint acknowledgement permits shallow next/previous work
```

The following old ZIP structures become removal targets once the cutover is
complete: standalone `ZipPlaCompatibleRasterPath`,
`ImageCache.suspend_raster_work`, compatible eligibility/timer/failure-key
state in `ViewerWindow`, ZIP participation in `_ImageLoadTask` and
`ViewerRenderTask`, ZIP prepared-display/paced-prefetch state, and
`ViewerWidget` direct-versus-legacy mode guards.  Folder, PDF, and external
archive handling may temporarily remain behind an explicitly named legacy
runtime boundary; there must be no per-feature fallback within a ZIP book.

### 11.6 Implemented first cutover

The first replacement unit is now implemented as a **book-level production
cutover**, not another eligibility path:

| ZipPlaFork source | Ported processing structure | NivisViewer destination |
|---|---|---|
| `PackedImageLoader.cs:283`, constructor; `:1197`, ZIP entry table; `:1781` / `:1856`, entry stream ownership; `:2601`, disposal | One runtime is created for one already-indexed persistent `ZipImageSource` and retired before its book source is closed. | `app/book_session.py`, `BookSession._replace_viewer_runtime`, `_retire_viewer_runtime`, `_release_retired_viewer_runtime`; `app/zip_raster_book_runtime.py`, `ZipRasterBookRuntime` |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | One display-unit runnable performs all logical pages in the unit: entry read, decode, EXIF-aware source creation, adjustments, wide split, rotation, filter/resize, and display-ready `QImage`. | `app/zip_raster_book_runtime.py`, `_ZipRasterUnitJob.run`, `_render_unit`, `_decode_page`, `_split_ranges`; existing `viewer_render.render_qimage` is the Qt/Pillow render adapter used inside that same job. |
| `GenerarClasses.cs:247-339`, `SetWorksOrder` / completion selection; `ViewerForm.cs:5371-5415`, completion; `:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | Fixed numerical current/next/previous bands define the unstarted order. At most one job is active; navigation does not cancel it, its completed bitmap remains cacheable, and the first unfinished page from the newest order starts at the completion boundary without waiting for paint. | `ZipRasterBookRuntime.request`, `_adopt_request`, `_active_job_is_artifact_compatible`, `_drive`, `_submit`, `_on_job_completed`; `ViewerWindow._zip_runtime_request` |
| `ViewerForm.cs:5371`, worker completion; `:5451`, `SetNewResizedImage`; `:6438`, `showCurrentPage` | The sole queued GUI completion creates `QPixmap` objects and publishes only a terminal complete single/spread/split frame. The preceding complete frame remains owned by the Widget until that transaction. | `ZipRasterBookRuntime._on_job_completed`; `ViewerWidget.commit_display_ready_frame`; `ViewerWindow._on_zip_runtime_frame_ready` |
| `ViewerForm.cs:5471`, `ReduceUsingMemory` | Source and display components belong to one cached frame/page lifecycle. One work-order-aware byte ledger evicts the farthest non-current display unit. | `app/zip_raster_book_runtime.py`, `_ZipRasterFrameStore`, `set_retention_order`, `can_admit_prefetch`, `put`, `_prune`, `_retention_rank`; `ZipRasterBookRuntime.set_cache_limits`, `request`, `_drive` |

Production ownership is now:

```text
BookSession
  -> one ZipRasterBookRuntime for one ZipImageSource
ViewerWindow._refresh_view
  -> ZipRasterRequest for every ZIP mode and feature
ZipRasterBookRuntime
  -> one active end-to-end display-unit job
  -> one page-record cache/work order/shutdown owner
ViewerWidget
  -> one complete-frame commit and paint acknowledgement
```

The following old production structures were removed from `ViewerWindow`:

- `_compatible_raster_request` and its ZIP/JPEG/single/fit/rotation/filter/
  page-list/magnifier eligibility matrix;
- compatible failure keys and failure-triggered fallback to the legacy path;
- compatible path ownership, signals, page-list visibility deactivation, and
  rotation/magnifier fallback/replay state;
- per-feature activation/deactivation of two ZIP Viewer engines.

For ZIP books, `ImageCache` is now suspended once at the book-runtime boundary;
`_ImageLoadTask`, legacy raster prefetch, `ViewerRenderTask`, and the prepared
display cache do not participate in page navigation. Folder, PDF, and external
archive sources still use that legacy subsystem until roadmap item 4. The old
standalone `app/zippla_compatible_raster_path.py` is no longer referenced by
production and remains only as historical A/B code pending removal together
with the old benchmark harness.

Nivis-specific behavior retained in the cutover includes complete-old-frame
retention, GUI-thread-only QPixmap creation, request/source generation checks,
single and spread ordering, LTR/RTL, cover/wide policy, wide split, all fit
modes, manual zoom, rotation, EXIF, resampling, image adjustments, magnifier
source handles, DPI-specific render specifications, terminal error frames,
Browser lane gating, and deferred source close.

Known boundaries after this first unit:

- logical `PageModel` navigation still advances before the frame transaction;
  `ViewerPresentationState` is roadmap item 2;
- Viewer page-list thumbnails are not yet sourced from the runtime and their
  legacy GUI-thread scale must be replaced by roadmap item 3;
- the Widget still names the runtime surface `direct display mode`; its old
  prepared-render guards can be removed after folder/external/PDF adapters use
  the same frame contract;
- decoder-scaled JPEG is a strategy inside the one job. Rotation, wide split,
  adjustments, non-standard resampling, and active magnifier deliberately use
  a full source so the runtime does not switch architectures;
- one running Pillow/native decode may finish after reversal, but its request
  cannot publish and no second Viewer job runs concurrently.

### 11.7 Post-cutover ownership corrections and validation

Integration review found and corrected the following subsystem-boundary
defects before validation:

- pending-open tokens are separate from the active book epoch, so a failed or
  cancelled replacement open cannot invalidate the still-displayed ZIP
  runtime;
- a retired `ZipImageSource` remains open until both its runtime worker and
  queued GUI completion have drained; after `ImageCache` itself is drained,
  non-runtime sources such as PDF still close synchronously;
- a prefetch artifact rejected by the byte budget is marked complete/blocked
  for that work order instead of being decoded in a loop;
- same-key current work and still-useful neighbor work adopt the latest
  request instead of being cancelled by every Window refresh;
- failure to start the current worker produces one terminal frame rather than
  leaving the interactive lane waiting forever;
- runtime shutdown owns both worker completion and queued-result drainage;
- the atomic Widget boundary rejects an incomplete two-slot spread.

The production runtime was then measured without showing a window or sending
native input. `scripts/benchmark_zip_runtime_navigation.py` created one
temporary 243,839,098-byte ZIP containing 12 identical-payload, deterministic
4096 x 6500 detailed JPEG entries. Each entry was 20,319,795 bytes. The
viewport was 1200 x 800, the normal artifact budget was 256 MiB, and the
pressure budget was 6 MiB. Times are offscreen evidence only and do not prove
real-device responsiveness.

| Scenario | Final request -> commit / paint | ZIP entry reads | Observable full-payload materialization | Source / display QImage | QPixmap.fromImage | GUI callbacks (frame / artifact / commit) | Paints (content / frame) | Jobs | Sampled working-set peak delta | Retained cache | Transit requested-page decodes | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| cold forward | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.281 MiB | 3 pages / 8,414,880 B | 0 | settled |
| cold reversal | 109 / 125 ms | 3 completed / 60,959,385 B; 1 cancelled read | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 4 | 43.375 MiB | 3 pages / 8,414,880 B | 1 already-running obsolete page | settled; 1 stale result rejected |
| forced-cold roundtrip | 109 / 109 ms | 4 completed / 81,279,180 B; 1 cancelled read | 8 / 162,558,360 B | 4 / 4 | 4 | 2 / 4 / 2 | 2 / 2 | 5 | 44.273 MiB | 3 pages / 8,414,880 B | 2 | settled; 1 stale result rejected |
| rapid final (requests 2..7) | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.516 MiB | 3 pages / 8,414,880 B | 1 (page 6 reused as final neighbor); pages 2..5: 0 | settled |
| ready hit | 0 / 0 ms | 0 / 0 B | 0 / 0 B | 0 / 0 | 0 | 1 / 0 / 1 | 1 / 1 | 0 | 0.012 MiB | 3 pages / 8,414,880 B | 0 | cache hit |
| 6 MiB memory pressure | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.066 MiB | 2 pages / 5,609,920 B | 0 | settled; no resubmit loop |

For the final displayed frames, worker-completion to GUI-ready time was 0 ms
at the probe's millisecond resolution; GUI-ready to explicit offscreen paint
was 0 ms except for one 16 ms event-pump interval during reversal. The
remaining cold critical path is therefore inside entry read plus Qt JPEG
decode, not repeated GUI commits or paints. In particular, each completed
JPEG currently has two observable full compressed-payload materializations:
one `BytesIO` and one `getvalue()` handoff. Decoder/backend work is deliberately
left as a later optimization only after the Viewer-wide structural cutover.

The monolithic 1,429-test process exposes a pre-existing Qt state-order hang
at the second application-controller test when another QApplication/session
test file precedes it (`test_app_icon.py` and `test_book_session.py` both
reproduce the ordering). `test_application_controller.py` passes all 27 tests
in its own process. Running the suite in file-group processes completed all
1,429 collected tests.
The first PDF split run also exposed a close-order regression; the fix above
was applied and the complete 73-test PDF group then passed.

### 11.8 Implemented second cutover: `ViewerPresentationState`

Roadmap item 2 is now implemented as a presentation-state replacement.  It is
not a cache or decoder adjustment.  `PageModel` remains the requested logical
topology, while one new state owner decides when a completed frame becomes the
displayed book/page and when its dependent UI and persistence values may
advance.

The fixed upstream revision and AGPL provenance remain those recorded in
sections 1 and 5.  The relevant structural source is
`source/ZipPla/ViewerForm.cs`: `bmwLoadEachPage_EachRunWorkerCompleted`
(`:5371`), `SetNewResizedImage` (`:5451`), `showCurrentPage` (`:6438`), and
`pbView_Paint` / `pbView_PaintToCanvas` (`:6748-6990`).  ZipPlaFork keeps the
accepted current page, completed resized artifact, trackbar, and Viewer paint
coordination inside one Viewer-form publication sequence.  NivisViewer now
ports that single-owner/publication structure, while deliberately retaining
its stronger behavior of leaving the preceding frame unmodified until an
entire replacement frame is ready.

The token, epoch, layout/DPR validation, immutable Python snapshots, history
transaction, saved-progress policy, Qt signals, and complete-old-frame swap
are independent NivisViewer implementations.  They are not line-for-line C#
translations.  The structural adoption is recorded as
AGPL-3.0-or-later-derived because the accepted-current/completed-artifact/UI
publication organization was taken from ZipPlaFork.

| Former owner or update site | Former meaning/problem | Replacement owner and boundary |
|---|---|---|
| `PageModel.current_index` / `focused_index` and `ViewerPageNavigationController._move` | Input immediately moves the logical/requested page.  Callers could mistake it for the visible page. | They remain request topology only. `ViewerPresentationState.request_frame` snapshots the requested single/spread display unit, direction, book, layout and DPR without changing the displayed snapshot. |
| `ViewerWindow._active_request_id`, `_visible_page_indexes`, `_display_unit` | Window-owned request serial, requested pages and readiness tracker were independent mutable fields. | `ViewerPresentationState.pending_request_serial`, `requested`, `requested_page_indexes`, and `display_tracker`. Read-only compatibility properties remain only for isolated legacy tests/callers. |
| `ViewerWindow._applied_display_request_id` | ZIP meant post-swap, prepared hit meant synchronous post-swap, and legacy cold meant render start before the actual swap. | `ViewerPresentationState.displayed.token.request_serial` means accepted presentation commit on every path. `ViewerWidget.frameCommitted` is the sole pixel-to-semantic commit bridge. |
| `_go_to_index_with_history`, `_go_to_model_move_with_history`, `_on_page_navigation_changed`, `_go_back_in_page_history`, `_go_forward_in_page_history` | History and metadata progress advanced at input time, including pages never shown during rapid input. | Navigation supplies `PresentationNavigation`; back/forward stacks mutate only in `commit_frame`. `BookSession.notify_page_changed` is emitted only by `_apply_presentation_commit`. |
| `_render_spread`, `_on_zip_runtime_frame_ready`, `_apply_pending_display_demand` | Slider, status and page-list selection had path-dependent pre/post-commit timing; legacy marked a request applied at render start. | These paths only feed terminal slots and a frame token to `ViewerWidget`. `_on_viewer_frame_committed` validates and commits; `_apply_presentation_commit` then updates slider, status, page-list, history actions and progress as one GUI-turn transaction. |
| `_update_slider`, `_update_status`, `_sync_page_list_selection` | They read `model.focused_index`, sometimes combining a requested path/page with the old frame's resolution. | They project only `slider_page_index`, `status_values`, failure state and `displayed_page` from `ViewerPresentationState`. A user-moved slider or page-list row is restored to the last committed value while the request loads. |
| `_save_current_reading_position`, `_queue_metadata_progress` | They saved the requested model focus, so rapid transit pages or an unrendered final page could become progress. | They save the committed book key and `progress_values`. Normal navigation uses the displayed commit page; the explicit “open first page but retain saved resume position” policy is carried in the initial request and committed deterministically. |
| `ViewerWidget._spread` / `_images`, `_commit_display`, `commit_display_ready_frame` | The Widget correctly owned pixels but emitted no common frame identity for all paths. | `ViewerFrameCommit` contains the opaque presentation token, Widget serial, complete spread identity, page indexes, image identities and failures. Both direct and prepared paths emit it only after the atomic pixel swap. |
| `ViewerWindow.open_path`, failed-open callback, empty-book path and shutdown | Pending replacement, active displayed book, and late callbacks used several unrelated generation fields. | `begin_replacement_open`, `fail_replacement_open`, `clear_book`, and `close` fence requests while preserving or clearing the committed snapshot according to the explicit book-state contract. |

`ViewerPresentationState` now exclusively owns the following semantic state:

- requested page/display unit and the last committed displayed page/unit;
- pending request serial and state-owned committed-frame serial;
- navigation direction and navigation/history intent;
- requested book identity, committed book epoch, layout signature and DPR;
- per-slot loading/readiness/failure, whole-frame loading and failure state;
- committed slider value, status values, history page and progress page;
- back/forward history stacks and pending replacement-open/closed fences.

The production transition is:

```text
EMPTY or COMMITTED(old frame)
  -- input/request --> REQUESTED(new token, requested moves, old display/UI stays)

REQUESTED
  -- newer input or reversal --> REQUESTED(new token; earlier tokens become stale)
  -- non-frame failure -------> FAILED_PENDING(old display/UI stays)
  -- exact complete frame ----> Widget atomic pixel swap
                                -> PRESENTATION COMMIT
                                -> displayed + slider + status + page-list
                                   + history + progress advance together

BOOK_REPLACEMENT_PENDING
  -- open failure ------------> COMMITTED(old book remains)
  -- source opened -----------> REQUESTED(new epoch, old frame remains)
  -- first exact frame -------> COMMITTED(new active presentation)

CLOSED
  -- any queued callback -----> rejected; no semantic mutation
```

Only a frame whose book epoch/source identity, request serial, complete display
unit, layout signature and DPR all match the current token can commit.  A
newer request makes every intermediate completion stale, including results
from the old navigation direction.  Stale frames do not update pixels,
displayed state, slider, status, history or progress.

For a spread, every logical slot must be terminal before the Widget may swap
or the state may commit. A genuine end-of-book missing partner is represented
by a complete one-slot display unit; an in-flight half-spread is not. A broken
or unsupported page can be a terminal error slot: if the complete error frame
is atomically swapped, that page becomes the displayed page exactly once and
the failure is exposed by the committed status. A failure that does not
produce a frame leaves the old displayed page intact.

Temporary loading/error messages used for book probing remain a separate
status-overlay layer; they do not redefine the committed page. Paint
acknowledgement still releases prefetch and first-frame gates, but it no
longer determines slider/history/progress. Those values are fixed at the
atomic frame commit.

The remaining compatibility surface is intentionally narrow:

- `_active_request_id`, `_applied_display_request_id`,
  `_visible_page_indexes`, and `_display_unit` are projections/adapters over
  the new state for legacy white-box tests and remaining pipeline calls; there
  is no duplicate production storage;
- `ViewerDisplayUnit` remains the per-request slot-readiness tracker owned by
  the presentation request and is not a second displayed-state owner;
- `PageModel` still owns requested topology and layout navigation, never the
  displayed presentation;
- `ViewerWidget` owns pixels and paint mechanics, not page history or reading
  progress.

The next replacement boundary is `ViewerPageListRuntime`.  Page-list selection
is already a read-only projection of the committed presentation.  The next
unit will take ownership only of row materialization, visible-range thumbnail
requests, thumbnail worker/cache lifetime, delayed updates and pause/resume
against current-page work.  A page-list click may submit a navigation request,
but that runtime must never assign displayed page, slider, history or progress
and must never select or invalidate the main Viewer decode pipeline.

Validation used only syntax checks, fake/temp data and offscreen Qt.  All
1,434 collected regression tests passed across controlled file/test-process
groups.  The new state contracts, Window/Widget integration, ZIP runtime/book
lifetime, metadata progress, prepared-render boundary, navigation/page-list/
magnifier/rotation/DPI behavior, PDF, external archive and image-source groups
are included.

A single combined Qt process remains unreliable because GUI state survives
between unrelated tests.  `test_application_controller.py` stops in Browser
fallback-icon painting when its first two tests share a process, while all 27
tests pass in individual processes.  `test_sprint15_models.py`,
`test_sprint18_navigation_followup.py`, and
`test_viewer_resampling_magnifier.py` also require an already-created session
`qapp` or their established companion group.  The full regression therefore
used those explicit isolation/bootstrap boundaries.  It also found three old
`_commit_display` test spies that did not accept the new `frame_token` keyword;
the adapters were corrected and the complete 39-test resampling/magnifier file
then passed.  These are test-harness boundary findings, not hidden production
fallbacks.

The production ZIP offscreen probe was rerun after the presentation cutover
with the same 12-entry, 4096 x 6500 detailed-JPEG fixture used in section 11.7
(20,319,795 bytes per entry; 243,839,098-byte ZIP; 1200 x 800 viewport).  It
does not claim real-device improvement and deliberately does not alter cache
counts or decoder strategy.

| Scenario | final request -> semantic-adjacent Widget commit / paint | ZIP reads / bytes | observable whole-payload copies / bytes | source + display QImage | QPixmap.fromImage | frame/artifact/commit callbacks | content/frame paints | jobs | sampled peak WS delta | retained pages / bytes | transit request decodes | worker-complete -> paint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cold forward | 125 / 125 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.066 MiB | 3 / 8,414,880 | 0 | 0 ms |
| cold reversal | 109 / 109 ms | 3 completed / 60,959,385; 1 cancelled | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 4 | 43.074 MiB | 3 / 8,414,880 | 1 | 0 ms |
| forced-cold roundtrip | 109 / 109 ms | 4 completed / 81,279,180; 1 cancelled | 8 / 162,558,360 | 4 + 4 | 4 | 2 / 4 / 2 | 2 / 2 | 5 | 44.477 MiB | 3 / 8,414,880 | 2 | 0 / 0 ms |
| rapid final (requests 2..7) | 125 / 125 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.293 MiB | 3 / 8,414,880 | 1 (page 6; pages 2..5 zero) | 0 ms |
| ready hit | 0 / 0 ms | 0 / 0 | 0 / 0 | 0 + 0 | 0 | 1 / 0 / 1 | 1 / 1 | 0 | 0.016 MiB | 3 / 8,414,880 | 0 | n/a |
| 6 MiB pressure | 141 / 141 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.344 MiB | 2 / 5,609,920 | 0 | 16 ms |

Every scenario settled before its timeout, produced exactly one final-frame
commit per final displayed request (two for the intentionally two-leg
roundtrip), and left no benchmark process behind.  The cold critical path
remains archive read plus JPEG decode; presentation commit did not add extra
QImage, QPixmap, GUI callback, paint or job work.  Real application launch and
native input remain intentionally untested.

## 12. Viewer-wide follow-up audit and third structural replacement

This audit was not limited to the reported large-ZIP cold miss. It re-read the
production paths for ZIP, folder/single-image books, RAR/7z, PDF, the Viewer
page list, presentation/UI projection, worker coordination, book replacement,
and shutdown before choosing the next unit. The fixed ZipPlaFork reference is
still revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later. The preserved license and copyright notices remain in
`licenses/ZipPlaFork/AGPL.txt`, `licenses/ZipPlaFork/About.txt`, and
`THIRD_PARTY_NOTICES.md`.

### 12.1 Newly confirmed subsystem differences

| Subsystem | ZipPlaFork fixed-revision structure | NivisViewer state at the start of this audit | Performance/stability consequence | Difficulty / Nivis feature impact | Decision |
|---|---|---|---|---|---|
| ZIP work order and completed-frame retention | `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, completion selection `:298-303`) retains a page-wide priority permutation. `source/ZipPla/ViewerForm.cs`, `priorityLevel` (`:5586-5605`) ranks current/next/previous/remaining pages. `ReduceUsingMemory` (`:5471-5524`) uses that order only when memory must be reduced. | `ViewerWindow._zip_runtime_request` made a correct three-unit active frontier, but `ZipRasterBookRuntime._prune_frames` treated those three keys as the complete cache membership and immediately removed every other completed frame before checking the configured unit/byte budget. A nominal 10-page cache therefore retained roughly three single pages. | Every move beyond the immediate neighbor converted a previously completed page back into a cold ZIP read/decode. Short forward/backward/roundtrip operation could not benefit from the configured cache even with ample memory. | Medium. No visible feature contract needs to change; eviction must still protect the current complete single/spread/split frame and obey the byte budget. | **Rank 1; structural replacement implemented in this section.** |
| ZIP archive/index/lifetime | `PackedImageLoader` retains the book/index and opens an entry stream per page, then disposes after the Viewer worker stops. | `BookSession` + `ZipImageSource` already retain one `ZipFile` and entry index, fence callbacks by epoch, and defer source close until runtime work drains. | No repeated ZIP reopen was found on the production runtime. The remaining cold cost is entry materialization/decode, not book lifetime. | High risk to replace; Nivis shutdown behavior is stronger. | **Maintain.** Optimize entry transport only after larger ownership cutovers. |
| Folder and single-image books | The same Viewer page worker and artifact arrays are used rather than a separate feature engine. | Both use `FolderImageSource` but return to legacy `_ImageLoadTask -> ImageCache -> ViewerRenderTask -> prepared QPixmap` with independent source/render caches and timers. JPEG/WebP target decode helpers already exist. | A common folder operation still pays two scheduling stages and keeps the old production architecture alive. A running Pillow decode is only stale-rejected, not source-cancelled. | Medium. Low source-adapter difficulty, but PageList currently depends on `ImageCache.pageLoaded`. | **Rank 3; structural port after PageList separation.** |
| RAR/7z/CBR/CB7 | ZipPlaFork's packed-loader family keeps archive/page ownership inside the Viewer loader. | `SevenZipImageSource` indexes once, but each page invokes a new 7-Zip/WinRAR process, materializes full stdout bytes, then performs a full Pillow decode. `solid` is known but not used for work order or extraction policy. | Process startup, repeated archive traversal (especially solid archives), whole-entry copying, and full-resolution decode can dominate. Merely connecting this source to the ZIP runtime would not remove the main cost. | High. Requires a book-scoped `ExternalArchivePageSource`, cancelable extraction/session policy, and solid-aware strategy while preserving optional external-tool behavior. | **Rank 4; full source/runtime boundary replacement.** |
| PDF | ZipPlaFork integrates document pages into its loader/page arrays. | `PdfImageSource` and `PdfiumService` already keep one document, target-render by viewport/DPR, serialize PDFium calls, deduplicate work, cancel, and close deterministically. The outer `ImageCache` worker waits on the PDF service worker and then converts PDF pixels through PIL before QImage/render preparation. | Document reopen is not a problem. Double asynchronous ownership and pixel conversion remain, but the source-specific target renderer is already mature. | High. Rotation, annotation, DPR, page size, service priority, and shutdown must remain intact. | **Rank 5; sibling `PdfBookRuntime` using the common frame contract, not a forced raster decoder.** |
| Viewer page list / thumbnail | `source/ZipPla/CatalogForm.cs`, `ThumbViewerItem` (`:30231`, `LoadAsync :30280-30302`, `Clear :30304-30308`) and `ThumbViewer.PaintPart` (`:32490-32525`) keep catalog thumbnail work separate, globally one-at-a-time, and limited to the visible region plus a small margin. `preRenderScroll` (`:32562-32673`) reuses the existing canvas while scrolling. | `ViewerWindow._rebuild_page_list` synchronously creates every `QListWidgetItem`; selection and thumbnail updates scan every row. `PageThumbnailProvider.create_icon` performs smooth large-QImage scaling plus `QPixmap.fromImage` on the GUI thread. It consumes legacy Viewer-cache results; ZIP runtime results do not populate it. Once shown, hiding/fullscreen does not reliably release all icon memory or stop legacy icon updates. | Visible page list can delay the first current request at open, delay current-frame handling inside `pageLoaded`, create O(page-count × decoded-count) row lookup, and retain unbudgeted icons. It is also the main dependency preventing removal of `ImageCache` for folder/external sources. | Medium-high. Filtering, committed selection, click navigation, size/DPR, and book epochs must remain; thumbnail work needs its own read-only source/session lifetime. | **Rank 2; full `ViewerPageListRuntime` replacement next.** |
| Source/display artifact reuse after layout change | `ViewerForm.cs` page-indexed `PreFilteredImageArray`, `OriginalImageInfoArray`, `ResizedSizeArray`, and `ResizedImageArray` (`:39-42`) preserve decoded source when only resized output is invalidated (`:2882-2896`, worker-start reuse `:2957-2988`). | `_UnitKey` includes the full render spec and `_CachedFrame` owns source QImage plus display pixmap together. `invalidate_layout` clears both, so resize/DPI/rotation/magnifier/layout changes can re-read and re-decode. | Feature changes may repeat archive work even though page navigation is otherwise ready. | High. Decode-resolution sufficiency, filters, rotation, split and magnifier source ownership need a page record with source/display variants, not a second independent cache. | **Fold into rank 3 common page-artifact runtime.** |
| Presentation/UI update | ZipPlaFork accepts the completed resized artifact and current/UI state in one Viewer-form sequence. | `ViewerPresentationState` now owns requested/displayed serials and atomically projects slider, status, history and progress from `ViewerWidget.frameCommitted`. | No new critical-path duplicate state update was found. Toolbar/menu projection still runs after commit but does not decode or scale images. | Low immediate benefit; changing it risks the completed-old-frame contract. | **Maintain.** |
| Browser/background work and active state | ZipPlaFork thumbnails use one semaphore; its remaining Catalog background-mode controller mainly affects metadata workers. | `ImageWorkCoordinator` reserves one Viewer lane and pauses the Browser lane while a cold current frame is outstanding, resuming after paint. | Nivis has the clearer priority boundary. Copying ZipPlaFork's CPU-count/thread-priority logic would not improve the actual thumbnail decoder path. | Low benefit. | **Maintain Nivis coordinator; use it from the future PageList runtime.** |
| Error, book switch and close | ZipPlaFork cancels/waits, clears arrays, disposes loader/bitmaps. | Terminal error frames, immutable request/book/layout checks, retired runtime/source drainage, failed replacement-open preservation, and asynchronous close are already explicit. | No forced GC or repeated book open was found. Literal synchronous wait/old-frame clear would be a regression. | High risk. | **Maintain Nivis lifetime; extend its ownership list when new runtimes are added.** |

The most important newly found defect was therefore not a decoder choice or a
cache-count preference. It was a **cache ownership error**: the active
scheduling frontier and the completed-artifact retention set were the same
mutable tuple. That contradicted the ZipPlaFork structure already cited as the
porting source.

### 12.2 Ranked replacement roadmap after the audit

| Rank | Large work unit | Real-device frequency/impact | Duplicate work and GUI impact | Old structure removable | Risk | Result |
|---:|---|---|---|---|---|---|
| 1 | **ZIP work-order / retention / eviction owner** | Every normal ZIP navigation; directly affects short reversal and roundtrip outside three pages. | Removes repeated entry read/decode/QImage/QPixmap/callback work for completed pages; no extra GUI stage. | Old `_frames` ownership and desired-set purge. | Medium-low because frame/publication contracts stay unchanged. | **Implemented now.** |
| 2 | **`ViewerPageListRuntime` + virtual row model** | Optional (default hidden), but severe when visible and a prerequisite for deleting legacy cache consumers. | Removes GUI smooth-scale, all-row item construction/search, offscreen thumbnail work, and main-source/cache coupling. | `PageThumbnailProvider`, PageList calls from `ImageCache.pageLoaded`, eager `QListWidgetItem` lifecycle. | Medium-high. | **Next.** |
| 3 | **Source-independent `RasterBookRuntime` / page artifact record for folder and single-image books** | Very common source types; every page still uses the legacy two-stage path. | Collapses decode→resize→publish and source/display lifetime; enables layout-only display invalidation. | Folder/single participation in `ImageCache`, prepared-display scheduler/cache, raster decode/display timers. | High; schedule after PageList no longer consumes the legacy cache. | Planned. |
| 4 | **Book-scoped external archive page source for RAR/7z** | Source-dependent but potentially dominant for solid/large archives. | Targets process-per-entry, repeated archive traversal and whole stdout copies, not merely Viewer callbacks. | `SevenZipImageSource.read_entry -> bytes` page lifecycle and per-page CLI session. | High and backend-specific. | Planned after common runtime contract. |
| 5 | **`PdfBookRuntime` plus source/display variant reuse** | PDF/resize/rotation-specific. | Removes outer worker wait and redundant pixel conversion while keeping PDFium target render. | PDF participation in `ImageCache`/prepared scheduler. | High; current PDF lifetime is already sound. | Planned last among these five. |

PageList was explicitly re-evaluated rather than accepted mechanically. It is
ranked second because it is normally hidden, whereas the selected cache defect
is on every ZIP request. It remains the next replacement because its direct
`ImageCache` dependency would otherwise force duplicate full-resolution decode
or missing thumbnails when folder/single/RAR/7z move to the common runtime.

### 12.3 Implemented replacement: `_ZipRasterFrameStore`

`app/zip_raster_book_runtime.py` now has one explicit owner for completed ZIP
frames. The old `ZipRasterBookRuntime._frames`, `_frame_bytes`, and
`_prune_frames` responsibilities were removed from the runtime and replaced by
`_ZipRasterFrameStore`:

- `ZipRasterRequest.navigation_direction` carries the committed navigation
  policy from `ViewerPresentationState` into the retention plan;
- `set_retention_order` records the current -> direction neighbor -> reverse
  neighbor frontier without declaring every other completed page invalid;
- `_retention_rank` represents the remaining full-book order by page distance
  and direction, avoiding an O(all pages) Python allocation on every wheel
  event;
- `put` retains a completed frame until the combined source/frame hard or soft
  byte policy actually requires reduction; there is no retained-unit cap;
- `_prune` evicts the lowest-ranked non-current frame only under real pressure;
- `can_admit_prefetch` allows a newly important neighbor to replace a farther
  frame, but stops low-priority decode before it would merely evict an
  equal-or-better retained frame;
- the active execution stays one-worker-wide.  The final production policy in
  sections 19--20 releases a four-forward/one-reverse complete-unit startup
  runway at commit and then walks the book lazily under the combined byte
  policy; physical paint is not a scheduling gate.

The adapted processing source is:

| ZipPlaFork source / method | Adopted structure | NivisViewer destination |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:298-303`) | Latest work order selects useful work without invalidating already completed artifacts. | `ZipRasterBookRuntime.request`, `_drive`; `_ZipRasterFrameStore.set_retention_order` |
| `source/ZipPla/ViewerForm.cs`, `priorityLevel` (`:5586-5605`) | Current, forward/reverse neighbors, then remaining pages define one importance order. | `ViewerWindow._zip_runtime_request`; `ZipRasterRequest.navigation_direction`; `_ZipRasterFrameStore._retention_rank` |
| `source/ZipPla/ViewerForm.cs`, `ReduceUsingMemory` (`:5471-5524`) | Completed source/display artifact is disposed from the least important end only when memory must be reduced. | `_ZipRasterFrameStore.put`, `_prune`, `_eviction_candidate`, `_frame_bytes`, `set_limits` |

This is a direct structural port/adaptation and is recorded as
AGPL-3.0-or-later-derived. It is not a line-for-line C# translation. The
distance rank is an independent Python representation of the full priority
order, and GUI-thread-only QPixmap ownership, immutable request/epoch/layout
validation, byte accounting, old-frame retention and pre-decode admission are
NivisViewer-specific extensions.

Production behavior changed from:

```text
new request
  -> active keys = current / next / previous
  -> delete every cached frame outside active keys
  -> enforce unit/byte budget
```

to:

```text
new request
  -> replace active job frontier = current / next / previous
  -> re-rank all completed frames around current + direction
  -> keep completed frames unchanged while within unit/byte budget
  -> if a nearer missing neighbor outranks a retained far frame, decode once
     and evict the far frame
  -> if the current frame already exhausts memory, stop background admission
     before ZIP read/decode
```

No display feature chooses another engine. Single/spread, LTR/RTL, cover/wide
policy, split pages, fit/manual zoom, rotation, EXIF, filters, resampling,
magnifier source, DPR, terminal error frame, atomic old-frame replacement,
failed book replacement and deferred archive close remain unchanged.

### 12.4 Offscreen evidence for the replaced contract

The benchmark uses one temporary offscreen ZIP with 12 identical detailed JPEG
entries, each 1600 x 2400 and 2,930,032 bytes, a 35,161,942-byte archive, a
1000 x 720 viewport, and a normal 192 MiB artifact budget. The new
`retained_roundtrip` walks from page 1 through pages 2, 3, 4 and 5, waits for
each production frame/neighbor order to settle, then returns to page 2 without
clearing the runtime. A is a benchmark-only reproduction of the removed
three-unit membership purge; B is the production store. Both use the same
`ViewerWindow -> ZipRasterBookRuntime`, decoder, render path and payload.

| Metric for final return to page 2 | Old A: three-unit purge | New B: retained frame store |
|---|---:|---:|
| request -> frame commit / paint | 31 / 31 ms | 0 / 0 ms |
| final frame cache hit | no | yes |
| ZIP entry opens / bytes | 3 / 8,790,096 | 0 / 0 |
| observable full-payload materializations / bytes | 6 / 17,580,192 | 0 / 0 |
| source / display QImage outputs | 3 / 3 | 0 / 0 |
| `QPixmap.fromImage` | 3 | 0 |
| GUI callbacks (frame / artifact / presentation commit) | 1 / 3 / 1 | 1 / 0 / 1 |
| paints (content / runtime frame) | 1 / 1 | 1 / 1 |
| worker jobs | 3 | 0 |
| transit-page decode / unpainted decode | 0 / 2 | 0 / 0 |
| retained pages / accounted bytes | 3 / 7,096,896 | 6 / 14,193,792 |
| scenario working-set delta / sampled peak delta | 3.465 / 8.492 MiB | 0.012 / 0.012 MiB |
| settled / cleanup cancellation | yes / no | yes / no |

The extra 7,096,896 accounted bytes in B are intentional reuse within the
configured 192 MiB budget, not unbounded icon/source memory. Under an extreme
1 MiB budget where the current frame itself accounts for 2,365,632 bytes, B
submitted exactly one current job, opened/read one 2,930,032-byte ZIP entry,
made two observable payload materializations (5,860,064 bytes), one source and
one display QImage, one QPixmap, one queued callback and one paint. It retained
only the current page, recorded one `prefetch_admission_stops`, settled, and
did not decode next/previous pages only to evict them.

The deliberately cold scenarios remain structurally unchanged: current is
still decoded first, prefetch starts only after paint, at most one worker runs,
and stale reversal results cannot publish. On the smaller fixture, cold
forward remained 31/31 ms with 3 jobs/3 reads, reversal remained 31/31 ms with
4 jobs and one stale cancellation, forced-cold roundtrip was 16/16 ms before
and 15/15 ms after, rapid-final was 31/31 ms before and 32/32 ms after, and a
ready hit remained 0/0 ms. These offscreen values do not establish real-device
improvement; they show that the replacement removes repeated work on retained
pages without changing the cold current-frame pipeline.

Validation used syntax/import checks and only pytest/offscreen Qt with
fake/mock/temp sources. The complete 1,435-test collection passed across
controlled process groups: 1,408 tests passed in isolated file/known-qapp
groups, and all 27 `test_application_controller.py` cases passed in separate
bounded processes. The directly related runtime, presentation, BookSession,
ViewerWindow/Widget, page-list selection, magnifier/rotation/DPI, PDF,
external-archive, metadata/progress, image-source and navigation groups are
included. A combined application-controller process still exhibits the known
Qt state-order stall and was not used as evidence. No real application,
native input, external GUI application, commit, or push was invoked.

### 12.5 Removed and remaining old structures

| Old structure | Classification after this change |
|---|---|
| `ZipRasterBookRuntime._frames` as a runtime-owned unranked dictionary, `_prune_frames` desired-set purge, and runtime `_frame_bytes` | **Removed/replaced now** by `_ZipRasterFrameStore`. |
| Three active work keys used as the completed-cache membership set | **Removed now.** They are scheduling/protection input only. |
| Prefetch that decodes despite a current-only byte budget, then discovers the result cannot coexist | **Removed now.** Admission stops before the ZIP read. |
| `PageThumbnailProvider` and PageList thumbnail calls from `ImageCache.pageLoaded` | **Remove in rank 2.** Still production for legacy sources only. |
| Folder/single/RAR/7z/PDF `ImageCache` + prepared display pipeline | **Maintain only as explicit other-format fallback until ranks 3-5 migrate.** Never a ZIP feature fallback. |
| `ZipPlaCompatibleRasterPath` and its old A/B | **Production-external historical code.** Remove/move to history after the remaining benchmark evidence no longer needs it. |
| compatibility-only page-state properties | **Remove after remaining legacy runtime callers/tests migrate.** They do not own production state. |

### 12.6 Next replacement boundary and real-device checks

The next subsystem is `ViewerPageListRuntime`, bounded as follows:

- own a lightweight page-index/row model and O(1) reverse map;
- request only the visible viewport plus a small margin;
- run one low-priority thumbnail job, replace the order during scrolling, and
  reject old book/spec generations;
- pause/cancel while the main current frame is cold and resume only after its
  accepted paint;
- perform decode/orientation/thumbnail scale off the GUI thread; perform only
  accepted visible QPixmap/QIcon upload on the GUI thread;
- own a byte budget and release offscreen/hidden artifacts;
- never assign displayed page, slider, history or progress; selection remains
  a projection of `ViewerPresentationState`;
- never share a ZIP entry lock/handle in a way that can block the current
  Viewer job; BookSession must defer the dedicated thumbnail source close
  until its one job and queued completion drain.

Real-device verification for the current replacement should use the same large
ZIP and compare: sequential navigation beyond three pages, immediate reversal,
two-to-eight-page roundtrips, repeated oscillation near the configured cache
edge, rapid wheel then return, single/spread and LTR/RTL, rotation/resize/DPI
invalidation, memory-pressure behavior, book replacement, Viewer close, and
Windows working-set stability. Expected evidence is fewer cold flashes/pauses
and no additional read/decode on a page still within the configured budget;
offscreen results alone are not a claim of improved physical-device feel.

## 13. Overnight whole-Viewer audit and fourth structural replacement

This audit started from NivisViewer commit
`da24bdd47fc706327b3c70cb0fa64bbeb450eee8` (`Separate ZIP frame retention
from the active work frontier`).  `HEAD`, `origin/main`, and `origin/HEAD` were
identical, and the worktree had no modified or untracked files.  That commit is
therefore the protected baseline for this unit.

The ZipPlaFork reference was checked again on 2026-08-01.  Remote `HEAD` and
`refs/heads/master` still both resolve to the fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`; there are no newer upstream
Viewer or Catalog changes to merge into this comparison.

### 13.1 Updated unresolved subsystem comparison

The comparison below was completed before implementing another subsystem.  A
"page move" frequency means the path can affect normal wheel/click/key
navigation; "optional-visible" means it is absent with the page-list dock
hidden but can be severe once the dock is enabled.

| Candidate subsystem | ZipPlaFork fixed-revision design | NivisViewer design at the audit baseline | NivisViewer problem and real-device relevance | Frequency / GUI and duplicate work | Port difficulty, feature effect, and risk | Replacement boundary and expected effect |
|---|---|---|---|---|---|---|
| Page list / thumbnail | `CatalogForm.cs` separates lightweight `ThumbViewerItem` metadata from images. `ThumbViewer.PaintPart` loads only the visible region plus one small margin, clears images outside it, and `ThumbViewerItem.LoadAsync` is globally one-at-a-time. Data-index/show-index arrays provide direct mapping and changed cells are redrawn locally. | `ViewerWindow` owns a `QListWidget`, synchronously creates every `QListWidgetItem`, scans every row for selection and every accepted thumbnail, and calls `PageThumbnailProvider.create_icon` on the GUI thread. The thumbnail source is the legacy full-size `ImageCache`; ZIP runtime artifacts do not populate the list. Hidden/fullscreen state can retain all icons after the list has once been shown. | Opening a large visible list can run O(all pages) widget creation before the current request. A legacy current/prefetch callback can smooth-scale a large image and upload a pixmap before main-frame preparation. Icons are outside the Viewer byte ledger, and the legacy-cache dependency blocks removal of the old folder/RAR/PDF pipeline. | **Optional-visible, every accepted legacy decode and every presentation selection.** GUI work is O(rows) lookup plus large-image scale/upload; row/icon memory and source decode can be duplicated. | Medium-high. Filtering, click/keyboard navigation, committed selection, DPR/size/rotation/filter generations, broken pages, all source types, book switch and close must remain. A thumbnail worker must not share the ZIP entry lock with current work. | **Full replacement:** `QAbstractListModel/QListView` virtual rows plus a book-owned `ViewerPageListRuntime`, visible+margin order, one low-priority worker, dedicated thumbnail source/artifact cache, byte budget and epoch/spec rejection. Expected to remove GUI scaling/all-row searches and current-cache coupling when the dock is visible. |
| Folder / single-image Viewer | The same page-worker/work-order/display-artifact structure is used for packed and ordinary images. | `FolderImageSource` has useful JPEG/WebP target-decode helpers, but production returns to `_ImageLoadTask -> ImageCache -> ViewerRenderTask -> prepared QPixmap`; source and display caches, timers and callbacks are separate. A single-image open is a folder book using this same path. | Every folder page miss crosses two schedulers and two caches. Running Pillow work cannot be source-cancelled, layout changes can repeat preparation, and the path keeps most legacy raster state alive. | **Common, every folder/single page move.** Normally one GUI callback per source and one per render plus cache bookkeeping; decoded and prepared artifacts overlap. | High as a full deletion, medium after PageList no longer consumes `ImageCache`. Spread, wide-page discovery, magnifier, adjustments, rotation and folder snapshot ordering must remain. | **Structural replacement after PageList:** source-independent raster book runtime/page artifact record using the existing presentation frame contract. Expected to remove the two-stage scheduler and enable display-only invalidation. |
| RAR / 7z / CBR / CB7 | Packed-image ownership remains book scoped and entry work participates in the same page order/cache lifecycle. | `SevenZipImageSource` keeps the entry listing but starts an external 7-Zip/WinRAR process for every page, materializes all stdout bytes, then performs full Pillow decode through the legacy pipeline. `solid` is recorded but not used by extraction order. | Process startup, repeated archive traversal and full-entry/full-raster materialization can dominate, especially for solid archives. Merely attaching the current source to a new scheduler would leave the primary cost intact. | **Format-dependent, every external-archive miss.** Low GUI cost itself, but high process/I/O/CPU duplication; reversal cancels by terminating the per-entry request after work may already have started. | High and backend-specific. Must retain optional external tools, Japanese paths, output/size limits, cancellation, solid behavior and safe process shutdown. Persistent-library support is not assumed and no dependency may be added. | **Full source/runtime replacement:** book-scoped `Rar7zBookRuntime`/page source with listing reuse, cancelable extraction policy and solid-aware work order. Expected effect can be large but is not safe as the first overnight cutover. |
| PDF | Document pages share the Viewer page order and page artifact lifecycle. | `PdfImageSource`/`PdfiumService` already keep one document, target-render by viewport/DPR, serialize/prioritize/deduplicate PDFium work and close via a control barrier. The outer `ImageCache` worker waits for the service, converts pixels through PIL, and then enters prepared rendering. | Archive/document lifetime is already sound. Remaining costs are double asynchronous ownership, PIL/pixel conversion, and source/prepared duplication rather than document reopen. | **PDF-only, every miss/rerender.** Callback and pixel conversion duplication; current priority inside the PDF service is already strong. | High. Native page rotation, annotations, zoom/DPR target size, labels, magnifier and shutdown ordering are mature and easy to regress. | **Sibling `PdfBookRuntime` later:** preserve PdfiumService and expose the common complete-frame/cache/paint contract. Expected to remove the outer wait/conversion stages without forcing PDF through a raster decoder abstraction. |
| Viewer UI critical path | Accepted resized image, current page and trackbar are coordinated in the Viewer form; Catalog redraw is local. | `ViewerPresentationState` now commits displayed page, slider, status, history and progress with `ViewerWidget.frameCommitted`. Page-list selection still performs a linear row search. Toolbar/menu projection is separate but does not decode or resize pixels. | The previous logical/visual split is resolved. No new slider/status/history/progress critical-path owner was found. Page-list selection remains the material synchronous outlier. | **Every committed page**, but current semantic projections are small; PageList adds O(rows). | Low benefit/high regression risk for another transaction rewrite. Fullscreen chrome, pan/zoom, slideshow and metadata policy are Nivis-specific. | **Maintain presentation transaction; move only PageList selection to O(1) model mapping.** No separate UI rewrite is justified now. |
| Cache / memory | Page-indexed source and resized arrays share one page lifecycle; `ReduceUsingMemory` evicts from the least-important end. Catalog images outside the visible range are explicitly disposed. | ZIP now has `_ZipRasterFrameStore`; other formats retain `ImageCache` plus Viewer prepared/render cache. PageList `QIcon/QPixmap` memory is unbudgeted. ZIP source/display variants are still invalidated together on layout changes. | The largest fixed ZIP retention defect is resolved. The next unbounded ownership is PageList icons. Legacy formats still duplicate source/display lifetime, and ZIP layout-only changes may repeat decode. | ZIP store: every ZIP request and now bounded. PageList: optional-visible and grows with visited rows. Legacy caches: every non-ZIP page. | PageList budget is medium risk; common artifact records are high risk because magnifier/source-resolution sufficiency must remain. | **First bound PageList independently; then introduce a common page-artifact record with source/display variants.** Do not retune ZIP counts or decoders in this unit. |
| Input / navigation | The worker consumes the latest work order; completed work remains cacheable, and the next order is chosen after completion. | Wheel/click/key navigation coalesces requests; `ViewerPresentationState` owns requested/displayed serials and direction; ZIP runtime has a one-job current/next/previous frontier with stale rejection and retained frames. Legacy sources keep their older decode/display demand timers. | ZIP navigation semantics are no longer the primary structural gap. PageList scroll has no visible-row scheduler because thumbnails are incidental cache callbacks. Non-ZIP reversal can leave one stale decode and a separate stale render. | **Every input.** ZIP duplicate transit work is bounded; PageList and legacy sources are the remaining work-order gaps. | Low risk to preserve current navigation; high risk to unify all source engines at once. | **Maintain navigation/presentation contracts.** PageList gets an independently replaceable visible order; folder/external/PDF migrate one book runtime at a time. |
| Book switch / close | Page workers are interrupted/waited before packed loaders and bitmaps are disposed. | `BookSession` separates pending-open from active epoch, retires ZIP runtime/source until workers and queued GUI completions drain, preserves the old frame on failed replacement, and PDF closes through a service barrier. PageList currently owns no worker/source and icons live until widget/model clearing. | Existing main-source lifetime is stronger than a literal synchronous port. A new PageList source would become an unsafe extra owner unless BookSession retires its runtime and waits for both worker completion and queued-result rejection. | **Every book switch/close.** Low steady-state GUI cost; high consequence if wrong. | Medium for a dedicated runtime, very high for broad lifetime unification. Deadlock or premature archive/document close is a stop condition. | **Extend BookSession ownership only for PageList runtime now.** The runtime owns its forked read-only source, cancels and drains before close; failed replacement leaves the active runtime/model untouched. |

### 13.2 Ranked replacement units

The already-completed ZIP frame-retention change is not ranked again.  Scores
are qualitative (`5` is strongest/highest); risk is inverse (`5` is most
risky).  "Overnight" means the boundary can be completed and reviewed without
leaving a mixed production engine.

| Rank | Large work unit | Real-device effect | Production frequency | Current/UI contention | Old code removable | ZipPla clarity | Risk | Overnight completion | Decision |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | **`ViewerPageListRuntime` + virtual row model** | 4 | 2 when hidden / 5 when visible | 5 | 4 | 5 | 3 | 4 | **Implement as this unit.** It is the next roadmap prerequisite and has a closed ownership boundary. |
| 2 | **Source-independent `FolderRasterBookRuntime` + page artifact record** | 4 | 5 | 3 | 5 | 4 | 4 | 2 | Next after PageList. Start only when the PageList no longer reads `ImageCache`. |
| 3 | **Book-scoped `Rar7zBookRuntime` / external archive page source** | 5 for affected books | 2 | 2 | 4 | 4 | 5 | 1 | High expected upside, but external-process/solid-archive lifetime is too broad for the first cutover. |
| 4 | **`PdfBookRuntime` preserving PdfiumService** | 3 | 2 | 2 | 3 | 3 | 5 | 1 | Defer; current document lifetime and prioritization are already correct. |
| 5 | **Common source/display variant and legacy prepared-pipeline removal** | 4 | 5 | 3 | 5 | 4 | 5 | 1 | End state spanning ranks 2-4, not a single safe overnight patch. |

### 13.3 Selected replacement contract

The selected subsystem is `ViewerPageListRuntime`; this decision follows the
audit rather than treating the previous roadmap ordering as automatic.  The
production boundary is:

```text
BookSession
  -> one ViewerPageListRuntime per installed book
       -> lazy independent thumbnail source/session
       -> latest visible rows + small margin
       -> at most one low-priority worker job
       -> target-sized QImage cache with a byte budget
       -> epoch/spec/visibility rejection and explicit shutdown

ViewerWindow
  -> QAbstractListModel / QListView lightweight rows
  -> viewport rows -> runtime work order
  -> accepted target-sized QImage -> one GUI QPixmap/QIcon upload
  -> click submits navigation only
  -> committed ViewerPresentationState projects selection through O(1) map
```

The runtime must pause/cancel while a current Viewer frame is cold, resume
only after the accepted paint/fallback releases the interactive lane, and use
an independent ZIP handle so a thumbnail cannot own the main
`ZipImageSource` lock.  Folder, external-archive and PDF thumbnail sources are
forked lazily off the GUI thread.  BookSession retirement must keep the main
source and the runtime alive until its worker and queued completion drain.

The immediate removal targets are eager `QListWidgetItem` ownership,
`PageThumbnailProvider`, GUI-thread smooth scaling, PageList calls from
`ImageCache.pageLoaded`, all-row selection/thumbnail scans, and hidden
unbudgeted icons.  `ViewerPresentationState`, the main ZIP runtime/cache,
Viewer atomic old-frame swap, Browser thumbnail subsystem, PDF service and
external archive backend remain independent and unchanged by this unit.

### 13.4 Implemented replacement: `ViewerPageListRuntime`

The selected boundary is now implemented.  It is one subsystem replacement,
not a thumbnail helper attached to the old `QListWidget`:

```text
BookSession (one owner per installed book)
  -> ViewerPageListRuntime
       -> lazy source-specific read-only fork
       -> replaceable visible-row order
       -> one active low-priority job
       -> entry read -> target decode/render -> orientation/filter/rotation
          -> final-size QImage
       -> desired/spec/book-generation validation
       -> visible-only QImage byte cache

ViewerWindow (GUI projection only)
  -> ViewerPageListModel / QListView virtual rows
  -> viewport + two-row margin request
  -> accepted final-size QImage -> QPixmap/QIcon once
  -> page click -> navigation request only
  -> frame commit -> O(1) committed-page selection
```

The production integration is split deliberately:

- `app/viewer_page_list_runtime.py` owns the virtual model, work order,
  one-job scheduler, cancellation, target-sized artifact, cache accounting,
  stale rejection and clone shutdown.  Worker code creates no `QPixmap` or
  `QIcon`.
- `app/book_session.py` creates exactly one runtime for each installed book,
  retires it on switch/close, and retains the main source until both native
  work and its queued completion have drained.
- `app/image_source.py` provides lazy independent folder, ZIP and 7-Zip/RAR
  thumbnail sessions.  The external-archive fork reuses the immutable listing
  snapshot instead of starting a second listing process.
- `app/pdf_image_source.py` opens a separate PDF document session through the
  existing `PdfiumService`; PageList renders directly to the thumbnail target
  and closes that document after callback drainage.
- `app/viewer_window.py` maps only the visible viewport plus two rows on each
  side, performs the accepted `QPixmap.fromImage`, and projects the committed
  presentation page through direct page/row mapping.  It pauses PageList when
  a ZIP, folder/external-archive, or PDF current frame is cold and resumes on
  the accepted paint (or the existing post-completion hidden-window fallback).

Hidden/fullscreen PageList state now has zero rows, zero desired jobs, zero
thumbnail artifacts and no open thumbnail-source fork.  Changing thumbnail
size, DPR, rotation or adjustments changes the immutable spec; late results
from the preceding spec cannot upload.  A rapid scroll replaces the order and
cancels a no-longer-visible active entry request.  Completed icons are kept
only for the current desired rows, while the QImage side has an explicit byte
budget.

Book replacement has a two-phase presentation boundary.  Source installation
stages the new runtime but does not relabel the old frame.  The preceding row
projection remains visible but disabled and no longer holds the retired
QObject.  The first complete replacement `ViewerPresentationState` frame
commit activates the new runtime/model atomically.  A failed replacement never
installs or stages a new runtime, so the active book/list remains usable.

### 13.5 Exact ZipPlaFork provenance and adaptation map

The fixed source is ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, file
`source/ZipPla/CatalogForm.cs`.  The structure originated in upstream commit
`8ea492821efa95ac66483246c5b17fa71b400f0e`; thumbnail concurrency is one at
the fixed revision.  The following control flow is directly structurally
ported and is therefore recorded as AGPL-3.0-or-later-derived:

| Upstream class / method | Adopted behavior | NivisViewer destination |
|---|---|---|
| `ThumbViewerItem.LoadAsync`, `ThumbViewerItem.Clear` | One thumbnail load lifecycle and explicit release outside the useful region. | `_ViewerPageThumbnailJob`; `ViewerPageListRuntime._drive`, `set_visible`, `_retain_cache` |
| `ThumbViewer.PaintPart`, `ThumbViewer.DrawItem` | Materialize/publish only items required by the painted region; update the completed item locally. | `ViewerWindow._update_page_list_visible_work`, `_on_page_list_thumbnail_ready`; `ViewerPageListModel.set_thumbnail` |
| `ThumbViewer.preRenderScroll`, `ThumbViewer.OnMouseWheel` | Re-evaluate the useful range after scrolling rather than filling the book. | `ViewerWindow._schedule_page_list_visible_work`; `ViewerPageListRuntime.request_visible_pages` |
| `ThumbViewer.SilentSet` and data/show-index mappings | Separate lightweight item identity from the displayed row and provide direct mapping. | `ViewerPageListModel.page_index_at`, `row_for_page`, filtered mapping |
| `ThumbViewer.Clear` | Dispose thumbnail ownership when the view/book no longer needs it. | `ViewerPageListRuntime.cancel` / `shutdown`; `ViewerPageListModel.clear` |
| `CatalogForm.bmwMakePreview_RunWorkerStarting` lightweight item setup | Metadata rows do not eagerly own decoded images. | `ViewerPageListModel.rowCount` / `data` |

NivisViewer's Qt model, book/spec generations, byte accounting, source forks,
cooperative cancellation, PDF target render, GUI-only pixmap upload,
presentation-commit staging and callback-drained source lifetime are independent
Python/Qt adaptations.  ZipPlaFork's paint-time task creation, static global
semaphore, 33 ms sleep, GDI drawing/bitmap code and alternate-data-stream cache
were not copied.  License text and upstream notices remain in
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`; the notice
mapping is also recorded in `THIRD_PARTY_NOTICES.md`.

### 13.6 Removed old PageList ownership

The following production structures were removed rather than left as a feature
fallback:

| Old structure | Result |
|---|---|
| `QListWidget` plus one `QListWidgetItem` for every page | Replaced by `QListView` + `ViewerPageListModel`; unfiltered row lookup allocates no page-index tuple/reverse map and creates no per-page item objects. |
| Linear current-page and thumbnail item scans | Replaced by `row_for_page` / `page_index_at` mappings. |
| `PageThumbnailProvider.create_icon` GUI smooth-scale of the full decoded image | Class removed.  Worker publishes a final-size QImage; GUI performs only accepted pixmap/icon upload. |
| PageList mutation from `ImageCache.pageLoaded` | Removed.  PageList has its own source, queue, artifacts and callbacks for every supported book source. |
| `_page_list_dirty`, eager `_rebuild_page_list`, `_update_page_list_thumbnail` | Removed.  Hidden state clears model/runtime ownership; visibility reconstructs only virtual metadata and the current work order. |
| Icons accumulated for every page visited while the list was ever visible | Replaced by visible-window retention and explicit release. |

The Browser thumbnail provider/cache is a separate subsystem and was not
changed.  `ImageCache` and the prepared-display scheduler remain only for the
main non-ZIP Viewer until the ranked folder/external/PDF runtimes replace them;
PageList is no longer a reason to preserve those structures.

### 13.7 Offscreen PageList A/B

`scripts/benchmark_viewer_page_list_runtime.py` uses offscreen Qt and one real
temporary stored JPEG ZIP.  A is a benchmark-only reproduction of the removed
PageList ownership (2,000 eager items, full-size decoded QImage presented to a
GUI smooth-scale/upload callback); it is **not** the superseded Viewer frame
engine.  B calls the production `ViewerPageListModel` and
`ViewerPageListRuntime`.  The physical ZIP contains one deterministic entry
mapped to 2,000 logical ids so both sides read identical 60,629-byte payloads.
The image is 1,600 x 2,400, thumbnail edge 160, visible range six, scroll order
six positions, artificial cancellable entry delay 8 ms, and B QImage budget
204,800 bytes.  No window was shown and no native input or external GUI was
used.

Detailed counters and worker-completion timestamps are disabled in the
BookSession production runtime.  The benchmark and focused runtime tests opt
in through `collect_metrics=True`; no timestamp log or per-job metrics object
churn is left on the normal Viewer path.

| Scenario | A / B elapsed ms | A / B row build ms | A GUI scale ms / B | A work items / B jobs | A / B queued callbacks | A / B successful transit decodes | A / B ZIP reads (bytes) | A / B QImage outputs | A / B QPixmap uploads |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hidden 2,000-page book | 8.511 / 0.295 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| first visible range | 189.678 / 85.298 | 6.826 / 0.127 | 28.858 / 0 | 6 / 6 | 6 / 6 | 0 / 0 | 6 / 6 (363,774 / 363,774) | 6 full / 6 target | 6 / 6 |
| rapid scroll to final range | 1,140.669 / 84.913 | 6.639 / 0.114 | 157.398 / 0 | 36 / 7 | 36 / 7 | 30 / 0 | 36 / 6 (2,182,644 / 363,774) | 36 full / 6 target | 36 / 6 |
| current-frame pause/resume | 188.255 / 115.009 | 6.767 / 0.109 | 26.259 / 0 | 6 / 7 | 6 / 7 | 0 / 0 | 6 / 6 (363,774 / 363,774) | 6 full / 6 target | 6 / 6 |
| six-range memory pressure | 1,157.438 / 550.485 | 6.755 / 0.117 | 167.346 / 0 | 36 / 36 | 36 / 36 | 30 / 30 | 36 / 36 (2,182,644 / 2,182,644) | 36 full / 36 target | 36 / 36 |

B's rapid and pause cases each submitted one cancelled job and rejected one
stale callback; rapid scrolling produced no successful transit decode.  While
the current-frame pause was held, its submitted-job count stayed `1 -> 1` and
entry reads stayed `0 -> 0`; after resume it restarted the requested work.

| Scenario | A / B retained pages | A icon bytes / B icon+QImage bytes | B QImage cache pages / bytes / evictions | A / B working-set delta MiB | A / B sampled peak delta MiB | B source open/fork/close |
|---|---:|---:|---:|---:|---:|---:|
| hidden | 0 / 0 | 0 / 0 | 0 / 0 / 0 | 0.680 / 0.062 | 0.680 / 0.062 | 1 / 0 / 1 |
| first visible | 6 / 6 | 307,200 / 547,840 | 2 / 136,960 / 4 | 3.988 / 1.547 | 14.965 / 1.547 | 2 / 1 / 2 |
| rapid scroll | 36 / 6 | 1,843,200 / 547,840 | 2 / 136,960 / 4 | 3.578 / 0.984 | 14.559 / 0.980 | 2 / 1 / 2 |
| pause/resume | 6 / 6 | 307,200 / 547,840 | 2 / 136,960 / 4 | 1.797 / 1.238 | 12.781 / 1.238 | 2 / 1 / 2 |
| memory pressure | 36 / 6 | 1,843,200 / 547,840 | 2 / 136,960 / 34 | 4.164 / 3.863 | 15.148 / 3.859 | 2 / 1 / 2 |

The retained-byte columns are conservative but not symmetric: A counts the
icons owned by the removed list but not the legacy `ImageCache` source raster;
B counts both visible icons and its QImage cache.  Working-set samples are
process-wide and affected by allocator retention/order.  They support bounded
ownership but are not a real-device measurement.

Every successful A ZIP read materialized one entry `BytesIO`; every successful
B read did the same and the current `ZipImageSource.open_qimage_at_most` added
one `BytesIO.getvalue()` full-payload copy.  Thus B performed 6 extra full
copies in the visible/pause cases and 36 in the memory-pressure case.  This is
reported as the remaining PageList source bottleneck rather than hidden by
fallback.  Both sides intentionally recorded zero paint events because the
benchmark measures artifact publication without showing a view; request to
actual screen paint still requires real-device confirmation.

### 13.8 Validation, remaining risk and next boundary

The major contracts cover virtual mapping, latest visible-order replacement,
one active job, pause/cancel/stale rejection, byte-budget/hidden cleanup,
clone close after queued callback drainage, and presentation-atomic book-list
switching.  Related offscreen regression includes BookSession, all built-in
source families, PDF, Viewer presentation/rendering and ZIP runtime.  The
final collection contains 1,440 tests and all pass under controlled process
boundaries: 1,348 tests in 81 independent file processes, all eight
`test_sprint15_models.py` cases with an explicitly created offscreen
`QApplication`, and all 84 application-controller/navigation-follow-up nodes
in independent processes.  The combined application-controller and
navigation-follow-up files retain their existing shared-Qt-state teardown
stall.  One application-controller node also timed out once in the automated
node loop and passed immediately on a direct isolated retry; no assertion
failed.  These conditions are reported rather than treating process teardown
as production evidence.

Remaining risks are: cooperative cancellation cannot interrupt every Pillow
codec after native decode has entered; the visible QIcon set is range-bounded
but Qt pixmap memory is not part of the QImage byte counter; first PageList use
opens one additional source/document session; filtering is O(all page names)
only when filter text changes; and ZIP JPEG thumbnail target decode still
materializes/copies the whole compressed entry once.  Offscreen timings do not
prove physical-device responsiveness.

The next structural replacement remains the source-independent
`FolderRasterBookRuntime` / page artifact record.  Its boundary starts at the
main Viewer's folder/single-image request and ends at complete-frame publish;
it may remove the `ImageCache -> ViewerRenderTask -> prepared display` split.
It must not absorb PageList, Browser thumbnails, `ViewerPresentationState`, or
the ZIP runtime, and it was deliberately not started in this unit.

## 14. Balanced modernization audit and decoded-source/display-frame split

### 14.1 Corrected objective and fixed baselines

The objective is not to turn NivisViewer into the same application as
ZipPlaFork.  ZipPlaFork remains the performance reference for archive,
work-order and page-artifact lifetime decisions; NivisViewer remains the UX
and modern-Qt reference where its contract is stronger.  The four decisions
used below are:

- **A / ZipPlaFork**: adopt the upstream structure because it is clearly
  simpler, faster and sufficiently safe;
- **B / NivisViewer**: retain the NivisViewer structure because its UX,
  feature semantics or structured lifetime is stronger;
- **C / Hybrid**: use the fast ZipPlaFork internal principle behind a
  NivisViewer external/Qt contract;
- **D / New design**: neither implementation is sufficient for a modern x64,
  high-DPI, large-image Viewer.

The NivisViewer audit started from clean worktree HEAD
`1445d106bdfc6b0445295fbdac311628836feb0c` (`Replace the viewer page list
with a virtual, book-scoped runtime`).  On 2026-08-20, ZipPlaFork remote
`HEAD` and `refs/heads/master` still resolved to the fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  Its license remains
AGPL-3.0-or-later and the preserved text/notices remain in
`licenses/ZipPlaFork/AGPL.txt`, `licenses/ZipPlaFork/About.txt`, and
`THIRD_PARTY_NOTICES.md`.

### 14.2 Actual ZIP call sequences used for the comparison

ZipPlaFork's fixed-revision cold page path is:

```text
ViewerForm input / command
  -> moveToNextPage / MoveToPreviousPage / movePageNatural
  -> currentPage + showCurrentPage
  -> SetBackgroundModeIfPausing
       -> if paused: SetBackgroundMode
            -> priorityLevel(current unit, index-increasing next,
                             index-decreasing previous, remaining)
            -> BackgroundMultiWorker.SetWorksOrder
       -> if active: keep the running job
  -> bmwLoadEachPage_DoWork (one page worker)
  -> PackedImageLoader.OpenImageStream / OpenInnerImageStream
  -> ImageLoader.GetFullBitmap + EXIF/filter/orientation
  -> GetResizedSize + BitmapResizer / post-filter
  -> VirtualBitmapEx
  -> BackgroundMultiWorker completion callback
  -> if currentPage changed while active: SetBackgroundMode for latest current
  -> SetNewResizedImage / ResizedImageArray
  -> showCurrentPage + pbView_Paint / pbView_PaintToCanvas
```

The production NivisViewer ZIP path at the start of this audit was the
following historical path (its paint gate is superseded by sections 19--20):

```text
wheel / shortcut / slider / virtual PageList
  -> ViewerPageNavigationController + PageModel
  -> ViewerWindow._refresh_view
  -> ViewerPresentationState.request_frame (requested only)
  -> ViewerWindow._zip_runtime_request
  -> ZipRasterBookRuntime.request
  -> _ZipRasterFrameStore lookup / latest work order
  -> _drive(current, then paint-gated next, then previous)
  -> _ZipRasterUnitJob.run (one display-unit worker)
  -> ZipImageSource persistent ZipFile entry read
  -> QImageReader or Pillow decode + EXIF/filter
  -> calculate_spread_layout + render_qimage
  -> queued ZipRasterBookRuntime._on_job_completed
  -> GUI-thread QPixmap.fromImage
  -> ViewerWidget.commit_display_ready_frame
  -> ViewerPresentationState.commit_frame
  -> slider/status/PageList/history/progress projection
  -> ViewerWidget.paintEvent
  -> framePainted -> release_prefetch
```

NivisViewer already has one production ZIP engine for single/spread, LTR/RTL,
rotation, resampling, image adjustment, wide split, magnifier source and PageList
visibility.  `ZipPlaCompatibleRasterPath`, the legacy `ImageCache` decode,
prepared-display scheduler and normal `ViewerRenderTask` are not reachable from
the ZIP main-display path.  PageList uses its own virtual model, runtime,
worker/cache and independently opened thumbnail source.

### 14.3 Strength/weakness comparison and A/B/C/D decision

| Subsystem | ZipPlaFork: strengths / weaknesses | NivisViewer: strengths / weaknesses | Modern third option and decision |
|---|---|---|---|
| Archive access | **+** One book-lifetime loader/index and one entry stream per page job; no managed whole-entry byte array on its normal ZIP path. **-** Open/index/sort is tied to a large synchronous `ViewerForm.OpenFile`. Replacement/reload waits before replacing the loader, but Viewer close has no explicit worker-drain/resource-graph shutdown. The direct GDI stream path also relies on a bitmap remaining valid after the entry stream is disposed. | **+** Persistent `ZipFile`, O(1) `NameToInfo/getinfo`, book epoch, cancellable request and callback-drained deferred close. **-** current JPEG path materializes a `BytesIO` and then a `getvalue()` handoff. | **C.** Keep `BookSession`/deferred close and owned-pixel lifetime; retain the ZipPla page-job/entry-stream principle without copying its GDI stream-lifetime assumption. A single seekable payload decoder remains a later optimization. |
| Page loading | **+** Entry read through display bitmap is one literal worker job. **-** Page arrays and UI form own many responsibilities. | **+** ZIP uses one display-unit QRunnable and publishes only terminal units. **-** Other raster sources still use the legacy multi-stage pipeline. | ZIP **C, already adopted**. A source-independent raster runtime is the next cross-format design. |
| Decode | **+** Direct stream-to-GDI decode and retained prefiltered source. **-** `System.Drawing`, .NET 4.5.2 and old external WebP/Susie/ffmpeg choices are not a modern decoder strategy; the stream/bitmap lifetime assumption is unsafe to translate literally. | **+** target-sized JPEG via `QImageReader`, EXIF auto transform, Pillow fallback and WebP support. **-** extension/backend branching is embedded in source methods; valid JPEG still has a compressed-payload copy. | **D.** Future capability-driven decoder registry with owned output pixels; do not translate GDI+ or Python `QIODevice` callbacks literally. |
| Resize | **+** Reuses retained source and creates resized output in the page job. **-** CPU/GDI-oriented implementation and manual bitmap lifetime. | **+** Qt/Pillow resampling modes, physical DPR target and same ZIP unit job. **-** source and layout frame shared one invalidation key at audit start. | **C/D.** Keep Qt render semantics; separate decoded source from display variants (implemented below). |
| Worker / scheduler | **+** One Viewer page job and replaceable `SetWorksOrder`; current/next/previous simplicity. **-** active stale work is not cancelled, reordering waits for its completion, all-page ordering/sorting remains, and `WorkSetGuid` is effectively `Guid.Empty` rather than a generation fence. Resize may use internal parallel execution, so this is not a one-compute-thread design. | **+** One raster lane, latest current, cooperative entry cancel, request/source epoch stale fence, 4 + 1 startup runway and seamless lazy book-wide continuation. **-** legacy non-raster formats still have load/render/prepared stages. | ZIP/Folder **C, superior Hybrid**. Keep the completion-order principle, not `BackgroundWorker`, ineffective generation or thread-priority details. |
| Prefetch | **+** One page job at a time and priority is rebuilt around current; exact `ResizedImageArray` hits bypass new work. **-** priority is always current then index-increasing neighbor then index-decreasing neighbor, not navigation-direction aware; active stale work finishes first, work eventually covers the book, and there is no paint acknowledgement. | **+** accepted commit releases a direction-aware four-forward/one-reverse complete-unit priority prefix, then the one worker continues the lazy book-wide iterator under combined byte admission. Exact-frame hits bypass worker/upload; paint remains an ownership/UI boundary. | **C.** Preserve Nivis complete-unit, direction-aware, byte-driven scheduling while retaining ZipPla's simple completion-boundary frontier. |
| Cache | **+** `PreFilteredImageArray` and `ResizedImageArray` separate source/display while sharing page lifecycle; an exact ready hit paints without a worker. **-** array ownership and manual dispose are form-wide and tied to GDI objects. | **+** deterministic unit/byte budget and ready frame worker bypass. **-** `_UnitKey` previously coupled decoded QImage to viewport/DPR/rotation/zoom frame identity. | **D.** Book-scoped decoded-source store plus layout-specific frame store, with paired validity and one combined budget (implemented). |
| Memory management | **+** source plus resized bytes are counted and least-useful pages are reduced. **-** budget changes with `ActiveForm` and volatile physical memory, eviction runs from an approximately 500 ms GUI timer, several pages can remain over the nominal budget, and scaled/source artifacts for one page are discarded together. | **+** explicit byte/unit settings and protected current complete frame. **-** Qt/plugin/transient compressed allocations remain unobservable; PageList has a separate budget. | **D.** Deterministic combined ledger now covers ZIP QImage+QPixmap without double-counting implicit shares; future runtimes need reservations and soft OS-pressure input. |
| Page state | **+** simple current page. **-** `currentPage`, trackbar and status can move before the target bitmap is ready; old CPU canvas is gray-masked as loading. | **+** `ViewerPresentationState` separates requested/displayed and commits slider/status/history/progress with a complete frame. **-** bookmark/export commands still use the requested `PageModel.current_index` in a few paths. | **B.** Preserve PresentationState; later move bookmark/export defaults to committed state. |
| Display commit | **+** completed resized bitmap is inserted in one UI completion. **-** no explicit epoch/layout/DPR token or immutable complete-spread transaction. | **+** complete single/spread atomic swap, old-frame retention, stale rejection and GUI-only QPixmap. **-** commit projects a broad action refresh. | **B.** Keep NivisViewer. Optimize projection only after profiling. |
| Page list / thumbnail | **+** visible-region loading and release rather than eager full-book thumbnails. **-** paint-time Catalog machinery, GDI ownership and a static semaphore shared by all ThumbViewers; cancellation is not passed into an active `GetThumbnail`. | **+** virtual Qt model, visible margin, O(1) mapping, independent source/cache/worker, pause/cancel, byte budget. **-** first use opens a second archive/document session. | Historically **D**, now **B / maintain**. NivisViewer's completed modern design is stronger than direct Catalog translation. |
| Input handling | **+** compact direct command mapping. **-** ready-frontier navigation can prevent rapid input from reaching a final cold page. | **+** wheel/shortcut/slider/PageList converge on one navigation/presentation boundary; rapid input coalesces to latest request. **-** requested PageModel moves ahead of displayed by design, so non-presentation commands must use the right owner. | **B/C.** Keep Nivis UX and ZipPla-style internal work-order rebuild. |
| Spread / LTR / RTL | **+** ordinary numerical `M=2` pages share level-0 priority; natural `NextPage`/`PreviousPage` generally waits for the current band before entering one cold band. **-** fixed numerical worker bands can disagree with wide/cover navigation topology, and logical current/loading paint are intertwined. | **+** pure `PageModel.spread_at` topology, complete-unit publication, wide split and RTL half order. **-** an initially unknown wide page can cause unit reconstruction. | **B**, while retaining only the useful complete-neighborhood priority principle and combining it with source reuse. |
| Rotation / EXIF | **+** orientation/filter is integrated in the page job. **-** old GDI/manual DPI model. | **+** QImageReader/Pillow EXIF and worker-side rotation; rotation is in presentation tokens. **-** previously forced archive re-decode because source/frame invalidated together. | UX **B**, artifact lifetime **D implemented**. |
| Magnifier | **+** `resetMagnifier`/`MagnifierCanvas`/`bwMagnifierMaker_DoWork` normally reuse `PreFilteredImageArray` and avoid another archive decode. **-** a separate worker/cache can contend with main resize and has no book epoch/request serial/DPR contract. | **+** retained source, selectable crop, quality mode, stale render key and full-resolution request. **-** audit found direct-display mode rejected the final magnifier render task. | **C/D.** Reuse the ZipPla retained-source principle behind the Nivis crop/UX and modern stale fence; direct-mode interactive render is enabled below. |
| Zoom / fit | **+** source/resized separation supports new resized output. **-** UI/bitmap code is tightly coupled. | **+** fit modes, manual zoom/pan and multiple resampling modes. **-** prior source/frame key caused repeated decode for variant changes. | UX **B**, source/display lifetime **D implemented**. |
| DPI | **+** manifest/manual DPI support for its WinForms era. **-** not a Qt6/fractional-DPR design. | **+** `devicePixelRatioF`, physical render targets, DPR-stamped pixmaps and stale-DPR rejection. **-** native upload memory is not fully measurable. | **B.** Do not port old WinForms DPI compensation. |
| Book switch | **+** serializes loader replacement after the old page job returns. **-** synchronous/open-form coupling; failed replacement clears current paths/slider instead of rolling back the old presentation. | **+** pending replacement, failed-open rollback, old displayed state, epoch and retired runtime/source drainage. **-** open start currently clears old runtime cache, so failed replacement may need re-decode after recovery. | **B.** Preserve Nivis lifetime; consider delaying old artifact retirement separately. |
| Close / shutdown | **-** `ViewerForm_FormClosing` saves settings but has no explicit main-worker drain or loader/page/magnifier/canvas resource-graph shutdown; `PackedImageLoader.Dispose` exists but is not explicitly reached from Viewer close. | **+** runtime/PageList/PDF drain, cooperative cancel and final source close after worker+callback lifetime. **-** native decoder cancellation is not always immediate. | **B.** NivisViewer is unambiguously stronger here. |
| Error handling | **+** a broken entry becomes a display-sized `VirtualBitmapEx` error artifact, preserving page completion. **-** broad catches have no typed error/epoch/serial ownership. | **+** typed terminal error frame, complete spread semantics, no feature engine fallback or double commit. **-** Qt failure followed by Pillow can reread the entry. | **B** for semantics; retain the upstream error-artifact principle while payload reuse is later **D** work. |
| Folder / single image | One Viewer loader structure covers it. | Modern UX/features, but main display still uses `ImageCache -> ViewerRenderTask -> prepared display`. | **C**, rank 2: source-independent `RasterBookRuntime`, while retaining Nivis presentation and widget contracts. |
| ZIP / CBZ | Strong archive and one-job principles, weaker presentation/cancel/modern decoder boundaries. | One production runtime, persistent archive, current-first lane, atomic frame, virtual PageList; remaining payload copy and source/frame coupling at audit start. | **C/D.** Keep the Hybrid engine; implement the artifact lifetime split now. |
| RAR / 7z / CBR / CB7 | Packed loader owns the book, but direct SharpCompress/GDI translation would import old constraints. | Entry index is retained, but each page may start a 7-Zip/WinRAR process and materialize stdout; solid archives are costly. | **D**, rank 3: book-scoped, cancellable, solid-aware external-archive runtime. |
| PDF | Integrated into the old loader family. | `PdfiumService` already owns document lifetime, priority, dedup, target render, cancel and close barrier; outer legacy Viewer stages remain. | Service **B**, outer pipeline **C**, rank 4 `PdfBookRuntime`. |
| UI-thread work | UI completion and GDI canvas paint are central; open/index/sort can be synchronous. | ZIP keeps archive/decode/resize off GUI; GUI does QPixmap upload, atomic state projection and paint. PageList scaling moved off GUI. | **B.** Keep Nivis Qt affinity/ownership; only the archive/work-order internals are Hybrid. Measure upload/paint before considering GPU/QRhi. |
| WebP / AVIF / future formats | WebP relies on old external integrations; no AVIF design. | WebP has Qt/Pillow paths; installed decoder capability and declared extensions are not yet unified. | **D**, rank 5 capability-driven decoder registry. GPU is conditional on measured upload/paint dominance. |

### 14.4 Ranked structural work

| Rank | Work unit | Real-device / UX impact | Complexity and compatibility |
|---:|---|---|---|
| 1 | ZIP decoded-source / display-frame lifetime split, including the direct-mode magnifier boundary | Removes repeated ZIP read/decode on resize, DPI, rotation, zoom, wide/spread reconstruction and magnifier resolution transitions; restores the ZIP magnifier render. | Medium-high. Preserves the existing ZIP runtime, presentation, spread and decoder contracts. **Implemented in this section.** |
| 2 | Source-independent `RasterBookRuntime` for folder/single images | Removes the two-stage legacy loader/render/prepared pipeline from a common Viewer path. | High. Must preserve file-change semantics, magnifier and all Viewer presentation features. **Implemented in section 15.** |
| 3 | Book-scoped RAR/7z runtime | Potentially removes process-per-entry and repeated solid-archive traversal, the largest format-specific cold cost. | Very high resource/cancellation/backend risk. |
| 4 | `PdfBookRuntime` around the retained `PdfiumService` | Removes outer cache/worker/conversion stages while retaining target rendering. | High rotation/DPR/annotation/shutdown compatibility risk. |
| 5 | Capability-driven decoder registry | Modern WebP/AVIF/native backend selection and one-payload fallback. | Medium-high; lower current ZIP page-turn impact than ranks 1-4. |

GPU compositing is not ranked until measurement shows that GUI-thread
`QPixmap.fromImage`/paint, rather than archive read and decoder work, dominates
the physical-device interval.

### 14.5 Implemented rank 1: page-artifact lifetime ownership

`app/zip_raster_book_runtime.py` now separates two lifetimes:

1. `_ZipRasterSourceStore` owns immutable decoded `QImage` sources by book
   epoch, source identity, image id, pixel-affecting adjustments, decoded pixel
   size and full/preview status.  A preview is reusable only when it meets the
   later target; a full source satisfies every later layout.
2. `_ZipRasterFrameStore` owns layout-dependent complete `QPixmap` frames by
   display unit and full render spec.  Cached frames reference source keys but
   do not own/count a second QImage copy.

The runtime resolves source hits before creating `_ZipRasterUnitJob`.  A source
hit still creates one worker job because resize/rotation/display preparation is
off the GUI thread, but it performs no archive entry read and no image decode.
An exact frame hit continues to create no job, callback or QPixmap.

The two stores share the configured book byte budget.  Decoded QImage bytes and
display QPixmap estimates are counted once; source eviction also removes frame
variants that would otherwise expose a pixmap without its magnifier source.
Current complete source/frame ownership is protected and may soft-overflow the
budget for one exceptional page rather than blanking the Viewer.
Prefetch admission now estimates both a missing decoded source and its physical
display frame instead of looking only at QPixmap bytes.  It does not discard a
completed artifact in advance, so cancellation cannot exchange a ready frame
for work that never commits.  This is a soft retained-artifact budget: transient
decoder/Pillow/upload allocations remain outside the ledger.  Unknown lazy page
dimensions use the largest observed source in the same book as a pragmatic
estimate; a highly dissimilar following page can therefore still create one
transient overshoot before normal eviction/failure suppression takes effect.
Only the source variant matching the active current adjustments is protected;
old brightness/contrast/gamma variants remain evictable.

`invalidate_layout()` now cancels the old work order and clears only display
frames.  Book close/switch or explicit `clear_artifacts=True` clears both
stores.  Therefore resize, DPR, rotation, zoom, wide/spread reconstruction and
magnifier transitions reuse a sufficient source and upgrade a preview to full
resolution at most once.

`app/viewer_widget.py` continues to reject normal legacy render jobs while a
book runtime owns direct display, but now permits `purpose="magnifier"`.  This
is a NivisViewer UX projection over the committed runtime QImage, not a switch
back to the old main Viewer engine.  The stale `key.purpose` reference in the
direct-mode prepared-display guard was also reduced to an unconditional direct
mode return.
`app/viewer_window.py` marks only preview ZIP sources with `rendered_size` for a
full-raster upgrade.  A full ZIP source no longer enters the PDF-only resolution
request branch and wait forever for a handler that intentionally ignores ZIP.
Direct-mode magnifier crops use the widget-owned one-thread lens pool rather
than the book runtime's single Viewer coordinator lane.  Their stale result is
still rejected, but a non-interruptible crop can no longer queue ahead of a
current-page archive/decode job.

Removed/replaced ownership in this unit:

| Previous structure | Result |
|---|---|
| `_CachedFrame` owned both source QImage and layout QPixmap under one `_UnitKey` | Replaced by source keys plus independent source/frame stores. |
| `invalidate_layout()` called `cancel(clear_artifacts=True)` | Replaced by display-only invalidation; decoded source remains book-scoped. |
| Frame byte accounting rediscovered/deduplicated source QImages inside each frame | Replaced by one source ledger plus pixmap-only frame ledger. |
| A source could not be reused across layout keys | Replaced by preview sufficiency/full-resolution lookup before decode. |
| Direct display rejected every `ViewerRenderTask`, including the magnifier | Normal main renders remain rejected; the explicit magnifier crop render is admitted. |

### 14.6 Provenance boundary

The performance principle is informed by ZipPlaFork fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:

- `source/ZipPla/ViewerForm.cs` fields `PreFilteredImageArray`,
  `OriginalImageInfoArray`, `ResizedSizeArray`, `ResizedImageArray`;
- `source/ZipPla/PackedImageLoader.cs` constructor (`:283-330`),
  `PackedImageEntry` (`:375-400`), `getZipArchiveEntries` (`:1197-1243`),
  `OpenImageStream` / `OpenInnerImageStream` (`:1781-1867`) and
  `SeekableStream` (`:2731-2890`);
- `source/ZipPla/ImageLoader.cs` `GetJpegOrientation` /
  `LoadRotateBitmap` (`:382-400`) and `GetFullBitmap(Stream)` (`:559-689`);
- `bmwLoadEachPage_DoWork` (`:3177-3550`) and its reuse of retained source;
- `NextPage` / `PreviousPage` (`:1903-2002`) and complete-spread gating;
- per-entry error artifact creation (`:3565-3579`);
- page completion/publication (`:5371-5415`);
- `SetNewResizedImage` (`:5451-5468`);
- `ReduceUsingMemory` (`:5471-5524`);
- `priorityLevel` (`:5586-5605`);
- paint/loading-mask paths (`:6768-6858`, `:6990-7321`);
- `source/ZipPla/GenerarClasses.cs`,
  `BackgroundMultiWorker.SetWorksOrder` (`:247-264`) and completion/next-work
  selection (`:266-303`).

The existing `ZipRasterBookRuntime`, unit job and work-order/cache ranking remain
the recorded direct AGPL-3.0-or-later structural port.  The new
`_ZipRasterSourceStore`, resolution-sufficiency lookup, paired source/frame
validity, combined Qt byte ledger, PresentationState integration and
direct-mode magnifier boundary are a new Python/Qt design rather than a
line-for-line translation.  No new dependency or license was added.

### 14.7 Validation and remaining boundary

The focused contract protects: preview reuse for a smaller layout, one-time
full-resolution upgrade, reuse of that full source for later DPR/layout work,
GUI-thread source attachment, complete spread/frame behavior, one ordered job
lane, stale rejection, budget admission, shutdown, and magnifier task tracking
while direct display is active.  Detailed offscreen A/B numbers are recorded
by `scripts/benchmark_zip_runtime_layout_reuse.py`; they remain supporting
evidence and do not establish physical-device responsiveness.

Final offscreen validation used independent pytest processes.  The focused
Viewer/runtime set passed 227 tests.  The complete suite was split into three
fresh processes and passed 651 + 507 + 283 = 1,441 tests.  One 20 ms
fullscreen-hide timer assertion missed its 30 ms deadline during an earlier
concurrent run; the test passed alone and its entire 507-test partition passed
on two clean reruns, so no product change was made to hide that scheduling
flake.  Syntax/import checks and `git diff --check` also passed.  One duplicate
subagent split process remained after its agent was interrupted; after its PID,
parent and executable were verified, only that test process was stopped and the
same 651-test partition passed sequentially.  No spawned test/benchmark process
remains.  The unrelated pre-existing ComfyUI Python process was neither stopped
nor modified.

The controlled default run used one temporary ZIP_STORED detailed JPEG,
2,400 x 3,600 pixels, 6,592,317 compressed bytes, a 1,200 x 800 viewport and a
256 MiB book budget.  Each side ran in a fresh offscreen child process with its
own `ZipImageSource` and `ZipRasterBookRuntime`.  A reproduced the old
layout-invalidation ownership by calling `cancel(clear_artifacts=True)` before
every variant; B used production `invalidate_layout()` and retained decoded
sources.  Neither side created a `ViewerWindow`, native input or an external
GUI application.

| Operation | A old clear-all ms / reads / decodes | B source-reuse ms / reads / decodes |
|---|---:|---:|
| Initial decoder-sized fit | 60.154 / 1 / 1 | 58.621 / 1 / 1 |
| Smaller resize | 43.725 / 1 / 1 | 2.106 / 0 / 0 |
| Full-resolution rotation upgrade | 95.530 / 1 / 1 | 95.260 / 1 / 1 |
| DPR/manual-zoom variant | 83.632 / 1 / 1 | 13.036 / 0 / 0 |
| Full-source magnifier-equivalent variant | 84.692 / 1 / 1 | 12.951 / 0 / 0 |
| **Total** | **367.824 / 5 / 5** | **182.047 / 2 / 2** |

| Counter / memory | A old clear-all | B source reuse | Difference |
|---|---:|---:|---:|
| ZIP entry bytes read | 32,961,585 | 13,184,634 | -19,776,951 (-60.0%) |
| Observable whole-payload API copies / bytes | 2 / 13,184,634 | 1 / 6,592,317 | -1 / -6,592,317 |
| Source-cache hits / misses | 0 / 5 | 3 / 2 | +3 hits / -3 misses |
| Worker jobs / queued callbacks | 5 / 5 | 5 / 5 | 0 / 0 |
| Display QImage outputs / `QPixmap.fromImage` | 5 / 5 | 5 / 5 | 0 / 0 |
| Final source / frame / total cache bytes | 25,920,000 / 8,640,000 / 34,560,000 | 25,920,000 / 8,640,000 / 34,560,000 | identical |
| Retained source pages / frame units | 1 / 1 | 1 / 1 | identical |
| Sampled peak working-set delta | 166.141 MiB | 97.703 MiB | -68.438 MiB |
| Process-lifetime peak delta | 170.156 MiB | 112.406 MiB | -57.750 MiB |
| End working-set delta | 97.191 MiB | 46.703 MiB | -50.488 MiB |

B is 2.020x faster (185.777 ms saved) for this five-layout sequence because it removes three ZIP
reads and decodes; it does not hide work by reducing render jobs, callbacks,
display QImages or pixmap uploads.  Final retained logical cache ownership is
identical.  The memory values are process-wide and allocator-sensitive, and
the magnifier-equivalent step measures source/display preparation rather than
an actual widget paint.  A separate offscreen integration contract confirms
that a direct-mode ZIP frame now completes the real magnifier task.

A final production-path supplement used `ViewerWindow ->
ZipRasterBookRuntime`, eleven 1,200 x 1,800 detailed JPEG pages (1,649,004
bytes per ZIP entry), an 800 x 600 viewport and explicit offscreen
`QWidget.render` paint.  It is not an old/new comparison; it checks the latest
path after the structural change.  All scenarios settled, every transit-page
decode count was zero, and ready/retained hits created no job, callback,
decode or QPixmap upload.

| Scenario | request→commit / paint ms | ZIP reads / decodes | jobs / stale | paints | retained pages | sampled peak delta MiB |
|---|---:|---:|---:|---:|---:|---:|
| cold forward | 15 / 15 | 1 / 1 | 3 / 0 | 1 | 3 | 4.582 |
| reversal | 15 / 15 | 0 / 0 | 4 / 0 | 1 | 4 | 0.035 |
| cold roundtrip | 0 / 0 | 0 / 1 | 5 / 1 | 2 | 3 | 2.602 |
| rapid final | 16 / 16 | 1 / 1 | 3 / 0 | 1 | 3 | 3.918 |
| retained roundtrip | 0 / 0 | 0 / 0 | 0 / 0 | 1 | 6 | 0.020 |
| ready | 0 / 0 | 0 / 0 | 0 / 0 | 1 | 3 | 0.012 |
| memory pressure | 15 / 15 | 0 / 0 | 1 / 0 | 1 | 1 | 0.043 |

The 0/15/16 ms values reflect offscreen event-loop/paint quantization and must
not be interpreted as physical-device page-turn latency.  Cancelled partial
entry bytes are also outside the source counter, as documented by the script.

Remaining known work includes the two compressed-payload materializations on
the normal JPEG path, Pillow/native decoder regions that cannot be interrupted
immediately, and Qt/plugin/native upload allocation outside the byte ledger.
A running high-quality magnifier `ViewerRenderTask` has no cooperative inner
cancel.  It is now isolated from the book Viewer lane, but can briefly contend
for CPU/memory bandwidth and shutdown still waits for its inner crop.  A
preview-to-full magnifier upgrade also uses `invalidate_layout()`, preserving
decoded sources but clearing unrelated ready neighbor frames; a targeted
`ensure_full_source` boundary should replace that broad frame invalidation.
Requested-page bookmark/export residue, broad post-commit action projection,
and all non-ZIP legacy main-display runtimes also remained at that point.  The
next structural boundary was rank 2, the source-independent
folder/single-image `RasterBookRuntime`; section 15 records its implementation.

## 15. Folder / directly-opened image RasterBookRuntime replacement (2026-08-20)

### 15.1 Scope and preserved product semantics

The performance target is the common Folder main-display path, not a WinForms
clone and not a change to NivisViewer's open behavior.  NivisViewer continues
to treat an image path as the selected starting page of its parent-folder book.
A folder containing one supported image is naturally a one-page runtime and
has no neighbor work.  Changing every image-path open into an isolated page
would remove existing next/previous navigation and is therefore outside this
performance replacement.  This is the deliberate resolution of the literal
"single image = one-page book" wording against the higher-level requirement to
preserve NivisViewer's established parent-folder navigation UX; no hidden
feature fallback is involved.

The start-of-work worktree was clean at NivisViewer HEAD
`925a17239296ace6b590ddcad469a7d61f022d9d`.  The fixed ZipPlaFork reference is
unchanged: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.  No
dependency or license was added.

### 15.2 Old production path

Before this replacement, both a Folder and an image path selected from that
Folder followed this sequence:

```text
path / folder snapshot
  -> FolderImageSource
  -> BookSession installs no viewer_runtime
  -> ViewerWindow._refresh_view
  -> decode-demand timer / ImageCache.preload_around
  -> _ImageLoadTask
  -> file full-byte read
  -> target JPEG QImageReader or WebP QImageReader or Pillow + EXIF
  -> ImageCache QImage + pageLoaded callback
  -> ViewerWindow._render_spread / display-demand timer
  -> ViewerWidget._prepare_display
  -> ViewerRenderTask
  -> resize / split / rotation
  -> queued GUI callback
  -> QPixmap.fromImage + prepared/render cache
  -> complete frame commit
```

The source and display stages used the same Viewer worker lane but were two
jobs with two callbacks and two cache owners.  `ImageCache` and
`ViewerWidget` independently evicted source and prepared artifacts, while
`ViewerWindow` reconciled their budgets and timers.  An exact prepared-frame
hit was fast, but a source-only hit still required the second worker.  A
running Folder Pillow/QImageReader decode could only be stale-rejected after it
returned.

### 15.3 New shared runtime contract

The existing ZIP execution core is now the source-neutral
`RasterBookRuntime`.  `ZipRasterBookRuntime` and the new
`FolderRasterBookRuntime` are thin source-type subclasses; they do not fork the
scheduler, stores, publication callback or shutdown path.  Neutral public
aliases live in `app/raster_book_runtime.py`; the implementation is temporarily
retained in its historical `app/zip_raster_book_runtime.py` module to keep the
already-tested ZIP import surface stable.  Moving the physical core module is
cleanup, not a second engine.

```text
BookSession (one source epoch)
  -> one RasterBookRuntime (Folder or ZIP source constraint)
ViewerWindow input
  -> ViewerPresentationState.request_frame
  -> RasterRequest(current, directional next, reverse neighbor)
RasterBookRuntime
  -> exact complete-frame lookup (ready hit: no worker)
  -> one active display-unit job
       -> cached sufficient decoded source, or file read + decode + EXIF/filter
       -> spread/split layout + rotation + physical-DPR resize
       -> one terminal display QImage result
  -> one queued GUI completion
       -> QPixmap.fromImage
       -> decoded-source store + complete-frame store under one budget
  -> ViewerWidget.commit_display_ready_frame
  -> ViewerPresentationState atomic commit and UI projections
  -> paint acknowledgement
  -> release next/previous prefetch
```

`invalidate_layout()` clears layout-dependent frames only.  A sufficient
decoder-sized preview is reused for smaller resize/DPR variants; one full
source upgrade satisfies later rotation, zoom, DPI and magnifier needs.  The
current complete frame and its active source remain protected, and prefetch is
admitted against the combined QImage/QPixmap ledger.  Exact ready hits emit the
frame synchronously without another worker, file read, decode, callback or
pixmap upload.

`BookSession` now retires every `RasterBookRuntime` through the same
callback-drain graph.  A Folder file handle is not kept for the book lifetime:
`FolderImageSource` closes it after the payload read and the runtime retains
only owned decoded pixels.  The old source remains alive until an in-flight
worker and its queued GUI callback have drained; a late result is rejected by
source epoch, identity and current work order.

### 15.4 Design decision table

| Boundary | ZipPlaFork strength used | NivisViewer strength retained / new design | Decision |
|---|---|---|---|
| Work order | One page worker and current-centered replaceable order | Navigation-direction neighbor order, request/source epochs and paint gate | **C / Hybrid** |
| End-to-end job | Decode, resize and display artifact belong to one work item | Qt worker affinity, physical DPR, modern decoders and complete spread transaction | **C / Hybrid** |
| Source/display lifetime | Retained source and resized image are separate reusable artifacts | Immutable QImage source plus GUI-owned QPixmap frame, combined deterministic byte budget | **D / New design** |
| Ready hit | Completed resized image bypasses worker | Atomic token validation and PresentationState commit | **C / Hybrid** |
| Page state / old frame | Complete page is the useful publication unit | requested/displayed separation, old-frame retention, atomic slider/status/history/progress | **B / NivisViewer** |
| High DPI / rotation / fit / zoom | Reuse source before producing another resized bitmap | Qt6 fractional DPR and Nivis feature/render contracts | **B/C** |
| Magnifier | Reuse retained full source | Nivis selection/crop UX and stale render key; only the lens projection task remains Widget-local | **C / Hybrid** |
| PageList | Separate visible-region thumbnail work | Virtual model, independent source/cache/worker and pause during current work | **B / NivisViewer** |
| Book switch / close | Do not dispose pixels still needed by a page job | pending replacement rollback, retired runtime/source graph and callback drain | **B / NivisViewer** |

### 15.5 Production ownership after replacement

Folder and directly-opened image parent books now use the runtime for single,
spread, LTR/RTL, split-wide, rotation, fit, manual zoom, image adjustments,
DPI/source upgrade, magnifier source and PageList-visible operation.  There is
no feature eligibility switch back to the old main engine.

The following old structures are removed from **Folder/single production main
display**, but not deleted globally:

- `_ImageLoadTask` / decoded `ImageCache` ownership;
- decode-demand and legacy raster-prefetch timers;
- normal-page `ViewerRenderTask`;
- prepared-display scheduler/cache and its independent eviction decisions;
- `ImageCache.pageLoaded` as a Folder display callback.

They remain explicitly for PDF, RAR/7z and custom/test `ImageSource` values
until those source families receive their own runtime.  The magnifier's
high-quality crop `ViewerRenderTask` also remains: it is an interactive
projection of the committed runtime source, not a fallback page engine.
PageList remains the independent `ViewerPageListRuntime` and thumbnail source;
it never consumes the main Folder source store.

Clipboard image and page-info commands formerly read `ImageCache` directly.
Runtime-backed books now project these values from
`ViewerWidget.displayed_source_snapshot()`, which uses the atomically committed
displayed page rather than a possibly newer requested page.  The legacy cache
lookup remains only as the other-format adapter.

### 15.6 Direct-port provenance

No new Folder-specific code was copied from ZipPlaFork.  Folder reuses the
already recorded AGPL-derived structural port in `RasterBookRuntime`:

| ZipPlaFork fixed-revision source | Adopted processing principle | NivisViewer implementation |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:266-303`) | One active job selected from a replaced current-centered order | `RasterBookRuntime.request`, `_drive`, `_submit`, `_cancel_active_job` |
| `source/ZipPla/ViewerForm.cs`, `bmwLoadEachPage_DoWork` (`:3177-3550`) | Read/decode/resize/display artifact in one page work item | `_ZipRasterUnitJob.run`, `_decode_page`, `_render_unit` (shared by Folder and ZIP despite the historical private name) |
| `ViewerForm.cs`, `SetNewResizedImage` (`:5451-5468`) | Publish only a completed resized artifact | `RasterBookRuntime._on_job_completed` and `ViewerWidget.commit_display_ready_frame` |
| `ViewerForm.cs`, `priorityLevel` (`:5586-5605`) and `ReduceUsingMemory` (`:5471-5524`) | Current-centered retention and farthest-useful eviction | source/frame stores, combined budget and prefetch admission |

Folder file access, Qt/Pillow decode, EXIF, DPR, source/frame separation,
generation fences, PresentationState, atomic commit, paint acknowledgement,
virtual PageList and structured shutdown are NivisViewer/modern Qt designs.

### 15.7 Folder production-path A/B

The final default A/B used fresh child processes and the same temporary
detailed-JPEG folders on both sides: eight 900 x 1,350 small pages, eight
2,400 x 3,600 large pages, an alternating mixed folder, a one-page folder and
a two-page replacement folder.  The viewport was 1,200 x 800 and the soft
book budget was 256 MiB.  A used a benchmark-only generic `ImageSource`
adapter around the real `FolderImageSource`, which selects the retained
`ImageCache -> ViewerRenderTask` production path without adding a product
fallback flag.  B used an ordinary `FolderImageSource` and aborted unless
`FolderRasterBookRuntime` and direct-display mode were active.  Windows file
cache state was not flushed.

| Aggregate counter | A old two-stage | B Folder runtime | A - B |
|---|---:|---:|---:|
| File reads / returned bytes | 34 / 144,890,230 | 34 / 139,232,524 | 0 / 5,657,706 |
| Observable full-payload copies / bytes | 68 / 289,780,460 | 68 / 278,465,048 | 0 / 11,315,412 |
| Successful decode calls | 34 | 34 | 0 |
| Direct source QImages / Pillow source images / Pillow-to-QImage | 33 / 1 / 35 | 30 / 4 / 35 | +3 / -3 / 0 |
| Display QImages / `QPixmap.fromImage` | 36 / 35 | 38 / 37 | -2 / -2 |
| Viewer jobs / GUI callbacks | 70 / 68 | 38 / 38 | **32 / 30 fewer in B** |
| Duplicate / transit decodes | 2 / 8 | 2 / 8 | 0 / 0 |
| Display commits / content paints | 19 / 20 | 19 / 20 | 0 / 0 |
| Scenario elapsed total | 1,908 ms | 1,736 ms | 172 ms; A/B = 1.099x |
| Peak sampled retained cache | 33,016,160 bytes | 52,335,024 bytes | B +19,318,864 |
| Maximum per-scenario sampled working-set delta | 86.500 MiB | 99.941 MiB | B +13.441 MiB |
| Close time (millisecond-rounded) | 0.0 ms | 0.0 ms | 0.0 ms |

| Scenario | A elapsed / input-to-commit / input-to-paint ms | B elapsed / input-to-commit / input-to-paint ms | A reads / decodes / jobs | B reads / decodes / jobs |
|---|---:|---:|---:|---:|
| Small forward | 187 / 0 / 0 | 93 / 0 / 0 | 5 / 5 / 10 | 5 / 5 / 5 |
| Large forward | 344 / 0 / 0 | 265 / 0 / 0 | 5 / 5 / 10 | 5 / 5 / 5 |
| Mixed cold forward | 140 / 31 / 31 | 109 / 16 / 16 | 3 / 3 / 6 | 3 / 3 / 3 |
| Mixed reversal | 141 / 93 / 93 | 125 / 94 / 94 | 4 / 4 / 7 | 4 / 4 / 4 |
| Mixed cold roundtrip | 235 / 47 / 47 | 204 / 47 / 47 | 6 / 6 / 12 | 6 / 6 / 6 |
| Mixed rapid final | 125 / 31 / 31 | 125 / 16 / 16 | 3 / 3 / 6 | 3 / 3 / 3 |
| Mixed ready hit | 32 / 0 / 0 | 16 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Resize | 16 / n/a / 16 | 16 / n/a / 16 | 0 / 0 / 0 | 0 / 0 / 0 |
| Rotation | 203 / 0 / 203 | 187 / 94 / 187 | 2 / 2 / 5 | 1 / 1 / 3 |
| DPR change | 31 / 0 / 31 | 188 / 172 / 188 | 0 / 0 / 2 | 2 / 2 / 4 |
| Magnifier | 141 / n/a / 125 | 79 / n/a / 63 | 1 / 1 / 2 | 0 / 0 / 0 |
| Switch large to mixed | 78 / 15 / 15 | 78 / 16 / 16 | 2 / 2 / 4 | 2 / 2 / 2 |
| Switch small to large | 110 / 47 / 47 | 110 / 47 / 47 | 2 / 2 / 4 | 2 / 2 / 2 |
| Switch to one-page folder | 94 / 78 / 78 | 125 / 109 / 109 | 1 / 1 / 2 | 1 / 1 / 1 |
| One-page ready hit | 31 / 0 / 0 | 16 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

Ready hits on both paths avoided reads, decodes and jobs; B also emitted its
cached complete frame without a GUI callback or pixmap upload.  B's structural
gain is clearest in the removal of the second normal-page job/callback: it
roughly halves those counts while preserving the same 19 semantic commits and
20 explicit offscreen paints.  Magnifier reuse removed one full decode in B.
The default sequence nevertheless needed the same total decode count because
some high-resolution layout transitions legitimately upgraded a decoder-sized
preview.  In particular, the DPR scenario was slower in this single run and
the reversal's final input latency was also worse, so the aggregate result is
not evidence that every interaction improved.

The retained-cache result is a regression: B keeps sufficient decoded sources
across layout variants while A often drops them.  The sampled working-set
maximum was 13.441 MiB higher in the final run, and earlier identical/default
and quick runs changed its sign and magnitude.  Native decoder, Pillow conversion,
atomic old/new frame overlap and QPixmap allocation are not all in the logical
cache ledger, so no memory improvement is claimed.  The numbers are reported
rather than treated as a pass.  Both sides closed all 4 created sources, had zero open
workers, zero unfinished cache/render tasks and a fully drained coordinator.
No benchmark child remained.

This benchmark uses explicit offscreen `QWidget.render`, approximately 5 ms
working-set sampling, hot OS file cache and application-visible counters.
`input -> paint` is event-loop-quantized and observable full-copy counts do not
include unobservable plugin/native sharing.  It is supporting evidence only;
physical-device responsiveness and memory must be checked separately.

### 15.8 Remaining risk and next boundary

A Folder file read, Pillow decode or WebP QImageReader decode already running
inside native/library code cannot be interrupted safely.  A reversal cancels queued
work and rejects the obsolete result, but the one running decode can occupy the
Viewer lane until it returns.  The runtime also counts retained artifacts, not
transient compressed payload, Pillow conversion, old/new atomic-swap overlap or
native QPixmap allocation, so physical peak working set can exceed its soft
budget.

Two deterministic integration stalls were found while validating the new
Folder path.  A worker-side JPEG `QImageReader.read()` could overlap lazy GUI
icon/plugin work (`QIcon.pixmap` or showing the PageList dock) and stop both
threads on Windows/offscreen Qt.  Folder JPEG target decode now stays wholly in
the worker's Pillow/libjpeg path (`draft`, EXIF transpose and bounded
`thumbnail`) and returns a detached QImage.  Cold raster demands also enter the
same six-millisecond replaceable request timer, so already-queued Browser paint
can finish before worker dispatch.  Pixel decode never moves to the GUI thread.
ZIP stream decode is unchanged, and Folder WebP still uses Qt's plugin reader;
that unobserved combination remains a targeted real-device risk.

Validation also closed two state/lifetime holes exposed by the cutover.  A
complete error frame for the initially requested broken page now releases the
interactive-open gate while retaining a successful spread partner, and Viewer
close re-stages that window's committed progress snapshot before flushing the
shared metadata queue.  Neither change lets requested-only state become saved
or displayed.

The next structural format boundary is RAR/7z.  `SevenZipImageSource` currently
retains an entry index but may launch an external extraction process and
materialize stdout for each page.  It must not simply subclass the Folder
runtime until process cancellation, solid-archive ordering, clone ownership
and close semantics are moved into a book-scoped external-archive source.

### 15.9 Validation result

Syntax and import checks passed for all changed/new Python modules.  The final
collection contains 1,446 tests.  They passed across per-file independent
offscreen pytest processes (416 + 747 + 283), including the Folder/ZIP
runtimes, source/frame reuse, stale reversal, ready hit, PageList separation,
single/spread/LTR/RTL, rotation, magnifier, DPR, broken-image error frame,
book replacement, metadata progress, PDF and external-archive regressions.

Validation exposed two test-harness assumptions rather than product fallback
requirements.  The PageList replacement test waited for the now-unused
`ImageCache` and was changed to wait for the first committed presentation
frame.  Three Browser delegate tests instantiated QWidget-derived objects
without a QApplication; the Windows native crash disappeared after declaring
their existing `qapp` precondition, and that file then passed 8/8.  One broad
parallel sweep of `test_browser_file_operations.py` and one first run of a
Sprint 14 file did not terminate, but the complete files passed immediately on
fresh-process rerun (21/21 and 60/60), and every parameterized node also passed
independently.  No benchmark/test child remained after validation.

No real application, native input, external GUI application, commit or push
was used.  Offscreen timing and sampled working set do not establish a
physical-device improvement.

## 16. ZIP / Folder navigation critical-path replacement (2026-08-20)

### 16.1 Fixed-reference call chain

The comparison remains fixed to ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` (AGPL-3.0-or-later).
Its relevant navigation chain is:

```text
KeyboardShortcut.Control_MouseWheel / Owner_KeyDownOrUp
  -> Command.NextPage / PreviousPage
  -> ViewerForm.moveToNextPage / MoveToPreviousPage
  -> movePageNatural
  -> NextPage / PreviousPage checks ResizedImageArray's ready frontier
  -> currentPage + showCurrentPage
  -> BackgroundMultiWorker.SetWorksOrder
  -> bmwLoadEachPage_DoWork (read/decode/filter/resize)
  -> SetNewResizedImage
  -> showCurrentPage / paint
```

Source locations are `KeyboardShortcut.cs:436-448,653-682`,
`ViewerForm.cs:1903-2002,3177-3550,5371-5415,5451-5468,5558-5605`, and
`GenerarClasses.cs:247-264,298-338`.  ZipPlaFork does not coalesce rapid cold
input: once navigation reaches an unready page, later next/previous operations
are effectively ignored until that ready frontier advances.  Its active job is
not preempted; only unstarted work is reordered.

NivisViewer keeps the user's final target instead of dropping input:

```text
ViewerWidget wheel/key signal
  -> input kind (discrete / wheel / key initial / key repeat / slider scrub)
  -> ViewerPageNavigationController / PageModel requested target
  -> ViewerPresentationState.request_frame
  -> ViewerWindow builds current -> directional next -> reverse neighbor
  -> exact frame lookup
       hit: synchronous complete-frame publication
       miss, discrete/leading input: immediate RasterBookRuntime.request
       miss, proven rapid wheel/repeat: RasterBookRuntime.stage
             (immediate order/cancel/retention fence)
             -> cadence-derived short trailing admission / release boundary
             -> one active display-unit job
  -> one queued GUI completion / QPixmap upload
  -> atomic complete-frame commit
  -> slider/status and semantic PresentationState commit
  -> paint acknowledgement
  -> bounded neighbor prefetch
```

### 16.2 Comparative decisions

| Boundary | ZipPlaFork | NivisViewer before this change | Decision and result |
|---|---|---|---|
| Rapid cold input | Stops at the ready frontier; no final-target coalescing | One adaptive rule delayed discrete input and wheel alike | **Hybrid/new design:** discrete and the leading/low-rate wheel packet dispatch immediately. The second same-direction rapid packet proves a burst; only then is the latest target staged. Input is not dropped. |
| Work-order reversal | Reorders unstarted work around latest `currentPage`; active work finishes | New order reached the runtime only after the timer | **ZipPla principle adopted:** order, retention and obsolete cancellation change at input time. Nivis epoch/serial cancellation remains stronger. |
| Ready hit | Reads completed resized image directly | Already bypassed timer, worker, decode and `QPixmap.fromImage` | **Nivis maintained:** synchronous atomic commit remains; per-input cache-limit pruning was removed. |
| Cold current / prefetch | One worker, active work not preempted | One active job; delayed staging could let old-direction work continue | **Nivis Hybrid maintained/improved:** current first, cancel/stale fence, then a memory-admitted 4 + 1 complete-unit runway and seamless lazy book-wide warm-up from accepted commit. Paint acknowledges ownership but no longer gates scheduling. |
| Source vs display retention | Original/resized artifacts have a simple page lifecycle | Source eviction also deleted a still-valid QPixmap frame | **Modern design:** a display frame survives source eviction; last painted and requested frames are protected until replacement paint. Magnifier rehydrates a missing source on demand. |
| Commit UI work | WinForms updates current controls in `showCurrentPage` | PageList scroll/work, all menu QAction writes and metadata staging ran synchronously before paint | **Modern design:** slider/status and semantic commit stay synchronous; invariant full-action sync is removed; PageList scroll/work and persistence are serial-guarded/coalesced side effects. |
| Replacement open | Old form state remains until new open completes | Provisional open cleared old runtime artifacts before success | **Nivis ownership fix:** stop old work but retain completed artifacts until replacement success; failed open restores a ready hit. |

`RasterBookRuntime.stage` remains a two-phase navigation contract. It installs the
new current/work order, updates eviction ranking, stops obsolete prefetch and
cancels a replaceable active job without starting a cold target.  A later
`request` for the exact staged object opens the execution gate.  A completion
arriving while staged may be retained but cannot change presentation state.
Already-cancelled A jobs are never re-adopted in an A -> B -> A reversal.

`NavigationAdmissionPolicy` now separates discrete, wheel, initial key press,
key repeat, slider scrub and internal refresh. Discrete navigation and the
leading wheel/key press have no timer. A same-direction wheel packet within
40 ms proves a burst; its trailing boundary is the input-event timestamp
interval plus 2 ms, clamped to 6-28 ms. Device timestamps are used instead of
GUI callback arrival time so a slow decode cannot misclassify queued rapid
input as a low-rate wheel. An initial key press is immediate; later auto-repeat
packets for that key stage regardless of a momentary worker-idle gap and the
physical key release dispatches the exact final target. The timer is only a
release boundary owned by this state; it never owns the requested page or work
order. ScrollEnd, key release, slider release, direction reversal and a ready
hit bypass the wait.

The raster viewport timer is also cancelled when a navigation request captures
the live viewport/DPR. It can no longer invalidate the just-built frame store
in the middle of a wheel burst. If no navigation occurs after a real resize,
the existing delayed layout refresh still runs.

Source hydration has a separate rollback boundary.  If a ready QPixmap no
longer owns a decoded source, the runtime temporarily withholds that one frame
while it loads the source, but keeps the frame as a ready rollback artifact.
A superseding navigation restores it before cancelling work, so A -> magnifier
-> B -> A cannot turn an already-ready page cold.  Magnifier requests also use
a current-only work order; next/previous are not decoded at full resolution
while the lens competes for interactive CPU and memory.  A late terminal
hydration callback may refresh the runtime-owned source, but cannot replace a
valid frame with an error or republish an already-committed ready hit.

### 16.3 Port provenance

No C# statement or WinForms implementation was copied literally in this
change.  The AGPL-derived processing principles and their translated owners
are:

| Fixed-revision source | Adopted principle | NivisViewer implementation |
|---|---|---|
| `GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` | Replace the unstarted order immediately around the latest current page | `RasterBookRuntime.stage`, `_adopt_request`, `_drive` |
| `ViewerForm.cs`, `NextPage` / `PreviousPage` ready-frontier checks | Do not start decode for every transit page of a cold burst | `NavigationAdmissionPolicy`, input-kind routing and `RasterBookRuntime.stage` |
| `ViewerForm.cs`, `SetNewResizedImage` / `showCurrentPage` | Publish only completed resized artifacts and reuse ready output directly | frame-store hit, `commit_display_ready_frame`, last-painted protection |

ZipPlaFork's dropped input, fixed index-side preference, uncancellable stale
job, all-book prefetch, pre-paint background work, WinForms/GDI canvas and
pre-completion logical-control update were deliberately not adopted.

### 16.4 Historical adaptive-admission A/B (superseded)

The following table preserves the former four-child A/B that selected the
adaptive gate. It is historical evidence, not the current production policy.
The benchmark no longer reconstructs that removed fallback; it now runs only
the production input-kind path. The same temporary 24-page fixture alternated
900 x 1,350 and
2,400 x 3,600 detailed JPEGs, with a 1,200 x 800 viewport and five-unit,
256 MiB cache.  Input is fake/offscreen `QWheelEvent`, never native input.

Work columns are `jobs / queued callbacks / file reads / decode attempts`.
Latency is final input to explicit offscreen paint.

| ZIP scenario | A work | B work | A paint ms | B paint ms |
|---|---:|---:|---:|---:|
| Ready hit | 1 / 1 / 0 / 0 | 1 / 1 / 0 / 0 | 1.842 | 2.029 |
| 10-page forward | 12 / 12 / 12 / 12 | 12 / 12 / 12 / 12 | 1.375 | 1.342 |
| Rapid wheel 12, 0 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 17.145 | 18.226 |
| Rapid wheel 12, 4 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 15.680 | 20.034 |
| Rapid wheel 12, 8 ms | 7 / 7 / 6 / 7 | **4 / 4 / 3 / 4** | 17.368 | 22.615 |
| Direction reversal | 4 / 4 / 4 / 4 | 4 / 4 / 3 / 4 | 84.443 | **53.056** |
| Two-page roundtrip | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.120 | 0.891 |
| Outside-cache return | 15 / 15 / 14 / 14 | 15 / 15 / 14 / 14 | 50.517 | 49.852 |
| Mixed direction changes | 8 / 8 / 8 / 8 | 8 / 8 / 8 / 8 | 1.486 | 1.216 |

| Folder scenario | A work | B work | A paint ms | B paint ms |
|---|---:|---:|---:|---:|
| Ready hit | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.311 | 1.319 |
| 10-page forward | 12 / 12 / 12 / 12 | 12 / 12 / 12 / 12 | 1.506 | 1.501 |
| Rapid wheel 12, 0 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 30.712 | **27.957** |
| Rapid wheel 12, 4 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 26.510 | 28.227 |
| Rapid wheel 12, 8 ms | 7 / 7 / 7 / 7 | **4 / 4 / 4 / 4** | 50.659 | **33.107** |
| Direction reversal | 4 / 4 / 4 / 4 | 4 / 4 / 4 / 4 | 85.742 | 84.163 |
| Two-page roundtrip | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.949 | 0.967 |
| Outside-cache return | 15 / 15 / 14 / 14 | 15 / 15 / 14 / 14 | 50.843 | 50.078 |
| Mixed direction changes | 8 / 8 / 8 / 8 | 8 / 8 / 8 / 8 | 1.851 | 1.778 |

The ready critical path creates zero jobs and decodes on both sides.  The one
ZIP ready-scenario job above is post-paint neighbor prefetch, outside
input-to-commit.  Forward-10 has nine ready hits and one miss.  The paced 8 ms
case removes 43% of ZIP and Folder job/callback/decode work in this large
mixed fixture.  ZIP pays 5.247 ms of final wait in this offscreen run, while
Folder improves by 17.552 ms because avoided native decode contention exceeds
the admission margin.  With a smaller quick fixture the work reduction was
69% (ZIP) and 71% (Folder).  The result is deliberately reported as a
source/backend-dependent trade, not a universal latency win.

| Aggregate | ZIP A | ZIP B | Folder A | Folder B |
|---|---:|---:|---:|---:|
| Jobs / callbacks | 53 / 53 | **50 / 50** | 52 / 52 | **49 / 49** |
| Reads / decode attempts (successes) | 50 / 51 (47) | **46 / 48 (46)** | 51 / 51 (51) | **48 / 48 (48)** |
| Stale / cancel | 3 / 5 | **1 / 2** | 4 / 5 | **2 / 2** |
| Cache hits / misses / evictions | 38 / 44 / 26 | 38 / 44 / 26 | 38 / 43 / 26 | 38 / 43 / 26 |
| Working-set delta MiB | +17.590 | +17.672 | +20.004 | +21.020 |
| Process-peak growth MiB | 34.914 | 35.000 | 35.297 | 36.434 |
| Shutdown ms | 2.197 | 2.041 | 1.875 | 1.787 |

All four children reported a completed coordinator, no remaining runtime job
and no residual benchmark process.  Timings use `perf_counter_ns`; the JSON
also records input-to-request, request-to-cache decision, request-to-decode,
decode-to-commit and commit-to-paint min/median/p95/max values.  Windows file
cache is hot, QPixmap/native allocations are not fully observable, explicit
offscreen render is not DWM presentation, and no real-device improvement is
claimed from these values alone.

### 16.5 Validation and remaining limits

Syntax/import checks passed for every changed Python file.  The runtime,
magnifier and ZIP/Folder integration group passed 59/59 in one independent
offscreen process.  Related files passed independently: ViewerWindow 23,
ViewerWidget 38, PresentationState 5, virtual PageList runtime 4, metadata
integration 19, BookSession 16, ImageSource 25 and PageModel 12.  The 57-test
Sprint 18 navigation file was covered by five independent functional groups
(core, slider, fullscreen chrome, canvas and settings), all passing.

One broad combined process aborted after 113 passes, and the whole Sprint 18
file also aborted after 40 tests, while a Pdfium service thread or a raster Qt
worker still existed during Python/Qt garbage collection.  The same nodes pass
when split into clean processes; therefore this is recorded as a suite-process
lifetime risk rather than hidden or attributed to an assertion failure.  No
test/benchmark process remained afterward.  The complete suite was not rerun
for this navigation-only change.

The benchmark uses `ZIP_STORED`, a hot OS file cache, explicit offscreen
`QWidget.render`, and no visible PageList.  It does not include native wheel
delivery, DWM/GPU presentation, disk-cold I/O or a real user's simultaneous
PageList/chrome activity.  Physical-device confirmation remains required.

### 16.6 Input-kind admission replacement (2026-08-20)

The production policy above was checked with the same quick temporary fixture
before and after the replacement. The earlier side is the immediately preceding
adaptive production path saved before editing; it is not the historical 6 ms
benchmark-only fallback. The current runner uses two fresh child processes
(ZIP and Folder) and its `--minimal` mode executes only cold single wheel,
ready hit, 12 packets at 8 ms and reversal. Synthetic wheel events carry device
timestamps; no native input or visible application was used.

| Source / scenario | Before request dispatch ms | After request dispatch ms | Before / after jobs-decodes | Before paint ms | After paint ms |
|---|---:|---:|---:|---:|---:|
| ZIP cold leading wheel | 5.875 | **0.200** | N/A / 1-1 before commit | N/A | 8.985 |
| ZIP ready hit | 0.295 | 0.390 | 0-0 / 0-0 before commit | 0.916 | 1.107 |
| ZIP rapid wheel, 12 x 8 ms | 14.904 | **10.596** | 4-4 / 4-4 total | 17.927 | **13.702** |
| ZIP reversal | 9.582 | **6.621** | 4-4 / 4-4 total | 17.140 | **14.399** |
| Folder cold leading wheel | 6.125 | **0.200** | N/A / 1-1 before commit | N/A | 13.752 |
| Folder ready hit | 0.347 | **0.256** | 0-0 / 0-0 before commit | 0.995 | **0.848** |
| Folder rapid wheel, 12 x 8 ms | 13.339 | **10.344** | 4-4 / 4-4 total | **18.738** | 20.648 |
| Folder reversal | 8.536 | **6.502** | 4-4 / 4-4 total | 19.286 | **19.232** |

Ready hit creates no critical-path job, decode or QPixmap upload on either
side; each scenario's one total job is neighbor prefetch released after paint.
The new policy removes about 5.7-5.9 ms of artificial admission from the first
cold wheel and 2.7-4.3 ms from final rapid/reversal dispatch while preserving
the preceding adaptive path's four-job rapid work bound. ZIP final paint also
improves in this run. Ready-hit critical work is unchanged; its sub-millisecond
movement is test-process noise. Folder rapid final paint is about 1.9 ms slower
in this sample even though dispatch is earlier, so no physical-device
improvement is inferred for that backend from one offscreen run.

The measured values use small 320 x 480 / 900 x 1,350 JPEGs, hot Windows file
cache, `perf_counter_ns`, explicit offscreen `QWidget.render`, five retained
units and a 64 MiB budget. Post-paint neighbor work is included in total jobs;
the cold single critical path itself is exactly one job/decode. These figures
select the implementation and guard against regression, but real wheel cadence,
disk-cold I/O and DWM presentation still require physical-device confirmation.

After the final input-routing and 64-bit timestamp fixes, syntax/import checks
passed and five independent offscreen pytest processes passed 17 + 2 + 6 + 44
+ 2 = 71 focused tests. They cover policy/core, production admission and
atomic old-frame behavior, ZIP/Folder ordered-runtime contracts, canvas/slider/
fullscreen routing, and book-switch lifetime. The production-only minimal
benchmark completed both fresh children, reported drained coordinators and no
remaining runtime task.

## 17. Large-raster preview/full tier replacement (2026-08-21)

This change addresses a physical-pixel path that becomes dominant when a
JPEG's short side reaches roughly 4,000-5,000 pixels.  That observed range is
**not a threshold in NivisViewer**: no branch compares either dimension with
4,000 or 5,000.  The audit did find a separate feature-gated cliff: requests
with the conditions below switched from decoder-sized preview to full-raster
work.  Pure standard fit was already decoder-scaled, so the offscreen evidence
does not by itself attribute the user's standard-fit slowdown to this
boundary.  A 5,000 x 7,500 32-bit surface alone is about 143 MiB, so decode,
orientation, adjustment, rotation, PIL-to-QImage materialization and a later
scale can multiply memory traffic even when the compressed ZIP entry is small.

### 17.1 Exact former production boundary

Before this replacement, decoder-sized JPEG was used only by the standard
`fit_window` / `fit_no_upscale` case with application rotation 0, no wide-page
split, no magnifier selection or active lens, and default
brightness/contrast/gamma.  The following conditions requested a full source:

- `fit_width`, `fit_height`, actual-size or manual zoom;
- a resampling mode other than `standard`;
- application rotation 90, 180 or 270 degrees;
- wide-page splitting;
- magnifier selection or an active magnifier;
- a non-default brightness, contrast or gamma adjustment; or
- failure/null output from the scaled decoder path.

The full Folder path decoded with Pillow, applied EXIF transpose, detached an
additional full-size copy, converted the whole raster to QImage, and then let
the render stage rotate and reduce it.  The ZIP generic path additionally
materialized the expanded entry before decode.  Rotation happened before the
final target scale, and adjustment could create more than one full PIL
intermediate.  Thus compressed byte size was not a reliable predictor of the
cost seen during page turns.

### 17.2 What ZipPlaFork does—and does not do

The comparison remains fixed to
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  Its relevant source is licensed
AGPL-3.0-or-later; the retained license and notices documented in section 1
continue to apply.

The inspected `source/ZipPla/ViewerForm.cs` page worker,
`source/ZipPla/ImageLoader.cs` decode/filter helpers and
`source/ZipPla/GenerarClasses.cs` work-order code do **not** provide a
decoder-sized JPEG path.  ZipPlaFork normally obtains an entry stream, creates
a full `Bitmap`, performs orientation/filter/rotation work on full source
pixels, and only then creates the resized display artifact.  Its light
navigation therefore comes from simple ownership and work order, not from
avoiding the original raster.

The following boundary is intentional:

| Reference behavior | Decision in NivisViewer |
|---|---|
| One Viewer worker and one current-centered replaceable order | **Adopted / retained.** `RasterBookRuntime` has one active unit job and re-evaluates current before directional neighbors. |
| A completed resized artifact is a direct display hit | **Adopted / retained.** A ready frame bypasses worker, decode and QPixmap upload. |
| Decode a full GDI `Bitmap` as the normal source for every page | **Not adopted.** This would reproduce the large-raster memory traffic this change removes. |
| WinForms/GDI page publication and control timing | **Not adopted.** Nivis atomic complete-frame commit, old-frame retention and `ViewerPresentationState` remain stronger. |
| WinForms-era DPI assumptions | **Not adopted.** Qt physical targets, fractional DPR and stale-DPR rejection remain authoritative. |

No C# statement was copied literally for the scaled-decode work below.  The
AGPL-derived parts remain the previously recorded one-worker/current-first and
completed-artifact-hit processing principles.  The preview/full split is a
NivisViewer modern design.

### 17.3 New physical-pixel contract

The raster job still owns decode through display-ready frame generation, but
the source request is now based on the layout that will consume it:

| Boundary | New contract |
|---|---|
| Preview target | Fit mode, logical display unit, per-page spread/split slot, physical viewport, DPR and application rotation determine the exact final physical-pixel target. Rotation 90/270 maps that target back to decoder axes. Decoder headroom is uniformly 1.0: the retained JPEG source is the smallest decoder-native tier that is still at least as large as the final target, not an arbitrary algorithm-specific intermediate. |
| ZIP JPEG | The ordinary/two-axis selected entry is read once into one reserved `QByteArray`; a seekable `QBuffer` and `QImageReader` request the smallest native JPEG reduction tier sufficient for the physical target. Unknown one-axis prefetch is the deliberate exception: it first reads only the JPEG header to validate allocation. A decoder that ignores the tier and returns more than twice the required edge is contained before that oversized raster enters the preview store. |
| Folder JPEG | Pillow uses the same 1/2, 1/4 or 1/8 native `draft` tier rule. Orientation and optional adjustment operate on that sufficient source; the redundant post-`exif_transpose` full-size copy is removed. The configured renderer then owns the one exact final layout scale. |
| Transform order | Preview-eligible fit requests perform at most one decoder-native integer reduction, then application rotation/crop/adjustment and exactly one configured resize to the exact DPR-aware physical target. The former arbitrary decoder-sized resize followed by another render resize is removed. Full decode is reserved for actual-size, manual zoom, magnifier-source promotion and nearest-neighbor semantics, or a decoder/format fallback that requires original pixels. |
| Source cache | Preview and full are distinct tiers and may coexist for the same page. Normal fit selects the smallest sufficient preview; a full promotion no longer destroys the useful fit preview or an already completed QPixmap. |
| Magnifier | Original-pixel promotion uses a current-only order. It does not start full-resolution next/previous prefetch. Cancelling the lens synchronously adopts the normal preview key, stales/cancels an incompatible full job, and returns through the retained frame without clearing either cache tier. |
| Admission / eviction | Admission uses decoded-source bytes plus frame bytes, not compressed entry length. Folder estimates its retained native JPEG reduction tier rather than the smaller exact target. Lazy one-axis fit uses a bounded provisional estimate to enter the worker, then a worker-only JPEG header probe checks the complete display unit's missing retained sources plus layout frame bytes before allocating pixels; over-budget/probe-failed prefetch is suppressed for that work order. Current preview, visible spread partner and last-painted frame are protected; optional full sources are preferred eviction candidates when normal-fit work needs room. |
| Publication | Only a complete frame reaches the GUI. QPixmap creation, atomic commit, serial/epoch/layout/DPR stale fences, presentation projection and paint acknowledgement are unchanged. |

This is deliberately a Hybrid/New-design decision: it keeps ZipPlaFork's
compact current-first worker discipline, improves on its full-source pixel
cost, and keeps NivisViewer's high-DPI, spread/RTL, magnifier, atomic
presentation and virtual PageList UX boundaries.

### 17.4 Historical standard-fit production A/B

This run predates both the final independent resampling authority in section
17.8 and the Startup Ready Runway in section 20.  A and B ran in fresh
offscreen child processes with the same alternating
1,200 x 1,800 / 4,500 x 7,000 solid-JPEG fixture, 1,200 x 800 viewport, five
retained units and 256 MiB budget.  A is the immediately preceding production
implementation; B is the production implementation after this replacement.
Latency is final synthetic input to explicit offscreen paint.  Scenario totals
include post-paint neighbor prefetch; the `critical` column stops at the final
frame commit.

| Source / scenario | Input -> paint ms, A -> B | Critical jobs / decode attempts, A -> B | Scenario jobs / callbacks, A -> B |
|---|---:|---:|---:|
| ZIP cold wheel | 12.692 -> 13.132 | 1 / 1 -> 1 / 1 | 3 / 3 -> 3 / 3 |
| ZIP ready hit | 1.240 -> 1.361 | 0 / 0 -> 0 / 0 | 1 / 1 -> 1 / 1 |
| ZIP rapid wheel, 12 x 8 ms | 15.580 -> 15.707 | 2 / 2 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| ZIP direction reversal | 17.591 -> 17.214 | 2 / 1 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| Folder cold wheel | 18.903 -> 15.893 | 1 / 1 -> 1 / 1 | 3 / 3 -> 3 / 3 |
| Folder ready hit | 1.450 -> 1.282 | 0 / 0 -> 0 / 0 | 1 / 1 -> 1 / 1 |
| Folder rapid wheel, 12 x 8 ms | 25.273 -> 20.341 | 2 / 2 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| Folder direction reversal | 22.404 -> 20.045 | 2 / 1 -> 2 / 2 | 4 / 4 -> 4 / 4 |

The standard ZIP path was decoder-scaled before this work and remains so:
large output is 480 x 746 and small output 497 x 746 on both sides.  B's
sub-millisecond ZIP movements are mixed/noise-sized and are not presented as
an improvement.  Folder A produced the same exact-size outputs.  Folder B uses
the native JPEG draft tier and therefore retains 563 x 875 for the large page
and 600 x 900 for the small page before final layout scaling.  It is faster in
all four sampled Folder scenarios, but its deliberately larger reusable source
also makes the aggregate memory result worse in this run.

| Aggregate | ZIP A | ZIP B | Folder A | Folder B |
|---|---:|---:|---:|---:|
| Jobs / queued callbacks | 12 / 12 | 12 / 12 | 12 / 12 | 12 / 12 |
| Reads / decode attempts (successful) | 11 / 11 (11) | 11 / 12 (11) | 11 / 11 (11) | 12 / 12 (12) |
| Stale results / cancel requests | 1 / 2 | 1 / 2 | 1 / 2 | 1 / 2 |
| Working-set delta MiB | 5.969 | 5.766 | 9.141 | 10.223 |
| Process-peak growth MiB | 13.266 | 14.488 | 20.141 | 24.051 |

The extra ZIP attempt is one cancelled attempt without another completed
entry read; the extra Folder attempt/read comes from scheduling around the
reversal.  Neither side duplicates a successful decode of the same active
target.  A ready hit still creates zero critical-path jobs and zero decodes;
in that measured predecessor, the one scenario-total job was neighbor prefetch
released only after paint.  That paint-gated scheduler is not the current
contract.

### 17.5 Historical former full-transform A/B

This comparison predates the final independent resampling authority in section
17.8.  The standard-fit fixture above could not demonstrate the then-current
main improvement because that path was already reduced.  A separate
isolated-worker A/B therefore used one 4,500 x 7,000 ZIP JPEG, rotation 90
degrees, brightness 1.2, the legacy `high_quality` mode and a 1,200 x 800
viewport.  A reconstructed the former full-source transform demand; B used the
intermediate layout-aware preview with 2.0 quality headroom.  Both generated
the same 1,200 x 771 display frame.

| Metric | A: former full transform | B: intermediate preview transform |
|---|---:|---:|
| Decoded source dimensions | 4,500 x 7,000 | 1,543 x 2,400 |
| Cold runtime completion ms | 507.020 | 95.235 |
| Decoded-source cache bytes | 94,500,000 | 11,116,800 |
| Total source + frame cache bytes | 98,200,800 | 14,817,600 |
| Working-set delta MiB | 97.273 | 15.664 |
| Sampled peak growth MiB | 423.066 | 54.781 |
| Jobs / decodes / reads / QPixmap creations | 1 / 1 / 1 / 1 | 1 / 1 / 1 / 1 |

The reduction came from doing less pixel work, not from skipping a job or
changing presentation semantics.  At that stage the preview was sized by the
2.0 high-quality headroom policy for the rotated output, while avoiding the
94.5 MB source and its full-sized transform intermediates in the normal fit
path.  The final production contract no longer uses that algorithm-specific
headroom; section 17.8 records the sufficient native JPEG tier plus one exact
configured final resize.  These values therefore remain historical evidence,
not a benchmark of the final filter authority.

### 17.6 Preview/full layout reuse A/B

This second comparison is a **current-runtime adapter comparison**, not a
checkout or executable snapshot of an old production binary.  Each side uses
a separate current `ZipRasterBookRuntime` process.  A calls
`cancel(clear_artifacts=True)` before each layout step to emulate the former
clear-and-redecode ownership.  B calls `invalidate_layout()` so frame artifacts
change while sufficient preview/full decoded sources remain owned by the book.
The sequence covers initial preview, smaller resize, full promotion, DPI/manual
zoom and magnifier-equivalent demand.

| Metric | A: clear source and frame | B: retain preview/full source tiers |
|---|---:|---:|
| Decode attempts / entry reads | 5 / 5 | 2 / 2 |
| Completed entry bytes | 2,473,465 | 989,386 |
| Sequence elapsed ms | 564.547 | 340.933 |
| Working-set delta MiB | 327.656 | 146.918 |

B decodes one sufficient preview and promotes once to full; later layout and
magnifier-equivalent requests reuse the matching tier.  The adapter nature of
A means these figures demonstrate the ownership/cache contract rather than an
exact historical end-user latency comparison.

### 17.7 Related validation and remaining limits

The focused runtime, image-source, Viewer integration, navigation,
presentation, magnifier, Folder/ZIP, layout and shutdown groups passed
**249 related tests** in independent offscreen processes.  The benchmark
children reported drained runtime/coordinator shutdown, and no test or
benchmark process remained afterward.  This was related-scope validation, not
a claim that every repository test ran in one process.

The fixtures use solid JPEG, a hot Windows file cache, synthetic input,
offscreen Qt and explicit `QWidget.render`; they do not exercise disk-cold I/O,
native wheel/key delivery, DWM/GPU presentation, or the exact compression and
pixel detail of the user's books.  Pillow native draft deliberately retains a
coarser decoder tier than the exact ZIP scaler, so the standard Folder memory
aggregate can rise even when transform latency falls.  QPixmap/native backing
allocations and transient decoder peaks are sampled rather than exhaustively
observable.  Actual-size, manual zoom, magnifier and nearest-neighbor mode still require
full pixels, and a protected current page may temporarily exceed the budget.
An unindexed `fit_width`/`fit_height` prefetch performs one extra header-only
entry/file access before decode so an extreme aspect cannot allocate beyond
the free prefetch budget.  The current page is intentionally not declined and
can still soft-overflow the retention budget when displaying an unusually long
page; physical-device checks must therefore include that case.

Accordingly, no physical-device improvement is inferred solely from these
offscreen timings.  The acceptance check remains repeated turns, reversal and
rapid wheel input on the user's 4,000-5,000-pixel and larger ZIP/Folder books,
including rotation, brightness/sharp or Lanczos rendering, magnifier enter/leave,
spread, DPR change and return to a previously completed normal-fit frame.

### 17.8 Final independent resampling authority

This subsection supersedes the older combined-mode and quality-headroom
wording elsewhere in section 17.  The production authority is now four
independent settings rather than one label that ambiguously controls both
reduction and enlargement:

| Surface | Reduction authority | Enlargement authority | Defaults |
|---|---|---|---|
| Normal Viewer | `viewer_downscale_algorithm`: `auto`, `fast`, `smooth`, `sharp`, `area`, `nearest` | `viewer_upscale_algorithm`: `auto`, `bilinear`, `bicubic`, `lanczos`, `nearest` | `auto` / `auto` |
| Magnifier | `magnifier_downscale_algorithm`: the same reduction set | `magnifier_upscale_algorithm`: the same enlargement set | `sharp` / `lanczos` |

`ResamplingPolicy` in `app/viewer_render.py` is the value contract.  Explicit
algorithms are part of `ViewerRenderKey` and `ZipRasterRenderSpec` equality, so
a cache hit can never silently reuse output produced by another filter.
`ViewerWindow` and `ViewerWidget` pass the normal pair to complete display-unit
rendering and the magnifier pair to lens rendering.  Equal-size output takes a
copy without a resampling pass.  A real size change uses one native Pillow
filter: `BOX`, `BILINEAR`, `BICUBIC`, `LANCZOS` or `NEAREST`; `auto` chooses
BOX for a reduction of at least one half, Lanczos for a moderate reduction and
bicubic for enlargement.  The compatibility-only legacy standard caller may
still use Qt's native `QImage.scaled` path.  No Python pixel loop, custom SIMD
resizer, new native library or GPU dependency was introduced: this remains a
Qt/Pillow-only design.

The final frame size is no longer clamped to a decoder result that is smaller
than the requested target.  The old sequence could accept such a source,
produce a target-underfilled frame and then let QPainter/device DPR enlarge it;
another path requested an arbitrary exact decoder size and subsequently ran
the selected resize filter again.  `jpeg_native_reduction_size`, the ZIP/Folder
JPEG readers, `RasterBookRuntime._contain_preview_source`, uniform decoder
headroom 1.0 and exact physical render targets now establish one rule: retain
the smallest native JPEG tier sufficient for the DPR-aware target, then run
exactly one deliberate final resize.  This keeps the selected filter
authoritative and prevents hidden double scaling without forcing normal fit to
retain the original raster.  A fresh decoder result is checked against the
same source-sufficiency postcondition used by the cache; if a backend returns
even one axis below the required size, that preview is not admitted and the
job promotes once to the full source.

The GUI creates one `QPixmap` from the exact display `QImage`, assigns the
request DPR and uses point-form painting for both normal frames and the
magnifier whenever the natural device-independent size matches the layout.
This removes the former destination-rectangle resample caused by fractional
DPR rounding.  The explicit exception is a `manual_zoom` artifact above
64 MiPixels: until tiled zoom rendering exists, it is proportionally bounded
and may be enlarged by QPainter to avoid a single multi-gigabyte allocation.
Ordinary fit and actual-size frames do not use that safety cap.

`ConfigManager._migrate_legacy_resampling_settings` performs a one-way
translation from `viewer_resampling_mode`, `magnifier_resampling_mode` and
`smooth_scaling`.  If both new keys for a surface already exist they win;
otherwise the legacy pair is translated once.  The legacy keys are then
removed during load/normalization/update and are never saved again.  Old API
setters expose only a compatibility view over the new settings, so there are
not two writable authorities.

The performance provenance remains ZipPlaFork fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later:

- `source/ZipPla/ViewerForm.cs`, `bmwLoadEachPage_DoWork`
  (`:3177-3550`) and its scaling selection (`:3405-3449`), plus
  `GetResizedSize` (`:5273`), informed the structural rule that one page job
  owns decode/filter/resize through a complete display artifact;
- `source/ZipPla/ViewerForm.cs`, `SetNewResizedImage` (`:5451-5468`), and
  the `PreFilteredImageArray` / `ResizedImageArray` fields informed retained
  source versus completed-output reuse; and
- `source/ZipPla/ImageLoader.cs`, `GetJpegOrientation` / `LoadRotateBitmap`
  (`:382-400`) and `GetFullBitmap(Stream)` (`:559-689`), together with
  `source/ZipPla/BitmapResizer.cs`, were evaluated for its decode/orientation/
  resize order.

The independent normal/magnifier reduction/enlargement policy, fractional-DPR
target, native JPEG tier selection, exact one-final-resize rule, immutable Qt
cache identity and one-way settings migration are NivisViewer modernizations,
not copied WinForms/GDI code.  ZipPlaFork's full-GDI-source policy and its
legacy resizer choices were deliberately not adopted.  The retained license
and copyright files at `licenses/ZipPlaFork/AGPL.txt` and
`licenses/ZipPlaFork/About.txt`, section 1 and `THIRD_PARTY_NOTICES.md` cover
the AGPL-derived structural principles.

### 17.9 User-facing resampling authority

The four production settings above were initially reachable from the
Viewer's View menu but were absent from `SettingsDialog`.  Consequently a user
looking in the persistent settings UI could not discover or change the
implemented algorithms.  `SettingsDialog._build_viewer_tab`,
`load_current_values` and `values` now expose the same four ConfigManager keys
under **画像の拡大縮小**, split into **通常表示** and **拡大鏡**, each with an
independent **縮小方式** and **拡大方式**.  Both the persistent dialog and the
existing Viewer radio menus use the shared label dictionaries in
`app/viewer_render.py`; unsupported or placeholder choices therefore cannot
drift into either UI.

| UI value | Downscale backend | Upscale backend |
|---|---|---|
| 自動 | BOX at 0.5 scale or below; otherwise Lanczos | Bicubic |
| 高速 | Pillow bilinear | — |
| 滑らか | Pillow bicubic | — |
| シャープ | Pillow Lanczos | — |
| 面積平均 | Pillow BOX | — |
| バイリニア | — | Pillow bilinear |
| バイキュービック | — | Pillow bicubic |
| Lanczos | — | Pillow Lanczos |
| 最近傍 | Pillow nearest | Pillow nearest |

Applying the dialog writes only the four modern keys.  `ConfigManager` emits
the normal settings to every open `ViewerWindow`; the window invalidates only
layout-dependent frame artifacts, keeps the runtime's decoded source store,
and immediately requests the current complete display unit with the new
algorithm identity.  Neighbor frames are regenerated by the existing
book-scoped warm-up order after the current frame.  Magnifier-only changes do
not invalidate normal frames: an active lens retains its old complete pixmap
until its new generation finishes, using the retained magnifier source.  The
frame identities continue to include purpose (normal/magnifier), both
algorithm directions, physical target, DPR, rotation and adjustments; the
decoded-source identity intentionally does not include resampling settings.

Representative offscreen fixtures cover fine black lines/text, photographic
gradients, screenshot-like one-pixel UI edges and pixel art.  They verify that
the supported explicit native filters produce distinct output families, while
also recording the intentional `auto` aliases (large reduction to BOX and
enlargement to bicubic).  Folder runtime regression varies the algorithms
between layout generations and confirms that the already sufficient decoded
source is reused rather than read or decoded again.

This UI completion adopts ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`'s independent
`StandardScalingAlgorithm` / `MagnifierScalingAlgorithm` and `ScaleDown` /
`ScaleUp` authority concept.  The relevant provenance remains
`source/ZipPla/ViewerForm.cs`: the two `ScalingAlgorithmPair` fields
(`:282-283`), `bmwLoadEachPage_DoWork` normal selection (`:3405-3449`),
magnifier selection (`:7803-7807`), configuration load/save
(`:5826-5829`, `:5958-5978`), `setStandardScalingAlgorithm`
(`:14574-14600`) and `setMagnifierScalingAlgorithm` (`:14772-14784`), under
AGPL-3.0-or-later.  NivisViewer does not copy ZipPlaFork's large legacy
Lanczos/Spline matrix: the modernized UI publishes only the Qt/Pillow-native
backends already used by production, preserves high-DPI physical targets and
adds live frame-only regeneration.

## 18. ZIP initial warm-up and automatic memory budget (2026-08-21)

This change addresses a different boundary from section 17. The current-page
preview was already fast and completed artifacts were retained correctly, but
an untouched newly opened ZIP did not populate beyond its tiny submitted
frontier. Increasing a nominal memory value therefore could not prepare the
same display-ready pages that became fast after manual navigation.

### 18.1 Production diagnosis before the replacement

The settings trace found two independent limits. The old checkout did not have
the described discrete Viewer-memory selector: prefetch presets supplied a
free-form MiB value, unknown values such as `auto` normalized to 256 MiB, and
normalization capped integers at 4,096 MiB. Supported integer values did reach
`ImageCache`, `BookSession` and an existing ZIP/Folder runtime, but
`BookSession` also copied the legacy `ImageCache.cache_size`—normally ten—into
the runtime unit ceiling. More importantly, `ViewerWindow._zip_runtime_request`
submitted only current plus one neighbor on each side. A byte budget cannot
start work that is absent from the order.

The following pre-change production probe used a temporary 24-page ZIP of
alternating 4,500 x 7,000 and 1,200 x 1,800 solid JPEGs, standard fit, a
1,200 x 746 physical Viewer, hidden PageList, offscreen Qt and an explicit
first paint. It then supplied no user input. `auto` here means the former token
that fell back to 256 MiB, not the resolver introduced below.

| Requested setting | Runtime budget | Immediately after paint | 1 second | 3 seconds | 5 seconds |
|---|---:|---|---|---|---|
| former `auto` | 256 MiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |
| fixed 256 MiB | 256 MiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |
| fixed 4 GiB | 4 GiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |

At one second the 5.763 MiB combined total was 2.780 MiB of display frames and
2.983 MiB of decoded sources. No source-only page existed: the one neighbor
that was requested did become display-ready. The initial work order was only
`[[0], [1]]`; 4 GiB still had about 4,090 MiB free. The runtime was idle,
paint release was complete, Browser/PageList pause was false, and admission had
not stopped. The three jobs and three callbacks included the initial
spec/layout transition. Thus first-paint resumption and cost admission were
healthy for the submitted frontier.

The user hypotheses classify as follows:

| Hypothesis | Result |
|---|---|
| A. budget propagation | **Partly confirmed.** Ordinary legacy integers propagated, but there was no true `auto`, no 8–32 GiB contract and no memory-mode UI independent of prefetch counts. |
| B. wide retention but narrow initial population | **Confirmed.** The submitted work order ended after the immediate neighbor regardless of free bytes. |
| C. first-paint resume failure | **Rejected by this probe.** The neighbor started after paint and all pause/gate state returned to idle. |
| D. admission/cost or fixed-count limit | **Partly confirmed.** Admission was not overestimating this fixture, but the inherited ten-unit safety ceiling would later waste a large budget. |
| E. background decode merely cannot keep up | **Not the stopping cause.** The worker had no remaining submitted work at one second. |

### 18.2 Fixed ZipPlaFork reference and adaptation boundary

The source comparison remains fixed to
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later. The retained license text, copyright notices and provenance
in section 1 continue to apply.

At that revision, the memory selector in `source/ZipPla/SettingForm.cs` uses
index 0 for Minimum, indices 1 through 8 for `(128 MiB << index)`—256 MiB
through 32 GiB—and `ulong.MaxValue` for Automatic. `ViewerForm.GetUserMemoryUBound`
caps user memory by process/address-space and physical-memory constraints.
`ViewerForm.SetMemoryUBound` combines process working set and available physical
memory with an active coefficient of 0.7 and an inactive coefficient of 0.3.
`SetBackgroundMode` and `BackgroundMultiWorker.SetWorksOrder` give the one
Viewer worker an all-page order: current fixed numerical band, next numerical
band, previous numerical band, then the remaining pages. `ReduceUsingMemory` evicts original and
resized ownership together from the low-priority end only when memory pressure
requires it, while keeping the minimum visible neighborhood.

The following is the exact adaptation boundary:

| Fixed-revision source / method | Adopted principle | NivisViewer implementation |
|---|---|---|
| `SettingForm.cs`, memory selector; `ViewerForm.GetUserMemoryUBound` / `SetMemoryUBound` | A user memory mode must resolve to the same real budget that controls background population | `app/viewer_memory_policy.py`; `ConfigManager.viewer_memory_mode`; `ViewerWindow._load_prefetch_settings` |
| `ViewerForm.SetBackgroundMode`, `priorityLevel`; `GenerarClasses.BackgroundMultiWorker.SetWorksOrder` | Keep a book-wide current-centered replaceable order instead of a three-unit submitted frontier | `ViewerWindow._zip_runtime_request`; `RasterBookRuntime._adopt_request`, `_drive` |
| `ViewerForm.ReduceUsingMemory` | Let memory pressure, current priority and completed-artifact usefulness decide the stopping/eviction point | combined source/frame ledger, `set_cache_limits`, prefetch admission and work-order-aware stores |

The WinForms control shape, `ulong.MaxValue` sentinel, continuously variable
limit, GDI ownership and exact active/inactive formula were not copied.
NivisViewer instead uses a stable modern Windows snapshot and listed buckets.
No C# statement was translated literally in this change; the all-book
memory-driven work-order structure is recorded as AGPL-derived.

### 18.3 First Hybrid replacement contract (historical predecessor)

This subsection and its probe record the first all-book replacement.  Sections
19--20 supersede its post-paint release, materialized finite order and
book-length safety ceiling with a count-free lazy plan, the commit-released
Startup Ready Runway and seamless book-wide continuation.  The memory-mode
resolution and combined-byte principles below remain applicable.

`viewer_memory_mode` is now an independent saved string with `minimal`
(128 MiB), fixed 256/512 MiB and 1/2/4/8/16/32 GiB modes, plus `auto`. Fixed
modes resolve byte-exactly: 256 MiB is 268,435,456 bytes and 4 GiB is
4,294,967,296 bytes. A legacy prefetch memory value migrates once to the
nearest listed bucket. The Raster ZIP and Folder runtimes use the new byte
budget; the old custom image-unit counts remain only for PDF, RAR/7z and other
unmigrated sources. `disabled` still disables Raster background warm-up, and
the direction-priority checkbox still controls whether travel direction or
numeric next is preferred.

Automatic mode reads total and available physical memory plus process working
set once when a ViewerWindow first resolves that mode, or when the user
explicitly changes back to it. It subtracts non-cache working set and an OS
reserve of the larger of 2 GiB or 20% of total RAM (bounded by total RAM), then
takes the smaller total-memory and available-memory bound. The result is
rounded down to one of the listed 128 MiB through 32 GiB buckets. Existing
cache bytes are added back once because they are already reflected in
`available`, avoiding double charge in the normal case. The available bound is
nevertheless `max(0, cache + available - reserve)`: when available memory is
below the reserve, that shortfall also reduces an existing cache instead of
freezing it as permanently protected. Snapshot failure falls back to a stable
512 MiB. Book switches do not resample fluctuating available memory; a mode
change is the explicit recalculation boundary.

After the current complete frame paints, `ViewerWindow` now submits a finite
book-wide display-unit order: current, the immediate preferred-direction unit,
the immediate opposite unit, and all remaining units by distance with the
preferred side winning ties. The runtime still has exactly one active unit
job, generates preview source plus layout-specific display-ready frame, and
checks each background key against combined byte admission. A key that cannot
fit is skipped for that replaceable request rather than ending the whole
frontier, so later small pages can still populate the remaining memory. The old
legacy ten-page cap is replaced by total book pages as a safety ceiling,
leaving the byte budget authoritative. Enlarging the budget clears
capacity-only skips and drives the already paint-released frontier immediately,
without a timer or a retry loop for broken/start failures.

A new request replaces the unstarted order immediately. A running background
job is retained only if it is still the highest-priority missing unit;
otherwise it is cooperatively cancelled and any queued obsolete result is
rejected before QPixmap creation or cache mutation. NivisViewer's stronger
contracts remain outside this borrowed scheduling principle: epoch/request and
layout/DPR stale fences, preview/full tiers, complete-spread atomic commit,
old-frame retention, synchronous ready hit, matching-paint acknowledgement,
high-DPI targets, current-only full-source requests for magnifier, actual size,
manual zoom and pixel mode, and a separately budgeted virtual PageList.

### 18.4 Post-change production probe

The same 24-page offscreen production probe was rerun after the replacement.
On this machine, saved mode `auto` resolved once to 34,359,738,368 bytes
(32,768 MiB) from the following snapshot: 134,909,497,344 total physical
bytes, 78,190,223,360 available bytes, 73,560,064 bytes of process working set
and a 26,981,899,468-byte reserve. Fixed 256 MiB and 4 GiB resolved to exactly
268,435,456 and 4,294,967,296 bytes. All three byte values reached the active
runtime unchanged, and its finite unit ceiling was the 24-page book size.

| Mode | Immediately after first paint | 1 second | 3 seconds | 5 seconds |
|---|---|---|---|---|
| auto / 32 GiB resolved | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |
| fixed 256 MiB | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |
| fixed 4 GiB | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |

At the first paint, frame and source stores each held 1,644,800 bytes, the
full work order was `[0]` through `[23]`, and one job/callback/read/decode had
completed. At one second, all 24 pages had display-ready frames, with
34,984,416 frame bytes and 35,196,896 source bytes: 70,181,312 bytes
(66.93 MiB) combined. There were no source-only pages, admission declines or
evictions. Totals were 25 jobs, 25 callbacks, 24 entry reads, 24 decodes and 25
QPixmap creations; the extra frame/job is the same initial layout/spec
transition observed in the baseline. The runtime then remained idle and
unpaused through five seconds.

This fixture fits below even the 256 MiB budget, so equal final residency is
expected and does not mean the settings collapsed to one value. The important
before/after difference is that the untouched book stopped at two ready pages
and 5.763 MiB before the replacement, but reached all 24 ready pages and
66.93 MiB after it. The byte limits themselves remained distinct and exact.

Warm navigation through the first twenty pages produced 19 hits and zero
misses, jobs, reads or decodes. A ten-page forward then five-page reverse
sequence produced 15 hits and again zero jobs, reads or decodes, ending on page
5. While background work was active, a request for page 17 cancelled one old
background attempt and rejected one stale result; no obsolete frame committed,
page 17 was the only new presentation commit, and the replacement order began
`17, 18, 16, 19, 15`. Closing while page 18 background work was active took
125.0 ms and drained the worker, queued callback and archive lifetime.

A forced low-budget check used 3,289,601 bytes, approximately current plus one
byte. It stabilized at 3,077,120 combined bytes with only page 0 ready, one
admission decline and no retry loop. Current display therefore remains allowed
under pressure while optional warm-up stops. The fixture uses solid JPEGs, a
hot temporary ZIP, synthetic/offscreen paint and no native input or DWM/GPU
presentation. These results establish population, priority and memory
contracts; they do not by themselves claim the user's physical-device page
turn latency.

### 18.5 Admission hardening, store scaling, and remaining boundaries

The post-probe safety follow-up extends exact worker-side admission beyond the
one-axis JPEG case. A lazy PNG, WebP or other non-JPEG background unit is
allowed through the GUI boundary only with a non-mutating capacity reservation.
Before allocating any pixel raster, the one runtime worker probes every unknown
member of that display unit, accounts for all missing retained sources at their
logical raster sizes, derives the complete layout-frame cost, and compares the
sum with current free bytes plus artifacts strictly below the candidate in the
latest retention order. ZIP probing uses the cancellable
sequential entry device and `QImageReader` header path; Folder probing uses its
source header boundary. Probe failure or an over-budget result declines only
that background key. `_drive` continues with later keys, so one exceptional
image cannot prevent smaller pages from filling otherwise usable memory. A
new request or larger budget retries capacity skips; broken entries and worker
start failures remain suppressed. An image that becomes current is not denied
by this optional-prefetch rule and retains the existing protected-current soft
overflow contract.

The reservation phase removes nothing. Only a successful result that still
matches the active request re-plans from its actual QImage sizes. It completes
every QPixmap upload first, then reclaims the required lower-rank source/frame
artifacts immediately before store insertion. Cancellation, stale completion,
upload failure and exact admission decline therefore preserve the old ready
cache; a successful page turn can still slide a full cache window toward the
new current page. The bounded transient is the single active worker result and
its GUI upload outside the retained-cache ledger.

Both source and frame stores now maintain aggregate bytes incrementally on
insert, replacement and removal. Their largest-artifact query uses a bounded
lazy heap ledger, while active work-order rank maps and a lazily built
worst-first candidate order avoid rescanning and sorting the full store for
every item removed during one pressure event. Protection remains store-aware:
current/required sources and current/last-painted frames are excluded from
their respective candidate orders. A live budget reduction first cancels a
non-current job carrying the obsolete larger admission snapshot, applies
byte-ranked eviction, then re-drives the latest order under the new limit. A
live increase clears only capacity declines and resumes the paint-released
order. An in-flight job adopts the larger allowance and is cancelled only when
a newly affordable, higher-priority skipped unit must overtake it.
These are NivisViewer scaling and lifetime changes; no additional ZipPlaFork
C# statement was copied for them.

The remaining boundaries are explicit:

| Residual | Current behavior and risk |
|---|---|
| Lazy-size wide/spread topology | A decoded preview/full source survives size discovery and can be reused. If the newly known aspect changes single, spread-partner or wide-split topology, however, the old layout key is not a complete frame for the new unit. A display-ready frame must be generated again, without another source decode when the retained tier is sufficient. |
| Very large books | Resolved in section 19: topology is built once per layout revision and navigation replaces only a lazy current-centered cursor. Physical-device responsiveness around 10,000 pages remains unverified, but the former per-input full-order materialization is gone. |
| PageList memory ownership | `ViewerPageListRuntime` remains deliberately separate with an explicit fixed 64 MiB QImage budget. It is neither derived from nor live-resized by `viewer_memory_mode`, so the main source+frame authority cannot silently change thumbnail retention. |

These residuals do not change the 24-page results in section 18.4. They limit
the claim to the measured ordinary book and establish the next optimization
boundaries rather than asserting constant-time ready hits for every book size
or topology transition.

## 19. Memory-driven raster cache ownership (2026-08-21 worktree)

This section records the cache-subsystem replacement visible in the current
worktree. It supersedes the count/frontier descriptions in sections 18.3 and
18.5 where they conflict. The policy, lazy planner, admission owner and
count-free source/frame stores are connected to the ZIP/Folder production
runtime and `ViewerWindow`; physical-device behavior remains explicitly
unverified.

### 19.1 Fixed-revision provenance and adopted boundary

The source reference remains
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.  Its retained
license and copyright material remain at `licenses/ZipPlaFork/AGPL.txt` and
`licenses/ZipPlaFork/About.txt`; the notices in section 1 and
`THIRD_PARTY_NOTICES.md` apply to this structural port.

The following table records the actual fixed-revision methods, not only the
general idea of a memory-aware cache:

| ZipPlaFork source / method | Fixed-revision behavior | Adopted NivisViewer principle |
|---|---|---|
| `source/ZipPla/ViewerForm.cs`, `GetUserMemoryUBound` (`:5442-5449`) | Caps the requested Viewer memory by physical RAM and process address-space constraints. | Resolve one explicit Viewer hard ceiling from the saved mode and machine snapshot rather than deriving residency from a page count. |
| `ViewerForm.SetMemoryUBound` (`:5424-5439`) | Recalculates a usable bound from process working set, available physical memory and active/inactive coefficients of 0.7/0.3. | Keep separate active/inactive population targets and react to physical-memory pressure, while using stable modern policy state rather than copying the exact formula. |
| `ViewerForm.SetNewResizedImage` (`:5451-5468`) | Recomputes the bound, makes room with `ReduceUsingMemory`, publishes a completed `VirtualBitmapEx` only if it can be admitted, otherwise disposes and requeues it. | Perform non-mutating admission before decode where possible and commit replacement/eviction only for a successful, still-relevant completed artifact. |
| `ViewerForm.ReduceUsingMemory` (`:5471-5524`) | Walks the low-priority tail of the latest work order, disposes resized and retained original ownership together, and uses a small count only as a minimum-neighborhood guarantee. | Evict from the least useful retention rank under byte pressure; current, spread partner and the last painted frame remain protected.  Page count is not a maximum-residency contract. |
| `ViewerForm.SetBackgroundMode` (`:5558-5582`) and `priorityLevel` (`:5586-5605`) | Builds an all-page order around current/next/previous/remaining bands, installs it, reduces memory, and runs the Viewer worker with thread count one. | Represent the whole book as current-centered work, re-prioritize it on navigation and run one active display-unit job. |
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`) and completion selection (`:266-303`) | Replaces the permutation while work is active; at a completion boundary it skips already-started entries and selects the first unfinished item from the newest order. | Replace unstarted background work around the latest current unit, while keeping NivisViewer's cooperative cancellation and request/epoch stale fences. |

This remains a direct AGPL-derived **structural** port, not a line-for-line C#
translation.  NivisViewer does not adopt `ulong.MaxValue` as an Auto sentinel,
GDI `Bitmap` arrays, WinForms active-form checks, the exact 0.7/0.3 equation,
or a full-array sort on every page turn.

### 19.2 Resolved memory policy

`app/viewer_memory_policy.py` now separates the configured ceiling from the
normal background-population target:

- `ViewerMemoryResolution.hard_limit_bytes` is the byte-exact fixed mode or the
  stable Auto bucket.  Compatibility properties `bytes` and `mib` expose the
  same hard ceiling to callers not yet migrated to the explicit names.
- `active_soft_target_bytes` is seven eighths (87.5%) of the hard ceiling and
  leaves room for one decoder result, QImage/QPixmap upload and Qt/native
  allocations that are not fully visible to the retained-artifact ledger.
- `inactive_soft_target_bytes` is one half of the hard ceiling.  Changing
  active state selects a different soft target; it does not reinterpret the
  saved setting or change the hard ceiling.
- The OS reserve is the larger of 2 GiB or 20% of total physical RAM, bounded
  by total RAM.  Existing cache is added back once when available memory is
  evaluated, while non-cache process working set is charged separately.
- Auto still resolves to a stable 128 MiB through 32 GiB bucket.  A live
  pressure sample may lower the pressure ceiling immediately, in 16 MiB
  granularity, but does not make Auto's hard ceiling flap.  Recovery requires
  meaningful headroom and grows by at most the larger of 64 MiB or one eighth
  of the hard ceiling per observation.  Only an explicit setting
  `reconfigure` resolves a different Auto hard ceiling.
- `ResolvedViewerMemoryPolicy.debug_values()` exposes saved mode, hard limit,
  both soft targets, selected target, reserve, pressure ceiling, cache bytes
  and the physical-memory snapshot to tests/benchmarks.  It emits no
  production log.

The current `ViewerWindow` worktree owns one `ResolvedViewerMemoryPolicy`,
samples physical memory on a five-second timer and on active/inactive changes,
and exposes both the hard budget and selected soft target.  `minimal` and the
fixed modes therefore remain hard ceilings even under pressure; pressure
changes how far optional background population may proceed.  Required current
display work remains outside the optional-background soft gate and may
temporarily overflow it, subject to subsequent low-priority reduction.

### 19.3 Lazy book-wide planner and page-count removal

`app/raster_warmup_planner.py` separates immutable book topology from one
navigation plan.  `ViewerWindow._raster_book_topology` walks the PageModel
display-unit boundary once per book generation, source identity and topology
revision.  A page turn then creates a small `RasterWarmupPlan`; it does not
materialize or sort a new all-page list.

After the first complete frame is accepted by the presentation commit, the
plan releases a **Startup Ready Runway** of up to four complete display units
in the preferred/navigation direction plus one in the reverse direction, in
`F1, R1, F2, F3, F4` priority order.  It
then continues seamlessly through the same lazy book-wide walk without waiting
for physical paint.  Paint acknowledgement remains the displayed-frame
ownership/reclamation and deferred-UI boundary; it is no longer a scheduling
gate.  Section 20 records why the preceding one-neighbor/paint-gated contract
made the available byte budget appear ineffective and how this runway replaces
it.

The 4 + 1 values are a startup minimum target, not a page-count ceiling.  Each
candidate is still admitted against the combined decoded-source plus
layout-frame byte policy, so a hard/soft limit, an oversized unit, a book end
or an already-ready/terminal unit can yield fewer new artifacts.  The normal
iterator still represents every single/spread display unit in the book and
creates candidates only as the one worker asks for them.  Direction reversal
or any navigation creates a new current-centered plan and therefore replaces
the unstarted cursor; completed artifacts remain reusable.  Capacity skips,
the unprocessed hint and explicit waiting/running/soft-target/hard-limit/
complete/suspended stop reasons belong to `RasterWarmupPlanner`, not to a
timer, paint event or cache membership.

Neither source nor frame store accepts a maximum page/unit count.
`cache_unit_limit` and the `set_cache_limits(unit_limit=...)` compatibility
surface have also been removed from `RasterBookRuntime`; callers, tests and
current runtime benchmarks use byte limits only. `BookSession` constructs ZIP
and Folder runtimes from the combined byte budget without supplying a page
count. Thus the removal boundary is explicit:

| Count-like value | Current classification |
|---|---|
| Raster artifact count | Diagnostic only; no constructor/property/setter count limit remains. |
| Book topology length | Finite iteration and identity safety boundary; not a cache cap. |
| current/spread/next/previous neighborhood | Minimum protection/priority band; not a maximum. |
| `ImageCache.cache_size` and legacy image prefetch counts | Retained for PDF, RAR/7z, custom sources and the non-raster pipeline; not the ZIP/Folder runtime membership rule. |
| PageList thumbnail count/range | Separate virtualized PageList ownership, outside the main raster source/frame ledger. |

Consequently a small-page book may retain hundreds or thousands of completed
units until the byte target is reached, while a large-image book naturally
retains fewer.  Reaching 24, 32 or 64 units is not itself a reason for the
raster worker to become idle.

### 19.4 NivisViewer-specific modern cache contract

The memory/work-order principle is combined with NivisViewer behavior that is
not copied from ZipPlaFork:

- `_ZipRasterSourceStore` owns decoded QImages separately from
  `_ZipRasterFrameStore`'s layout/DPR/rotation-specific complete frames; their
  actual byte costs form one combined ledger at the runtime boundary.
- Normal fit warm-up stores a sufficient preview source and a display-ready
  frame.  Full decoded source is promoted for the current page only when
  magnifier, actual size, manual zoom, nearest-neighbor source semantics or another explicit
  full-resolution demand requires it.  A sufficient preview/full tier can
  survive a frame-layout invalidation and avoid another archive/file decode.
- Retention rank comes from the current lazy plan and direction.  Current
  display unit, complete spread partner and last-painted frame are protected;
  obsolete layout/DPR/rotation variants, unnecessary full sources and distant
  opposite-side artifacts are natural low-priority candidates.
- Admission calculates free bytes plus reclaimable strictly lower-rank
  artifacts without first deleting them.  Unknown-cost background pages are
  header-probed by the one worker.  A stale, cancelled, failed or oversized
  result leaves the existing ready cache intact; one skipped oversized page
  does not end the remaining book-wide walk.
- A successful relevant result is rechecked using actual QImage/frame costs.
  Every QPixmap upload must then succeed before lower-rank ownership is
  reclaimed and the new artifact is inserted. This preserves ready-hit reuse
  and avoids destroying the old frame for a speculative decode or failed GUI
  upload.
- Request/source epoch, layout and DPR validation, complete-spread atomic
  commit, old-frame retention through replacement paint, high-DPI targets,
  virtual PageList isolation and callback-drained shutdown remain authoritative
  NivisViewer contracts.

`app/raster_admission_policy.py` is the standalone soft/hard decision owner
(`ADMIT`, reclaim, exact probe, soft stop, hard refusal and oversized skip) used
by `RasterBookRuntime`. Known costs are decided before dispatch; unknown PNG/
WebP-like sizes are header-probed by the one worker and reported through the
same capacity/oversized diagnostics before pixel decode. The lazy planner
pauses at soft target without consuming the deferred unit, and a later target
increase resets that cursor and resumes immediately. Final 200-500-page
population figures are recorded below after the reproducible CLI run;
physical-device responsiveness remains validation work rather than an inferred
result.

### 19.5 Reproducible 300-page population result

`scripts/benchmark_raster_memory_population.py` drives the production ZIP or
Folder runtime directly in a fresh offscreen child process. It paints and
acknowledges the first complete frame, then samples at 1, 3 and 10 seconds.
Fixtures live only in a temporary directory; no application window or native
input is created. The default safe matrix uses 300 JPEG pages, a 400 x 600
physical-pixel target, small 240 x 360 images, large 2400 x 3600 images, and an
alternating mixed book. The 4 GiB/Auto cases remain bounded to at most
576,000,000 retained source+frame pixel bytes and do not reserve their hard
limit up front.

On the measured 128-GiB host, resolved hard/active-soft values were:

| Mode | Hard limit | Active soft target |
|---|---:|---:|
| 256 MiB | 268,435,456 B | 234,881,024 B |
| 4 GiB | 4,294,967,296 B | 3,758,096,384 B |
| Auto | 34,359,738,368 B (32 GiB bucket) | 30,064,771,072 B (28 GiB) |

Every first-paint sample had exactly one ready page, one running worker and
stop reason `running`. The following values are from the second complete run:

| Source/profile/mode | First paint / cache | Ready at 1s / 3s / 10s | 10s cache | Final state / capacity skips | Read / decode / duplicate | WS delta |
|---|---:|---:|---:|---|---:|---:|
| ZIP small 256 | 6.498 ms / 691,200 B | 155 / 300 / 300 | 207,360,000 B | complete / 0 | 300 / 300 / 0 | 108,257,280 B |
| ZIP large 256 | 14.600 ms / 1,920,000 B | 87 / 122 / 122 | 234,240,000 B | complete_with_skips / 178 | 122 / 122 / 0 | 121,462,784 B |
| ZIP mixed 256 | 7.377 ms / 691,200 B | 112 / 179 / 179 | 233,088,000 B | complete_with_skips / 121 | 179 / 179 / 0 | 129,900,544 B |
| Folder small 256 | 8.339 ms / 604,800 B | 154 / 300 / 300 | 181,440,000 B | complete / 0 | 300 / 300 / 0 | 186,617,856 B |
| Folder large 256 | 18.065 ms / 2,580,000 B | 81 / 89 / 89 | 229,620,000 B | complete_with_skips / 211 | 89 / 89 / 0 | 235,204,608 B |
| Folder mixed 256 | 8.619 ms / 604,800 B | 110 / 144 / 144 | 229,305,600 B | complete_with_skips / 156 | 144 / 144 / 0 | 239,611,904 B |
| ZIP mixed 4 GiB | 6.659 ms / 691,200 B | 112 / 300 / 300 | 391,680,000 B | complete / 0 | 300 / 300 / 0 | 217,829,376 B |
| ZIP mixed Auto | 6.458 ms / 691,200 B | 111 / 299 / 300 | 391,680,000 B | complete / 0 | 300 / 300 / 0 | 219,541,504 B |
| Folder mixed 4 GiB | 10.664 ms / 604,800 B | 109 / 296 / 300 | 477,720,000 B | complete / 0 | 300 / 300 / 0 | 500,289,536 B |
| Folder mixed Auto | 8.538 ms / 604,800 B | 109 / 297 / 300 | 477,720,000 B | complete / 0 | 300 / 300 / 0 | 500,363,264 B |

All cases finished with source-only count zero, no duplicate decode, no
eviction, no oversized skip, no stale/cancel result and no soft/hard overflow.
In the 256-MiB large/mixed cases, `complete_with_skips` means that the planner
visited the complete book and skipped units that could not be admitted below
the active soft target; `missing_ready_page_count` records those pages while
`unprocessed_unit_count` correctly reaches zero. One page did not halt later
smaller work. The 4-GiB and Auto mixed books populated all 300 pages, proving
that 24/32/64 pages are not hidden stop conditions.

Every child exited normally. Runtime callbacks drained, active job count and
unfinished task count reached zero, ZIP/folder sources closed, fixtures were
renameable before teardown, and temporary directories were removed. Shutdown
took 12.950--29.778 ms.

The pre-change ad-hoc baseline is not a valid numeric A/B for ready counts: it
used 160 x 240 small pages and populated the 256-MiB hard limit, whereas this
benchmark uses 240 x 360 pages and intentionally stops optional work at the
active soft target. Its `195 ready / 266,014,320 B` therefore must not be
compared with `122` or `179` as a regression. The valid contract comparison is
that both old and new 4-GiB/Auto runs can retain all 300 pages, while the new
path additionally has no count cap, an explicit soft/hard boundary, lazy
book-wide completion and exact skip/restart diagnostics.

### 19.6 Remaining validation boundary

The offscreen results demonstrate memory-driven population and resource
lifetime, not physical-device smoothness. Task-local decoder buffers, QPixmap
upload and Qt/native allocations are transient and cannot be charged exactly
to the retained ledger; process working set is therefore expected to differ
from `cache_used_bytes`. The five-second pressure sampler, active/inactive
shrink and incremental recovery are covered by policy/runtime tests, including
preservation of an unrelated in-flight decode during recovery. Real-device
checks must still cover long idle warm-up, a page turn during that warm-up,
direction reversal, magnifier/full-source promotion, window deactivation and
returning to an already warmed page.

## 20. Historical raster book-open critical path and request-scoped Startup Ready Runway (superseded)

This section records the request-scoped four-forward/one-reverse predecessor.
It superseded the one-neighbor/paint gate described by the earlier wording in
sections 18.3 and 19.3, but is itself superseded by the persistent book-scoped
planner and presentation-surface ownership in section 21.  Its controlled A/B
tables remain useful historical evidence for the removed paint-idle gate; its
planner creation/release lifecycle is no longer the production contract.  The
byte-driven stores, PresentationState and complete-frame atomic swap remain in
force.  Physical-device behavior is not inferred from either structural
change.

### 20.1 Fixed-revision ZipPlaFork book-open call sequence

The reference is still
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  The ordinary ZIP path at that
revision is the following concrete sequence:

```text
ViewerForm.OpenFile(path, ...)
  -> cancel the preceding BackgroundMultiWorker work set
  -> save preceding history / update access time / catalog selection request
  -> getPackedImageLoader
     -> Program.GetPackedImageLoader
     -> PackedImageLoader(..., onMemory=None)
        -> File.OpenRead once and retain the stream in the loader
  -> PackedImageLoader.GetPackedImageEntries or GetPackedImageFullEntries
     -> getZipArchiveEntries
        -> new ZipArchive(retained stream, Read) once
        -> ZipArchive.Entries.ToArray -> image filtering
     -> getNameSortedEntries / logical-name sort
  -> choose currentPage and update the page control
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(all-page arguments, true)
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData/loader, allocate page arrays, SetBackgroundMode
        -> priorityLevel + BackgroundMultiWorker.SetWorksOrder
     -> BackgroundMultiWorker starts WorksOrder[0]
     -> bmwLoadEachPage_DoWork(the first page in that numerical priority band;
                               normally current, not a topology guarantee)
        -> PackedImageLoader.OpenImageStream / OpenInnerImageStream
        -> entry stream, decode, orientation/filter, resize, VirtualBitmapEx
     -> bmwLoadEachPage_EachRunWorkerCompleted
        -> SetNewResizedImage -> completed artifact publication
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> select the first unfinished item in the newest order immediately
```

The source locations are `source/ZipPla/ViewerForm.cs:2308-2771`
(`OpenFile`), `:2919-3003` (`bmwLoadEachPage_RunWorkerStarting`),
`:3177-3602` (`bmwLoadEachPage_DoWork`), `:5371-5415` (completion),
`:5451-5468` (`SetNewResizedImage`) and `:5558-5605`
(`SetBackgroundMode` / `priorityLevel`); `source/ZipPla/PackedImageLoader.cs`
`:283-330` (retained file stream), `:1197-1243` (`getZipArchiveEntries`) and
`:1781-1867` (entry-stream access); and
`source/ZipPla/GenerarClasses.cs:247-264,266-339`
(`SetWorksOrder` and latest-order completion selection).  The configured
Viewer worker count is one in `ViewerForm.Designer.cs:1628-1636`.  Revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` is the fixed source snapshot, not
the commit that introduced that Viewer setting: the revision itself changes
only `CatalogForm.ThumbViewerItem` thumbnail concurrency.  The audited Viewer
worker and methods were already present in base import/core commit
`5e942ef0006decf01c21d9dbe037dba50e763420`.

ZipPlaFork therefore does perform synchronous work before the first page job:
history/catalog calls, archive entry enumeration and sorting, slider/control
projection, old resized-array disposal and the worker-starting array setup are
not free.  Its important work-order property is narrower: once the current
display artifact is published, the same one-worker scheduler can select the
next ordered page at that completion boundary.  It has no Qt physical-paint
acknowledgement gate.  The ordinary loader retains one archive stream/reader;
copying the whole archive to memory is an optional mode, not the default path.

This replacement does **not** copy ZipPlaFork's pre-open old-frame clearing,
synchronous archive enumeration, WinForms control updates, GDI bitmap arrays,
weak request identity or wait-cancel ownership.  Those would regress
NivisViewer's asynchronous open, rollback frame, high-DPI and stale-result
contracts.

### 20.2 NivisViewer path before this worktree change

Archive open and entry enumeration were already off the GUI thread, but the
GUI handoff performed several non-current operations before it could request
the first frame:

```text
ViewerWindow.open_path
  -> _save_current_reading_position
     -> MetadataStore.update_progress -> MetadataStore.flush (synchronous)
  -> BookSession.open_book_async
     -> _BookOpenWorker.run
        -> source factory -> ZipImageSource.__init__
           -> zipfile.ZipFile retained for the book lifetime
        -> ZipImageSource.list_images
           -> infolist, extension/display-name filtering, natural sort
  -> BookSession._on_async_prepared                         [GUI]
     -> PageModel.set_prepared_source
     -> _replace_viewer_runtime -> old runtime shutdown/cache clearing
     -> _replace_page_list_runtime -> thumbnail runtime construction
     -> ImageCache.set_source -> duplicate source and full image-id ownership
  -> ViewerWindow._finish_opened_book                      [GUI]
     -> MetadataStore.record_book_opened
        -> history_changed -> HistoryModel.refresh
     -> _add_recent_path -> recent QAction projection
     -> _refresh_view -> topology/current runtime request
     -> book_changed -> Browser/book projection
  -> RasterBookRuntime current read/decode/frame job
  -> ViewerWindow._on_zip_runtime_frame_ready
     -> QPixmap-backed complete-frame atomic commit
  -> ViewerWidget.paintEvent / framePainted
     -> release the whole background warm-up order
```

`_BookOpenWorker.run`, `ZipImageSource.__init__` and
`ZipImageSource.list_images` were not the GUI-thread regression: they kept one
ZIP handle and produced the page index in the open worker.  The avoidable
critical-path costs began at `BookSession._on_async_prepared` and
`ViewerWindow._finish_opened_book`.  They included destruction of a potentially
large preceding source/frame cache, eager PageList ownership, an unused legacy
`ImageCache` page-list copy, synchronous SQLite/history and recent-action
projection, and Browser work made eligible before the current frame.  The
runtime then left its one Viewer lane idle between accepted current commit and
physical paint even when the next display unit was known and admissible.

The first topology/index construction remains required once per book/layout
revision, and the current page still cannot decode until asynchronous ZIP
listing and natural sorting complete.  This change does not hide those costs;
it removes unrelated ownership/projection work around them.

### 20.3 Historical request-scoped Hybrid sequence (superseded)

At this predecessor stage, the ZIP/Folder production handoff was:

```text
ViewerWindow.open_path
  -> save preceding progress without a synchronous metadata flush
  -> asynchronous source open, ZIP listing and PageModel construction
  -> BookSession installs the new RasterBookRuntime with explicit hard/soft bytes
     -> retire old Viewer/PageList work without clearing retained artifacts
     -> defer replacement PageList construction
     -> ImageCache.suspend_for_book_runtime (fully detached legacy owner)
  -> ViewerWindow stages book-open metadata/recent/book_changed projection
  -> submit only the current complete display unit
  -> worker read/decode/render -> validated complete RasterFrame
  -> atomic PresentationState/ViewerWidget commit
     -> slider/status/displayed state update together
     -> release_startup_runway(request_id)
        -> prioritize up to four forward + one reverse complete display units
        -> continue through the lazy book-wide order with the same one worker
  -> matching ViewerWidget paint acknowledgement
     -> release_prefetch(request_id): mark painted ownership / reclaim safely
     -> start the zero-delay presentation-side-effect timer
  -> after paintEvent returns
     -> construct/activate the virtual PageList runtime
     -> reclaim retired Viewer/PageList/source ownership
     -> record book-open metadata, recent actions and book_changed/Browser projection
     -> project page history/actions/PageList selection and persist committed progress
```

`ViewerWindow._on_zip_runtime_frame_ready` releases startup work only after the
request/book epoch, source identity, complete display-unit identity, layout/DPR
contract, accepted presentation token and positive committed frame serial have
all survived validation.  `RasterBookRuntime.release_startup_runway` is
idempotent per request and also requires the current complete frame to exist in
the frame store.  `RasterWarmupPlanner.release_startup_runway` makes up to four
preferred-direction and one reverse-direction **complete display units** the
priority prefix, ordered `F1, R1, F2, F3, F4`.  When those topology units exist
and pass byte admission, making all five ready before lower-ranked work is the
startup guarantee.  A two-page spread consumes one unit, not two slots; a split
wide page is never exposed half-complete.  When this prefix is ready, terminal
or safely declined by byte admission, `next_candidate` proceeds directly into
the ordinary lazy whole-book iterator.  It does not return to a paint-waiting
state.

The exact cause of the former “only one neighbor is ready” symptom was the
intermediate `release_after_commit(unit_limit=1)` contract: after one useful
missing display unit it explicitly restored `waiting_for_paint`, leaving the
single worker idle until a matching physical-paint acknowledgement opened the
remaining order.  The combined cache still had free bytes, so increasing its
budget could not create work hidden behind this scheduler admission gate.  The
new 4 + 1 runway removes that gate; it is a startup minimum target and priority
prefix, **not** a retention limit.  Every candidate is still admitted against
the combined decoded-source plus layout-frame hard/soft byte policy.  A small
budget, oversized artifact or a book boundary may therefore produce fewer than
five newly ready units, while a large budget can retain and warm far more than
five.

The runway is topology-based rather than screen-coordinate-based.  In single
or spread mode it follows complete `RasterDisplayUnit` entries; LTR/RTL and the
current navigation direction determine the preferred side; wide/split units
remain atomic; and a start/end boundary neither wraps nor duplicates units.
Every accepted navigation or direction reversal constructed a fresh
current-centered planner and request-scoped release.  Unstarted old-direction
work disappeared, an active job was adopted only when it was the exact first
missing unit of the replacement runway, and otherwise it was
cancelled/stale-rejected.  Completed old-direction artifacts remained reusable
under the byte budget.  Within one request, the runway continued into the lazy
book-wide iterator without a paint gap; navigation nevertheless discarded that
planner owner and its scan/admission state.  Section 21 replaces this remaining
reset boundary.  Physical paint still acknowledges ownership, releases the
preceding displayed-frame protection and gates PageList/metadata/history/
Browser projection and retired-resource cleanup; it does not decide whether
raster warm-up may continue.

This is intentionally Hybrid.  It adopts ZipPlaFork's useful completion-boundary
continuation but keeps NivisViewer's Qt publication boundary: QPixmap creation,
atomic old-frame replacement and physical-paint acknowledgement are unchanged.
PageList, SQLite/history/progress, recent-action rebuild, Browser projection
and large retired-cache destruction remain paint-gated and are run by
`ViewerWindow._flush_presentation_side_effects` on its zero-delay timer, outside
the `paintEvent` call stack.  Slider and status remain part of the accepted
atomic presentation commit; moving them after paint would reintroduce a split
displayed-page owner.

### 20.4 Ownership, memory-setting and lifecycle cleanup

| Boundary | Before | Current worktree contract |
|---|---|---|
| Main raster source owner | `RasterBookRuntime` plus an attached, suspended `ImageCache` with another full image-id list | `PageModel` owns page metadata and `RasterBookRuntime` owns ZIP/Folder decode/frame state; `ImageCache.suspend_for_book_runtime` cancels legacy work and clears its source and IDs. |
| Runtime memory authority | New runtimes were initially seeded through the legacy `ImageCache` budget, then corrected by Window policy | `BookSession.set_viewer_runtime_memory_limits` owns explicit hard/soft byte values used both for construction and live updates. |
| Persisted Raster memory setting | `viewer_memory_mode` coexisted with normalized/saved `viewer_cache_max_memory_mib` and prefetch presets still exposed a hidden memory field | `viewer_memory_mode` is the sole Raster memory authority. `viewer_cache_max_memory_mib` is accepted only for one-time migration when no explicit mode exists, is removed from normalized state and is never re-saved. A newer explicit mode wins. |
| Legacy format settings | Raster and legacy meanings were entangled | `cache_size`, image/PDF forward/backward units and preset/direction controls remain for PDF, RAR/7z, custom/non-raster paths; their continued existence is not a Raster cache cap. |
| PageList memory | Derived as one quarter of the main budget, clamped to 8--64 MiB | An explicit 64 MiB `ViewerPageListRuntime` QImage budget defines an independent virtual-thumbnail boundary; the runtime itself is not constructed until first paint. |
| Replacement cache destruction | Old Viewer/PageList runtime shutdown could clear a large ready cache during new-book GUI installation | `RasterBookRuntime.retire` and `ViewerPageListRuntime.retire` stop acceptance/cancel work but retain artifacts. `BookSession.release_retired_book_resources` performs final shutdown after the replacement paint. |
| Failed/no-paint/rapid switch | Cleanup relied primarily on normal runtime idle/paint flow | Failed replacement keeps the active presentation. The bounded no-paint fallback schedules the same post-paint projections. `close_book` releases deferred owners, and a raster A -> raster B -> non-raster C chain releases them before C because no later raster paint exists. Running callbacks retain their source through the existing retired-source drain maps. |

The concrete NivisViewer owners are `app/viewer_window.py`
(`open_path`, `_finish_opened_book`, `_save_current_reading_position`,
`_apply_book_open_projection`, `_flush_book_open_projection`,
`_on_zip_runtime_frame_ready`, `_on_zip_runtime_frame_painted` and
`_flush_presentation_side_effects`); `app/book_session.py`
(`_on_async_prepared`, `_replace_viewer_runtime`, `_replace_page_list_runtime`,
`ensure_page_list_runtime`, `set_viewer_runtime_memory_limits` and
`release_retired_book_resources`); `app/image_cache.py`
(`suspend_for_book_runtime`); `app/zip_raster_book_runtime.py`
(`RasterBookRuntime.release_startup_runway`, `_first_missing_startup_key`,
`release_prefetch`, `_adopt_request`, `retire` and `_drive`);
`app/raster_warmup_planner.py` (`RasterWarmupPlan.startup_runway_units`,
`RasterWarmupPlanner.release_startup_runway`, `next_candidate` and
`release_after_paint`); `app/viewer_page_list_runtime.py` (`retire`); and
`app/config_manager.py` (legacy memory-key migration and removal).

Deferring retirement intentionally permits the preceding completed frame/cache
to coexist with the new current decode for one replacement-paint interval.  It
prevents a large GUI-thread deallocation burst before current dispatch and
preserves rollback, but may cause a short transient working-set peak.  Close,
non-raster transition and queued-callback drain remain explicit terminal
boundaries rather than relying on object finalizers.

### 20.5 Historical adoption decision and AGPL provenance

| Fixed-revision source / method | Principle evaluated | NivisViewer result |
|---|---|---|
| `ViewerForm.cs`, `bmwLoadEachPage_RunWorkerStarting`, `SetBackgroundMode`, `priorityLevel`; `GenerarClasses.cs`, `SetWorksOrder` / completion selection | One Viewer lane receives current-centered work and chooses the next useful item at a completion boundary without a paint-idle gap. | **Superseded Hybrid predecessor:** `RasterBookRuntime.release_startup_runway` and a request-created `RasterWarmupPlanner` supplied a four-forward/one-reverse prefix. Section 21 retains the priority principle but replaces request-scoped planner ownership with one persistent book-scoped owner. |
| `PackedImageLoader.cs`, constructor, `getZipArchiveEntries`, `OpenImageStream` / `OpenInnerImageStream` | One book-scoped archive handle/index and entry-local page work. | **Already adopted/maintained:** `ZipImageSource.__init__`, `list_images`, entry-read methods and `ZipRasterBookRuntime` keep structured lifetime, cancellation and stale fences. No new archive copy is added. |
| `ViewerForm.cs`, `OpenFile`, `clearResizedImageArray` | Replacement preparation and old ownership disposal before new work. | **Not adopted:** old complete output is retained; `BookSession` retires work immediately but moves bulk cache destruction after successful replacement paint. |
| No ZipPlaFork counterpart | Async archive listing, PresentationState, PageList virtualization, post-paint side effects, migration-only settings and callback-drained rapid switching. | **NivisViewer/New design maintained:** these are modern Qt/UX/lifetime structures, not translations of WinForms code. |

The one-worker completion-order and replaceable current-centered order
principles in the first row are derived from the fixed AGPL-3.0-or-later
ZipPlaFork source and are recorded as a direct structural port.  The exact
4 + 1 startup shape, navigation-direction awareness, complete-display-unit
topology, combined Qt byte admission, epoch/request stale cancellation and
paint-independent promotion to a lazy iterator are NivisViewer
modernizations; ZipPlaFork does not contain that Qt contract.  No C# statement,
GDI type or WinForms control code was copied literally for this change.  The
retained license and copyright material at
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`, section 1
and `THIRD_PARTY_NOTICES.md` apply.

### 20.6 Historical controlled A/B that exposed the paint-idle cause

This A/B predates the final Startup Ready Runway and is retained as diagnosis,
not as a benchmark of the current 4 + 1 contract.  It compared only the then-new
one-neighbor scheduling boundary, not a checkout of the removed GUI ownership
path.  Both sides used the same generated large ZIP, fixed memory policy,
decoded/frame stores and one-worker runtime in fresh offscreen child processes:

- **A / paint gate:** accept the current frame, withhold
  the then-current `release_initial_warmup`, acknowledge paint, then call normal
  `release_prefetch`.
- **B / one-neighbor predecessor:** accept the same current frame, call the
  then-production `release_initial_warmup`, permit exactly one nearest missing
  unit through `release_after_commit(unit_limit=1)`, then
  acknowledge paint and release the remaining order normally.

The intended matrix is a 100--300-page large-image ZIP under fixed 256 MiB,
fixed 4 GiB and resolved Auto.  It records archive opens/listings, page count,
work submitted before current, current entry read/decode/job/callback counts,
open-to-current commit, commit-to-first-neighbor-ready, first-paint time,
ready/source/frame residency, retained bytes, working-set delta and shutdown
drain.  A controlled commit-to-paint delay may expose worker idle time, but
must be labelled synthetic; it is not physical presentation latency.  A direct
runtime runner also cannot measure deferred MetadataStore, QAction, PageList or
Browser work, so Window-level contract tests remain the evidence for those
boundaries.

The completed run used 200 2400 x 3600 JPEG pages in a 109,721,422-byte ZIP,
a 400 x 600 target and an explicit synthetic 20-ms accepted-commit-to-paint
boundary.  Values below are milliseconds; each A and B ran in a fresh process.

| Mode | A request->commit / commit->next / paint->3 ready | B request->commit / commit->next / paint->3 ready | Ready before paint / jobs before paint, A->B | Ready at 250/500/1000 ms, A->B |
|---|---:|---:|---:|---:|
| fixed 256 MiB | 14.376 / 31.266 / 18.599 | 14.482 / 8.183 / 10.795 | 1/1 -> 2/2 | 29/56/106 -> 31/55/97 |
| fixed 4 GiB | 15.948 / 31.561 / 21.161 | 16.780 / 10.840 / 10.606 | 1/1 -> 2/2 | 29/55/106 -> 28/52/101 |
| Auto | 14.703 / 32.153 / 21.610 | 15.518 / 10.280 / 10.469 | 1/1 -> 2/2 | 29/54/104 -> 28/53/107 |

First-paint latency was 35.013 -> 35.285 ms (256 MiB), 36.410 -> 37.413 ms
(4 GiB) and 35.673 -> 36.220 ms (Auto).  Those 0.272--1.003-ms differences
are within isolated-run noise; the controlled result does not show a material
first-frame regression for the one-neighbor predecessor.  B retained one
additional 1,920,000-byte complete
source+frame artifact before paint.  At one second B retained 186,240,000 /
193,920,000 / 205,440,000 bytes and its working-set deltas were 98,160,640 /
102,424,576 / 107,077,632 bytes for 256 MiB / 4 GiB / Auto respectively.  A's
corresponding values were 203,520,000 / 203,520,000 / 199,680,000 bytes and
106,905,600 / 107,311,104 / 105,340,928 bytes.  The ready-count variation after
250 ms is normal decoder-throughput noise rather than a policy regression.

Every case opened the archive and built the listing once, submitted zero jobs
before the initial request, produced zero duplicate successful decodes and
drained callbacks, closed the source and removed the temporary fixture at
shutdown.  The first-neighbor gap fell from 31--32 ms to 8--11 ms and the
post-paint three-ready gap from 18.6--21.6 ms to 10.5--10.8 ms.  The synthetic
20-ms boundary deliberately amplifies otherwise platform-dependent paint idle;
it is not claimed as physical presentation latency.  More importantly, this
run isolated the former scheduler cause: a useful job could execute at commit,
but `unit_limit=1` deliberately put the worker back behind the paint gate.  It
did not justify retaining that one-unit restriction.

A separate three-second commit-gate reach run verifies that memory mode, not a
hidden page/unit count, remains authoritative:

| Mode | Resolved hard / soft bytes | Ready pages / retained bytes | Jobs / reads / decodes / duplicates | Stop reason / WS delta |
|---|---:|---:|---:|---:|
| fixed 256 MiB | 268,435,456 / 234,881,024 | 122 / 234,240,000 | 122 / 122 / 122 / 0 | `complete_with_skips` / 122,003,456 B |
| fixed 4 GiB | 4,294,967,296 / 3,758,096,384 | 200 / 384,000,000 | 200 / 200 / 200 / 0 | `complete` / 197,177,344 B |
| Auto | 34,359,738,368 / 30,064,771,072 | 200 / 384,000,000 | 200 / 200 / 200 / 0 | `complete` / 197,832,704 B |

All three reach cases had one archive open, one listing, zero evictions and a
clean callback/source/temporary-file shutdown.  Offscreen evidence establishes
the paint-idle diagnosis and absence of a hidden count cap only.  The final
4-forward/1-reverse runway and seamless book-wide promotion supersede both A
and B; no new physical latency number is inferred from this historical table.
Native ZIP I/O, decoder, QPixmap upload, DWM/GPU presentation and physical
wheel feel still require the user's real-device check.

#### 20.6.1 Final request-scoped runway validation (historical)

The final implementation was rerun with a temporary 200-page ZIP containing
identical 2,400 x 3,600 detailed JPEG entries, a 400 x 600 viewport, 256 MiB
hard / 224 MiB soft raster policy, one worker and a deterministic 20 ms
commit-to-paint interval.  Each side ran in a fresh offscreen process.  This
first table isolates the scheduler on the final code: A withholds the commit
release until paint; B calls the production Startup Ready Runway.

| Final-code scheduler A/B | A: paint gate | B: Startup Ready Runway |
|---|---:|---:|
| Request -> first commit ms | 17.836 | 16.797 |
| Request -> first paint ms | 38.842 | 37.274 |
| Commit -> next ready ms | 34.371 | 13.728 |
| Paint -> three ready ms | 26.130 | 7.807 |
| Ready units at first paint / 50 / 100 / 250 ms | 1 / 4 / 8 / 19 | 2 / 6 / 10 / 21 |
| First-five forward hits at first paint / 50 ms | 0 / 3 | 1 / 5 |
| Cache bytes at first paint / 50 / 100 / 250 ms | 3,120,000 / 12,480,000 / 24,960,000 / 59,280,000 | 6,240,000 / 18,720,000 / 31,200,000 / 65,520,000 |
| Duplicate successful decodes | 0 | 0 |

The first-commit difference is isolated-process noise because B is released
only after that commit.  The useful result is the eliminated scheduler idle:
the next and third complete units arrive about 20.6 and 18.3 ms earlier in this
controlled boundary.  B performs exactly the additional reads/decodes needed
for its two extra ready units; job, callback and QPixmap counts remain one per
completed unit.

A separate 100 ms pre-paint reach probe compares the task-start production
(`release_after_commit(unit_limit=1)`) with the final production.  The old
runtime stopped at two ready units (current plus one neighbor), so only one of
the first five forward turns was a hit.  The final runtime reached eight ready
units, making all first five turns hits, and had already submitted the ninth
single-worker job.  The corresponding exact combined-cache values were
3,840,000 versus 24,960,000 bytes.  Both sides reported zero duplicate decode,
drained callbacks, closed sources and unlocked/removed temporary fixtures.

This holistic predecessor/final comparison also contains the resampling
correctness change: the 2,400 x 3,600 JPEG source retained for a 400 x 600
frame is now the sufficient native 600 x 900 tier (2,160,000 pixel bytes), not
the former arbitrary 400 x 600 decoder output (960,000 bytes).  The final
QPixmap remains exactly 400 x 600 (960,000 bytes).  In the 20 ms runs that
changed task-start predecessor versus final population from 2 / 7 / 11 / 28
ready units at first paint / 50 / 100 / 250 ms to 2 / 6 / 10 / 21 and raised
the sampled working-set delta at 250 ms from 31,473,664 to 71,028,736 bytes.
That throughput/memory cost buys a non-undersized reusable source and the
configured one-final-resize quality contract; it is not attributed to the
runway scheduler and still requires physical quality/latency evaluation.

### 20.7 Remaining risk and physical-device boundary

- ZIP central-directory enumeration, display-name decoding and natural sort
  still precede the first current request, although they run in the book-open
  worker.  If open latency remains proportional to entry count, that index path
  is the next measured boundary.
- Up to four preferred-direction and one reverse complete display units can now
  consume CPU/memory bandwidth before the current paint, followed by seamless
  book-wide warm-up.  The combined hard/soft byte policy remains the actual
  retention/admission ceiling; a real-device first-paint or immediate-input
  regression should tune the startup runway/admission policy, not restore a
  paint-dependent scheduler gate.
- PageList construction and retired-cache disposal move after paint, not out of
  the process.  They may affect the immediately following interaction and need
  a physical rapid-open/page-turn check.
- Replacement retirement may transiently retain two book caches.  Rapid raster
  replacement, transition to PDF/RAR/7z, hidden/no-paint fallback and close
  must be checked for bounded memory and zero late-state mutation.
- The persistent ZIP handle and one active decode still depend on Python/Qt
  backend behavior.  Matching ZipPlaFork's work order does not by itself prove
  matching native decoder or renderer latency.

### 20.8 Final validation and shutdown observations

Final syntax/import checks and `git diff --check` passed.  The focused runway,
source-tier, resampling, configuration-migration, Folder/ZIP runtime and Viewer
integration group passed 205 tests.  The complete repository suite was then
split across fresh pytest processes and passed **1,516 tests** across all 89
test files; lifecycle-sensitive partitions were rerun one file per process.
This process boundary is intentional: two earlier broad multi-file processes
aborted during Python/Qt teardown while old Pdfium or Windows shell-preview
workers from preceding tests still existed, although every affected file
passed in a fresh process.  That remains a suite-process lifetime risk rather
than evidence of a raster assertion failure.

Two older integration tests had treated complete-frame commit as if post-paint
metadata/PageList projection had already run.  Their bounded waits now observe
the public reading-progress or PageList epoch/row result, preserving the
production `commit -> paint -> noncritical projection` boundary.  The run also
found and fixed an unrelated real shutdown race: `FileOperationPanel` scheduled
an idle close without a QObject context, so the callable could reach a deleted
`WA_DeleteOnClose` wrapper.  The callback is now context-bound; 76 related file
operation tests passed.

All task-owned pytest and benchmark PIDs were identified by executable,
command line and parent PID, terminated where an aborted process had remained,
and rechecked.  No pytest, benchmark or compile process from this work remains;
unrelated pre-existing Python processes were not touched.  Named benchmark and
stranded pytest temporary directories created by this validation were removed.

## 21. Presentation-surface ownership and persistent book-scoped continuous warm-up (2026-08-21 worktree)

This section supersedes the request-scoped Startup Ready Runway lifecycle in
section 20 while retaining its useful evidence and byte-driven cache contract.
The production change has two independent but user-visible goals:

1. the idle prompt must never regain the canvas after a valid complete frame
   has committed; and
2. one raster-book planner must continue across page requests instead of
   recreating a four-forward/one-reverse startup phase on every navigation.

The fixed ZipPlaFork reference remains
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later.  As recorded in section 1, this hash is a fixed source
snapshot.  The commit itself changes only the `CatalogForm.ThumbViewerItem`
thumbnail semaphore from processor-count concurrency to one.  It did not
introduce `ViewerForm`'s `bmwLoadEachPage.ThreadCount = 1`; the audited Viewer
implementation is inherited from `5e942ef0006decf01c21d9dbe037dba50e763420`.

### 21.1 Superseded production defects

The former empty-state behavior had no single presentation owner.
`ViewerWidget.paintEvent` inferred the idle prompt solely from an empty local
`_images` list.  During an initial asynchronous open, that list is legitimately
empty before the first complete frame; during replacement it may also be
temporarily detached while the old presentation is still the rollback owner.
The Widget could therefore draw `画像を開いてください` without knowing whether
the application was idle, loading, retaining a replacement frame or reporting
an open error.  A viewport notification also called
`ViewerPresentationState.supersede_pending` before the 120-ms resize debounce
had a replacement request ready.  On a cold initial open this could reject the
only in-flight first frame and leave the locally inferred idle surface visible
until the delayed layout request completed.  A zero-image async source result
could likewise reach the successful installation path without any frame that
could end that interval.

The former warm-up path removed the paint gate inside one request, but still
created a new `RasterWarmupPlanner` in `RasterBookRuntime._adopt_request` for
every navigation.  It reset request release flags, failed/admission suppression
and the lazy iterator, then adopted an active background job only when it was
the exact first missing unit of the replacement 4 + 1 runway.  Consequently a
ready hit or a short direction change could cancel useful work, restart the
same priority prefix and lose the previous book scan/admission ledger even
though completed source/frame artifacts remained cached.  Section 20's 4 + 1
prefix was not a cache limit, but its *owner lifetime* was still too short.

### 21.2 Exact fixed-revision ZipPlaFork work and paint sequence

The reference sequence was re-audited from the fixed Git object rather than
inferred from method names:

```text
ViewerForm.OpenFile
  -> choose currentPage and update the trackbar
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(arguments, waitCancel=true)
     -> cancel/drain the preceding work set before installing the replacement
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData and its PackedImageLoader
        -> dispose the preceding loader only after old work has drained
        -> allocate PreFilteredImageArray / OriginalImageInfoArray
        -> allocate ResizedImageArray / ResizedSizeArray for the whole book
        -> SetBackgroundMode
           -> priorityLevel over every page
           -> BackgroundMultiWorker.SetWorksOrder(all-page permutation)
           -> ThreadCount = 1
     -> bmwLoadEachPage_DoWork(work number)
        -> OpenImageStream / decode / pre-filter source retention
        -> target resize / post-filter / complete VirtualBitmapEx
        -> exception becomes a terminal error VirtualBitmapEx
     -> bmwLoadEachPage_EachRunWorkerCompleted                 [UI completion]
        -> if currentPage changed: SetBackgroundMode for latest current
        -> SetNewResizedImage: memory admission and completed artifact publish
        -> if page is in the current visible band: showCurrentPage(false)
           -> Invalidate only; no synchronous paint and no scheduler release
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> if order changed, restart its cursor at the newest permutation
        -> skip every WorksStarted item
        -> start the first unfinished item immediately
     -> later WM_PAINT / pbView_Paint / pbView_PaintToCanvas
```

The decisive ordering is that `EachRunWorkerCompleted` runs before
`privateRunWorkerCompletedEventHandler` chooses the next page.  A current change
therefore replaces the unstarted order at the completion boundary; the sole
already-running page is not preempted.  Its result remains useful book cache
data, then the next job comes from the latest current-centered permutation.
`showCurrentPage` only calls `Invalidate`, so physical paint is not a worker
gate.

For single-page binding (`MaxPageCountInWindow = 1`), `priorityLevel` produces
current, numerical next, numerical previous, then the remaining book.  For a
two-page binding it produces the current numerical two-page band, the next
numerical two-page band, the previous numerical two-page band, then the rest.
Within a band the normal tie-break is distance from current; while the pointer
is over the trackbar it is distance from the pointer-mapped value.  This is a
fixed page-number band, not the actual wide/cover-aware display-unit topology.
`SetBackgroundMode` also runs before the replacement `BindingMode` is assigned
later in `bmwLoadEachPage_RunWorkerStarting`, so initial ordering can observe
the preceding binding value.  These upstream quirks are not ported.

`SetNewResizedImage` is completed-bitmap cache publication, not a displayed
frame transaction.  It admits a `VirtualBitmapEx` into `ResizedImageArray`, or
disposes and `ReworkOrder`s it.  `ReduceUsingMemory` evicts from the far end of
the latest priority order and disposes the same page's resized artifact and
pre-filtered source.  Completed pages survive navigation until that memory
eviction, reload or book replacement.  Natural next-page input refuses to move
beyond an unfinished display frontier, while direct/trackbar movement can
advance logical current before pixels.  Incomplete spread slots are not drawn,
but their completion can still request a loading paint; `pbView_Paint` applies
a half-gray mask to the retained canvas.  Status is also projected during
paint.  These logical/display and loading semantics are deliberately **not**
ported.

### 21.3 Single owner for EMPTY, LOADING, DISPLAYED and ERROR

`ViewerPresentationState` now owns a monotonic `PresentationSurface` containing
`mode`, `revision` and an optional message.  The possible modes are `EMPTY`,
`LOADING`, `DISPLAYED` and `ERROR`.  `ViewerWindow._project_presentation_surface`
is the sole bridge to `ViewerWidget.apply_presentation_surface`; the Widget
rejects an older revision and no longer derives the mode from image-list
emptiness.  `ViewerWidget.paintEvent` uses the idle prompt only for explicit
`EMPTY`, uses a loading message for `LOADING`, preserves committed pixels for
`DISPLAYED`, and uses the presentation-owned failure text for `ERROR`.

The state transitions are:

| Sequence | Presentation surface and canvas contract |
|---|---|
| Initial idle -> open | `EMPTY -> LOADING`; no `画像を開いてください` inference while the book/source/current frame is pending. |
| First complete frame | `LOADING -> DISPLAYED` in the same accepted presentation commit that updates displayed page, slider and status. Paint and post-paint callbacks do not demote it. |
| Delayed open/resize callback after commit | The Window projects the current state, and the Widget refuses a lower surface revision. A callback from the old interval cannot apply `EMPTY` or clear the frame. |
| Replacement open with an old committed frame | `DISPLAYED` remains `DISPLAYED` while replacement source/current is pending. The old complete frame stays the rollback owner. |
| Replacement failure | With an old committed frame, remain `DISPLAYED`; do not clear or show an empty/error placeholder over it. |
| Initial open failure | With no committed frame, transition to explicit `ERROR`; a cancelled initial open may return to `EMPTY`. |
| Zero readable images | `BookSession` closes the provisional source and emits `ImageSourceError(code="no_images")`; it is not installed as a successful empty book. |
| Explicit close/clear | Only `clear_book`/`close` transitions to `EMPTY`, after which Widget clearing and the idle prompt are valid. |

The viewport path now fences the old physical layout/DPR immediately.  When no
frame has committed, the surface remains explicit `LOADING` and the replacement
layout request is dispatched on the next event turn, rather than after the
normal 120-ms resize debounce.  With a committed frame, that old frame remains
`DISPLAYED` while the replacement layout is debounced.  A viewport event during
a provisional replacement open is recorded but cannot reactivate or request
the still-installed old book; success or failure consumes the live viewport
when it next requests the authoritative frame.  Loading and replacement state
therefore remain presentation concepts, not side effects of
`ViewerWidget.clear()` or an empty `_images` collection.

The concrete ownership is in `app/viewer_presentation_state.py`
(`PresentationSurfaceMode`, `PresentationSurface`,
`ViewerPresentationState.request_frame`, `commit_frame`,
`begin_replacement_open`, `supersede_pending`, `fail_replacement_open`,
`fail_pending`, `clear_book` and `close`); `app/viewer_widget.py`
(`apply_presentation_surface` and `paintEvent`); `app/viewer_window.py`
(`open_path`, `_on_async_book_open_failed`, `_on_zip_runtime_frame_ready`,
`_project_presentation_surface`, `_on_viewport_changed` and close handling);
and `app/book_session.py` (synchronous/asynchronous zero-image rejection).

### 21.4 Persistent book-scoped work-order owner

The request-scoped startup phase is replaced by one planner with the runtime's
book lifetime:

```text
first display request
  -> RasterBookRuntime._adopt_request
     -> create RasterWarmupPlanner once
     -> submit current complete display unit only
first accepted complete commit
  -> release_continuous_warmup(request_id)
     -> RasterWarmupPlanner.release_after_first_commit once
     -> urgent prefix F1, R1, F2, F3, F4
     -> same iterator continues through the remaining book
navigation / ready hit / direction reversal
  -> _adopt_request
     -> recenter the existing planner; do not replace its owner
     -> preserve release state, visited identities and capacity skips
     -> retain ready source/frame artifacts under the byte budget
     -> if current is ready: immediate cache-hit publication, no decode job
     -> if current is cold: current is first in the replacement unstarted order
     -> if the sole job has already started and its book/source/render identity
        is artifact-compatible with the new topology, let it finish and retain
        its artifact even outside the new urgent band
     -> replace/cancel only an unstarted queued job for the latest current
provisional replacement open
  -> suspend the old runtime without destroying planner/store/scan ownership
  -> failed replacement recenters and resumes that same book planner
  -> successful replacement retires it at the book-lifetime boundary
old-request completion already queued on the GUI thread
  -> accept it as cache data when source epoch/identity/render spec/topology match
  -> never use its old request serial to mutate presentation state
paint acknowledgement
  -> transfer displayed ownership / reclaim safe bytes / release UI side effects
  -> never release, pause or recreate raster scheduling
```

`RasterWarmupPlan.priority_band_identities` and
`iter_continuous_units` express 4 + 1 as a scheduling band, not a phase or
retention count.  The same topology handles single, spread, LTR/RTL, wide split
and explicit terminal one-page spread units.  After the priority band, the
iterator immediately emits the remaining book order.  On recenter the Python
iterator is reconstructed around the latest current, but the planner object,
released state, visited-identity ledger, capacity skips, ready frame store and
decoded source store survive.  This is an order replacement over unstarted
work, not a return to startup.

Ready-ahead is continuously maintained as urgency.  A unit entering the new
priority band can discard a previous soft-target capacity skip and is evaluated
with protected admission rank; distant work remains soft-target work.  Cache
retention and eviction still use the full direction/distance rank and combined
source+layout-frame byte budget.  No new page-count cache limit is introduced.
Layout/DPR/render-signature change keeps the book-scoped planner and decoded
sources but resets physical-frame capacity observations before rebuilding
layout-dependent frames.

`RasterBookRuntime._result_is_artifact_compatible` deliberately separates
artifact usefulness from presentation serial.  A completed result from the
preceding navigation may enter the source/frame stores only if the runtime is
accepting, source epoch and source identity match, render spec matches and the
unit still exists in the current topology.  `frameReady` remains tied to the
latest current key/request, so an artifact-compatible old result cannot commit
an obsolete page.  Book switch, source epoch change, incompatible layout,
cancel, retire and shutdown remain hard rejection/drain boundaries.

The concrete NivisViewer implementation is `app/raster_warmup_planner.py`
(`RasterWarmupPlan.priority_band_identities`, `iter_continuous_units`,
`RasterWarmupPlanner.recenter`, `release_after_first_commit`,
`discard_capacity_skip` and `next_candidate`) and
`app/zip_raster_book_runtime.py` (`RasterBookRuntime._adopt_request`,
`_active_job_is_artifact_compatible`, `suspend`, `release_continuous_warmup`, `release_prefetch`,
`_drive`, `_admission_rank`, `_on_job_completed` and
`_result_is_artifact_compatible`).  `release_startup_runway` remains only a
compatibility adapter; it is no longer a separate production phase.

### 21.5 Adopted, retained and rejected structures

| Decision | Structure | Result |
|---|---|---|
| **Adopt from ZipPlaFork** | One Viewer lane continues at each completion boundary without waiting for paint. | The released book-scoped planner calls `_drive` from every terminal job result and continues to the next useful candidate. |
| **Adopt from ZipPlaFork** | Replace the unstarted all-book order around latest current while allowing the sole started compatible job to finish. | `recenter` preserves planner/store state; `_active_job_is_artifact_compatible` adopts any started same-book/source/render-spec job that still belongs to the current topology, not only an urgent-band job. |
| **Adopt from ZipPlaFork** | Completed page artifacts survive navigation and are evicted by current-centered memory policy. | Compatible old-request results may populate the byte-budgeted source/frame stores without presentation publication. |
| **Hybrid modernization** | Current/next/previous bands. | NivisViewer uses direction-aware complete display units and a 4-forward/1-reverse urgent band before the remaining book, rather than ZipPlaFork's numerical next-before-previous page bands. |
| **Keep NivisViewer** | Atomic frame commit and old-frame rollback. | Surface mode, displayed page, slider and status change only at accepted complete-frame commit; spread/split never publishes a partial unit. |
| **Keep NivisViewer** | Epoch/request/layout/DPR fences, input coalescing and structured shutdown. | Artifact compatibility is separate from latest-request presentation authority; book/source replacement still drains callbacks safely. |
| **Reject upstream literal behavior** | Clear old resized arrays before open, advance current/status before pixels, refuse natural input beyond the ready frontier, half-gray the old canvas, and rely on wait-cancel/`Guid.Empty`. | Replacement frame is retained; requests remain coalesced; loading/error/empty are explicit; generation/serial fences remain mandatory. |
| **Reject upstream literal memory rule** | `MaxPageCountInWindow * 4` minimum bitmap admission and coupled source/display eviction. | Combined source/frame bytes remain the only capacity authority, with independent source/frame reuse. |
| **Reject unrelated fixed-commit change** | Catalog thumbnail semaphore set to one by `07955f5`. | Virtual PageList keeps its independent worker/runtime and budget; the Catalog commit is provenance context, not a Viewer algorithm port. |

### 21.6 Direct structural-port and license map

| Fixed-revision source / class / method | Derived processing principle | NivisViewer counterpart |
|---|---|---|
| `source/ZipPla/ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage` | One active Viewer page worker. | `app/zip_raster_book_runtime.py`, `RasterBookRuntime._submit`, `_drive` and active-job ownership. |
| `source/ZipPla/ViewerForm.cs:2919-3007`, `bmwLoadEachPage_RunWorkerStarting`; `:5558-5605`, `SetBackgroundMode` / `priorityLevel` | Book-wide page arrays and a current-centered replaceable work order. | `app/raster_warmup_planner.py`, persistent `RasterWarmupPlanner`, priority band and continuous all-book iterator; runtime `_adopt_request`. |
| `source/ZipPla/ViewerForm.cs:3177-3602`, `bmwLoadEachPage_DoWork` | One page job yields a terminal display-ready artifact or error. | Existing `_ZipRasterUnitJob.run` / runtime decode-render path; Nivis extends the job boundary to an atomic complete display unit. |
| `source/ZipPla/ViewerForm.cs:5371-5415`, `bmwLoadEachPage_EachRunWorkerCompleted`; `source/ZipPla/GenerarClasses.cs:247-264,266-339`, `SetWorksOrder` / completion selection | Current change is observed before the completion handler chooses the next unfinished job; one old active result remains reusable. | `RasterWarmupPlanner.recenter`, `RasterBookRuntime._active_job_is_artifact_compatible`, `_on_job_completed`, `_result_is_artifact_compatible` and `_drive`. |
| `source/ZipPla/ViewerForm.cs:5451-5524`, `SetNewResizedImage` / `ReduceUsingMemory` | Publish completed artifact only and remove lower-priority retained work under memory pressure. | `_ZipRasterFrameStore`, `_ZipRasterSourceStore`, `_admission_rank`, combined-budget enforcement and atomic cache insertion after successful QPixmap creation. |
| `source/ZipPla/ViewerForm.cs:6438-6487,6768-6879,6990-7280`, `showCurrentPage` / paint path | Paint is separate from worker continuation; upstream loading/display semantics were also evaluated. | Only the non-gating separation is adopted. `ViewerPresentationState` and `PresentationSurface` replace the upstream logical/display and half-gray behavior. |
| `source/ZipPla/PackedImageLoader.cs:283-330,1197-1243,1781-1867`, loader/index/entry stream | Book-scoped archive/index lifetime and entry-local read. | Existing `ZipImageSource` and `ZipRasterBookRuntime`; unchanged by this planner/surface replacement. |

These principles remain a direct structural port from the fixed
AGPL-3.0-or-later snapshot.  No C# statement, WinForms control, GDI bitmap loop
or `BackgroundWorker` implementation is copied literally in this section's
Python changes.  Original copyright and license material remains at
`licenses/ZipPlaFork/About.txt` and `licenses/ZipPlaFork/AGPL.txt`; section 1
and `THIRD_PARTY_NOTICES.md` remain applicable.

### 21.7 Historical persistent-planner baseline (superseded by section 22)

The implementation adds contract coverage for presentation-surface ownership,
zero-image open failure, old-layout first-frame rejection with an immediate
cold replacement request, normal resize-debounce restoration,
persistent planner identity/recenter state, continuous first-commit release,
complete spread units, active-job adoption, artifact-compatible old results and
book switch/close/cancel fences.

This table is the pre-adoption baseline retained to show why persistent planner
ownership alone was insufficient.  Its `cancel 8` result is not the current
production contract; section 22 records the focused started-job adoption A/B.

| Scenario / metric | Request-scoped section-20 predecessor | Persistent book-scoped planner | Result |
|---|---:|---:|---|
| Empty prompt during initial loading | Widget inferred `EMPTY` from no local images | Explicit `LOADING`; stale surface projection rejected | Window/Widget tests observe no demotion after complete commit. |
| Initial failure / failed or cancelled replacement | No explicit surface owner | `ERROR` / retained `DISPLAYED` | Old pixmap, page state and the same planner survive replacement rollback. |
| First-frame commit / offscreen draw | 16 ms / <1 ms | 16 ms / <1 ms | Same fixture; draw resolution is below the timer resolution. The controlled 20-ms paint-ack delay is synthetic. |
| First 10 turns, 8-ms cadence, single | 10 hit / 0 miss | 10 hit / 0 miss | Both are ready-hit fast paths; current reduces scheduling churn rather than improving this already-warm hit rate. |
| First 10 turns, zero-delay stress, single | 3 hit / 7 miss | 3 hit / 7 miss | No hit-rate improvement when the worker is given no population time. Total elapsed is 94 -> 94 ms; the final per-turn sample is 0 -> 16 ms at the coarse timer quantum, so this is not claimed as an improvement. |
| Spread / LTR / RTL / reversal | historical contracts | complete-unit order and latest-frame contract retained | 11 selected offscreen contract tests pass; this is structural validation, not a numeric A/B. |
| Ready-ahead at commit / paint / 50 / 100 / 250 ms | 0 / 2 / 5 / 11 / 30 | 0 / 3 / 7 / 13 / 30 | Current begins the continuous next work one completion earlier; both reach 30 by 250 ms. |
| Planner objects across initial + ten turns | 11 | 1 | Release remains one book-scoped event; navigation recenters the same owner. |
| 8-ms navigation churn, four-run median | jobs 25, cancel 9, stale 8 | jobs 24, cancel 8, stale 0 | Ranges: jobs 23--27 -> 23--24; cancel 8--10 -> 8--9; stale 7--9 -> always 0. |
| Duplicate successful decode | 0 | 0 | Compatible immutable artifacts are retained without duplicate decode or stale presentation publication. |
| 250-ms combined cache, representative run | 59,520,000 B | 59,520,000 B | No page-count retention limit or memory increase in the matched sample. |
| Shutdown / callback drain | clean | clean | Both child processes drained, closed the ZIP source and left no benchmark/pytest process. |

Production metrics already expose `ready_ahead_unit_count`,
`priority_band_target_units`, `continuous_warmup_releases`,
`warmup_planner_creations`, `warmup_planner_recenters` and
`compatible_old_results` alongside cache hit/miss, jobs, cancel, stale and
eviction counts.  They are diagnostic evidence only; byte budget, visual
correctness and real-device first-ten-turn behavior remain the acceptance
criteria.  Real application launch, native input and external GUI automation
remain outside this offscreen validation boundary.

The reproducible runner is `scripts/benchmark_persistent_raster_warmup.py`.
The matched fixture is one 69,606,482-byte `ZIP_STORED` archive containing 100
1,600 x 2,400 JPEG pages (SHA-256
`5875cf121405248842df98cc11e28c0c1cdc27d9206cd8af4191b26a7efd1b76`), a
400 x 600 target, 512 MiB hard / 448 MiB soft limits and a synthetic 20-ms
commit-to-paint acknowledgement.  `HEAD` was read-only `git archive` output at
`199ab05b8460160a64c2d20655f36984440cbb60`; no checkout or worktree mutation
was used.  Four independent 8-ms cadence runs had elapsed medians 195.5 -> 187
ms, but offscreen decode scheduling is not physical wheel latency.  The
zero-delay result is retained explicitly because it bounds the claim: this
change removes planner/stale churn and forms ready-ahead sooner, but it does not
make an already-cold final target decode faster than the decoder itself.

Final syntax/import checks and `git diff --check` passed.  The focused
presentation, BookSession, Widget/Window, planner, ZIP runtime, Folder runtime
and raster-Viewer integration group passed **130 tests** in one offscreen
process.  A separate 11-test selection covering the complete planner module,
LTR/RTL slot order, direction reversal/compatible-result adoption, complete
spread rotation and magnifier-source retention also passed.  The full
repository suite was not rerun for this change; the selected scope follows the
task's requirement to validate major ownership/work-order contracts rather
than grow or repeatedly execute unrelated boundary tests.

## 22. Fixed-revision input frontier and artifact-compatible started-job adoption (2026-08-21 worktree)

Section 21 established one persistent book-scoped planner, but its first
implementation could still cancel the sole already-started background job when
navigation moved that job outside the new urgent band.  This section records
the narrower final work-order correction: navigation replaces only work that
has not started; a started job for the same book/source/render specification is
allowed to finish and its compatible artifact is retained.  The requested page
still changes immediately while the displayed complete frame remains atomic.
The startup runway, input timers and cache/admission policy are unchanged.

### 22.1 Exact open, work-state and next-work sequence at the fixed snapshot

The following sequence was re-audited directly at ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:

```text
ViewerForm.OpenFile
  -> create/retain PackedImageLoader and enumerate/sort its image entries
  -> select currentPage and project the trackbar
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(arguments,
                                                         waitCancel=true)
     -> if the preceding work set is busy:
          CancelAsync marks that set cancelled and queues the new arguments
          the already-running DoWork is not preempted
          its eventual completion is forced to Cancelled and is not published
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData and the new book-scoped PackedImageLoader
        -> dispose the preceding loader only after the preceding work drains
        -> allocate whole-book source/info/resized/size arrays
        -> SetBackgroundMode
           -> priorityLevel for every numerical page
           -> SetWorksOrder(the resulting all-page permutation)
     -> BackgroundMultiWorker.RunWorkerAsync
        -> allocate Works, WorksStarted=false and ResultArgs=null per work
        -> choose WorksOrder[0] and mark that item WorksStarted=true
        -> run at most one Viewer item because ThreadCount=1
     -> bmwLoadEachPage_DoWork(WorkNumber)
        -> open only that entry when its retained source is absent
        -> decode, orientation/pre-filter and source retention
        -> resize/post-filter and return one complete VirtualBitmapEx
        -> return a terminal error bitmap for a per-entry failure
     -> bmwLoadEachPage_EachRunWorkerCompleted                 [UI callback]
        -> if currentPage changed, SetBackgroundMode(latest current)
        -> SetNewResizedImage: admit/publish the completed bitmap as cache data
        -> if it intersects the latest visible numerical band:
             showCurrentPage(false) -> Invalidate only
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> store ResultArgs[completed WorkNumber]
        -> if SetWorksOrder changed the order, reset the next-order cursor
        -> skip every WorksStarted item in the newest WorksOrder
        -> mark/start the first unfinished item immediately
     -> later WM_PAINT / pbView_Paint / pbView_PaintToCanvas
```

The relevant upstream methods are `source/ZipPla/ViewerForm.cs:2308-2771`
(`OpenFile`), `:2919-3007` (`bmwLoadEachPage_RunWorkerStarting`),
`:3177-3602` (`bmwLoadEachPage_DoWork`), `:5371-5415`
(`bmwLoadEachPage_EachRunWorkerCompleted`), `:5451-5524`
(`SetNewResizedImage` / `ReduceUsingMemory`) and `:5558-5605`
(`SetBackgroundMode` / `priorityLevel`), plus
`source/ZipPla/GenerarClasses.cs:247-339` (`SetWorksOrder`, `ReworkOrder` and
completion/next-work selection).  `ViewerForm.Designer.cs:1628-1636` fixes the
Viewer worker count at one.

`WorksStarted`, `ResultArgs` and resident bitmap state are distinct.  Waiting
means `WorksStarted=false`; running means `WorksStarted=true` and
`ResultArgs=null`; scheduler-completed means both started and a non-null result.
`ReworkOrder` makes an item waiting again by clearing `WorksStarted` even when
an older `ResultArgs` object still exists.  Conversely, a scheduler-completed
item need not still own a resident bitmap after memory eviction.  Navigation
does not cancel the one running work item.  It only replaces the unstarted
permutation; book/reload replacement uses the separate drain/cancel boundary.

`EachRunWorkerCompleted` executes before the internal completion handler chooses
the next work item.  It can therefore rebuild the order around the latest
`currentPage`, publish the just-finished bitmap, and then let the scheduler pick
the first unfinished item from that newest order.  `showCurrentPage` merely
invalidates the control.  The next decode normally starts before the queued
Windows paint, so paint is not a scheduling gate.

### 22.2 Natural WheelDown and forced one-page input are different models

The default natural wheel call sequence is:

```text
ExtendedKeys.WheelDown -> Command.NextPage
  -> getMouseGestureSettingTemplate -> moveToNextPage
  -> NextPage -> movePageNatural(1)
```

This path is in `ViewerForm.cs:15852`, `:13049-13050`, `:12403-12417`,
`:1903-1948` and `:9279-9360`.  It has no pending final target.  In single mode,
an unfinished current page rejects WheelDown without changing current, work
order or cancellation state.  A ready current page may advance once into a cold
destination; further WheelDown is rejected until that new current completes.
In ordinary two-page spread mode both pages of the current numerical band must
be ready before the move, after which it may likewise enter one cold next band.
Wide/cover branches can reduce that move to one-page behavior.  Thus ten rapid
natural WheelDown inputs immediately after a cold open accept zero moves while
page 0 is unfinished, or at most one cold step if page 0 becomes ready during
the burst.  More steps require intervening completion frontiers.  Inputs beyond
the frontier are dropped, not coalesced.

The default `MoveForwardOnePage` command is different:

```text
Down -> Command.MoveForwardOnePage
  -> movePageToMinimulForward
  -> moveDividedPage -> isMovablePage -> movePage
```

The path is `ViewerForm.cs:15858`, `:13053-13054`, `:9536-9589`,
`:9630-9694` and `:9759-9772`.  With `MaxDivision=1`, ten rapid commands can
advance logical `currentPage` from `c` to `c+10` while the sole job for `c`
continues.  They do not enqueue ten decodes.  At the next worker completion,
`bmwLoadEachPage_EachRunWorkerCompleted` observes the latest `currentPage`,
calls `SetBackgroundMode`, admits the completed old bitmap if memory permits,
and the internal completion handler starts the first unfinished item around
`c+10`.  Intermediate pages are decoded only if a worker completion happens
between input events.  This is **completion-boundary coalescing to the latest
logical current**, not a pending-request/final-target state machine.

For an ordinary middle-of-book current, the numerical order is:

| Binding | Fixed `priorityLevel` page band |
|---|---|
| single, `M=1` | `c`, `c+1`, `c-1`, then the remaining pages by distance |
| spread, `M=2` | `c,c+1`, then `c+2,c+3`, then `c-2,c-1`, then the rest |

At book start this becomes `0,1,2,...` in single mode.  In ordinary spread it
also begins `0,1,2,3,...`, and natural navigation normally waits for the page-0
band partner before accepting the next spread.  These are fixed number bands,
not wide/cover-aware complete display units.  `SetBackgroundMode` is called in
`bmwLoadEachPage_RunWorkerStarting` before the replacement `BindingMode` is
assigned later in that method, so the initial `M` can even reflect the prior
mode.  NivisViewer does not adopt either mismatch.

The one-input state model is therefore:

| Input/state | ZipPlaFork decode / retain / drop behavior |
|---|---|
| Natural input while current band is unfinished | Reject input; do not move current, create work or cancel; the running job continues. |
| Natural input from ready current to ready destination | Move current and paint retained bitmap immediately; background job continues and the newest order is selected at its completion. |
| Natural input from ready current to cold destination | Accept one move, keep the running job, draw the loading treatment; reject subsequent natural inputs until completion. |
| Forced one-page or trackbar move beyond the frontier | Advance logical current; do not create one job per input; retain the running result and choose latest current at the completion boundary. |
| Reversal to a resident page | Change current and repaint the resident bitmap; the sole old-direction job still finishes and may enter cache before the order recenters. |

### 22.3 Bitmap completion and memory admission

`SetNewResizedImage` is cache publication, not a displayed-frame commit.  Its
dynamic upper bound comes from `SetMemoryUBound`; active and inactive modes use
different fractions of current process/available memory.  Before admitting a
new `VirtualBitmapEx`, `ReduceUsingMemory` evicts from the tail of the latest
work order and disposes both that page's resized bitmap and pre-filtered source.
The new bitmap is admitted when it fits, or while the resident count is below
`MaxPageCountInWindow * 4` (four single pages or eight spread pages).  Otherwise
it is disposed and `ReworkOrder` makes that page schedulable again.  When the
required numerical neighborhood is resident but no further bitmap can be
admitted, the worker can pause at `ThreadCount=0`; later navigation calls
`SetBackgroundModeIfPausing` to resume it.

These rules explain why an old running job is not inherently stale in
ZipPlaFork: if its bitmap passes admission it remains useful book cache even
when `currentPage` changed during decode.  They also explain what is not being
ported: the fixed `M * 4` minimum, coupled source/display eviction and logical
current/loading canvas semantics are WinForms-era policies, not requirements of
the completion-boundary work-order principle.

### 22.4 NivisViewer Hybrid implementation

NivisViewer now adopts the useful upstream boundary without adopting its input
loss or split presentation state:

```text
latest navigation input
  -> accept final requested unit and serial immediately
  -> keep displayed page, slider, status and old complete frame unchanged
  -> RasterWarmupPlanner.recenter(latest complete-unit topology)
  -> active job handling
     -> exact current key: adopt latest request as current
     -> already started + same source epoch/identity/render spec + unit still
        in current topology: let it finish and adopt it as cache-producing work
     -> not started: remove/replace it before setting a cancellation flag
     -> worker already finished but GUI callback is still queued:
        release the physical one-worker slot immediately
        record its key as pending completion so it cannot be decoded twice
  -> compatible completion
     -> store source/frame artifact under the combined byte budget
     -> publish only when it is the latest current key/request
  -> final current completion
     -> atomic ViewerPresentationState/frame commit
```

The implementation is in `app/zip_raster_book_runtime.py`:
`RasterBookRuntime._adopt_request`,
`_active_job_is_artifact_compatible`, `_take_unstarted_job`,
`_release_finished_active_slot`, `_has_pending_completion`,
`_result_is_artifact_compatible`, `_on_job_completed` and `_drive`, with
diagnostic `work_order_changes`, `running_job_adoptions`,
`queued_job_replacements` and `finished_job_slot_releases`.  A same-book
compatible started job is no longer restricted to the 4-forward/1-reverse
urgent band.  `tryTake` is attempted before cancellation, closing the Qt
queued-to-running race without poisoning a job that just started.  A finished
QRunnable is detached from `_active_job` even if its queued QImage/QPixmap
publication callback has not run; `_pending_completion_keys` is the duplicate
decode fence until that callback is consumed.
Incompatible source epoch, source identity, render specification, topology,
book switch, retire, shutdown and close remain hard rejection boundaries.

This preserves the NivisViewer advantages: every input can update the final
requested target, the old complete frame remains displayed during the miss,
stale serials cannot mutate presentation, single/spread/wide/RTL use one actual
display-unit topology, and paint acknowledgement owns only displayed-resource
reclamation and deferred UI effects.  It does not release, pause or advance the
raster worker.  Across the initial request plus ten navigation requests, the
same planner reports **one creation and ten recenters**.

Only the artifact-compatible started-job policy and its metrics changed in
this focused implementation, together with the worker-finished/GUI-pending
state split needed to make that policy continue immediately.  The continuous
runway (`F1, R1, F2, F3, F4`),
input admission/coalescing timers, combined byte budget, retention rank,
eviction and source/frame cache formats are unchanged, making the A/B attribution
narrow.

### 22.5 Focused offscreen A/B

The before/after comparison uses the same fake/offscreen raster fixture and
changes only the focused work-ownership boundary above: started-job retention,
safe queued replacement and worker-finished/GUI-pending separation.  The
figures are supporting evidence, not a claim about native wheel feel or
physical paint latency.

The final rerun used one unchanged 100-page, 1600 x 2400 JPEG, ZIP_STORED
fixture (`69,606,482` bytes, SHA-256
`5875cf121405248842df98cc11e28c0c1cdc27d9206cd8af4191b26a7efd1b76`), a
400 x 600 physical target, 20-ms fake paint acknowledgement and the same
512/448-MiB hard/soft budgets.  Each cell is `before -> after`.  No runway,
timer, radius or budget value differs between the two sides.

| Scenario | first frame ms | ready hits / 10 | jobs | successful artifacts | cancel | running adoption | ready ahead | final ms | elapsed ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single LTR, 8 ms | 16 -> 16 | 10 -> 10 | 24 -> 24 | 15 -> 23 | 9 -> 0 | 1 -> 9 | 4 -> 12 | 0 -> 0 | 204 -> 203 |
| single RTL, 8 ms | 15 -> 16 | 10 -> 10 | 24 -> 24 | 15 -> 23 | 9 -> 0 | 1 -> 10 | 4 -> 12 | 0 -> 0 | 203 -> 203 |
| spread LTR, 8 ms | 15 -> 16 | 10 -> 10 | 16 -> 15 | 29 -> 29 | 1 -> 0 | 9 -> 10 | 3 -> 3 | 0 -> 0 | 203 -> 203 |
| spread RTL, 8 ms | 15 -> 15 | 10 -> 10 | 16 -> 16 | 30 -> 30 | 1 -> 0 | 9 -> 10 | 4 -> 4 | 0 -> 0 | 203 -> 203 |
| single LTR, no added delay | 16 -> 0* | 3 -> 2 | 12 -> 12 | 11 -> 11 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 0 -> 15 | 94 -> 94 |
| single RTL, no added delay | 0* -> 16 | 2 -> 3 | 12 -> 12 | 11 -> 11 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 0 -> 0 | 93 -> 94 |
| spread LTR, no added delay | 16 -> 16 | 2 -> 2 | 12 -> 12 | 22 -> 22 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 16 -> 16 | 141 -> 141 |
| spread RTL, no added delay | 15 -> 15 | 2 -> 1 | 12 -> 12 | 22 -> 22 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 15 -> 15 | 140 -> 140 |

All eight runs kept one planner, ten recenters, zero duplicate successful
decode, zero stale presentation result and clean shutdown.  `0*` is the
millisecond timer's resolution floor, not a zero-cost first frame.  The
no-added-delay rows wait for each frame before issuing the next request; they
are a cache-readiness stress bound, not a simultaneous input burst.  Their
one-hit scheduling jitter is therefore not used as an improvement claim.

The actual production burst contract is covered separately with page 1 held
inside its decode while ten wheel targets advance from page 0 to page 10:

| State after first-frame commit | Previous Nivis policy | Hybrid policy |
|---|---|---|
| input 1 | requested 1, displayed 0; running page 1 is exact current | same |
| input 2 | requested 2; set cancellation on running page 1 | requested 2; adopt page 1 as useful book work |
| inputs 3-10 | requested ends at 10; cancelled page 1 still occupies the sole worker | requested ends at 10; displayed remains 0; page 1 remains useful and is adopted nine times total |
| worker exits before GUI callback | page 1 result is discarded; final job waits for the queued callback to clear `_active_job` | page 1 key enters the pending-completion ledger and page 10 starts immediately without waiting for QPixmap publication |
| terminal artifacts / presentation | pages 0 and 10; one cancel; displayed sequence 0 -> 10 | pages 0, 1 and 10; zero cancel/stale/duplicate; displayed sequence 0 -> 10 only |

The decoded page order is `0, 1, 10` on the Hybrid path; page 1 is retained
instead of being thrown away, and no intermediate frame is committed.  This is
the direct answer to the open-runway problem: useful work and the latest order
are independently owned.  Adoption does not manufacture decoded pixels at an
actual 0-ms cadence, but it prevents navigation from converting an occupied
worker into discarded work.  Physical ZIP entry I/O, decoder throughput,
QPixmap upload, DWM/GPU presentation and native-input behavior remain for the
real-device check.

### 22.6 Adoption decision and AGPL provenance

| Decision | Fixed-revision source principle | NivisViewer result |
|---|---|---|
| **Adopt structurally** | `bmwLoadEachPage_EachRunWorkerCompleted` observes latest `currentPage`; `SetBackgroundMode` replaces the unstarted order; `BackgroundMultiWorker` lets the sole started work finish and selects next work at completion. | `RasterBookRuntime._adopt_request` keeps a started artifact-compatible same-book job and replaces an unstarted job; `_on_job_completed` stores the artifact, while only latest-current authority can publish. |
| **Adopt structurally** | `SetNewResizedImage` makes a completed old-current bitmap reusable cache data; worker continuation does not wait for paint. | Compatible old-request results can enter the frame/source stores; `_drive` continues independently of paint acknowledgement. |
| **Modernize the boundary** | The WinForms completion callback publishes the bitmap and then its internal handler immediately chooses the next work before paint. | A finished QRunnable releases the scheduler slot before its queued GUI callback; a pending-key ledger prevents duplicate decode, so QPixmap upload and callback order do not gate a different final target. |
| **Keep NivisViewer** | No upstream equivalent for requested/displayed serial separation or atomic complete-unit commit. | Final requested input is accepted, displayed/slider/status remain on the prior complete frame, and spread/wide commit only as one complete unit. |
| **Do not adopt** | Natural WheelDown drops input at the unfinished frontier; forced movement advances logical current/status before pixels; loading uses a half-gray canvas. | Input coalescing retains the final target and PresentationState prevents partial/stale display publication. |
| **Do not adopt** | Fixed numerical `M=1/2` bands, coupled source/display eviction and the `M * 4` minimum bitmap rule. | Actual display-unit topology and the existing combined byte-driven source/frame stores remain authoritative. |

The structural source is
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) at fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ViewerForm.cs` methods `bmwLoadEachPage_RunWorkerStarting`,
`bmwLoadEachPage_DoWork`, `bmwLoadEachPage_EachRunWorkerCompleted`,
`SetNewResizedImage`, `SetBackgroundMode`, `NextPage`, `movePageNatural` and
`movePageToMinimulForward`, plus `source/ZipPla/GenerarClasses.cs`
`SetWorksOrder`, `ReworkOrder` and the internal completion handler.  The source
is AGPL-3.0-or-later.  Original copyright and license materials remain in
`licenses/ZipPlaFork/About.txt` and `licenses/ZipPlaFork/AGPL.txt`, with the
notice in `THIRD_PARTY_NOTICES.md` and section 1 still applying.

As in section 21, the fixed hash is a source snapshot, **not** the commit that
introduced the one-worker Viewer setting.  Commit `07955f5` only changes the
Catalog thumbnail semaphore; the audited Viewer worker was already present in
`5e942ef0006decf01c21d9dbe037dba50e763420`.  The NivisViewer implementation is
a Python/Qt structural translation of the completion-boundary ownership rule;
it does not copy WinForms controls, GDI bitmap arrays or `BackgroundWorker`
statements literally.

## 23. Magnifier artifact and manual viewport transform

This section fixes the interactive magnifier and manual-zoom pan boundary. It
does not alter the book runtime, navigation work order, decoded-source store or
normal display-frame cache described above.

### 23.1 Fixed-revision Magnifier flow

At revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, ZipPlaFork separates
construction of a magnified page canvas from movement over that canvas:

```text
MagnifierPhase1
  -> validate current resized/pre-filtered pages and enter MagnifierOpening
  -> MagnifierPhase2 waits for Magnifier_ZoomedPage/current-page readiness

bwMagnifierMaker_DoWork
  -> read retained PreFilteredImageArray pages
  -> choose MagnifierScalingAlgorithm.ScaleUp or ScaleDown
  -> resize each required page once into MagnifierCanvas
  -> publish Magnifier_ZoomedPage in RunWorkerCompleted

pbView_MouseMove while ViewerMode == Magnifier
  -> retain latest cursor position
  -> pbPaintInvalidate only

paint
  -> GetMagnifierRectangle computes the latest source rectangle
  -> draw the relevant region from Magnifier_ZoomedPage.Bitmaps/Offsets
```

`TouchListener_Pan` accumulates a position delta consumed by
`GetMagnifierRectangle`; `MagnifierAutoScroll` likewise changes that position.
Neither path reconstructs `Magnifier_ZoomedPage` for each movement. A page,
zoom/layout or source change is the artifact-generation boundary; cursor/touch
movement is a viewport boundary. The prepared canvas may be queued while the
opening preview is visible, and `MagnifierPhase2` enters the final mode only
when the matching current-page canvas is ready.

The principal source locations are `source/ZipPla/ViewerForm.cs`:
`bwMagnifierMaker_DoWork` / `RunWorkerCompleted` (`:7720-8018`),
`MagnifierPhase1` / `MagnifierPhase2` (`:8051-8088`),
`GetMagnifierRectangle` (`:8137-8370`), `MagnifierAutoScroll`
(`:8384-8460`), `pbView_MouseMove` (`:10578-10623`),
`TouchListener_Pan` (`:1449-1530`) and the Magnifier paint branch
(`:7026-7058`).

### 23.2 Previous NivisViewer critical path

Before this change, every active-lens `ViewerWidget.mouseMoveEvent` called both
`_update_magnifier_selection` and `_request_magnifier_render`. The cursor crop
was part of `ViewerRenderKey`, so each distinct position incremented a request
generation, attempted to cancel the preceding task and scheduled a new
`ViewerRenderTask`. That worker cropped a `QImage`, converted through Pillow,
resampled to the viewport, returned another `QImage`, and the GUI callback ran
`QPixmap.fromImage`. Repaint also emitted the normal page-paint notification,
allowing unrelated after-paint scheduling to run during pointer tracking.

The old/new one-move sequences are:

| Boundary | Before | After |
|---|---|---|
| Cursor event | compute crop and create crop-specific generation/key | store latest normalized source rectangle |
| Worker | cancel/replace or run crop + resample | none |
| GUI upload | new `QPixmap.fromImage` for a completed crop | none |
| Cache | insert position-specific result | retain the page/zoom artifact |
| Paint | draw full viewport crop pixmap and emit page paint callbacks | draw the latest artifact source rectangle; no page-commit callback |

### 23.3 Hybrid artifact design

NivisViewer adopts ZipPlaFork's preparation/movement split as a modern Qt
Hybrid. `ViewerWidget._request_magnifier_render` creates one independent
artifact identity from source `QImage.cacheKey`, physical artifact target,
DPR, rotation, split range and the selected magnifier down/up algorithms. The
cursor crop and navigation request serial are deliberately absent. A small
two-entry LRU retains the current/recent artifact; it is capped at 160 MiB and
each artifact at 32 million physical pixels (at most about 128 MiB for a
32-bit pixmap). Pathological images that reach the cap use QPainter's final
source-rectangle mapping instead of allocating an unbounded canvas. A tiled
renderer was not added because ordinary manga pages are covered by this
bounded whole-artifact design.

The current preview source can produce an immediate provisional artifact.
`magnifierSourceResolutionRequested` / `magnifierPdfResolutionRequested` is
emitted at most once for that source identity; cursor motion does not re-enter
promotion. When the runtime attaches the current page's full source,
`resume_magnifier_after_source_render` remaps the normalized viewport and
builds one replacement artifact. The old preview remains visible until that
artifact is complete. Source/page/render-generation and artifact-key checks
reject late callbacks after page change, book switch, close or lens cancel.
The normal ready-frame cache and its resampling authority are unchanged.

One newly committed display frame sets a paint-ack flag. `contentPainted` and
`framePainted` are emitted only by its first completed paint; later magnifier
or pan repaints are pure viewport operations. This keeps PageList, history,
progress, prefetch and Browser projection out of the pointer-move call path.

### 23.4 Manual zoom and pan ownership

`ViewerWidget.ViewTransform` is now the single viewport-state owner for zoom
factor, pan offset, cursor zoom anchor, physical viewport size, physical
display-unit bounds, rotation, DPR and transform generation. Left-button
movement becomes pan only after the existing pointer-controller drag
threshold. A sub-threshold release still follows the configured canvas click
navigation. Open-hand/closed-hand cursors expose the two states.

Pan changes only `ViewTransform.pan_offset` and schedules a QWidget repaint;
it neither invalidates a layout artifact nor creates a worker, decoder,
resampler or QPixmap. Overscroll is clamped independently on each axis and an
axis smaller than the viewport remains centered. Page change and return to a
fit mode reset pan. Resize/DPR recomputes physical bounds and clamps the
existing pan. Rotation currently retains the established reset behavior; a
future source-coordinate focus model would be needed to preserve a meaningful
focus through 90-degree axis exchange.

Ctrl+wheel retains the existing zoom gesture but now treats the cursor as its
anchor: the normalized display-unit point under the cursor remains there after
the scale change, subject to edge clamping. Arrow keys pan while the manual or
actual-size image can move; Shift uses a larger viewport-relative step. At an
edge, the pan operation returns false and the existing page-navigation handler
can receive the key. NivisViewer has no production touch-pan gesture owner at
this boundary, so no synthetic touch subsystem was added; future touch input
should call the same clamped `ViewTransform` operation.

### 23.5 Focused offscreen contract

The focused fake-mouse/offscreen checks exercise 100 cursor moves after the
artifact is ready, preview/full-source promotion, PDF promotion, manual zoom at
200%, drag threshold/clamping, cursor-anchored zoom, keyboard pan, click
navigation coexistence, direct ZIP/Folder presentation and paint
acknowledgement. For the 100-move lens case the post-artifact counts are:

| Operation | Before (structural count) | After |
|---|---:|---:|
| crop/resample requests | 100 | 0 |
| worker jobs | up to 100 position jobs | 0 |
| `QPixmap.fromImage` | up to 100 completions | 0 |
| source promotion requests | could be re-entered by movement | 0 (one at lens start only when needed) |
| page-commit paint callbacks | up to one per serviced repaint | 0 |

The initial lens open or a source/zoom/algorithm change still performs exactly
one necessary artifact job and one QPixmap upload. Pan tests keep render
generation, render-cache keys and task count unchanged. These counts establish
the production boundary but are not a claim about native mouse latency, GPU
upload or DWM presentation; those remain real-device checks.

### 23.6 Provenance

The structural source is [`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork)
at fixed revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ViewerForm.cs` methods and fields listed in section 23.1. It is
AGPL-3.0-or-later. License and copyright notices remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt` and
`THIRD_PARTY_NOTICES.md`. NivisViewer copies the prepared-canvas/view-position
separation structurally, not the WinForms/GDI control or bitmap statements;
the bounded Qt artifact cache, DPR-aware identity, preview/full promotion,
stale fences and `ViewTransform` are NivisViewer-specific implementations.

## 24. Browser filename rating and file detail (2026-08-22)

### 24.1 Fixed-revision ZipPlaFork contract

The rating source was re-audited at fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`. `source/ZipPla/ZipPlaInfo.cs`
class `ZipPlaInfo` constructs `fileNameRegex`, parses the first valid block in
its constructor, exposes `Rating`, serializes known values with
`GetInfoString`, and places the canonical block before the final extension in
`GetPathOfCurrentInfo`. The grammar is a case-insensitive, optional
single-leading-space block `{zpi$...}`. Known semicolon-separated parameters
are serialized in `c`, `b`, `r`, `t`, `d` order. Rating is `r=1` through
`r=5`; no token means unrated, and zero is not a separately persisted state.
Changing only `Rating` preserves parsed cover, binding, tags and legacy
direction. Invalid blocks such as `r=0`, `r=6` or unknown parameters do not
match the whole metadata grammar and remain ordinary filename text.

`source/ZipPla/CatalogForm.cs` draws five stars at the thumbnail's upper-left
after the thumbnail layer: completed stars are Gold and remaining stars are
LightSlateGray over a black background. It also draws before thumbnail loading
has completed. Horizontal position maps linearly and clamps to 1..5. Hover is
preview-only; left click changes the pointed item even when other rows are
selected, middle click clears it, and context-menu commands apply none/1..5 to
all selected items. Rating sorting leaves unrated entries behind rated entries
in both directions. The fixed revision contains rating filtering, but
NivisViewer has no existing Browser query/filter authority into which it can
be added without inventing a new language, so filter support remains a future
UX unit.

### 24.2 NivisViewer Hybrid implementation

`app/zippla_filename_metadata.py` is a direct Python structural translation of
the parser/serializer contract above. `app/rating_rename_service.py` performs a
same-directory rename only: it checks collision, never rewrites payload bytes,
verifies nanosecond mtime after rename, and restores only if the backend
changed it. Browser scanning parses the filename without opening the file and
separates physical path from the metadata-free display name.

`BrowserItemModel` owns the parsed value, preview value and rating sort. Its
rating-rename relocation changes the path identity in place, migrates the
existing thumbnail `QImage`, preview/error/signature, cut state and cached
dimensions, and then re-sorts without a directory rescan. Therefore a direct
rating change performs no image read/decode and no QPixmap construction.
Selection/current/scroll identities are remapped after a rating sort move.
Browser navigation snapshots already derive from the model's visible order,
so rating-sorted Folder Viewer next/previous retains that same topology.

`BrowserItemDelegate` provides a DPR-aware upper-left overlay, five-way hit
test and hover preview while preserving IconMode virtualization. A direct
left/middle click is consumed before Qt changes the multiselection, so it
changes that one file only. The context menu is the explicit batch authority.
Files owned by a live Viewer use the existing affected-Viewer confirmation and
close contract; the current immutable Folder book topology has no safe live
path-relocation API. NivisViewer has no filesystem watcher in this Browser, so
there is no duplicate self-event to suppress; the model is updated
synchronously and a later explicit refresh reads the filename authority.

The status bar's permanent right-hand `browser_file_detail_label` contains
only file size and logical image dimensions. Dimensions reuse the Browser
model cache or a one-thread Pillow header probe (no pixel `load`); EXIF
orientations 5..8 swap logical axes. A selection generation plus normalized
path identity rejects late results. Archive/PDF/folder selections do not probe
cover dimensions, and multi-selection retains the existing left-hand status
while clearing this single-image detail.

### 24.3 Provenance

The translated grammar, star interaction and sort semantics derive from
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork), revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ZipPlaInfo.cs` (`ZipPlaInfo` constructor, `Rating`,
`GetInfoString`, `GetPathOfCurrentInfo`) and `source/ZipPla/CatalogForm.cs`
(catalog rating paint/hit/rename/context/sort handlers). That source is
AGPL-3.0-or-later. Original notices and license text remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt`, and
`THIRD_PARTY_NOTICES.md`. Qt delegate painting, asynchronous header probing,
model cache relocation, stale fences and status-bar placement are
NivisViewer-specific modernizations.
