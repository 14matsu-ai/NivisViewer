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
| ページ移動要求 | `ViewerForm.cs:12403`, `moveToNextPage`; `:12424`, `MoveToPreviousPage`; `:9279`, `movePageNatural` | `NextPage` / `PreviousPage`が有効な完成済み表示単位を返した場合だけ`currentPage`を変更する。移動不能時は旧currentへ戻す。 |
| current / 未完成判定 | `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage` | singleでは現在unitがreadyなら次の未完成pageへ1つ進み、そこで後続入力を止める。spreadのprevious等には次unitのsize/ready不足で移動しない分岐がある。ready frontierを越えて最終入力pageへcoalesceする設計ではない。 |
| 方向管理 | `ViewerForm.cs:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | 直前の移動方向を状態として保持しない。常に新しい`currentPage`を中心にpriorityを再計算する。 |
| current / next / previous priority | `ViewerForm.cs:5586`, `priorityLevel` | level 0=current display unit、level 1=数値上のnext display unit、level 2=数値上のprevious display unit、level 3=その他。逆方向移動中でも数値上のnextがpreviousより先である。 |
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
| spread protection | `ViewerForm.cs:1903-2002`, `NextPage` / `PreviousPage`; `:5273`, `GetResizedSize`; `priorityLevel` level 0 | current binding内の最大1/2 pageを同じ表示単位としてsize判定し、partnerもlevel 0で優先する。 |

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
| priority制御 | current unit → 数値上next → 数値上previous → rest | current 800、spread/prepared 700以下、interactive 600、next 500、previous 400。decode/render共通domain | NivisViewer | currentとspreadを保護しつつ、renderがdecode priorityに負けない | 構造移植 |
| direction reversal handling | directionを追跡せず、新current基準で全order再計算 | directionを追跡し、queued decode/renderを昇格・降格し、paced planを作り直す | NivisViewer | 反転直後の直近両側を保護し、旧方向の未開始workを残しにくい | 部分移植（独自拡張を維持） |
| cache structure | source/displayを別配列で持つがpage indexとeviction lifecycleを共有 | source `QImage` cache、render `QPixmap` cache、prepared unit metadataの二層 | ZipPlaForkは単純、NivisViewerはQt機能要件に適合 | Nivisはmagnifier、resize、resampling再生成のためsource/display分離が必要だが複雑 | 維持 |
| eviction policy | display artifact bytesを基準に遠方からevictし、同page sourceも同時破棄 | sourceとpixmapを双方byte計上し、それぞれpriority-aware eviction | NivisViewerの計上精度、ZipPlaForkのlifecycle単純性 | Nivisは実resident artifactをより正確に上限へ反映する | 部分移植 |
| prefetch policy | 全page work setをcurrent近傍から1 jobずつ消化 | current paint後、1 display unitずつdecodeからprepared terminalまでpaced実行 | NivisViewer | 通過pageや複数unitの同時未完成を抑止し、設定した近傍だけに限定できる | 構造移植 |
| display apply timing | normal moveはtarget readyまで`currentPage`を進めない | logical targetは先へ進むがvisualはold frameを維持し、完成unitだけatomic commit | visual品質は同等、state整合はZipPlaFork | 暗転は双方回避するが、Nivisはslider/statusが画像より先行し得る | 部分移植候補 |
| dark-frame avoidance | ready確認後に移動し、reusable canvasを無条件clearしない | old complete `QPixmap`を新unit完成まで保持 | NivisViewer | Qtで安全なatomic swapを行い、error terminalもpaint完了として扱う | 維持 |
| cold miss behavior | current pageのliteral 1 jobが完了してから次job | current visibleのみをpaced single laneでdecode→signal→render→commit→paintし、その後prefetch | 構造要件は同等、cancel/stale拒否はNivisViewer | 4096 x 6500 JPEGのforced coldでcurrent以外を先行decodeせず、53.336–60.200 msでpaintした | 構造移植 |
| memory use | full decoded sourceとresized artifact。`usedMemory`はdisplayのみ計上 | standard JPEGはdecoder-size source、source+pixmapをcombined byte計上 | NivisViewer | 大JPEGでfull source rasterを常駐させず、二重満額budgetも廃止 | 部分移植 |
| large ZIP behavior | entry streamを同じpage jobのdecoderへ渡す。optional archive-memory modeあり | entryをpreallocated `BytesIO`へ展開し、decoder-scale後に別render task | 既知の実機体感はZipPlaFork、offscreen構造検証はNivisViewerも合格 | Nivisはbuffer copyが残る一方、decoder-scaled JPEG、current-only cold start、通過job抑止が有効。実機の同一ZIP再確認は未実施 | 構造移植、entry stream直結は検討継続 |
| stale result rejection | wait-cancelとloader/array replacement | request ID、source/render generation、source identity | NivisViewer | 高速入力、resize、book replacementの古い結果を明示的に拒否できる | 維持 |
| spread protection | current binding partnerをlevel 0、size/ready判定を一体化 | decode protection、prepared-unit protection、atomic multi-slot commit | NivisViewer | active paced spreadを含め、source/render両層で保護する | 維持 |
| QPixmap / paint | GDI `VirtualBitmapEx`とreusable bitmap canvas | worker `QImage`、GUI-thread one-time `QPixmap.fromImage`、cached paint | NivisViewer | QtのGUI resource境界を守りつつnavigation時変換を避ける | 維持 |
| Browser contention | Viewer比較対象内にNivis相当のapplication-wide lane制御なし | current cold中はBrowser laneをpause、paint/fallbackでrelease | NivisViewer | Viewerを他画面のdecode競合から保護する | 維持 |

### 4.1 Final comparison for eligible ZIP/JPEG

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針（維持 / 部分移植 / 構造移植 / 全置換） |
| --- | --- | --- | --- | --- | --- |
| decode pipeline | persistent entry streamからnative decoderへ渡し、同じpage jobでorientation、resize、display bitmapまで作る | 新Bはentryを一つのreserved `QByteArray`へ読み、seekable `QBuffer` / `QImageReader`でdecoder scaleし、同じjobでdisplay-ready `QImage`まで作る | native streamはZipPlaFork、Qt/Python上の実測は新B構成 | Python sequential streamは約1,985 callback/pageで旧Aより遅かった。QByteArray版は1 materializationを許容してcallbackをC++内へ戻す | 構造移植 |
| scheduler / queue | page worker 1、completion 1、完了境界でpriority orderを再評価 | `_ZipPlaCompatibleRasterJob`最大1、completion 1、先行queueを作らない | 同等、新Bはstale境界が強い | cold currentは旧A 2 task / 2 callback、新B 1 / 1 | 全置換 |
| priority制御 | current → numeric next → numeric previous | current完成・matching paint後に移動方向側 → 反対側 | 新B | current paintまで近傍I/O/decodeを開始しない | 構造移植 |
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
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork`; `:5371`, completion; `:5451`, `SetNewResizedImage` | current unitをdecodeからdisplay-ready publishまで完了してから遠方unitへ進む | `app/viewer_window.py`, `_apply_pending_decode_demand`, `_start_raster_prefetch_pipeline`, `_advance_raster_prefetch_pipeline`, `_on_viewer_content_painted`; `app/viewer_widget.py`, `prepared_display_is_ready`, `renderWorkFinished` | 構造移植。Nivisはliteral 1 QRunnableではなく、decode task → GUI signal → render taskのpaced single lane。 |
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
| 5. Prefetch | `priorityLevel` orders the current display unit first, numeric next second, previous third, then remaining pages; a changed current recomputes the order.  Memory admission can stop/rework the frontier. | Window raster plan, `ImageCache` wanted/protected/ranks, Widget prepared-unit requests, compatible-path neighbors, and multiple idle/paint timers all plan related work. | Four planners must agree. Direction reversal can leave a running legacy decode or render, and cache admission/prefetch release are owned by different objects. | A replaceable work order provides one current -> forward neighbor -> reverse neighbor policy and stops remote work when the page-artifact budget is full. | High | Spread partner is part of level 0; configurable direction and PDF rolling behavior remain policy inputs. | **Full replacement** inside the new runtime. |
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
| `GenerarClasses.cs:247`, `SetWorksOrder`; `ViewerForm.cs:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | The current display unit is first, followed by the current-direction neighbor and reverse neighbor. At most one job is active; a new request replaces the pending order and can leave at most the one already-running obsolete decoder. Prefetch is released only after the current frame paints. | `ZipRasterBookRuntime.request`, `_drive`, `_submit`, `_cancel_active_job`, `release_prefetch`; `ViewerWindow._zip_runtime_request`, `_on_zip_runtime_frame_painted` |
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
- `put` retains a completed frame until the unit limit or byte budget is
  actually exceeded;
- `_prune` evicts the lowest-ranked non-current frame only under real pressure;
- `can_admit_prefetch` allows a newly important neighbor to replace a farther
  frame, but stops low-priority decode before it would merely evict an
  equal-or-better retained frame;
- the active execution frontier remains bounded to current, next and previous,
  stays one-worker-wide, and remains paint-gated. The replacement therefore
  does not turn idle time into an unbounded whole-book decoder.

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

The production NivisViewer ZIP path at the start of this audit is:

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
| Worker / scheduler | **+** One Viewer page job and replaceable `SetWorksOrder`; current/next/previous simplicity. **-** active stale work is not cancelled, reordering waits for its completion, all-page ordering/sorting remains, and `WorkSetGuid` is effectively `Guid.Empty` rather than a generation fence. Resize may use internal parallel execution, so this is not a one-compute-thread design. | **+** One ZIP lane, latest current, cooperative entry cancel, request/source epoch stale fence and paint-gated prefetch. **-** legacy formats still have load/render/prepared stages. | ZIP **C, already superior Hybrid**. Keep the ordering principle, not `BackgroundWorker`, ineffective generation or thread-priority details. |
| Prefetch | **+** One page job at a time and priority is rebuilt around current; exact `ResizedImageArray` hits bypass new work. **-** priority is always current then index-increasing neighbor then index-decreasing neighbor, not navigation-direction aware; active stale work finishes first, work eventually covers the book, and there is no paint acknowledgement. | **+** only current/directional next/previous, current before neighbor, post-paint release, exact-frame worker bypass and combined source/frame memory admission. **-** a 250 ms no-paint fallback and coordinator bookkeeping remain. | **C.** Preserve the bounded/direction-aware Nivis implementation while retaining ZipPla's simple ordered frontier. |
| Cache | **+** `PreFilteredImageArray` and `ResizedImageArray` separate source/display while sharing page lifecycle; an exact ready hit paints without a worker. **-** array ownership and manual dispose are form-wide and tied to GDI objects. | **+** deterministic unit/byte budget and ready frame worker bypass. **-** `_UnitKey` previously coupled decoded QImage to viewport/DPR/rotation/zoom frame identity. | **D.** Book-scoped decoded-source store plus layout-specific frame store, with paired validity and one combined budget (implemented). |
| Memory management | **+** source plus resized bytes are counted and least-useful pages are reduced. **-** budget changes with `ActiveForm` and volatile physical memory, eviction runs from an approximately 500 ms GUI timer, several pages can remain over the nominal budget, and scaled/source artifacts for one page are discarded together. | **+** explicit byte/unit settings and protected current complete frame. **-** Qt/plugin/transient compressed allocations remain unobservable; PageList has a separate budget. | **D.** Deterministic combined ledger now covers ZIP QImage+QPixmap without double-counting implicit shares; future runtimes need reservations and soft OS-pressure input. |
| Page state | **+** simple current page. **-** `currentPage`, trackbar and status can move before the target bitmap is ready; old CPU canvas is gray-masked as loading. | **+** `ViewerPresentationState` separates requested/displayed and commits slider/status/history/progress with a complete frame. **-** bookmark/export commands still use the requested `PageModel.current_index` in a few paths. | **B.** Preserve PresentationState; later move bookmark/export defaults to committed state. |
| Display commit | **+** completed resized bitmap is inserted in one UI completion. **-** no explicit epoch/layout/DPR token or immutable complete-spread transaction. | **+** complete single/spread atomic swap, old-frame retention, stale rejection and GUI-only QPixmap. **-** commit projects a broad action refresh. | **B.** Keep NivisViewer. Optimize projection only after profiling. |
| Page list / thumbnail | **+** visible-region loading and release rather than eager full-book thumbnails. **-** paint-time Catalog machinery, GDI ownership and a static semaphore shared by all ThumbViewers; cancellation is not passed into an active `GetThumbnail`. | **+** virtual Qt model, visible margin, O(1) mapping, independent source/cache/worker, pause/cancel, byte budget. **-** first use opens a second archive/document session. | Historically **D**, now **B / maintain**. NivisViewer's completed modern design is stronger than direct Catalog translation. |
| Input handling | **+** compact direct command mapping. **-** ready-frontier navigation can prevent rapid input from reaching a final cold page. | **+** wheel/shortcut/slider/PageList converge on one navigation/presentation boundary; rapid input coalesces to latest request. **-** requested PageModel moves ahead of displayed by design, so non-presentation commands must use the right owner. | **B/C.** Keep Nivis UX and ZipPla-style internal work-order rebuild. |
| Spread / LTR / RTL | **+** current binding pages share level-0 priority; `NextPage`/`PreviousPage` keep the current unit until required slots are ready, so a partial spread is not painted as the new page. **-** topology and Viewer loading are intertwined. | **+** pure `PageModel.spread_at` topology, complete-unit publication, wide split and RTL half order. **-** an initially unknown wide page can cause unit reconstruction. | **B**, while retaining ZipPla's complete-unit principle and combining it with source reuse. |
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
  -> ViewerPageNavigationController / PageModel requested target
  -> ViewerPresentationState.request_frame
  -> ViewerWindow builds current -> directional next -> reverse neighbor
  -> exact frame lookup
       hit: synchronous complete-frame publication
       miss: RasterBookRuntime.stage (immediate order/cancel/retention fence)
             -> adaptive replaceable cold admission
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
| Rapid cold input | Stops at the ready frontier; no final-target coalescing | A 6 ms timer delayed both decode and adoption of the new work order, so old prefetch/current work continued during the wait | **Hybrid/new design:** immediately stage every intent, but admit only the leading/final cold target. Input is not dropped. |
| Work-order reversal | Reorders unstarted work around latest `currentPage`; active work finishes | New order reached the runtime only after the timer | **ZipPla principle adopted:** order, retention and obsolete cancellation change at input time. Nivis epoch/serial cancellation remains stronger. |
| Ready hit | Reads completed resized image directly | Already bypassed timer, worker, decode and `QPixmap.fromImage` | **Nivis maintained:** synchronous atomic commit remains; per-input cache-limit pruning was removed. |
| Cold current / prefetch | One worker, active work not preempted | One active job and paint-gated prefetch, but delayed staging could let old-direction work continue | **Nivis Hybrid maintained/improved:** current first, cancel/stale fence, prefetch only after matching paint. |
| Source vs display retention | Original/resized artifacts have a simple page lifecycle | Source eviction also deleted a still-valid QPixmap frame | **Modern design:** a display frame survives source eviction; last painted and requested frames are protected until replacement paint. Magnifier rehydrates a missing source on demand. |
| Commit UI work | WinForms updates current controls in `showCurrentPage` | PageList scroll/work, all menu QAction writes and metadata staging ran synchronously before paint | **Modern design:** slider/status and semantic commit stay synchronous; invariant full-action sync is removed; PageList scroll/work and persistence are serial-guarded/coalesced side effects. |
| Replacement open | Old form state remains until new open completes | Provisional open cleared old runtime artifacts before success | **Nivis ownership fix:** stop old work but retain completed artifacts until replacement success; failed open restores a ready hit. |

`RasterBookRuntime.stage` is a two-phase navigation contract.  It installs the
new current/work order, updates eviction ranking, stops obsolete prefetch and
cancels a replaceable active job without starting a cold target.  A later
`request` for the exact staged object opens the execution gate.  A completion
arriving while staged may be retained but cannot change presentation state.
Already-cancelled A jobs are never re-adopted in an A -> B -> A reversal.

The cold gate keeps a 6 ms leading delay for a discrete miss.  Once another
normal navigation arrives within 120 ms, its trailing delay is derived from
the observed input cadence (`1.25 * interval + 2 ms`, clamped to 8-32 ms).
The first 18-48 ms version removed transit work but made already-coalesced
0-4 ms bursts needlessly slower; the measured margin now stays just beyond
the packet cadence instead of becoming the dominant latency.

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
| `ViewerForm.cs`, `NextPage` / `PreviousPage` ready-frontier checks | Do not start decode for every transit page of a cold burst | `ViewerWindow._raster_cold_dispatch_delay` and staged cold admission |
| `ViewerForm.cs`, `SetNewResizedImage` / `showCurrentPage` | Publish only completed resized artifacts and reuse ready output directly | frame-store hit, `commit_display_ready_frame`, last-painted protection |

ZipPlaFork's dropped input, fixed index-side preference, uncancellable stale
job, all-book prefetch, pre-paint background work, WinForms/GDI canvas and
pre-completion logical-control update were deliberately not adopted.

### 16.4 Production-path admission A/B

`scripts/benchmark_raster_navigation_critical_path.py` uses four fresh child
processes: ZIP/Folder times benchmark-only baseline A and production B.  A
changes only the child process (`stage` is a no-op and every cold delay is
6 ms); it does not add a product fallback.  B uses the production staged
runtime.  The same temporary 24-page fixture alternates 900 x 1,350 and
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
