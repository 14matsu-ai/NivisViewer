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
| current / 未完成判定 | `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage` | 必要な`ResizedImageArray`または`ResizedSizeArray`が未完成なら、通常のnatural navigationは現在位置を返して先へ進まない。 |
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
| 表示差し替え | `ViewerForm.cs:5371`, `bmwLoadEachPage_EachRunWorkerCompleted`; `:6438`, `showCurrentPage` | current unitの完成結果が公開された後にinvalidateする。normal navigation自体も未完成先へ進まない。 |
| paint / 暗転回避 | `ViewerForm.cs:6768`, `pbView_Paint`; `:6990`, `pbView_PaintToCanvas` | reusable GDI canvasへdisplay-size artifactを描き、最後にcanvasを表示する。通常移動では未完成先へ切り替えず、無条件のblank clearを挟まない。 |
| prefetch | `ViewerForm.cs:5558`, `SetBackgroundMode`; `GenerarClasses.BackgroundMultiWorker` | page配列全体をwork setとし、current近傍から遠方へ1 jobずつ進む。memory admission不能時は再work化または必要近傍完成後にpauseする。 |
| source / display cache | `ViewerForm.cs` fields `PreFilteredImageArray`, `OriginalImageInfoArray`, `ResizedSizeArray`, `ResizedImageArray` | decoded/filter済みsourceとdisplay-ready artifactを別配列で保持するが、同じpage indexとeviction lifecycleに結び付ける。 |
| memory accounting / eviction | `ViewerForm.cs:5417-5449`, `SetMemoryUBound`; `:5471`, `ReduceUsingMemory` | `usedMemory`がbyte計上するのはdisplay artifact (`VirtualBitmapEx`)。source bytesを加算してはいない。ただしdisplay artifactをevictすると同じpageの`PreFilteredImageArray`とmetadataも同時にdisposeする。priorityの遠い側から破棄し、最低近傍数を保護する。 |
| 方向反転 | `ViewerForm.cs:5377-5384`, `SetBackgroundMode` call | travel direction専用のqueue反転はない。新current基準のorder再構築により、旧方向の未開始jobを後方へ送る。実行中の最大1 jobは完了する。 |
| stale work / source replacement | `ViewerForm.cs:2757-2771`, open path; `:2882-2915`, `Reload` / `clearResizedImageArray`; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 新loader/arrayへ切り替える前に旧resized配列をclearし、`waitCancel=true`で旧worker終了を待つ。意味のあるgeneration IDではなく、wait-cancelとobject replacementで隔離する。 |
| spread protection | `ViewerForm.cs:1903-2002`, `NextPage` / `PreviousPage`; `:5273`, `GetResizedSize`; `priorityLevel` level 0 | current binding内の最大1/2 pageを同じ表示単位としてsize判定し、partnerもlevel 0で優先する。 |

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

## 4. Structural comparison and adoption decision

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
| `PackedImageLoader.cs:1781`, `OpenImageStream`; `:1856`, `OpenInnerImageStream` | entry単位の読み出しとdecodeへの受け渡し | `app/image_source.py`, `ZipImageSource._read_entry_stream`, `open_qimage_at_most` | 現在は独立実装。Nivisはfull entry bufferを作るため、direct stream構造は未移植。 |
| `ViewerForm.cs:2757-2771`, open; `:2882-2915`, reload; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 古いworkを新sourceへ混入させない | `app/image_cache.py`, `generation`, `_on_loaded`; `app/viewer_window.py`, request ID / `ViewerDisplayUnit`; `app/viewer_widget.py`, render generations | 目的を独立実装。Nivisのgeneration方式を維持。 |

## 6. Removed, replaced, and deliberately retained structures

今回、production Viewer pathでは次を置換した。

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

次は撤去していない。

- `ImageCache`のdecoded/decoder-sized `QImage` cache
- `ViewerWidget`のdisplay-ready `QPixmap` cacheとprepared-unit metadata
- decode taskとrender taskの型およびGUI signal境界
- request ID、source generation、render generation、source identity確認
- coordinatorを注入しないtest/fake用のlocal one-worker fallback pools
- Pillow/PySide6/PDFiumに適合した既存decode/render実装

したがって、今回の実装を「ZipPlaForkの1 jobをそのまま移植した」とは表現しない。
正確には、二段taskを維持したまま、1 unitがdisplay-ready terminalになるまで
次unitをadmitしないpaced single-lane processing structureを移植した。

## 7. Current ready/cold measurement status

次表は最新のpaced single-lane、共通priority、combined cache実装を対象とする。
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
- Raster ZIP decode still materializes a complete uncompressed entry in
  `BytesIO` before decoder input. Direct entry-stream decode remains a possible
  cold-miss optimization after cancellation and Windows lifetime contracts are
  preserved.
- The paced dispatcher retains a Qt queued-signal boundary between decode and
  render instead of one literal runnable. Its remaining dispatch/GUI conversion
  cost is included in the current 53.336–60.200 ms target-decode forced-cold
  paint results.
  Whether it remains perceptible must still be decided on the real device.
- If a source-sized standard render is still running when zoom changes again,
  the stale generation is rejected correctly but the latest generation may
  repeat that one preparation. Completed source-sized artifacts are reused.
- When the page-list dock is visible, its legacy thumbnail path can still
  perform a large-image Smooth scale on the GUI thread. It is independent of
  the normal hidden page-list navigation path and should be moved to a worker
  in a separate change.
- The 128 MiB resident-cache pressure run measured 121.47 MiB of counted
  source-plus-pixmap artifacts, but the sampled process working-set delta was
  146.04 MiB. Task-local images, Qt allocations, decoder buffers, and the
  generated ZIP are outside the resident-cache accounting, so the setting is
  not a hard process-memory limit.
